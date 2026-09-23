"""Общий смысловой план (StoryPlan) из ContentPackage: один раз на пакет, без шаблона.

Порядок: эффективный бриф (бриф пакета плюс явные настройки запроса — язык, число слайдов,
seed) → смысловой хеш `content_hash` (нормализованный пакет, эффективный бриф, модель, версии
скилла и промпта, схема ответа) → выдержка содержания для модели с идентификаторами блоков,
фактов, наборов данных и изображений → вызов скилла `story_planner` по схеме ответа →
сборка StoryPlan кодом: нормализация идентификаторов, проверка ссылок, покрытие обязательных
фактов и пунктов брифа, допущения из недостающих данных → проверка контракта и связей.
Значения фактов модель не переписывает: в тезисах они подставляются через {fact:<id>}.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from presentation_designer.contracts import StoryPlan
from presentation_designer.contracts.validators import check_story_plan
from presentation_designer.generation.grounding import (
    CONCEPT_POLICY,
    CONCEPT_WARNING,
    concept_notice,
    topic_only,
)
from presentation_designer.shared.settings import Settings, get_settings

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]

STORY_VERSION = "0.1.3"
STORY_SCHEMA_VERSION = "1.2"
THESIS_KINDS = ("section", "claim", "evidence", "conclusion", "call_to_action", "context")
VISUALS = (
    "text",
    "bullets",
    "number",
    "chart",
    "table",
    "diagram",
    "image",
    "quote",
    "comparison",
    "timeline",
)
REDUCTIONS = (
    "drop_examples",
    "merge_with",
    "shorten_wording",
    "table_to_chart",
    "chart_to_number",
    "drop_entirely",
)
FACT_REF = re.compile(r"\{fact:([A-Za-z0-9_.:-]+)\}")
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]*")

# Схема ответа модели: короче контракта, идентификаторы и метаданные добавляет код.
STORY_MODEL_SCHEMA: JsonDict = {
    "type": "object",
    "required": ["key_takeaway", "theses"],
    "properties": {
        "key_takeaway": {"type": "string"},
        "theses": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "kind", "statement", "required"],
                "properties": {
                    "id": {"type": "string"},
                    "kind": {"type": "string", "enum": list(THESIS_KINDS)},
                    "parent": {"type": "string"},
                    "statement": {"type": "string"},
                    "explanation": {"type": "string"},
                    "required": {"type": "boolean"},
                    "source_refs": {"type": "array", "items": {"type": "string"}},
                    "fact_refs": {"type": "array", "items": {"type": "string"}},
                    "dataset_refs": {"type": "array", "items": {"type": "string"}},
                    "asset_refs": {"type": "array", "items": {"type": "string"}},
                    "visual": {"type": "string", "enum": list(VISUALS)},
                    "covers": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "allowed_reductions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["thesis_id", "reduction"],
                "properties": {
                    "thesis_id": {"type": "string"},
                    "reduction": {"type": "string", "enum": list(REDUCTIONS)},
                    "target_thesis_id": {"type": "string"},
                    "note": {"type": "string"},
                },
            },
        },
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
}


@dataclass
class StoryResult:
    story: JsonDict
    report: JsonDict = field(default_factory=dict)


class StoryError(RuntimeError):
    """План не построен: код для контракта error."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---------- эффективный бриф и хеш ----------


def effective_brief(package: JsonDict, settings: JsonDict | None) -> JsonDict:
    """Бриф пакета с явными настройками запроса поверх: язык и число слайдов."""
    brief = dict(package.get("brief") or {})
    settings = settings or {}
    out: JsonDict = {}
    for key in ("purpose", "title", "audience", "goal", "language", "tone"):
        value = brief.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    for key in ("must_include", "avoid"):
        items = brief.get(key)
        if isinstance(items, list) and items:
            out[key] = [str(i).strip() for i in items if str(i).strip()]
    if isinstance(settings.get("language"), str) and settings["language"].strip():
        out["language"] = settings["language"].strip()
    out.setdefault("language", "ru")
    out.setdefault("purpose", "other")
    slide_count: JsonDict = {}
    for source in (brief.get("slide_count"), settings.get("slide_count")):
        if isinstance(source, dict):
            slide_count = {
                k: int(v)
                for k, v in source.items()
                if k in ("exact", "min", "max") and isinstance(v, int)
            }
            if slide_count:
                break
    if slide_count.get("exact"):
        slide_count = {"exact": slide_count["exact"]}
    if slide_count:
        out["slide_count"] = slide_count
    return out


