"""Снимок открытой презентации (этап 36): офисная копия или ревизия варианта вместе с тем, что
о них знает сервер, — ComposedDeck и план ревизии-источника, профиль шаблона, исходная
ревизия копии (для признака ручных правок). Ревизии неизменяемы, поэтому снимок кэшируется по
адресу ревизии.
"""

from __future__ import annotations

import json
import logging
import re
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from presentation_designer.generation.snapshot import deck_snapshot
from presentation_designer.pipeline.office import OfficeStore
from presentation_designer.pipeline.state import NotFound

if TYPE_CHECKING:
    from presentation_designer.pipeline.jobs import Orchestrator

JsonDict = dict[str, Any]
log = logging.getLogger(__name__)

_CACHE: OrderedDict[tuple[str, ...], JsonDict] = OrderedDict()
_CACHE_SIZE = 8
_ARTIFACT = re.compile(r"^([A-Za-z0-9_-]+)/r(\d+)/deck\.pptx$")


def _cached(key: tuple[str, ...]) -> JsonDict | None:
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    return None


def _remember(key: tuple[str, ...], value: JsonDict) -> JsonDict:
    _CACHE[key] = value
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return value


def office_store(o: Orchestrator) -> OfficeStore | None:
    cfg = o.settings.onlyoffice
    if not cfg.enabled or len(cfg.jwt_secret) < 32:
        return None
    return OfficeStore(o.settings.paths.data_dir)


def office_snapshot(
    o: Orchestrator, store: OfficeStore, document_id: str, revision: int
) -> JsonDict:
    """Снимок ревизии офисной копии. Копия сгенерированной колоды получает слайды плана и
    слоты из ComposedDeck ревизии-источника; сохранённая ревизия — признак ручных правок
    относительно исходной."""
    key = ("office", document_id, str(revision))
    cached = _cached(key)
    if cached is not None:
        return cached
    document = store.get(document_id)
    data = store.read(document_id, revision)
    composed, plan, template_id = _source_context(o, str(document.get("source") or ""))
    profile = _profile(o, template_id)
    base = None
    if revision > 0:
        base = deck_snapshot(
            store.read(document_id, 0), composed=composed, plan=plan, profile=profile
        )
    snapshot = deck_snapshot(
        data,
        source={"kind": "office", "document_id": document_id, "revision": revision},
        composed=composed,
        plan=plan,
        profile=profile,
        base=base,
    )
    return _remember(key, snapshot)


def variant_snapshot(o: Orchestrator, job_id: str, variant_id: str, revision: int) -> JsonDict:
    """Снимок ревизии варианта: PPTX ревизии, её ComposedDeck и план."""
    key = ("variant", job_id, variant_id, str(revision))
    cached = _cached(key)
    if cached is not None:
        return cached
    directory = o.artifacts.revision_dir(job_id, variant_id, revision)
    data = (directory / "deck.pptx").read_bytes()
    template_id = _job_template(o, job_id)
    source = {"kind": "variant", "job_id": job_id, "variant_id": variant_id, "revision": revision}
    snapshot = deck_snapshot(
        data,
        source=source,
        composed=_json(directory / "composed.json"),
        plan=_json(directory / "plan.json"),
        profile=_profile(o, template_id),
    )
    return _remember(key, snapshot)


def document_belongs(document: JsonDict, project: JsonDict) -> bool:
    """Офисная копия этого проекта: из его сборки или копия его шаблона."""
    source = str(document.get("source") or "")
    job_id = project.get("job_id")
    return bool(job_id and source.startswith(f"{job_id}/")) or source.startswith(
        f"project/{project['project_id']}/"
    )


def project_snapshot(
    o: Orchestrator,
    project: JsonDict,
    office: JsonDict | None,
    variant: JsonDict | None,
) -> JsonDict | None:
    """Снимок того, что открыто в проекте: офисная копия из запроса (если она этого проекта),
    иначе последняя ревизия выбранного варианта. Сбой — без снимка, а не ошибка чата."""
    try:
        store = office_store(o) if office else None
        if store is not None and office is not None:
            document = store.get(str(office["document_id"]))
            if document_belongs(document, project):
                return office_snapshot(o, store, str(document["id"]), int(office["revision"]))
        if variant and project.get("job_id") and variant.get("revision"):
            return variant_snapshot(
                o, str(project["job_id"]), str(variant["variant_id"]), int(variant["revision"])
            )
    except Exception:
        log.warning("снимок презентации для чата не построен", exc_info=True)
    return None


def _source_context(
    o: Orchestrator, source: str
) -> tuple[JsonDict | None, JsonDict | None, str | None]:
    """ComposedDeck, план и шаблон по источнику офисной копии: артефакт задания
    (`<job_id>/<variant>/r<N>/deck.pptx`) или копия шаблона проекта
    (`project/<id>/template/<template_id>/<sha>`)."""
    parts = source.split("/")
    if len(parts) >= 4 and parts[0] == "project" and parts[2] == "template":
        return None, None, parts[3]
    job_id = parts[0]
    template_id = _job_template(o, job_id)
    match = _ARTIFACT.match("/".join(parts[1:]))
    if not match or not job_id:
        return None, None, template_id
    directory = o.artifacts.revision_dir(job_id, match.group(1), int(match.group(2)))
    return _json(directory / "composed.json"), _json(directory / "plan.json"), template_id


def _job_template(o: Orchestrator, job_id: str) -> str | None:
    try:
        return str(o.state.get_generation(job_id)["template_id"])
    except (NotFound, KeyError):
        return None


def _profile(o: Orchestrator, template_id: str | None) -> JsonDict | None:
    if not template_id:
        return None
    try:
        profile = o.state.get_template(template_id).get("profile")
    except NotFound:
        return None
    return profile if isinstance(profile, dict) else None


def _json(path: Any) -> JsonDict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


__all__ = [
    "document_belongs",
    "office_snapshot",
    "office_store",
    "project_snapshot",
    "variant_snapshot",
]
