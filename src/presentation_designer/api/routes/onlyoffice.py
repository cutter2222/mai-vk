"""Local ONLYOFFICE integration. JWT authenticates service traffic, not application users."""

from __future__ import annotations

import json
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, model_validator

from presentation_designer.api.deps import Orch
from presentation_designer.api.errors import ApiError
from presentation_designer.export.office_download import export_revision
from presentation_designer.export.office_ooxml import validate_saved_pptx
from presentation_designer.export.office_preview import cache_path, preview_page, preview_revision
from presentation_designer.export.office_source import saved_url, source_path
from presentation_designer.export.pdf import ConversionError, RendererUnavailableError
from presentation_designer.generation import office_edit, office_logo, office_object_edit
from presentation_designer.generation.office_objects import ObjectTarget, objects
from presentation_designer.llm.types import LlmError
from presentation_designer.parsing.template.embedded_fonts import prepare_fonts
from presentation_designer.pipeline.artifacts import content_type_for
from presentation_designer.pipeline.office import OfficeStore, sign, verify
from presentation_designer.pipeline.results import build_generation_result
from presentation_designer.pipeline.state import NotFound
from presentation_designer.shared.text import plural

router = APIRouter(prefix="/office", tags=["onlyoffice"])


class OpenRequest(BaseModel):
    job_id: str = Field(max_length=100)
    artifact: str = Field(max_length=300)


class LogoRequest(BaseModel):
    """Знак шаблона на всех слайдах: заменить картинкой из файлов проекта или убрать."""

    action: Literal["replace", "remove"]
    file_id: str | None = Field(default=None, max_length=100)


class EditRequest(BaseModel):
    revision: int = Field(ge=0)
    instruction: str = Field(min_length=1, max_length=4000)
    target: ObjectTarget | None = None
    targets: list[ObjectTarget] | None = Field(default=None, min_length=1, max_length=100)
    logo: LogoRequest | None = None

    @model_validator(mode="after")
    def validate_targets(self) -> EditRequest:
        if self.logo is not None and (self.target is not None or self.targets is not None):
            raise ValueError("Логотип меняется на всех слайдах: объекты не передаются")
        if self.targets is not None:
            if self.target is not None:
                raise ValueError("Передайте target или targets, не оба поля")
            keys = {(target.slide, target.shape_id) for target in self.targets}
            if len(keys) != len(self.targets):
                raise ValueError("Объекты не должны повторяться")
        return self


class ImageRequest(BaseModel):
    """Картинка для текущего слайда живого редактора: файл проекта или ресурс шаблона."""

    project_id: str | None = Field(default=None, max_length=100)
    file_id: str | None = Field(default=None, max_length=100)
    template_id: str | None = Field(default=None, max_length=100)
    asset_id: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_source(self) -> ImageRequest:
        from_file = bool(self.project_id and self.file_id)
        from_template = bool(self.template_id and self.asset_id)
        if from_file == from_template:
            raise ValueError("Передайте project_id и file_id или template_id и asset_id")
        return self


# Картинки, которые Document Server вставляет по ссылке; тип — по расширению проверенного файла.
INSERTABLE = {"png": "png", "jpg": "jpg", "jpeg": "jpg", "gif": "gif", "bmp": "bmp"}


@router.post("/documents/{document_id}/images")
def insert_image(document_id: str, body: ImageRequest, orch: Orch) -> dict[str, Any]:
    """Подписанная команда `docEditor.insertImage`: Document Server сам скачивает картинку
    по внутреннему адресу API (`storage_url`) и ставит её на текущий слайд. Сервер только
    проверяет источник и подписывает ссылку, документ не меняет."""
    store(orch).get(document_id)
    cfg = orch.settings.onlyoffice
    base = cfg.storage_url.rstrip("/") + "/api"
    if body.project_id and body.file_id:
        try:
            row = orch.state.get_file(body.file_id, body.project_id)
        except NotFound as exc:
            raise ApiError(404, "file_not_found", "Файл не найден в проекте") from exc
        ext = _extension(str(row["name"]))
        check = row.get("check") or {}
        if check.get("status") != "ok" or check.get("format") != "image" or ext not in INSERTABLE:
            raise ApiError(422, "file_not_image", "На слайд вставляются картинки PNG или JPEG")
        if not orch.files.path_for(str(row["sha256"])).is_file():
            raise ApiError(404, "file_missing", "Байты файла удалены из хранилища")
        url = f"{base}/projects/{quote(body.project_id)}/files/{quote(body.file_id)}/content"
    else:
        template_id, asset_id = str(body.template_id), str(body.asset_id)
        try:
            template = orch.state.get_template(template_id)
        except NotFound as exc:
            raise ApiError(404, "template_not_found", "Шаблон не найден") from exc
        asset = next(
            (
                a
                for a in (template.get("profile") or {}).get("assets") or []
                if a.get("asset_id") == asset_id
            ),
            None,
        )
        if asset is None:
            raise ApiError(404, "asset_not_found", "Ресурс не найден в профиле шаблона")
        ext = _extension(str(asset.get("media_path") or ""))
        if ext not in INSERTABLE:
            raise ApiError(422, "file_not_image", "Этот ресурс шаблона нельзя вставить картинкой")
        try:
            orch.extract_template_media(template_id, asset)
        except FileNotFoundError as exc:
            raise ApiError(404, "asset_not_found", "Файл ресурса отсутствует") from exc
        url = f"{base}/templates/{quote(template_id)}/media/{quote(asset_id)}"
    command: dict[str, Any] = {"c": "add", "images": [{"fileType": INSERTABLE[ext], "url": url}]}
    return {**command, "token": sign(command, cfg.jwt_secret)}


