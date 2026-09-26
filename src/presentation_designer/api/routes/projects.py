"""Проекты: список с состоянием, проект по идентификатору, лента событий, файлы проекта."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from presentation_designer.api.deps import Orch
from presentation_designer.api.errors import ApiError
from presentation_designer.contracts import models as m
from presentation_designer.parsing.content import chat_text
from presentation_designer.pipeline.files import MIME_BY_FORMAT, default_kind, format_for, safe_name
from presentation_designer.pipeline.results import result_or_none
from presentation_designer.pipeline.state import NotFound
from presentation_designer.pipeline.thumbnails import thumbnail

router = APIRouter(tags=["projects"])

DEFAULT_TITLE = "Новая презентация"
DEFAULT_BRIEF: dict[str, Any] = {
    "purpose": "",
    "title": "",
    "audience": "",
    "goal": "",
    "language": "ru",
    "tone": "",
    "must_include": [],
    "avoid": [],
}
DEFAULT_SETTINGS: dict[str, Any] = {
    "mode": "range",
    "min": 10,
    "max": 15,
    "exact": 12,
    "variants": ["compact", "balanced", "detailed"],
    "contextual": True,
    "images": False,
    "seed": None,
    "force": False,
}


class ProjectCreate(BaseModel):
    title: str | None = Field(None, max_length=200)
    brief: m.BriefDraft | None = None
    settings: m.SettingsDraft | None = None
    job_id: str | None = None


class ProjectPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    template_id: str | None = None
    package_id: str | None = None
    job_id: str | None = None
    chosen_variant: str | None = None
    brief: m.BriefDraft | None = None
    settings: m.SettingsDraft | None = None


class SlideRef(BaseModel):
    job_id: str
    variant_id: str
    revision: int = Field(..., ge=1)
    slide_index: int = Field(..., ge=0)


class EventCreate(BaseModel):
    role: str
    kind: str
    text: str | None = None
    file_ids: list[str] | None = None
    file_id: str | None = None
    resolved: str | None = None
    template_id: str | None = None
    package_id: str | None = None
    job_id: str | None = None
    understood: list[str] | None = None
    missing_purpose: bool | None = None
    brief_source: str | None = None
    slide_ref: SlideRef | None = None
    variant_id: str | None = None
    edit_job_id: str | None = None
    slide_index: int | None = Field(None, ge=0)
    document_id: str | None = None
    revision: int | None = Field(None, ge=0)
    base_revision: int | None = Field(None, ge=0)
    slides: list[int] | None = None
    undone: bool | None = None


class EventPatch(BaseModel):
    resolved: str | None = None
    text: str | None = None
    undone: bool | None = None


class FilePatch(BaseModel):
    kind: str | None = None
    template_id: str | None = None
    package_id: str | None = None


def _project_doc(project: dict[str, Any]) -> dict[str, Any]:
    doc = {"schema_version": "1.6", **project}
    return m.Project.model_validate(doc).model_dump(mode="json", exclude_none=True)


def _event_doc(event: dict[str, Any]) -> dict[str, Any]:
    return m.Event.model_validate(event).model_dump(mode="json", exclude_none=True)


@router.get("/projects")
def list_projects(orch: Orch) -> list[dict[str, Any]]:
    """Список для главной: состояние задания и миниатюра приходят вместе с проектом."""
    templates = {t["template_id"]: t for t in orch.state.list_templates()}
    out: list[dict[str, Any]] = []
    for project in orch.state.list_projects():
        item: dict[str, Any] = dict(project)
        files_count, _ = orch.state.project_usage(project["project_id"])
        item["files_count"] = files_count
        template = templates.get(project["template_id"] or "")
        item["template_name"] = template["name"] if template else None
        item["job_status"] = None
        item["thumbnail_url"] = None
        item["slide_count"] = None
        if project["job_id"]:
            result = result_or_none(orch.state, project["job_id"])
            if result:
                item["job_status"] = result["status"]
                variants = result["variants"]
                chosen = next(
                    (
                        v
                        for v in variants
                        if v["variant_id"] == project["chosen_variant"]
                        and v["artifacts"].get("thumbnails")
                    ),
                    None,
                )
                variant = chosen or next(
                    (v for v in variants if v["artifacts"].get("thumbnails")), None
                )
                if variant:
                    item["thumbnail_url"] = (
                        f"/api/generations/{project['job_id']}/artifacts/{variant['artifacts']['thumbnails'][0]['name']}"
                    )
                    item["slide_count"] = variant.get("slide_count")
        if (
            item["thumbnail_url"] is None
            and template
            and template["status"] == "succeeded"
            and template["previews"]
        ):
            item["thumbnail_url"] = (
                f"/api/templates/{template['template_id']}/assets/{template['previews'][0]}"
            )
        out.append(item)
    return out


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, orch: Orch) -> dict[str, Any]:
    brief = body.brief.model_dump() if body.brief else dict(DEFAULT_BRIEF)
    settings = body.settings.model_dump() if body.settings else dict(DEFAULT_SETTINGS)
    project = orch.state.create_project(
        (body.title or DEFAULT_TITLE).strip() or DEFAULT_TITLE, brief, settings, job_id=body.job_id
    )
    return _project_doc(project)


@router.get("/projects/{project_id}")
def get_project(project_id: str, orch: Orch) -> dict[str, Any]:
    try:
        return _project_doc(orch.state.get_project(project_id))
    except NotFound as e:
        raise ApiError(404, "project_not_found", "Проект не найден") from e


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, body: ProjectPatch, orch: Orch) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    for key in ("brief", "settings"):
        if key in patch and patch[key] is not None:
            patch[key] = patch[key]
    try:
        return _project_doc(orch.state.update_project(project_id, patch))
    except NotFound as e:
        raise ApiError(404, "project_not_found", "Проект не найден") from e


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, orch: Orch) -> Response:
    """Удаляет проект и ленту, снимает ссылки на файлы;
    артефакты заданий остаются до сборки мусора."""
    try:
        orch.state.delete_project(project_id)
    except NotFound as e:
        raise ApiError(404, "project_not_found", "Проект не найден") from e
    return Response(status_code=204)


# ---------- лента событий ----------


@router.get("/projects/{project_id}/events")
def list_events(project_id: str, orch: Orch) -> list[dict[str, Any]]:
    try:
        return [_event_doc(e) for e in orch.state.list_events(project_id)]
    except NotFound as e:
        raise ApiError(404, "project_not_found", "Проект не найден") from e


@router.post("/projects/{project_id}/events", status_code=201)
def append_event(project_id: str, body: EventCreate, orch: Orch) -> dict[str, Any]:
    payload = body.model_dump(exclude_none=True)
    try:
        event = orch.state.append_event(project_id, payload)
    except NotFound as e:
        raise ApiError(404, "project_not_found", "Проект не найден") from e
    return _event_doc(event)


@router.patch("/projects/{project_id}/events/{event_id}")
def patch_event(project_id: str, event_id: str, body: EventPatch, orch: Orch) -> dict[str, Any]:
    try:
        return _event_doc(
            orch.state.patch_event(project_id, event_id, body.model_dump(exclude_none=True))
        )
    except NotFound as e:
        raise ApiError(404, "event_not_found", "Событие не найдено") from e


# ---------- файлы проекта ----------


@router.post("/projects/{project_id}/files", status_code=201)
def upload_files(project_id: str, files: list[UploadFile], orch: Orch) -> list[dict[str, Any]]:
    """Загрузка одного или нескольких файлов потоком: проверка, дедупликация по sha256,
    квоты проекта."""
    try:
        orch.state.get_project(project_id)
    except NotFound as e:
        raise ApiError(404, "project_not_found", "Проект не найден") from e
    if not files:
        raise ApiError(400, "file_required", "Не переданы файлы")
    limits = orch.settings.limits
    count, total = orch.state.project_usage(project_id)
    if count + len(files) > limits.max_project_files:
        raise ApiError(
            413, "project_files_quota", f"В проекте не больше {limits.max_project_files} файлов"
        )
    out: list[dict[str, Any]] = []
    for upload in files:
        name = safe_name(upload.filename or "file")
        stored = orch.files.store(upload.file, name)
        if (
            total + stored.size_bytes > limits.max_project_mb * 1024 * 1024
            and not orch.state.blob_references(stored.sha256)
        ):
            orch.files.remove(stored.sha256)
            raise ApiError(
                413,
                "project_size_quota",
                f"Суммарный объём файлов проекта не больше {limits.max_project_mb} МБ",
            )
        total += stored.size_bytes
        mime = upload.content_type or MIME_BY_FORMAT.get(
            format_for(name), "application/octet-stream"
        )
        row = orch.state.add_file(
            project_id,
            sha256=stored.sha256,
            name=name,
            size_bytes=stored.size_bytes,
            mime=mime,
            kind=default_kind(name),
            check=stored.check,
        )
        out.append(m.ProjectFile.model_validate(row).model_dump(mode="json", exclude_none=True))
    return out


class TextMaterial(BaseModel):
    text: str = Field(..., min_length=1, max_length=100_000)


CHAT_TEXT_NAME = "Текст из чата"


@router.post("/projects/{project_id}/files/text", status_code=201)
def upload_text(project_id: str, body: TextMaterial, orch: Orch) -> dict[str, Any]:
    """Сообщение чата с содержанием (раскладка «Слайд N», тезисы, цифры) — материал проекта:
    Markdown с восстановленными строками разбирается импортом, как документ, и его факты
    доходят до слайдов, а не теряются в брифе."""
    try:
        project = orch.state.get_project(project_id)
    except NotFound as e:
        raise ApiError(404, "project_not_found", "Проект не найден") from e
    limits = orch.settings.limits
    count, total = orch.state.project_usage(project_id)
    if count + 1 > limits.max_project_files:
        raise ApiError(
            413, "project_files_quota", f"В проекте не больше {limits.max_project_files} файлов"
        )
    taken = {f["name"] for f in project["files"]}
    name = next(
        n
        for n in (f"{CHAT_TEXT_NAME}{f' {i}' if i > 1 else ''}.md" for i in range(1, count + 2))
        if n not in taken
    )
    stored = orch.files.store_bytes(chat_text.to_markdown(body.text).encode("utf-8"), name)
    if (
        total + stored.size_bytes > limits.max_project_mb * 1024 * 1024
        and not orch.state.blob_references(stored.sha256)
    ):
        orch.files.remove(stored.sha256)
        raise ApiError(
            413,
            "project_size_quota",
            f"Суммарный объём файлов проекта не больше {limits.max_project_mb} МБ",
        )
    row = orch.state.add_file(
        project_id,
        sha256=stored.sha256,
        name=name,
        size_bytes=stored.size_bytes,
        mime=MIME_BY_FORMAT.get("markdown", "text/markdown"),
        kind="material",
        check=stored.check,
    )
    return m.ProjectFile.model_validate(row).model_dump(mode="json", exclude_none=True)


@router.patch("/projects/{project_id}/files/{file_id}")
def patch_file(project_id: str, file_id: str, body: FilePatch, orch: Orch) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True)
    if "kind" in patch and patch["kind"] not in {"template", "material", "other"}:
        raise ApiError(422, "invalid_kind", "Вид файла: template, material или other")
    try:
        row = orch.state.patch_file(project_id, file_id, patch)
    except NotFound as e:
        raise ApiError(404, "file_not_found", "Файл не найден") from e
    return m.ProjectFile.model_validate(row).model_dump(mode="json", exclude_none=True)


@router.delete("/projects/{project_id}/files/{file_id}", status_code=204)
def delete_file(project_id: str, file_id: str, orch: Orch) -> Response:
    """Снимает ссылку проекта на файл; байты без ссылок удаляет сборка мусора
    по сроку из конфига."""
    try:
        orch.state.delete_file(project_id, file_id)
    except NotFound as e:
        raise ApiError(404, "file_not_found", "Файл не найден") from e
    return Response(status_code=204)


# Байты файла по идентификатору не меняются: ссылка кэшируется навсегда.
IMMUTABLE = "private, max-age=31536000, immutable"
# Картинки и PDF открываются во вкладке; остальное только скачивается. Тип берётся из
# проверенного формата, а не из заголовка браузера при загрузке: SVG и HTML под видом
# картинки не исполняются на домене сервиса.
INLINE_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "pdf": "application/pdf",
}


def _project_file(orch: Orch, project_id: str, file_id: str) -> dict[str, Any]:
    try:
        return orch.state.get_file(file_id, project_id)
    except NotFound as e:
        raise ApiError(404, "file_not_found", "Файл не найден") from e


def _not_modified(request: Request, sha256: str) -> tuple[bool, str]:
    etag = f'"{sha256}"'
    return request.headers.get("if-none-match") == etag, etag


@router.get("/projects/{project_id}/files/{file_id}/content")
def file_content(project_id: str, file_id: str, orch: Orch, request: Request) -> Response:
    """Байты файла проекта: картинка для сетки «Файлы» и для вставки на слайд редактором."""
    row = _project_file(orch, project_id, file_id)
    fresh, etag = _not_modified(request, row["sha256"])
    if fresh:
        return Response(status_code=304)
    path = orch.files.path_for(row["sha256"])
    if not path.is_file():
        raise ApiError(404, "file_missing", "Байты файла удалены из хранилища")
    ext = row["name"].rsplit(".", 1)[-1].lower() if "." in row["name"] else ""
    checked = (row.get("check") or {}).get("status") == "ok"
    inline = INLINE_TYPES.get(ext) if checked else None
    fallback = MIME_BY_FORMAT.get(format_for(row["name"]), "application/octet-stream")
    return FileResponse(
        path,
        filename=row["name"],
        media_type=inline or fallback,
        content_disposition_type="inline" if inline else "attachment",
        headers={"ETag": etag, "Cache-Control": IMMUTABLE, "X-Content-Type-Options": "nosniff"},
    )


@router.get("/projects/{project_id}/files/{file_id}/thumbnail")
def file_thumbnail(project_id: str, file_id: str, orch: Orch, request: Request) -> Response:
    """Миниатюра WebP до 480 px: картинка, первая страница PDF, обложка PPTX (из пакета или
    первый образец разобранного шаблона). Строится один раз на байты; `404
    thumbnail_unavailable` — показать значок типа."""
    row = _project_file(orch, project_id, file_id)
    fresh, etag = _not_modified(request, row["sha256"])
    if fresh:
        return Response(status_code=304)
    if (row.get("check") or {}).get("status") != "ok":
        raise ApiError(404, "thumbnail_unavailable", "Для этого файла миниатюры нет")
    try:
        path = thumbnail(
            orch.files,
            row["sha256"],
            str(row["check"].get("format") or ""),
            fallback=_template_cover(orch, row.get("template_id")),
        )
    except FileNotFoundError as e:
        raise ApiError(404, "file_missing", "Байты файла удалены из хранилища") from e
    if path is None:
        raise ApiError(404, "thumbnail_unavailable", "Для этого файла миниатюры нет")
    return FileResponse(
        path, media_type="image/webp", headers={"ETag": etag, "Cache-Control": IMMUTABLE}
    )


def _template_cover(orch: Orch, template_id: str | None) -> Path | None:
    """Первый отрисованный слайд шаблона, если PPTX уже разобран как шаблон."""
    if not template_id:
        return None
    try:
        template = orch.state.get_template(template_id)
    except NotFound:
        return None
    slides = sorted(
        (name for name in template.get("previews") or [] if "/layout-" not in name),
        key=lambda name: (len(name), name),
    )
    if template["status"] != "succeeded" or not slides:
        return None
    return orch.artifacts.template_dir(template_id) / str(slides[0])
