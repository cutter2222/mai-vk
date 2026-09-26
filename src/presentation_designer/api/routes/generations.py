"""Генерация: запуск задания, результат, смысловой план, отчёты аудита, исправления,
правки слайдов по запросу и из визуального редактора, артефакты."""

from __future__ import annotations

import json
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from presentation_designer.api.deps import Orch
from presentation_designer.api.errors import ApiError
from presentation_designer.contracts import models as m
from presentation_designer.contracts import validators as v
from presentation_designer.pipeline.artifacts import content_type_for
from presentation_designer.pipeline.jobs import Orchestrator
from presentation_designer.pipeline.results import build_generation_result
from presentation_designer.pipeline.state import NotFound

router = APIRouter(tags=["generations"])

ATTACHMENT_SUFFIXES = (".pptx", ".pdf", ".html")


class RepairRequest(BaseModel):
    base_revision: int = Field(..., ge=1)
    issue_ids: list[str]


class EditRequest(BaseModel):
    base_revision: int = Field(..., ge=1)
    slide_index: int = Field(..., ge=0)
    instruction: str = Field("", max_length=2000)


class RevertRequest(BaseModel):
    """Отмена последней правки: вернуть файлы ревизии `to_revision` новой ревизией."""

    base_revision: int = Field(..., ge=1)
    to_revision: int = Field(..., ge=1)
    slide_index: int = Field(..., ge=0)


class PatchRequest(BaseModel):
    """Ручные правки из визуального редактора: списки overrides по слайдам (замена
    целиком), при перестановке — новый порядок всех слайдов, а `template_logo` снимает или
    возвращает знак шаблона во всей колоде (он живёт на макетах, а не на слайде)."""

    base_revision: int = Field(..., ge=1)
    slides: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    order: list[str] | None = Field(None, max_length=500)
    template_logo: Literal["keep", "drop"] | None = None


def _generation(orch: Orchestrator, job_id: str) -> dict[str, Any]:
    try:
        return orch.state.get_generation(job_id)
    except NotFound as e:
        raise ApiError(
            404,
            "job_not_found",
            "Задание не найдено. Проверьте ссылку или начните новую генерацию.",
        ) from e


@router.post("/generations", status_code=202)
def create_generation(body: dict[str, Any], orch: Orch) -> dict[str, Any]:
    request = m.GenerationRequest.model_validate(body)
    # Нарушения границ отклоняют запрос; предупреждения вроде «exact вместе с диапазоном»
    # не блокируют.
    blocking = [
        x
        for x in v.check_generation_request(request)
        if x.code in {"slide_count_range", "variants_duplicate", "variants_original_alone"}
    ]
    if blocking:
        raise ApiError(
            422,
            blocking[0].code,
            "Минимум слайдов больше максимума"
            if blocking[0].code == "slide_count_range"
            else blocking[0].message,
        )
    try:
        orch.state.get_template(request.template_id)
    except NotFound as e:
        raise ApiError(404, "template_not_found", "Шаблон не найден") from e
    try:
        orch.state.get_package(request.package_id)
    except NotFound as e:
        raise ApiError(404, "package_not_found", "Контент-пакет не найден") from e
    job = orch.submit_generation(request.model_dump(mode="json", exclude_none=True))
    return {"job_id": job["job_id"]}


@router.get("/generations/{job_id}")
def get_generation(job_id: str, orch: Orch) -> dict[str, Any]:
    _generation(orch, job_id)
    return build_generation_result(orch.state, job_id)


@router.get("/generations/{job_id}/story")
def get_story(job_id: str, orch: Orch) -> dict[str, Any]:
    gen = _generation(orch, job_id)
    if not gen.get("story"):
        raise ApiError(404, "story_not_ready", "Смысловой план ещё не построен")
    return gen["story"]  # type: ignore[no-any-return]


@router.get("/generations/{job_id}/variants/{variant_id}/audit")
def get_audit(
    job_id: str, variant_id: str, orch: Orch, revision: int | None = None
) -> dict[str, Any]:
    _generation(orch, job_id)
    try:
        variant = orch.state.get_variant(job_id, variant_id)
    except NotFound as e:
        raise ApiError(404, "variant_not_found", "Вариант не найден") from e
    rev = revision or variant["revision"]
    path = orch.artifacts.revision_dir(job_id, variant_id, rev) / "audit.json"
    manifest = orch.artifacts.read_manifest(job_id, variant_id, rev)
    if f"{orch.artifacts.prefix(variant_id, rev)}audit.json" not in manifest or not path.is_file():
        raise ApiError(404, "audit_not_found", "Отчёт аудита для этой ревизии не найден")
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@router.post("/generations/{job_id}/variants/{variant_id}/repairs", status_code=202)
def create_repair(job_id: str, variant_id: str, body: RepairRequest, orch: Orch) -> dict[str, Any]:
    _generation(orch, job_id)
    if not body.issue_ids:
        raise ApiError(422, "issues_required", "Выберите хотя бы одну находку")
    try:
        job = orch.submit_repair(job_id, variant_id, body.base_revision, body.issue_ids)
    except NotFound as e:
        raise ApiError(404, "variant_not_found", "Вариант не найден") from e
    return {"repair_job_id": job["job_id"]}