def normalize_package(package: JsonDict) -> JsonDict:
    """Смысловое содержание пакета без идентификаторов заданий и времени."""
    blocks = [
        {
            k: v
            for k, v in b.items()
            if k
            in (
                "block_id",
                "kind",
                "level",
                "text",
                "items",
                "dataset_id",
                "asset_id",
                "importance",
                "caption",
            )
        }
        for b in package.get("blocks", [])
    ]
    facts = [
        {
            k: v
            for k, v in f.items()
            if k
            in (
                "fact_id",
                "raw",
                "kind",
                "value",
                "unit",
                "label",
                "must_keep",
                "context",
                "derived",
            )
        }
        for f in package.get("facts", [])
    ]
    datasets = [
        {k: v for k, v in d.items() if k in ("dataset_id", "title", "columns", "rows")}
        for d in package.get("datasets", [])
    ]
    assets = [
        {
            k: v
            for k, v in a.items()
            if k in ("asset_id", "kind", "caption", "sha256", "width_px", "height_px")
        }
        for a in package.get("assets", [])
    ]
    return {
        "mode": package.get("mode"),
        "blocks": blocks,
        "facts": facts,
        "datasets": datasets,
        "assets": assets,
        "missing_data": package.get("missing_data", []),
    }


def story_key(
    package: JsonDict,
    settings: JsonDict | None,
    *,
    skill: Any = None,
    model: JsonDict | None = None,
    nonce: str | None = None,
) -> str:
    """content_hash: содержание, эффективный бриф, модель, скилл/промпт, схема и параметры."""
    settings = settings or {}
    material: JsonDict = {
        "story_version": STORY_VERSION,
        "schema": STORY_SCHEMA_VERSION,
        "package": normalize_package(package),
        "topic_only": topic_only(package),
        "effective_brief": effective_brief(package, settings),
        "seed": settings.get("seed"),
        "skill": list(skill.ref) if skill is not None else None,
        "prompts": sorted(p.ref for p in skill.prompts.values()) if skill is not None else None,
        "params": dict(skill.manifest.params or {}) if skill is not None else None,
        "model": model,
        "nonce": nonce,
    }
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


# ---------- выдержка для модели ----------


