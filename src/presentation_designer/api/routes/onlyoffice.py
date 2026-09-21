"""Local ONLYOFFICE integration. JWT authenticates service traffic, not application users."""

from __future__ import annotations

import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from presentation_designer.api.deps import Orch
from presentation_designer.api.errors import ApiError
from presentation_designer.export.office_download import export_revision
from presentation_designer.export.office_source import saved_url, source_path
from presentation_designer.export.pdf import ConversionError, RendererUnavailableError
from presentation_designer.generation import office_edit
from presentation_designer.llm.types import LlmError
from presentation_designer.parsing.template.embedded_fonts import prepare_fonts
from presentation_designer.pipeline.artifacts import content_type_for
from presentation_designer.pipeline.office import OfficeStore, sign, verify
from presentation_designer.pipeline.results import build_generation_result
from presentation_designer.pipeline.state import NotFound

router = APIRouter(prefix="/office", tags=["onlyoffice"])


class OpenRequest(BaseModel):
    job_id: str = Field(max_length=100)
    artifact: str = Field(max_length=300)


class EditRequest(BaseModel):
    revision: int = Field(ge=0)
    instruction: str = Field(min_length=1, max_length=4000)


@router.post("/documents/{document_id}/edit")
async def edit(document_id: str, body: EditRequest, orch: Orch) -> dict[str, Any]:
    office = store(orch)
    token = office.begin_edit(document_id, body.revision)
    try:
        original = office.read(document_id, body.revision)
        plan = await office_edit.propose(original, body.instruction, orch.settings)
        updated = office_edit.patch_pptx(original, plan)
        office.commit_edit(document_id, token, body.revision, updated)
        return {
            "document": office.get(document_id),
            "changed": original != updated,
            "message": plan.explanation,
        }
    except (ValueError, LlmError) as exc:
        raise ApiError(422, "office_edit_failed", "Правка не применена: " + str(exc)) from exc
    finally:
        office.end_edit(document_id, token)


def store(orch: Orch) -> OfficeStore:
    cfg = orch.settings.onlyoffice
    if not cfg.enabled or len(cfg.jwt_secret) < 32:
        raise ApiError(503, "office_disabled", "ONLYOFFICE не настроен")
    return OfficeStore(orch.settings.paths.data_dir)


@router.get("/capabilities")
def capabilities(orch: Orch) -> dict[str, bool]:
    cfg = orch.settings.onlyoffice
    return {"enabled": cfg.enabled and len(cfg.jwt_secret) >= 32}


@router.post("/documents")
def create(body: OpenRequest, orch: Orch) -> dict[str, Any]:
    office = store(orch)
    result = build_generation_result(orch.state, body.job_id)
    if body.artifact not in result["artifacts_manifest"] or not body.artifact.endswith(".pptx"):
        raise ApiError(404, "artifact_not_found", "PPTX не найден в манифесте")
    path = orch.artifacts.resolve(body.job_id, body.artifact)
    if not path.is_file():
        raise ApiError(404, "artifact_not_found", "PPTX отсутствует")
    if path.stat().st_size > orch.files.max_bytes:
        raise ApiError(413, "office_file_too_large", "PPTX превышает лимит загрузки")
    return office.create(
        f"{body.job_id}/{body.artifact}",
        f"{body.job_id}-{body.artifact.replace('/', '-')}",
        path.read_bytes(),
    )


def template_source(template_id: str, orch: Orch) -> tuple[dict[str, Any], Path]:
    try:
        template = orch.state.get_template(template_id)
    except NotFound as exc:
        raise ApiError(404, "template_not_found", "Шаблон не найден") from exc
    path = orch.files.path_for(template["sha256"])
    if not path.is_file():
        raise ApiError(404, "template_source_missing", "Исходный PPTX отсутствует")
    return template, path