@router.post("/generations/{job_id}/variants/{variant_id}/revert", status_code=202)
def revert_edit(job_id: str, variant_id: str, body: RevertRequest, orch: Orch) -> dict[str, Any]:
    """Отмена последней правки варианта из чата: копия прежней ревизии новой ревизией."""
    _generation(orch, job_id)
    try:
        job = orch.submit_revert(
            job_id, variant_id, body.base_revision, body.to_revision, body.slide_index
        )
    except NotFound as e:
        raise ApiError(404, "variant_not_found", "Вариант не найден") from e
    return {"edit_job_id": job["job_id"]}


@router.post("/generations/{job_id}/variants/{variant_id}/edits", status_code=202)
def create_edit(job_id: str, variant_id: str, body: EditRequest, orch: Orch) -> dict[str, Any]:
    """Правка одного слайда по инструкции из чата: новая ревизия варианта, как у исправления."""
    _generation(orch, job_id)
    instruction = " ".join(body.instruction.split())
    if not instruction:
        raise ApiError(422, "instruction_required", "Напишите, что изменить на слайде")
    # Конфликты (устаревшая ревизия, идущая правка, слайд вне диапазона) отдаёт обработчик
    # ConflictError в app.py: 409 для ревизии и занятости, 422 для остального.
    try:
        job = orch.submit_edit(
            job_id, variant_id, body.base_revision, body.slide_index, instruction
        )
    except NotFound as e:
        raise ApiError(404, "variant_not_found", "Вариант не найден") from e
    return {"edit_job_id": job["job_id"]}


@router.post("/generations/{job_id}/variants/{variant_id}/patches", status_code=202)
def create_patch(job_id: str, variant_id: str, body: PatchRequest, orch: Orch) -> dict[str, Any]:
    """Ручные правки редактора: новая ревизия варианта без модели. Документ проверяется по
    схеме slide_patch и против ComposedDeck базовой ревизии; конфликты — как у правок из
    чата (409 revision_stale / repair_in_progress, 422 остальное)."""
    _generation(orch, job_id)
    if not body.slides and body.order is None and body.template_logo is None:
        raise ApiError(422, "patch_empty", "В запросе нет ни правок, ни нового порядка")
    document = {
        "schema_version": "1.0",
        "job_id": job_id,
        "variant_id": variant_id,
        "base_revision": body.base_revision,
        "slides": body.slides,
        **({"order": body.order} if body.order is not None else {}),
        **({"template_logo": body.template_logo} if body.template_logo is not None else {}),
    }
    try:
        m.SlidePatch.model_validate(document)
    except ValidationError as e:
        errors = [
            f"{'.'.join(str(x) for x in err.get('loc', ()))}: {err.get('msg')}"
            for err in e.errors()[:20]
        ]
        raise ApiError(
            422,
            "patch_invalid",
            "Правки не соответствуют схеме: " + "; ".join(errors[:6]),
            {"violations": errors},
        ) from e
    try:
        job = orch.submit_patch(
            job_id,
            variant_id,
            body.base_revision,
            body.slides,
            body.order,
            body.template_logo,
        )
    except NotFound as e:
        raise ApiError(404, "variant_not_found", "Вариант не найден") from e
    return {"patch_job_id": job["job_id"]}


@router.get("/generations/{job_id}/artifacts/{name:path}")
def get_artifact(job_id: str, name: str, orch: Orch) -> FileResponse:
    """Только имена из манифеста задания; пути от клиента не принимаются."""
    _generation(orch, job_id)
    result = build_generation_result(orch.state, job_id)
    entry = result["artifacts_manifest"].get(name)
    if not entry:
        raise ApiError(404, "artifact_not_found", "Артефакт не найден в манифесте задания")
    try:
        path = orch.artifacts.resolve(job_id, name)
    except ValueError as e:
        raise ApiError(404, "artifact_not_found", "Артефакт не найден в манифесте задания") from e
    if not path.is_file():
        raise ApiError(404, "artifact_not_found", "Файл артефакта отсутствует")
    headers: dict[str, str] = {}
    if name.endswith(ATTACHMENT_SUFFIXES):
        variant, rev, _ = name.split("/", 2)
        filename = f"{variant}-{rev}{path.suffix}"
        headers["Content-Disposition"] = (
            f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
        )
    return FileResponse(
        path, media_type=entry.get("content_type") or content_type_for(name), headers=headers
    )