def story_digest(package: JsonDict, brief: JsonDict, *, max_chars: int = 14000) -> tuple[str, bool]:
    """Текст запроса: бриф, блоки с идентификаторами, факты, наборы данных, изображения,
    недостающие данные. Возвращает текст и признак усечения."""
    lines: list[str] = []
    sc = brief.get("slide_count") or {}
    slides = (
        f"ровно {sc['exact']}"
        if sc.get("exact")
        else f"{sc.get('min', '?')}–{sc.get('max', '?')}"
        if sc
        else "не задано"
    )
    lines.append(
        "Бриф: назначение — "
        + str(brief.get("purpose"))
        + f"; тема — «{brief.get('title', '')}»"
        + (f"; аудитория — {brief['audience']}" if brief.get("audience") else "")
        + (f"; цель — {brief['goal']}" if brief.get("goal") else "")
        + (f"; тон — {brief['tone']}" if brief.get("tone") else "")
        + f"; язык — {brief.get('language', 'ru')}"
        + f"; слайдов — {slides}."
    )
    if brief.get("must_include"):
        lines.append("Обязательно включить: " + "; ".join(brief["must_include"]) + ".")
    if brief.get("avoid"):
        lines.append("Избегать: " + "; ".join(brief["avoid"]) + ".")
    mode = package.get("mode")
    if topic_only(package):
        lines.append(CONCEPT_POLICY)
    else:
        lines.append(
            {
                "brief": "Режим: содержание предоставлено в брифе. Опирайся на него; "
                "не добавляй неподтверждённые характеристики, результаты и прогнозы.",
                "package": "Режим: материалы без брифа.",
            }.get(str(mode), "Режим: бриф и материалы.")
        )
    facts_by_block: dict[str, list[str]] = {}
    for f in package.get("facts", []):
        if f.get("block_id"):
            facts_by_block.setdefault(f["block_id"], []).append(f["fact_id"])
    datasets = {d["dataset_id"]: d for d in package.get("datasets", [])}
    assets = {a["asset_id"]: a for a in package.get("assets", [])}
    lines.append("Содержание (идентификатор · вид · текст):")
    truncated = False
    budget = max_chars
    block_lines: list[str] = []
    for b in package.get("blocks", []):
        kind = b.get("kind")
        if kind == "heading":
            text = f"{b['block_id']} · заголовок {b.get('level', 1)} · {b.get('text', '')}"
        elif kind == "bullets":
            items = [str(i)[:160] for i in (b.get("items") or [])[:12]]
            text = f"{b['block_id']} · список · " + " | ".join(items)
        elif kind == "table":
            ds = datasets.get(b.get("dataset_id", ""))
            if ds is None:
                continue
            cols = ", ".join(
                f"{c['name']}" + (f" ({c['unit']})" if c.get("unit") else "") for c in ds["columns"]
            )
            rows = "; ".join(" | ".join(str(v) for v in row) for row in ds["rows"][:6])
            more = (
                f" … всего строк {ds.get('total_rows', len(ds['rows']))}"
                if len(ds["rows"]) > 6
                else ""
            )
            text = (
                f"{b['block_id']} · таблица {ds['dataset_id']}"
                + (f" «{ds['title']}»" if ds.get("title") else "")
                + f" · колонки: {cols} · строки: {rows}{more}"
            )
        elif kind == "figure":
            a = assets.get(b.get("asset_id", ""))
            if a is None:
                continue
            text = (
                f"{b['block_id']} · изображение {a['asset_id']} ({a.get('kind', 'image')}, "
                f"{a.get('width_px', '?')}×{a.get('height_px', '?')})"
                + (f" «{a['caption']}»" if a.get("caption") else "")
            )
        else:
            text = f"{b['block_id']} · {kind} · {str(b.get('text', ''))[:500]}"
        refs = facts_by_block.get(b["block_id"])
        if refs:
            text += f" [факты: {', '.join(refs[:10])}]"
        if b.get("importance") == "must":
            text += " [обязательно]"
        if budget - len(text) < 0:
            truncated = True
            break
        budget -= len(text)
        block_lines.append(text)
    lines.extend(block_lines)
    if truncated:
        lines.append("… остальные блоки опущены из-за объёма.")
    facts = package.get("facts", [])
    if facts:
        lines.append(
            "Факты (идентификатор · значение · показатель · период · сравнение · обязательный):"
        )
        for f in facts[:120]:
            ctx = f.get("context") or {}
            lines.append(
                f"{f['fact_id']} · {f.get('raw', '')} · "
                f"{ctx.get('metric') or f.get('label') or '—'} · "
                f"{ctx.get('period') or '—'} · {ctx.get('comparison') or '—'} · "
                f"{'да' if f.get('must_keep', True) else 'нет'}"
                + (" · производный" if f.get("derived") else "")
            )
        if len(facts) > 120:
            lines.append(f"… ещё {len(facts) - 120} фактов.")
    if datasets:
        lines.append(
            "Наборы данных: "
            + "; ".join(
                f"{d['dataset_id']}"
                + (f" «{d['title']}»" if d.get("title") else "")
                + f" ({len(d['rows'])} строк)"
                for d in datasets.values()
            )
        )
    if assets:
        lines.append(
            "Изображения: "
            + "; ".join(
                f"{a['asset_id']} ({a.get('kind', 'image')})"
                + (f" «{a['caption']}»" if a.get("caption") else "")
                for a in assets.values()
            )
        )
    missing = package.get("missing_data") or []
    if missing:
        lines.append(
            "Недостающие данные (не выдумывать, оформить как допущение): "
            + "; ".join(
                f"{m['what']}" + (f" — {m['why_needed']}" if m.get("why_needed") else "")
                for m in missing
            )
        )
    return "\n".join(lines), truncated