def _extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


@router.get("/documents/{document_id}/objects/{revision}")
def document_objects(document_id: str, revision: int, orch: Orch) -> dict[str, Any]:
    data = store(orch).read(document_id, revision)
    return {"revision": revision, "objects": [obj.model_dump() for obj in objects(data)]}


@router.post("/documents/{document_id}/edit")
async def edit(document_id: str, body: EditRequest, orch: Orch) -> dict[str, Any]:
    office = store(orch)
    token = office.begin_edit(document_id, body.revision)
    try:
        original = office.read(document_id, body.revision)
        if body.logo is not None:
            updated, explanation = _apply_logo(orch, office.get(document_id), original, body.logo)
        elif body.targets is not None:
            multi_plan = await office_object_edit.propose_many(
                original, body.instruction, orch.settings, body.targets
            )
            updated = office_object_edit.patch_objects(original, body.targets, multi_plan)
            explanation = multi_plan.explanation
        elif body.target is not None:
            object_plan = await office_object_edit.propose(
                original, body.instruction, orch.settings, body.target
            )
            updated = office_object_edit.patch_object(original, body.target, object_plan)
            explanation = object_plan.explanation
        else:
            plan = await office_edit.propose(original, body.instruction, orch.settings)
            updated = office_edit.patch_pptx(original, plan)
            explanation = plan.explanation
        office.commit_edit(document_id, token, body.revision, updated)
        return {
            "document": office.get(document_id),
            "changed": original != updated,
            "message": explanation,
        }
    except (ValueError, LlmError) as exc:
        raise ApiError(422, "office_edit_failed", "Правка не применена: " + str(exc)) from exc
    finally:
        office.end_edit(document_id, token)


def _document_template(orch: Orch, source: str) -> str | None:
    """Шаблон офисной копии по её источнику: копия шаблона проекта
    (`project/<id>/template/<template_id>/<sha>`) или артефакт задания (`<job_id>/<путь>`)."""
    parts = source.split("/")
    if len(parts) >= 4 and parts[0] == "project" and parts[2] == "template":
        return parts[3]
    try:
        return str(orch.state.get_generation(parts[0])["template_id"])
    except (NotFound, KeyError):
        return None


def _apply_logo(
    orch: Orch, document: dict[str, Any], original: bytes, logo: LogoRequest
) -> tuple[bytes, str]:
    """Знак шаблона заменяется или убирается на образцах, макетах и слайдах сразу; картинки
    знака берутся из профиля шаблона, в документе находятся по содержимому."""
    template_id = _document_template(orch, str(document.get("source") or ""))
    try:
        template = orch.state.get_template(template_id) if template_id else None
    except NotFound:
        template = None
    if template is None:
        raise ValueError("не найден шаблон этой презентации")
    if template.get("status") != "succeeded" or not template.get("profile"):
        raise ValueError("шаблон ещё разбирается — повторите через минуту")
    hashes = office_logo.logo_hashes(template["profile"])
    if not hashes:
        raise ValueError("в шаблоне нет логотипа на макетах")
    image: bytes | None = None
    if logo.action == "replace":
        if not logo.file_id:
            raise ValueError("прикрепите картинку нового логотипа (PNG или JPG)")
        try:
            row = orch.state.get_file(logo.file_id)
        except NotFound as exc:
            raise ValueError("картинка логотипа не найдена в файлах проекта") from exc
        image = orch.files.path_for(str(row["sha256"])).read_bytes()
    updated, count = office_logo.replace_logo(original, hashes, image)
    if not count:
        raise ValueError("логотипа шаблона в этой презентации уже нет")
    done = "заменён" if image is not None else "убран"
    places = f"{count} {plural(count, 'место', 'места', 'мест')}"
    return updated, f"Логотип шаблона {done} на всех слайдах ({places} в макетах)."


def store(orch: Orch) -> OfficeStore:
    cfg = orch.settings.onlyoffice
    if not cfg.enabled or len(cfg.jwt_secret) < 32:
        raise ApiError(503, "office_disabled", "ONLYOFFICE не настроен")
    return OfficeStore(orch.settings.paths.data_dir)