@router.post("/templates/{template_id}/config")
def template_config(template_id: str, orch: Orch) -> dict[str, Any]:
    # Просмотр не создаёт OfficeStore/ревизий и не меняет профиль анализатора.
    cfg = orch.settings.onlyoffice
    if not cfg.enabled or len(cfg.jwt_secret) < 32:
        raise ApiError(503, "office_disabled", "ONLYOFFICE не настроен")
    template, path = template_source(template_id, orch)
    font_report = prepare_fonts(path, orch.settings.paths.data_dir)
    token = sign(
        {
            "scope": "office-template",
            "template_id": template_id,
            "sha256": template["sha256"],
            "exp": int(time.time()) + 86400,
        },
        cfg.jwt_secret,
    )
    editor = {
        "documentType": "slide",
        "type": "desktop",
        "width": "100%",
        "height": "100%",
        "document": {
            "fileType": "pptx",
            "key": f"template-{template['sha256']}",
            "title": template["name"],
            "url": (
                f"{cfg.storage_url.rstrip('/')}/api/office/templates/{template_id}/source"
                f"?{urlencode({'token': token})}"
            ),
            "permissions": {"edit": False, "download": False, "print": True, "comment": False},
        },
        "editorConfig": {
            "mode": "view",
            "lang": "ru",
            "user": {"id": uuid.uuid4().hex, "name": "Просмотр шаблона"},
            "customization": {
                "anonymous": {"request": False},
                "compactHeader": True,
                "toolbarHideFileName": True,
                "plugins": False,
                "macros": False,
                "help": False,
                "feedback": {"visible": False},
                "suggestFeature": False,
            },
        },
    }
    return {
        "font_report": font_report,
        "script_url": (
            f"{cfg.public_url.rstrip('/')}/web-apps/apps/api/documents/api.js?rendering=pd16v2"
        ),
        "config": {**editor, "token": sign(editor, cfg.jwt_secret)},
    }


@router.get("/templates/{template_id}/source")
def signed_template_source(template_id: str, token: str, orch: Orch) -> FileResponse:
    cfg = orch.settings.onlyoffice
    if not cfg.enabled or len(cfg.jwt_secret) < 32:
        raise ApiError(503, "office_disabled", "ONLYOFFICE не настроен")
    claims = verify(token, orch.settings.onlyoffice.jwt_secret)
    if (
        claims.get("scope") != "office-template"
        or claims.get("template_id") != template_id
        or "exp" not in claims
    ):
        raise ApiError(403, "office_token_invalid", "Недействительная ссылка шаблона")
    template, path = template_source(template_id, orch)
    if claims.get("sha256") != template["sha256"]:
        raise ApiError(403, "office_token_invalid", "Версия шаблона изменилась")
    return FileResponse(
        path, media_type=content_type_for("deck.pptx"), headers={"Cache-Control": "no-store"}
    )


@router.get("/documents/{document_id}")
def detail(document_id: str, orch: Orch) -> dict[str, Any]:
    return store(orch).get(document_id)


@router.post("/documents/{document_id}/config")
def config(document_id: str, orch: Orch) -> dict[str, Any]:
    office = store(orch)
    doc = office.open(document_id)
    font_report = prepare_fonts(
        office.read(document_id, doc["seed_revision"]), orch.settings.paths.data_dir,
    )
    cfg = orch.settings.onlyoffice
    revision = doc["seed_revision"]
    token = sign(
        {
            "document_id": document_id,
            "revision": revision,
            "scope": "office-file",
            "exp": int(time.time()) + 7 * 86400,
        },
        cfg.jwt_secret,
    )
    base = f"{cfg.storage_url.rstrip('/')}/api/office/documents/{document_id}"
    editor = {
        "documentType": "slide",
        "type": "desktop",
        "width": "100%",
        "height": "100%",
        "document": {
            "fileType": "pptx",
            "key": doc["active_key"],
            "title": doc["title"],
            "url": f"{base}/source/{revision}?{urlencode({'token': token})}",
            "permissions": {"edit": True, "download": False, "print": True},
        },
        "editorConfig": {
            "mode": "edit",
            "lang": "ru",
            "callbackUrl": f"{base}/callback",
            "user": {"id": uuid.uuid4().hex, "name": "Локальный редактор"},
            "customization": {
                "forcesave": True,
                "autosave": True,
                # The host project already shows the title; do not repeat a raw artifact ID.
                "compactHeader": True,
                "toolbarHideFileName": True,
                # AI is provided by the host; do not expose a second plugin workflow.
                "plugins": False,
                "macros": False,
                "help": False,
                "feedback": {"visible": False},
                "suggestFeature": False,
            },
        },
    }
    return {
        "font_report": font_report,
        "script_url": (
            f"{cfg.public_url.rstrip('/')}/web-apps/apps/api/documents/api.js?rendering=pd16v2"
        ),
        "config": {**editor, "token": sign(editor, cfg.jwt_secret)},
    }