# ---------- сборка плана ----------


def _validate_answer(value: Any) -> Any:
    """Проверка для клиента моделей: негодный ответ повторяется с подсказкой."""
    if not isinstance(value, dict):
        raise ValueError("ответ должен быть объектом")
    theses = value.get("theses")
    if not isinstance(theses, list) or not theses:
        raise ValueError("нужен непустой список theses")
    good = [t for t in theses if isinstance(t, dict) and str(t.get("statement", "")).strip()]
    if not good:
        raise ValueError("у тезисов должны быть непустые statement")
    if not str(value.get("key_takeaway", "")).strip():
        raise ValueError("нужен key_takeaway")
    return value


def assemble_story(
    answer: JsonDict,
    package: JsonDict,
    brief: JsonDict,
    *,
    content_hash: str,
    story_id: str,
    generation_meta: JsonDict,
) -> tuple[JsonDict, list[JsonDict]]:
    """StoryPlan из ответа модели: идентификаторы t1..tN, проверенные ссылки, покрытие
    обязательных фактов и пунктов брифа, допущения. Возвращает план и список правок."""
    fixes: list[JsonDict] = []
    facts = {f["fact_id"]: f for f in package.get("facts", [])}
    blocks = {b["block_id"] for b in package.get("blocks", [])}
    datasets = {d["dataset_id"] for d in package.get("datasets", [])}
    assets = {a["asset_id"] for a in package.get("assets", [])}
    raw_theses = [t for t in answer.get("theses", []) if isinstance(t, dict)]
    id_map: dict[str, str] = {}
    theses: list[JsonDict] = []
    for raw in raw_theses:
        statement = str(raw.get("statement", "")).strip()
        if not statement:
            continue
        if len(theses) >= 40:
            fixes.append(
                {"code": "theses_truncated", "message": "тезисов больше 40: остальные отброшены"}
            )
            break
        new_id = f"t{len(theses) + 1}"
        old_id = str(raw.get("id", "")).strip()
        if old_id:
            id_map[old_id] = new_id
        kind = raw.get("kind") if raw.get("kind") in THESIS_KINDS else "claim"
        thesis: JsonDict = {
            "thesis_id": new_id,
            "order": len(theses) + 1,
            "kind": kind,
            "statement": statement[:400],
            "required": bool(raw.get("required", True)),
        }
        explanation = str(raw.get("explanation", "")).strip()
        if explanation:
            thesis["explanation"] = explanation[:800]
        if raw.get("parent"):
            thesis["_parent_raw"] = str(raw["parent"])
        for key, known in (
            ("source_refs", blocks),
            ("fact_refs", set(facts)),
            ("dataset_refs", datasets),
            ("asset_refs", assets),
        ):
            values = raw.get(key)
            if isinstance(values, list):
                kept = [str(v) for v in values if str(v) in known]
                unknown = [str(v) for v in values if str(v) not in known]
                if unknown:
                    fixes.append(
                        {
                            "code": "ref_unknown",
                            "message": f"{new_id}.{key}: неизвестные {', '.join(unknown[:5])}",
                        }
                    )
                if kept:
                    thesis[key] = list(dict.fromkeys(kept))
        if raw.get("visual") in VISUALS:
            thesis["suggested_visual"] = raw["visual"]
        covers = raw.get("covers")
        if isinstance(covers, list):
            thesis["_covers"] = [str(c) for c in covers]
        # {fact:id} в формулировке должен ссылаться на существующий факт.
        for field_name in ("statement", "explanation"):
            text = thesis.get(field_name)
            if not text:
                continue
            for ref in FACT_REF.findall(text):
                if ref not in facts:
                    text = text.replace(f"{{fact:{ref}}}", "").replace("  ", " ").strip()
                    fixes.append(
                        {"code": "fact_placeholder_unknown", "message": f"{new_id}: {ref}"}
                    )
                else:
                    refs = thesis.setdefault("fact_refs", [])
                    if ref not in refs:
                        refs.append(ref)
            thesis[field_name] = text
        theses.append(thesis)
    if not theses:
        raise StoryError("story_empty", "Модель не вернула ни одного тезиса")
    # Родители по новым идентификаторам.
    for thesis in theses:
        parent_raw = thesis.pop("_parent_raw", None)
        if parent_raw:
            parent = id_map.get(parent_raw)
            if parent and parent != thesis["thesis_id"]:
                thesis["parent_id"] = parent
            else:
                fixes.append(
                    {"code": "parent_unknown", "message": f"{thesis['thesis_id']}: {parent_raw}"}
                )
    # Покрытие обязательных фактов.
    must_keep = [f for f in facts.values() if f.get("must_keep", True)]
    used: set[str] = set()
    for thesis in theses:
        used |= set(thesis.get("fact_refs", []))
    uncovered = [f for f in must_keep if f["fact_id"] not in used]
    for fact in uncovered:
        host = next(
            (
                t
                for t in theses
                if fact.get("block_id") and fact["block_id"] in t.get("source_refs", [])
            ),
            None,
        )
        if host is not None:
            host.setdefault("fact_refs", []).append(fact["fact_id"])
            fixes.append(
                {
                    "code": "must_keep_attached",
                    "message": f"{fact['fact_id']} → {host['thesis_id']}",
                }
            )
    uncovered = [
        f for f in uncovered if not any(f["fact_id"] in t.get("fact_refs", []) for t in theses)
    ]
    if uncovered:
        insert_at = next(
            (i for i, t in enumerate(theses) if t["kind"] in ("conclusion", "call_to_action")),
            len(theses),
        )
        statement = "Ключевые показатели из материалов: " + ", ".join(
            f"{(f.get('label') or f.get('raw'))} — {{fact:{f['fact_id']}}}" for f in uncovered[:8]
        )
        extra: JsonDict = {
            "thesis_id": "",
            "order": 0,
            "kind": "evidence",
            "statement": statement[:400],
            "required": True,
            "fact_refs": [f["fact_id"] for f in uncovered],
            "source_refs": list(
                dict.fromkeys(f["block_id"] for f in uncovered if f.get("block_id") in blocks)
            ),
            "suggested_visual": "number" if len(uncovered) <= 4 else "table",
        }
        theses.insert(insert_at, extra)
        fixes.append(
            {
                "code": "must_keep_added",
                "message": f"добавлен тезис с фактами {', '.join(f['fact_id'] for f in uncovered)}",
            }
        )
    # Покрытие пунктов брифа.
    coverage_items: list[JsonDict] = []
    for item in brief.get("must_include") or []:
        matched = [
            t
            for t in theses
            if item in t.get("_covers", [])
            or _mentions(item, t.get("statement", "") + " " + t.get("explanation", ""))
        ]
        if not matched:
            extra_item: JsonDict = {
                "thesis_id": "",
                "order": 0,
                "kind": "claim",
                "statement": item[:1].upper() + item[1:],
                "required": True,
                "suggested_visual": "text",
                "_covers": [item],
            }
            theses.append(extra_item)
            matched = [extra_item]
            fixes.append({"code": "must_include_added", "message": item})
        coverage_items.append({"item": item, "thesis": matched})
    # Порядок и идентификаторы после вставок; служебные поля убираем.
    old_ids = {t["thesis_id"]: t for t in theses if t["thesis_id"]}
    renumber: dict[str, str] = {}
    for i, thesis in enumerate(theses, start=1):
        if thesis["thesis_id"]:
            renumber[thesis["thesis_id"]] = f"t{i}"
        thesis["thesis_id"] = f"t{i}"
        thesis["order"] = i
    for thesis in theses:
        if thesis.get("parent_id"):
            thesis["parent_id"] = renumber.get(thesis["parent_id"], thesis["parent_id"])
            if thesis["parent_id"] not in {t["thesis_id"] for t in theses}:
                thesis.pop("parent_id")
        thesis.pop("_covers", None)
    _ = old_ids
    coverage_must_include = [
        {"item": c["item"], "thesis_ids": [t["thesis_id"] for t in c["thesis"]]}
        for c in coverage_items
    ]
    # Допустимые сокращения.
    reductions: list[JsonDict] = []
    thesis_ids = {t["thesis_id"] for t in theses}
    for raw in answer.get("allowed_reductions", []) or []:
        if not isinstance(raw, dict):
            continue
        tid = renumber.get(id_map.get(str(raw.get("thesis_id", "")), ""), "")
        red = raw.get("reduction")
        if tid not in thesis_ids or red not in REDUCTIONS:
            continue
        reduction: JsonDict = {"thesis_id": tid, "reduction": red}
        if red == "merge_with":
            target = renumber.get(id_map.get(str(raw.get("target_thesis_id", "")), ""), "")
            if target not in thesis_ids or target == tid:
                continue
            reduction["target_thesis_id"] = target
        if raw.get("note"):
            reduction["note"] = str(raw["note"])[:200]
        reductions.append(reduction)
    # Допущения: от модели и из недостающих данных.
    assumptions = [
        str(a).strip()[:300] for a in (answer.get("assumptions") or []) if str(a).strip()
    ]
    for m in package.get("missing_data") or []:
        note = f"«{m.get('what')}» в материалах отсутствует" + (
            f": {m['why_needed']}" if m.get("why_needed") else ""
        )
        if note not in assumptions:
            assumptions.append(note)
    if topic_only(package):
        notice = concept_notice(str(brief.get("language", "ru")))
        if notice not in assumptions:
            assumptions.append(notice)
        fixes.append({"code": CONCEPT_WARNING, "message": notice})
    used_after: set[str] = set()
    for thesis in theses:
        used_after |= set(thesis.get("fact_refs", []))
    story: JsonDict = {
        "schema_version": STORY_SCHEMA_VERSION,
        "story_id": story_id,
        "package_id": package["package_id"],
        "content_hash": content_hash,
        "language": brief.get("language", "ru"),
        "purpose": brief.get("purpose", "other"),
    }
    if brief.get("audience"):
        story["audience"] = brief["audience"]
    if brief.get("goal"):
        story["goal"] = brief["goal"]
    story["effective_brief"] = brief
    story["key_takeaway"] = (
        str(answer.get("key_takeaway", "")).strip()[:300] or theses[0]["statement"]
    )
    story["theses"] = theses
    if reductions:
        story["allowed_reductions"] = reductions
    if assumptions:
        story["assumptions"] = assumptions
    story["coverage"] = {
        "must_keep_facts": {
            "total": len(must_keep),
            "covered": sum(1 for f in must_keep if f["fact_id"] in used_after),
        },
        "must_include": coverage_must_include,
    }
    story["generation_meta"] = generation_meta
    story["warnings"] = [
        {"code": fix["code"], "message": fix["message"]}
        for fix in fixes
        if fix["code"] != "ref_unknown"
    ]
    return story, fixes


