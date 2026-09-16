"""Содержание: контент-пакет из файлов проекта (file_ids) или multipart для CLI,
результат импорта."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile

from presentation_designer.api.deps import Orch
from presentation_designer.api.errors import ApiError
from presentation_designer.pipeline.files import MIME_BY_FORMAT, default_kind, format_for, safe_name
from presentation_designer.pipeline.results import package_detail
from presentation_designer.pipeline.state import NotFound

router = APIRouter(tags=["content"])


def _parse_brief(value: Any) -> dict[str, Any] | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as e:
            raise ApiError(400, "invalid_brief", "Поле brief должно быть JSON") from e
    if not isinstance(value, dict):
        raise ApiError(400, "invalid_brief", "Поле brief должно быть объектом")
    cleaned = {k: v for k, v in value.items() if v not in ("", None, [])}
    return cleaned or None


@router.post("/content", status_code=202)
async def create_content(request: Request, orch: Orch) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    sources: list[dict[str, Any]] = []
    brief: dict[str, Any] | None
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        uploads = [v for v in form.getlist("files") if isinstance(v, UploadFile)]
        brief = _parse_brief(form.get("brief"))
        if len(uploads) > orch.settings.limits.max_content_files:
            raise ApiError(
                400, "too_many_files", f"Не больше {orch.settings.limits.max_content_files} файлов"
            )
        for upload in uploads:
            name = safe_name(upload.filename or "file")
            stored = orch.files.store(upload.file, name)
            mime = upload.content_type or MIME_BY_FORMAT.get(
                format_for(name), "application/octet-stream"
            )
            row = orch.state.add_file(
                None,
                sha256=stored.sha256,
                name=name,
                size_bytes=stored.size_bytes,
                mime=mime,
                kind=default_kind(name),
                check=stored.check,
            )
            sources.append(row)
    else:
        try:
            data = json.loads(await request.body() or b"{}")
        except json.JSONDecodeError as e:
            raise ApiError(400, "invalid_json", "Тело запроса не JSON") from e
        file_ids = data.get("file_ids") or []
        if not isinstance(file_ids, list):
            raise ApiError(400, "invalid_request", "file_ids должен быть списком")
        if len(file_ids) > orch.settings.limits.max_content_files:
            raise ApiError(
                400, "too_many_files", f"Не больше {orch.settings.limits.max_content_files} файлов"
            )
        brief = _parse_brief(data.get("brief"))
        for file_id in file_ids:
            try:
                sources.append(orch.state.get_file(str(file_id)))
            except NotFound as e:
                raise ApiError(404, "file_not_found", f"Файл {file_id} не найден") from e
    if not sources and not brief:
        raise ApiError(400, "content_required", "Добавьте файлы контент-пакета или заполните бриф")
    rejected = [s["name"] for s in sources if (s.get("check") or {}).get("status") == "rejected"]
    if rejected:
        raise ApiError(422, "file_rejected", f"Файлы не прошли проверку: {', '.join(rejected)}")
    package, cached = orch.submit_package(sources=sources, brief=brief)
    for src in sources:
        orch.state.patch_file(
            src.get("project_id"), src["file_id"], {"package_id": package["package_id"]}
        )
    return {"package_id": package["package_id"], "job_id": package["job_id"], "cached": cached}


@router.get("/content/{package_id}")
def get_content(package_id: str, orch: Orch) -> dict[str, Any]:
    try:
        return package_detail(orch.state.get_package(package_id))
    except NotFound as e:
        raise ApiError(404, "package_not_found", "Контент-пакет не найден") from e


@router.get("/content/{package_id}/assets/{name:path}")
def content_asset(package_id: str, name: str, orch: Orch) -> FileResponse:
    try:
        package = orch.state.get_package(package_id)
    except NotFound as e:
        raise ApiError(404, "package_not_found", "Контент-пакет не найден") from e
    assets = {a.get("path") for a in (package.get("package") or {}).get("assets", [])}
    path = orch.artifacts.package_dir(package_id) / name
    if name not in assets or not path.is_file():
        raise ApiError(404, "asset_not_found", "Ресурс не найден в манифесте пакета")
    return FileResponse(path)
