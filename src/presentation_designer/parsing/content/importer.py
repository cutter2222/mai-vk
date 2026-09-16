"""Сборка ContentPackage из файлов проекта и брифа.

Порядок: разбор каждого файла (кэш по sha256, формату, версии парсера и параметрам) →
сквозные идентификаторы источников, блоков, наборов данных и ресурсов в порядке файлов →
реестр фактов из текста и таблиц с производными показателями → уточнение контекста
неоднозначных фактов моделью (в пределах бюджета времени, необязательно) → недостающие данные
по брифу → метаданные импорта. Режим брифа без файлов даёт пакет из одного источника
`user_input`: показатели не выдумываются, отсутствующее попадает в `missing_data`.
Слой не знает об HTTP, очереди и путях клиента: файлы приходят уже из хранилища.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from presentation_designer.contracts import CONTRACTS_VERSION, ContentPackage
from presentation_designer.contracts.validators import check_content_package
from presentation_designer.parsing.content import datasets as datasets_mod
from presentation_designer.parsing.content import facts as facts_mod
from presentation_designer.parsing.content.parsers import (
    PARSER_VERSIONS,
    SUPPORTED_FORMATS,
    ParseCache,
    ParsedDocument,
    ParserError,
    parse_file,
    parse_key,
)
from presentation_designer.parsing.content.parsers.base import ParsedBlock, blocks_from_text
from presentation_designer.shared.settings import Settings, get_settings

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]

IMPORTER_NAME = "content_importer"
IMPORTER_VERSION = "0.1.0"
BRIEF_PARSER_VERSION = "0.1.0"
PURPOSES = ("feature", "product", "project", "initiative", "report", "other")
_EXT_BY_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/bmp": "bmp",
    "image/webp": "webp",
    "image/tiff": "tiff",
    "image/emf": "emf",
    "image/wmf": "wmf",
}


@dataclass(frozen=True)
class MaterialFile:
    """Файл проекта из хранилища загрузок: слой получает путь по идентификатору, не от клиента."""

    file_id: str
    name: str
    sha256: str
    size_bytes: int
    format: str
    path: pathlib.Path | None


@dataclass
class ImportResult:
    package: JsonDict
    assets: dict[str, bytes]
    report: JsonDict
    warnings: list[JsonDict] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---------- бриф ----------


def normalize_brief(
    brief: JsonDict | None, *, fallback_title: str = "Презентация"
) -> tuple[JsonDict, list[str]]:
    """Бриф пакета с умолчаниями; возвращает и список недостающих обязательных полей."""
    supplied = {k: v for k, v in (brief or {}).items() if v not in ("", None, [])}
    out: JsonDict = {}
    for key in ("purpose", "title", "audience", "goal", "language", "tone", "notes"):
        value = supplied.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    for key in ("must_include", "avoid"):
        items = supplied.get(key)
        if isinstance(items, list):
            cleaned = [str(i).strip() for i in items if str(i).strip()]
            if cleaned:
                out[key] = cleaned
    slide_count = supplied.get("slide_count")
    if isinstance(slide_count, dict):
        sc = {
            k: int(v) for k, v in slide_count.items() if k in ("min", "max") and isinstance(v, int)
        }
        if "exact" in slide_count and isinstance(slide_count["exact"], int):
            sc = {"min": int(slide_count["exact"]), "max": int(slide_count["exact"])}
        if sc:
            out["slide_count"] = sc
    missing = [k for k in ("purpose", "title") if k not in out]
    if out.get("purpose") not in PURPOSES:
        if "purpose" in out and "purpose" not in missing:
            missing.append("purpose")
        out["purpose"] = "other"
    out.setdefault("title", fallback_title)
    out.setdefault("language", "ru")
    return out, missing


def brief_document(brief: JsonDict) -> ParsedDocument:
    """Бриф как источник содержания в режиме без файлов: заголовок, цель, обязательные пункты."""
    doc = ParsedDocument("user_input")
    title = brief.get("title") or "Презентация"
    doc.blocks.append(ParsedBlock("heading", text=title, level=1, tags=["brief"]))
    for key, label in (("goal", "Цель"), ("audience", "Аудитория"), ("tone", "Тон")):
        if brief.get(key):
            doc.blocks.append(
                ParsedBlock("paragraph", text=f"{label}: {brief[key]}", tags=["brief", key])
            )
    if brief.get("must_include"):
        doc.blocks.append(
            ParsedBlock(
                "bullets", items=list(brief["must_include"]), tags=["brief", "must_include"]
            )
        )
    if brief.get("notes"):
        for block in blocks_from_text(str(brief["notes"]), allow_headings=False):
            block.tags = [*block.tags, "brief", "notes"]
            doc.blocks.append(block)
    doc.units = {"chars": doc.text_chars}
    return doc


# ---------- ключи ----------


def parse_params(settings: Settings) -> JsonDict:
    ci = settings.content_import
    return {
        "max_block_chars": ci.max_block_chars,
        "min_image_px": ci.min_image_px,
    }


def import_key(files: list[MaterialFile], settings: Settings) -> str:
    """Ключ кэша импорта: sha256 файлов в их порядке, параметры разбора, версии парсеров,
    импортёра и контрактов. Бриф в ключ не входит."""
    material = {
        "files": [(f.sha256, f.format) for f in files],
        "params": parse_params(settings),
        "parsers": PARSER_VERSIONS,
        "importer": IMPORTER_VERSION,
        "contracts": CONTRACTS_VERSION,
        "max_dataset_rows": settings.content_import.max_dataset_rows,
        "max_facts": settings.content_import.max_facts,
        "max_assets": settings.content_import.max_assets,
    }
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


def import_version() -> str:
    """Версия импортёра для идемпотентности пакета в pipeline."""
    parsers = ",".join(f"{k}={v}" for k, v in sorted(PARSER_VERSIONS.items()))
    return f"{IMPORTER_VERSION}/{CONTRACTS_VERSION}/{parsers}"


# ---------- сборка ----------


class _Ids:
    def __init__(self) -> None:
        self.block = 0
        self.dataset = 0
        self.asset = 0

    def next_block(self) -> str:
        self.block += 1
        return f"b{self.block}"

    def next_dataset(self) -> str:
        self.dataset += 1
        return f"ds{self.dataset}"

    def next_asset(self) -> str:
        self.asset += 1
        return f"img_{self.asset}"


def import_content(
    files: list[MaterialFile],
    brief: JsonDict | None,
    *,
    package_id: str,
    mode: str | None = None,
    settings: Settings | None = None,
    llm_client: Any = None,
    skill: Any = None,
    cache: ParseCache | None = None,
    use_model: bool | None = None,
) -> ImportResult:
    settings = settings or get_settings()
    ci = settings.content_import
    started = time.perf_counter()
    timings: dict[str, int] = {}
    cache = cache if cache is not None else ParseCache(settings.import_cache_dir)
    mode = mode or ("mixed" if files and brief else "brief" if brief else "package")

    fallback_title = files[0].name if files else "Презентация"
    package_brief, missing_brief = normalize_brief(
        brief, fallback_title=pathlib.Path(fallback_title).stem
    )
    warnings: list[JsonDict] = []
    if brief is not None and missing_brief:
        warnings.append(
            {
                "code": "brief_incomplete",
                "message": f"В брифе не заданы поля: {', '.join(missing_brief)}; "
                "применены умолчания",
            }
        )
    elif brief is None and mode != "package":
        warnings.append(
            {"code": "brief_incomplete", "message": "Бриф не задан; применены умолчания"}
        )

    # 1. Разбор файлов (с кэшем).
    parsed: list[tuple[MaterialFile, ParsedDocument | None, JsonDict]] = []
    hits = missed = 0
    params = parse_params(settings)
    t0 = time.perf_counter()
    for material in files:
        source: JsonDict = {
            "kind": material.format if material.format in SUPPORTED_FORMATS else "text",
            "name": material.name,
            "sha256": material.sha256,
            "size_bytes": material.size_bytes,
            "extracted": False,
            "warnings": [],
            "file_id": material.file_id,
        }
        if material.format not in SUPPORTED_FORMATS:
            source["warnings"].append(
                {
                    "code": "format_unsupported",
                    "message": f"формат {material.format} не импортируется",
                }
            )
            parsed.append((material, None, source))
            continue
        source["parser"] = {"name": material.format, "version": PARSER_VERSIONS[material.format]}
        key = parse_key(material.sha256, material.format, params)
        doc = cache.get(key)
        if doc is None:
            missed += 1
            try:
                doc = parse_file(
                    material.path or pathlib.Path(material.name),
                    material.format,
                    max_block_chars=ci.max_block_chars,
                    min_image_px=ci.min_image_px,
                    name=material.name,
                )
            except ParserError as e:
                source["warnings"].append({"code": e.code, "message": str(e)})
                parsed.append((material, None, source))
                continue
            except Exception as e:
                log.exception("разбор %s не удался", material.name)
                source["warnings"].append(
                    {"code": "parse_failed", "message": f"файл не разобран: {e}"[:300]}
                )
                parsed.append((material, None, source))
                continue
            cache.put(key, doc)
        else:
            hits += 1
        source["extracted"] = doc.extracted
        source["warnings"].extend(doc.warnings)
        if doc.units:
            source["units"] = {k: int(v) for k, v in doc.units.items()}
        parsed.append((material, doc, source))
    timings["parse_ms"] = int((time.perf_counter() - t0) * 1000)

    # 2. Сквозные идентификаторы: источники, блоки, наборы данных, ресурсы.
    ids = _Ids()
    sources: list[JsonDict] = []
    blocks: list[JsonDict] = []
    datasets: list[datasets_mod.Dataset] = []
    assets: list[JsonDict] = []
    asset_bytes: dict[str, bytes] = {}
    asset_by_sha: dict[str, str] = {}
    text_units: list[facts_mod.TextUnit] = []
    order = 0
    documents: list[tuple[str, ParsedDocument]] = []
    if brief is not None:
        sources.append(
            {
                "source_id": "src_brief",
                "kind": "user_input",
                "name": "бриф",
                "extracted": True,
                "parser": {"name": "brief", "version": BRIEF_PARSER_VERSION},
            }
        )
        if not files:
            # Без материалов бриф — единственный источник содержания.
            documents.append(("src_brief", brief_document(package_brief)))
    for i, (_material, doc, source) in enumerate(parsed, start=1):
        source_id = f"src_{i}"
        sources.append({"source_id": source_id, **source})
        if doc is not None:
            documents.append((source_id, doc))
    for source_id, doc in documents:
        heading: str | None = None
        for pb in doc.blocks:
            order += 1
            block_id = ids.next_block()
            block: JsonDict = {
                "block_id": block_id,
                "source_id": source_id,
                "order": order,
                "kind": pb.kind,
            }
            location = pb.location.as_dict()
            if pb.kind == "heading":
                block["text"] = pb.text
                block["level"] = pb.level or 1
                heading = pb.text
            elif pb.kind in ("paragraph", "quote", "code", "kpi"):
                block["text"] = pb.text
            elif pb.kind == "bullets":
                block["items"] = list(pb.items)
            elif pb.kind == "table" and pb.table_ref is not None and pb.table_ref < len(doc.tables):
                dataset = datasets_mod.build_dataset(
                    doc.tables[pb.table_ref],
                    dataset_id=ids.next_dataset(),
                    source_id=source_id,
                    block_id=block_id,
                    max_rows=ci.max_dataset_rows,
                )
                if dataset is None:
                    ids.block -= 1
                    order -= 1
                    continue
                if not dataset.title and heading:
                    dataset.title = heading
                datasets.append(dataset)
                block["dataset_id"] = dataset.dataset_id
                block["importance"] = "must"
            elif (
                pb.kind == "figure" and pb.image_ref is not None and pb.image_ref < len(doc.images)
            ):
                image = doc.images[pb.image_ref]
                sha = hashlib.sha256(image.data).hexdigest()
                asset_id = asset_by_sha.get(sha)
                if asset_id is None:
                    if len(assets) >= ci.max_assets:
                        if not any(w["code"] == "assets_truncated" for w in warnings):
                            warnings.append(
                                {
                                    "code": "assets_truncated",
                                    "message": f"изображений больше {ci.max_assets}: "
                                    "остальные не включены",
                                }
                            )
                        ids.block -= 1
                        order -= 1
                        continue
                    asset_id = ids.next_asset()
                    ext = _EXT_BY_MIME.get(image.mime, "bin")
                    path = f"assets/{asset_id}.{ext}"
                    asset: JsonDict = {
                        "asset_id": asset_id,
                        "kind": image.kind
                        if image.kind
                        in ("image", "logo", "screenshot", "chart_image", "diagram_image", "photo")
                        else "image",
                        "path": path,
                        "width_px": image.width_px,
                        "height_px": image.height_px,
                        "source_id": source_id,
                        "sha256": sha,
                        "mime": image.mime,
                    }
                    caption = pb.caption or image.caption
                    if caption:
                        asset["caption"] = caption
                    assets.append(asset)
                    asset_bytes[path] = image.data
                    asset_by_sha[sha] = asset_id
                block["asset_id"] = asset_id
                block["importance"] = "should"
            else:
                ids.block -= 1
                order -= 1
                continue
            if pb.caption:
                block["caption"] = pb.caption
            if location:
                block["source_location"] = location
            if pb.tags:
                block["tags"] = list(pb.tags)
            if "notes" in pb.tags:
                block["importance"] = "could"
            blocks.append(block)
            if pb.kind in ("paragraph", "quote", "kpi", "heading"):
                text_units.append(
                    facts_mod.TextUnit(
                        block_id,
                        source_id,
                        pb.text,
                        location,
                        heading if pb.kind != "heading" else None,
                    )
                )
            elif pb.kind == "bullets":
                for item in pb.items:
                    text_units.append(
                        facts_mod.TextUnit(block_id, source_id, item, location, heading)
                    )

    # 3. Факты: текст, затем наборы данных.
    t0 = time.perf_counter()
    facts = facts_mod.extract_text_facts(text_units, start_index=1, max_facts=ci.max_facts)
    for dataset in datasets:
        if len(facts) >= ci.max_facts:
            break
        facts.extend(
            facts_mod.extract_dataset_facts(
                dataset, start_index=len(facts) + 1, max_facts=ci.max_facts - len(facts)
            )
        )
    if len(facts) >= ci.max_facts:
        warnings.append(
            {
                "code": "facts_truncated",
                "message": f"фактов больше {ci.max_facts}: остальные не включены",
            }
        )
    fact_blocks = {f.block_id for f in facts if f.must_keep}
    for block in blocks:
        if block["block_id"] in fact_blocks and block["kind"] in (
            "paragraph",
            "bullets",
            "quote",
            "kpi",
        ):
            block["importance"] = "must"
        elif block["kind"] in ("paragraph", "bullets", "quote") and "importance" not in block:
            block["importance"] = "should"
    timings["facts_ms"] = int((time.perf_counter() - t0) * 1000)

    # 4. Контекст неоднозначных фактов моделью.
    model_summary: JsonDict = {}
    enable_model = ci.fact_context_model if use_model is None else use_model
    if enable_model and llm_client is not None and skill is not None:
        t0 = time.perf_counter()
        model_summary = facts_mod.refine_facts_with_model(
            facts, llm_client, skill, budget_s=float(ci.fact_context_budget_s)
        )
        timings["model_ms"] = int((time.perf_counter() - t0) * 1000)

    # 5. Недостающие данные.
    missing_data = find_missing_data(package_brief, blocks, facts, mode)

    # 6. Метаданные и документ.
    import_meta: JsonDict = {
        "importer": {"name": IMPORTER_NAME, "version": IMPORTER_VERSION},
        "parsers": {
            fmt: PARSER_VERSIONS[fmt]
            for fmt in sorted({m.format for m in files if m.format in PARSER_VERSIONS})
        },
        "import_key": import_key(files, settings),
        "cache": {"files_hit": hits, "files_missed": missed},
        "model_calls": int(model_summary.get("calls", 0)),
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "created_at": now_iso(),
    }
    if skill is not None and model_summary.get("calls"):
        import_meta["skills"] = [{"name": skill.name, "version": skill.version}]
        import_meta["prompts"] = [
            {"name": p.id, "version": p.version} for p in skill.prompts.values()
        ]
        try:
            from presentation_designer.llm import skill_model_ref

            ref = skill_model_ref(llm_client, skill)
            import_meta["models"] = [ref] if ref else []
        except Exception:  # конфиг моделей недоступен — без ссылок
            pass
    package: JsonDict = {
        "schema_version": "1.3",
        "package_id": package_id,
        "mode": mode,
        "created_at": now_iso(),
        "brief": package_brief,
        "sources": sources,
        "blocks": blocks,
        "facts": [f.as_dict() for f in facts],
        "assets": assets,
        "datasets": [d.as_dict() for d in datasets],
        "warnings": warnings,
        "missing_data": missing_data,
        "import_meta": import_meta,
    }
    doc_model = ContentPackage.model_validate(package)
    violations = check_content_package(doc_model)
    if violations:
        raise ValueError("пакет нарушает связи контракта: " + "; ".join(str(v) for v in violations))
    report: JsonDict = {
        "timings_ms": timings,
        "counts": {
            "sources": len(sources),
            "blocks": len(blocks),
            "facts": len(facts),
            "facts_must_keep": sum(1 for f in facts if f.must_keep),
            "facts_ambiguous": sum(1 for f in facts if f.ambiguous),
            "datasets": len(datasets),
            "assets": len(assets),
            "missing_data": len(missing_data),
        },
        "cache": {"files_hit": hits, "files_missed": missed},
        "model": model_summary,
        "warnings": warnings + [w for s in sources for w in s.get("warnings", [])],
        "total_ms": import_meta["duration_ms"],
    }
    return ImportResult(package=package, assets=asset_bytes, report=report, warnings=warnings)


def find_missing_data(
    brief: JsonDict, blocks: list[JsonDict], facts: list[facts_mod.Fact], mode: str
) -> list[JsonDict]:
    """Пункты must_include без опоры в материалах и отсутствие показателей в режиме брифа."""
    corpus = " ".join(
        [
            *(b.get("text", "") for b in blocks if "brief" not in (b.get("tags") or [])),
            *(" ".join(b.get("items", [])) for b in blocks if "brief" not in (b.get("tags") or [])),
            *(f.label or "" for f in facts),
        ]
    ).lower()
    missing: list[JsonDict] = []
    for item in brief.get("must_include") or []:
        words = [w.lower() for w in facts_mod._WORD.findall(item) if len(w) >= 4]
        if not words:
            continue
        hits = sum(1 for w in words if w[:5] in corpus)
        if hits < max(1, (len(words) + 1) // 2):
            missing.append(
                {
                    "what": item,
                    "why_needed": "обязательный пункт брифа, в материалах не найден",
                    "thesis_hint": item,
                }
            )
    if mode == "brief" and not any(f.kind in ("number", "percent", "money") for f in facts):
        missing.append(
            {
                "what": "показатели и цифры",
                "why_needed": "в брифе нет данных; цифры в презентации не выдумываются",
                "thesis_hint": "результаты и метрики",
            }
        )
    return missing


__all__ = [
    "IMPORTER_NAME",
    "IMPORTER_VERSION",
    "ImportResult",
    "MaterialFile",
    "brief_document",
    "find_missing_data",
    "import_content",
    "import_key",
    "import_version",
    "normalize_brief",
    "parse_params",
]