def _mentions(item: str, text: str) -> bool:
    words = [w.lower() for w in _WORD.findall(item) if len(w) >= 4]
    if not words:
        return False
    lowered = text.lower()
    hits = sum(1 for w in words if w[:5] in lowered)
    return hits >= max(1, (len(words) + 1) // 2)


# ---------- без модели ----------


def outline_without_model(package: JsonDict) -> JsonDict:
    """Детерминированный черновик ответа модели по структуре материалов: разделы из
    заголовков, тезисы из абзацев и списков, таблицы и изображения как свидетельства.
    Используется только по явному запросу (CLI --no-model) и помечается в плане."""
    theses: list[JsonDict] = []
    facts_by_block: dict[str, list[str]] = {}
    for f in package.get("facts", []):
        if f.get("block_id"):
            facts_by_block.setdefault(f["block_id"], []).append(f["fact_id"])
    section: str | None = None
    for b in package.get("blocks", []):
        kind = b.get("kind")
        tid = f"t{len(theses) + 1}"
        if kind == "heading":
            section = tid
            theses.append(
                {
                    "id": tid,
                    "kind": "section",
                    "statement": b.get("text", ""),
                    "required": True,
                    "source_refs": [b["block_id"]],
                }
            )
        elif kind in ("paragraph", "quote"):
            refs = facts_by_block.get(b["block_id"], [])
            theses.append(
                {
                    "id": tid,
                    "kind": "claim",
                    "parent": section,
                    "statement": str(b.get("text", ""))[:200],
                    "required": b.get("importance") == "must",
                    "source_refs": [b["block_id"]],
                    "fact_refs": refs,
                    "visual": "number" if refs else "text",
                }
            )
        elif kind == "bullets":
            theses.append(
                {
                    "id": tid,
                    "kind": "claim",
                    "parent": section,
                    "statement": "; ".join(b.get("items", [])[:5])[:200],
                    "required": True,
                    "source_refs": [b["block_id"]],
                    "fact_refs": facts_by_block.get(b["block_id"], []),
                    "visual": "bullets",
                }
            )
        elif kind == "table":
            theses.append(
                {
                    "id": tid,
                    "kind": "evidence",
                    "parent": section,
                    "statement": f"Данные: {b.get('caption') or b.get('dataset_id')}",
                    "required": True,
                    "source_refs": [b["block_id"]],
                    "dataset_refs": [b["dataset_id"]],
                    "fact_refs": facts_by_block.get(b["block_id"], []),
                    "visual": "chart",
                }
            )
        elif kind == "figure":
            theses.append(
                {
                    "id": tid,
                    "kind": "evidence",
                    "parent": section,
                    "statement": f"Иллюстрация: {b.get('caption') or b.get('asset_id')}",
                    "required": False,
                    "source_refs": [b["block_id"]],
                    "asset_refs": [b["asset_id"]],
                    "visual": "image",
                }
            )
    brief = package.get("brief") or {}
    theses.append(
        {
            "id": f"t{len(theses) + 1}",
            "kind": "conclusion",
            "statement": brief.get("goal") or f"Итог: {brief.get('title', 'презентация')}",
            "required": True,
        }
    )
    return {
        "key_takeaway": brief.get("goal") or brief.get("title") or "Итоги",
        "theses": theses,
        "allowed_reductions": [],
        "assumptions": ["план построен без модели по структуре материалов"],
    }


# ---------- вызов ----------


def build_story(
    package: JsonDict,
    settings: JsonDict | None,
    *,
    client: Any = None,
    skill: Any = None,
    app_settings: Settings | None = None,
    deadline_s: float | None = None,
    use_model: bool = True,
    story_id: str | None = None,
    nonce: str | None = None,
) -> StoryResult:
    """StoryPlan для пакета. С моделью — скилл story_planner; без неё (use_model=False) —
    детерминированный черновик с отметкой в generation_meta и предупреждением."""
    app_settings = app_settings or get_settings()
    started = time.perf_counter()
    settings = settings or {}
    brief = effective_brief(package, settings)
    model_ref: JsonDict | None = None
    if use_model and client is not None:
        model_ref = llm_model_ref(client, skill)
    content_hash = story_key(
        package, settings, skill=skill if use_model else None, model=model_ref, nonce=nonce
    )
    story_id = story_id or f"story_{content_hash[7:19]}"
    digest, truncated = story_digest(package, brief)
    report: JsonDict = {
        "digest_chars": len(digest),
        "digest_truncated": truncated,
        "content_hash": content_hash,
    }
    meta: JsonDict = {"skills": [], "prompts": [], "models": [], "created_at": now_iso()}
    if use_model:
        if client is None or skill is None:
            raise StoryError(
                "story_llm_not_configured",
                "Провайдер моделей не настроен: смысловой план не построен",
            )
        from presentation_designer.llm.types import Deadline, LlmError

        params = skill.manifest.params or {}
        budget = float(deadline_s if deadline_s is not None else params.get("time_budget_s", 90))
        req = skill.request("story.outline", digest, schema=STORY_MODEL_SCHEMA, stage="story")
        req.schema_name = "story_outline"
        req.deadline = Deadline.after(budget)
        if settings.get("seed") is not None:
            req.seed = int(settings["seed"])
        if nonce:
            req.regenerate_nonce = nonce
        try:
            resp = client.complete_sync(req, validator=_validate_answer)
        except LlmError as e:
            raise StoryError(
                e.code, f"Смысловой план не построен: {e}", retryable=e.retryable
            ) from e
        answer = resp.parsed
        meta = {
            "skills": [{"name": skill.name, "version": skill.version}],
            "prompts": [{"name": p.id, "version": p.version} for p in skill.prompts.values()],
            "models": [model_ref] if model_ref else [],
            "temperature": float(params.get("temperature", 0.2)),
            "prompt_tokens": int(resp.usage.prompt_tokens),
            "completion_tokens": int(resp.usage.completion_tokens),
            "cache_hit": bool(resp.cache_hit),
            "created_at": now_iso(),
        }
        if settings.get("seed") is not None:
            meta["seed"] = int(settings["seed"])
        report["llm"] = {
            "latency_ms": resp.latency_ms,
            "quota_wait_ms": resp.quota_wait_ms,
            "attempts": resp.attempts,
            "cache_hit": resp.cache_hit,
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
        }
    else:
        answer = outline_without_model(package)
        meta["skills"] = [{"name": "story_planner", "version": "none"}]
    story, fixes = assemble_story(
        answer, package, brief, content_hash=content_hash, story_id=story_id, generation_meta=meta
    )
    if not use_model:
        story.setdefault("warnings", []).append(
            {
                "code": "story_without_model",
                "message": "план построен без модели по структуре материалов",
            }
        )
    doc = StoryPlan.model_validate(story)
    from presentation_designer.contracts import ContentPackage

    violations = check_story_plan(doc, ContentPackage.model_validate(package))
    if violations:
        raise StoryError(
            "story_invalid",
            "План нарушает связи контракта: " + "; ".join(str(v) for v in violations[:5]),
        )
    report["fixes"] = fixes
    report["counts"] = {
        "theses": len(story["theses"]),
        "required": sum(1 for t in story["theses"] if t["required"]),
        "reductions": len(story.get("allowed_reductions", [])),
        "assumptions": len(story.get("assumptions", [])),
    }
    report["coverage"] = story["coverage"]
    report["total_ms"] = int((time.perf_counter() - started) * 1000)
    return StoryResult(story=story, report=report)


def llm_model_ref(client: Any, skill: Any = None) -> JsonDict | None:
    """Модель роли llm с режимом рассуждения скилла. Ревизия весов входит в ключ кэша
    ответов адаптера; в model_ref контракта её нет."""
    try:
        from presentation_designer.llm import skill_model_ref

        return skill_model_ref(client, skill)
    except Exception:
        return None


__all__ = [
    "STORY_MODEL_SCHEMA",
    "STORY_VERSION",
    "StoryError",
    "StoryResult",
    "assemble_story",
    "build_story",
    "effective_brief",
    "llm_model_ref",
    "normalize_package",
    "outline_without_model",
    "story_digest",
    "story_key",
]