@router.get("/capabilities")
def capabilities(orch: Orch) -> dict[str, Any]:
    """Включён ли редактор и адрес его SDK: интерфейс прогревает редактор заранее, пока
    презентация ещё готовится, а не после того, как файл появился."""
    cfg = orch.settings.onlyoffice
    enabled = cfg.enabled and len(cfg.jwt_secret) >= 32
    if not enabled:
        return {"enabled": False}
    return {"enabled": True, "script_url": _script_url(cfg.public_url)}


def _script_url(public_url: str) -> str:
    return f"{public_url.rstrip('/')}/web-apps/apps/api/documents/api.js?rendering=pd16v2"


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


class TemplateCopyRequest(BaseModel):
    template_id: str = Field(max_length=100)


@router.post("/projects/{project_id}/template")
def project_template_copy(project_id: str, body: TemplateCopyRequest, orch: Orch) -> dict[str, Any]:
    office = store(orch)
    try:
        project = orch.state.get_project(project_id)
    except NotFound as exc:
        raise ApiError(404, "project_not_found", "Проект не найден") from exc
    if project.get("template_id") != body.template_id:
        raise ApiError(409, "template_changed", "Шаблон проекта изменился. Обновите страницу")
    template, path = template_source(body.template_id, orch)
    if path.stat().st_size > orch.files.max_bytes:
        raise ApiError(413, "office_file_too_large", "PPTX превышает лимит загрузки")
    # A project-local copy: never edit the library template or another project's copy.
    return office.create(
        f"project/{project_id}/template/{body.template_id}/{template['sha256']}",
        template["name"],
        path.read_bytes(),
    )


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
            "permissions": {
                "edit": False,
                "download": False,
                "print": True,
                "comment": False,
                "chat": False,
            },
        },
        "editorConfig": {
            "mode": "view",
            "lang": "ru",
            "user": {"id": uuid.uuid4().hex, "name": "Просмотр шаблона"},
            "customization": {
                "anonymous": {"request": False},
                "compactHeader": True,
                "toolbarHideFileName": True,
                "uiTheme": "theme-white",
                "compactToolbar": True,
                "hideRightMenu": True,
                "hideRulers": True,
                "hideNotes": True,
                "comments": False,
                "features": {"featuresTips": False},
                "zoom": -1,
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
        "script_url": _script_url(cfg.public_url),
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
        office.read(document_id, doc["seed_revision"]),
        orch.settings.paths.data_dir,
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
            "permissions": {
                "edit": True,
                "download": False,
                "print": True,
                "chat": False,
                "comment": False,
            },
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
                # Standard customization only: no paid White Label layout overrides.
                # Users can expand tools again via View; their saved preferences win.
                "uiTheme": "theme-white",
                "compactToolbar": True,
                "hideRightMenu": True,
                "hideRulers": True,
                "hideNotes": True,
                "comments": False,
                "features": {"featuresTips": False},
                "zoom": -1,
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
        "script_url": _script_url(cfg.public_url),
        "config": {**editor, "token": sign(editor, cfg.jwt_secret)},
    }


@router.get("/documents/{document_id}/preview/{revision}")
def preview(document_id: str, revision: int, orch: Orch) -> dict[str, Any]:
    content = store(orch).read(document_id, revision)
    try:
        _, manifest = preview_revision(content, orch.settings)
    except (ConversionError, RendererUnavailableError, OSError, ValueError) as exc:
        raise ApiError(503, "office_preview_failed", "Не удалось сформировать превью") from exc
    return {"revision": revision, **manifest}


@router.get("/documents/{document_id}/preview/{revision}/{name}")
def preview_image(document_id: str, revision: int, name: str, orch: Orch) -> FileResponse:
    content = store(orch).read(document_id, revision)
    root = cache_path(content, orch.settings)
    manifest = root / "manifest.json"
    try:
        slides = json.loads(manifest.read_text(encoding="utf-8"))["slides"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ApiError(404, "office_preview_missing", "Превью не найдено") from exc
    if name not in slides or Path(name).name != name:
        raise ApiError(404, "office_preview_missing", "Превью не найдено")
    try:
        image = preview_page(root, name)
    except (ConversionError, OSError, ValueError) as exc:
        raise ApiError(503, "office_preview_failed", "Не удалось отрисовать слайд") from exc
    return FileResponse(
        image,
        media_type="image/png",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


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
    document = office.get(document_id)
    if document["active_key"] != key:
        return {"error": 1}
    data = None
    raw_data = None
    normalized_parts: list[str] = []
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
            raw_data = data
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "deck.pptx"
                path.write_bytes(data)
                # Check ZIP paths, CRC and uncompressed size before reading XML parts.
                if orch.files.check(path, "pptx")["status"] != "ok":
                    raise ValueError("invalid saved PPTX")
            data, normalized_parts = validate_saved_pptx(
                data, office.read(document_id, document["seed_revision"])
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            office.callback(document_id, key, 7, None)
            return {"error": 1}
    return {
        "error": 0
        if office.callback(
            document_id,
            key,
            status,
            data,
            raw_pptx=raw_data if normalized_parts else None,
            normalized_parts=normalized_parts,
        )
        else 1
    }