@router.get("/documents/{document_id}/source/{revision}")
def source(document_id: str, revision: int, token: str, orch: Orch) -> Response:
    office = store(orch)
    claims = verify(token, orch.settings.onlyoffice.jwt_secret)
    if (claims.get("scope"), claims.get("document_id"), claims.get("revision")) != (
        "office-file",
        document_id,
        revision,
    ):
        raise ApiError(403, "office_token_invalid", "Подпись не соответствует файлу")
    return Response(office.read(document_id, revision), media_type=content_type_for("deck.pptx"))


@router.get("/documents/{document_id}/download/{revision}")
def download(
    document_id: str, revision: int, orch: Orch, format: Literal["pptx", "pdf", "html"] = "pptx"
) -> Response:
    content = store(orch).read(document_id, revision)
    if format != "pptx":
        try:
            content = export_revision(content, format, orch.settings)
        except (ConversionError, RendererUnavailableError) as exc:
            raise ApiError(
                503, "office_export_failed", "Не удалось экспортировать презентацию"
            ) from exc
    return Response(
        content,
        media_type=content_type_for(f"deck.{format}"),
        headers={
            "Content-Disposition": (
                f'attachment; filename="office-{document_id}-v{revision}.{format}"'
            ),
            "Cache-Control": "no-store",
        },
    )


@router.get("/conversions/{key}")
def conversion_source(key: str, orch: Orch, token: str = "") -> FileResponse:
    cfg = orch.settings.onlyoffice
    if not cfg.enabled or len(cfg.jwt_secret) < 32:
        raise ApiError(503, "office_disabled", "ONLYOFFICE не настроен")
    claims = verify(token, cfg.jwt_secret)
    if (
        claims.get("scope") != "office-conversion"
        or claims.get("key") != key
        or "exp" not in claims
    ):
        raise ApiError(403, "office_token_invalid", "Недействительная ссылка конвертации")
    try:
        path = source_path(orch.settings, key)
    except ValueError as exc:
        raise ApiError(404, "conversion_not_found", "Файл конвертации не найден") from exc
    if not path.is_file():
        raise ApiError(404, "conversion_not_found", "Файл конвертации удалён")
    return FileResponse(
        path, media_type=content_type_for("deck.pptx"), headers={"Cache-Control": "no-store"}
    )


@router.post("/documents/{document_id}/callback")
async def callback(document_id: str, request: Request, orch: Orch) -> dict[str, int]:
    office = store(orch)
    if len(await request.body()) > 131072:
        raise ApiError(413, "office_callback_too_large", "Слишком большой callback")
    try:
        body = await request.json()
    except ValueError as exc:
        raise ApiError(400, "office_callback_invalid", "Некорректный JSON") from exc
    if not isinstance(body, dict):
        raise ApiError(400, "office_callback_invalid", "Ожидался объект callback")
    # JWT_IN_BODY=true: use ONLY signed claims, not unsigned siblings from the HTTP body.
    claims = verify(body.get("token", ""), orch.settings.onlyoffice.jwt_secret)
    key, status = claims.get("key"), claims.get("status")
    if not isinstance(key, str) or type(status) is not int or status not in {1, 2, 3, 4, 6, 7}:
        return {"error": 1}
    if office.get(document_id)["active_key"] != key:
        return {"error": 1}
    data = None
    if status in {2, 6}:
        try:
            cfg = orch.settings.onlyoffice
            url = saved_url(claims["url"], cfg.internal_url, cfg.public_url)
            async with httpx.AsyncClient(
                timeout=60, follow_redirects=False, trust_env=False
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > orch.files.max_bytes:
                            raise ValueError("save exceeds size limit")
                    data = bytes(chunks)
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "deck.pptx"
                path.write_bytes(data)
                if orch.files.check(path, "pptx")["status"] != "ok":
                    raise ValueError("invalid saved PPTX")
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            office.callback(document_id, key, 7, None)
            return {"error": 1}
    return {"error": 0 if office.callback(document_id, key, status, data) else 1}
