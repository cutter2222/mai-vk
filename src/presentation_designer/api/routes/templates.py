"""Шаблоны: загрузка (multipart или file_id проекта), список, профиль, превью."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile

from presentation_designer.api.deps import Orch
from presentation_designer.api.errors import ApiError
from presentation_designer.pipeline.files import MIME_BY_FORMAT, format_for, safe_name
from presentation_designer.pipeline.results import template_detail
from presentation_designer.pipeline.state import NotFound

router = APIRouter(tags=["templates"])


async def _body(request: Request) -> tuple[UploadFile | None, dict[str, Any]]:
    """multipart с полем file или JSON с file_id."""
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        fields = {k: v for k, v in form.items() if isinstance(v, str)}
        return (upload if isinstance(upload, UploadFile) else None), fields
    if content_type.startswith("application/json"):
        raw = await request.body()
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError as e:
            raise ApiError(400, "invalid_json", "Тело запроса не JSON") from e
        return None, data if isinstance(data, dict) else {}
    return None, {}


@router.post("/templates", status_code=202)
async def upload_template(request: Request, orch: Orch) -> dict[str, Any]:
    upload, fields = await _body(request)
    if upload is not None:
        name = safe_name(upload.filename or "template.pptx")
        if format_for(name) != "pptx":
            raise ApiError(415, "unsupported_format", "Поддерживается только формат PPTX")
        stored = orch.files.store(upload.file, name)
        orch.state.add_file(
            None,
            sha256=stored.sha256,
            name=name,
            size_bytes=stored.size_bytes,
            mime=MIME_BY_FORMAT["pptx"],
            kind="template",
            check=stored.check,
        )
        template, cached = orch.submit_template(
            sha256=stored.sha256, name=name, size_bytes=stored.size_bytes
        )
    elif fields.get("file_id"):
        try:
            row = orch.state.get_file(str(fields["file_id"]))
        except NotFound as e:
            raise ApiError(404, "file_not_found", "Файл не найден") from e
        if (row.get("check") or {}).get("format") != "pptx":
            raise ApiError(415, "unsupported_format", "Шаблоном может быть только PPTX")
        template, cached = orch.submit_template(
            sha256=row["sha256"], name=row["name"], size_bytes=row["size_bytes"]
        )
        orch.state.patch_file(
            row.get("project_id"),
            row["file_id"],
            {"kind": "template", "template_id": template["template_id"]},
        )
    else:
        raise ApiError(
            400, "file_required", "Не передан файл шаблона: multipart-поле file или JSON {file_id}"
        )
    return {"template_id": template["template_id"], "job_id": template["job_id"], "cached": cached}


@router.get("/templates")
def list_templates(orch: Orch) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in orch.state.list_templates():
        item: dict[str, Any] = {
            "template_id": t["template_id"],
            "name": t["name"],
            "status": t["status"],
            "created_at": t["created_at"],
        }
        stats = (t.get("profile") or {}).get("stats") or {}
        if stats.get("slides"):
            item["slide_count"] = stats["slides"]
        out.append(item)
    return out


@router.get("/templates/{template_id}")
def get_template(template_id: str, orch: Orch) -> dict[str, Any]:
    try:
        return template_detail(orch.state, orch.state.get_template(template_id))
    except NotFound as e:
        raise ApiError(404, "template_not_found", "Шаблон не найден") from e


@router.get("/templates/{template_id}/assets/{name:path}")
def template_asset(template_id: str, name: str, orch: Orch) -> FileResponse:
    try:
        template = orch.state.get_template(template_id)
    except NotFound as e:
        raise ApiError(404, "template_not_found", "Шаблон не найден") from e
    if name not in template["previews"]:
        raise ApiError(404, "asset_not_found", "Ресурс не найден в манифесте шаблона")
    path = orch.artifacts.template_dir(template_id) / name
    if not path.is_file():
        raise ApiError(404, "asset_not_found", "Файл ресурса отсутствует")
    return FileResponse(path, media_type="image/png" if name.endswith(".png") else None)
