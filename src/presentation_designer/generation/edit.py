"""Правка одного слайда готового плана по инструкции из чата (скилл `slide_editor`).

Модель получает инструкцию, текущий слайд (композиция и что она вмещает, заголовок, текст,
пункты, факты), соседей заголовками, тезисы слайда, факты и данные пакета, короткий список
композиций шаблона с ёмкостью; отвечает слайдом в формате планировщика или отказом с
причиной. Дальше всё делает код планировщика: черновик через `drafts_from_answer` (пакет из
тезисов слайда; служебные роли — черновик по роли из пула роли), `fit_draft` с одним
повтором по подсказке переполнения, `slide_from_draft` с сохранением `slide_id` и `order`,
`revision_note`, пересчёт покрытия и сравнения, проверка связей контракта. Остальные слайды
плана не меняются: новая ревизия собирается по всему плану, но отличается одним слайдом.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.contracts import ContentPackage, SlidePlan, StoryPlan, TemplateProfile
from presentation_designer.contracts.validators import check_slide_plan
from presentation_designer.generation import capacity as cap
from presentation_designer.generation.matching import (
    CONTENT_VISUALS,
    FIXED_ROLES,
    Need,
    PatternInfo,
    candidates_for,
    fixed_pattern_pool,
)
from presentation_designer.generation.variants import (
    FACT_REF,
    PLAN_MODEL_SCHEMA,
    VARIANT_RULES,
    Context,
    Draft,
    Packet,
    Thesis,
    _drop_overflowing_optional,
    _fact_line,
    _visual_kind,
    build_context,
    drafts_from_answer,
    fit_draft,
    now_iso,
    overflow_hint,
    packet_candidates,
    slide_chars,
    slide_from_draft,
    with_capacity_hint,
)
from presentation_designer.shared.settings import Settings, get_settings

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]

EDIT_VERSION = "0.1.0"

EDIT_MODEL_SCHEMA: JsonDict = {
    "type": "object",
    "required": ["unchanged", "slides"],
    "properties": {
        "unchanged": {"type": "boolean"},
        "reason": {"type": "string"},
        "change_note": {"type": "string"},
        "slides": PLAN_MODEL_SCHEMA["properties"]["slides"],
    },
}

# Подача, которую просит инструкция: пул композиций этой подачи добавляется к кандидатам.
VISUAL_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"диаграмм|график", re.I), "chart"),
    (re.compile(r"таблиц", re.I), "table"),
    (re.compile(r"карточк", re.I), "cards"),
    (re.compile(r"показател|цифр|числ", re.I), "number"),
    (re.compile(r"схем|процесс|шаг", re.I), "diagram"),
    (re.compile(r"картинк|фото|изображен|иллюстрац", re.I), "image"),
    (re.compile(r"списк|списо|пункт|буллит", re.I), "bullets"),
    (re.compile(r"сравнен|до и после", re.I), "comparison"),
    (re.compile(r"таймлайн|хронолог|этап", re.I), "timeline"),
    (re.compile(r"цитат", re.I), "quote"),
)
SERVICE_KINDS = {
    "title": "title",
    "section_divider": "divider",
    "agenda": "agenda",
    "thanks": "final",
    "qr": "final",
}
MAX_INSTRUCTION_CHARS = 1000
OTHER_FACTS_LIMIT = 24


class EditError(RuntimeError):
    """Правка не выполнена: код для контракта error и подробности."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: JsonDict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details or {}


@dataclass
class EditResult:
    """Новый план (None, если просьба отклонена), пояснение для пользователя и отчёт."""

    plan: JsonDict | None
    changed: bool
    slide_id: str
    change_note: str = ""
    reason: str = ""
    report: JsonDict = field(default_factory=dict)


# ---------- слайд плана ----------


def ordered_slides(plan: JsonDict) -> list[JsonDict]:
    return sorted(plan.get("slides") or [], key=lambda s: int(s.get("order", 0)))


def slide_at(plan: JsonDict, slide_index: int) -> JsonDict:
    slides = ordered_slides(plan)
    if slide_index < 0 or slide_index >= len(slides):
        raise EditError(
            "slide_index_out_of_range",
            f"В плане {len(slides)} слайдов, слайда с номером {slide_index + 1} нет",
        )
    return slides[slide_index]


def _clean(text: Any, limit: int = 600) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def requested_visuals(instruction: str) -> list[str]:
    return [visual for rx, visual in VISUAL_HINTS if rx.search(instruction)]


def slide_theses(ctx: Context, slide: JsonDict) -> list[Thesis]:
    out: list[Thesis] = []
    for tid in slide.get("thesis_refs") or []:
        t = ctx.thesis(str(tid))
        if t is not None and t.kind != "section":
            out.append(t)
    return out


def is_service(slide: JsonDict) -> bool:
    return str(slide.get("role")) in FIXED_ROLES


def _slide_items(slide: JsonDict) -> int:
    return max((len(b["items"]) for b in slide.get("blocks") or [] if b.get("items")), default=0)


def edit_candidates(
    ctx: Context, slide: JsonDict, theses: list[Thesis], instruction: str
) -> list[PatternInfo]:
    """Композиции для правки: текущая первой, кандидаты по тезисам слайда, пул подачи, которую
    просит инструкция; у служебных слайдов — пул роли."""
    by_id = {p.pattern_id: p for p in ctx.patterns}
    out: dict[str, PatternInfo] = {}
    current = by_id.get(str(slide.get("pattern_id")))
    if current is not None:
        out[current.pattern_id] = current
    if is_service(slide) or not theses:
        role = current.role if current is not None else str(slide.get("role", "title"))
        for p in fixed_pattern_pool(ctx.patterns, role, has_datasets=ctx.has_datasets):
            out.setdefault(p.pattern_id, p)
    else:
        packet = Packet(0, theses, 1, 1, 1)
        for lst in packet_candidates(ctx, packet).values():
            for c in lst:
                out.setdefault(c.pattern_id, c)
        has_ds = any(d in ctx.datasets for t in theses for d in t.dataset_refs)
        has_image = any(a in ctx.assets for t in theses for a in t.asset_refs)
        chars = len(slide.get("title") or "") + sum(
            len(str(b.get("text") or "")) for b in slide.get("blocks") or []
        )
        for visual in requested_visuals(instruction):
            need = Need(
                visual,
                items=max(3, _slide_items(slide)),
                numbers=min(4, len(slide.get("fact_refs") or [])) if visual == "number" else 0,
                text_chars=max(chars, 40),
                has_dataset=has_ds,
                has_image=has_image,
            )
            for c in candidates_for(
                ctx.patterns, need, ctx.variant_id, limit=3, has_datasets=has_ds
            ):
                out.setdefault(c.pattern_id, c)
    limit = max(4, int(ctx.app.plan.max_candidates))
    return list(out.values())[:limit]


# ---------- выдержка для модели ----------


def _unsubstitute(text: str, ctx: Context, fact_ids: list[str]) -> str:
    """Значения фактов в заголовке плана обратно в ссылки {fact:id}: модель должна
    ссылаться на факты, а не переписывать числа."""
    out = text
    for fid in fact_ids:
        fact = ctx.facts.get(fid)
        if not fact:
            continue
        value = cap.fact_text(fact)
        if value and value in out:
            out = out.replace(value, f"{{fact:{fid}}}")
    return out


def describe_blocks(slide: JsonDict) -> list[str]:
    lines: list[str] = []
    for b in slide.get("blocks") or []:
        kind = str(b.get("kind"))
        slot = str(b.get("slot_id"))
        if kind == "title":
            continue
        if kind in ("subtitle", "body", "caption", "label", "date") and b.get("text"):
            label = {"subtitle": "подзаголовок", "body": "текст", "caption": "подпись"}.get(
                kind, kind
            )
            lines.append(f"  {label} ({slot}): {_clean(b['text'], 400)}")
        elif kind == "bullets" and b.get("items"):
            items = "; ".join(
                f"{i + 1}) {_clean(it.get('text'), 200)}" for i, it in enumerate(b["items"])
            )
            lines.append(f"  список ({slot}): {items}")
        elif kind == "number" and b.get("number"):
            lines.append(f"  показатель ({slot}): {{fact:{b['number']['fact_id']}}}")
        elif kind == "table" and b.get("table"):
            t = b["table"]
            lines.append(
                f"  таблица ({slot}): набор {t.get('dataset_id')}, колонки "
                f"{', '.join(t.get('columns') or []) or 'все'}"
            )
        elif kind == "chart" and b.get("chart"):
            c = b["chart"]
            lines.append(
                f"  диаграмма ({slot}): {c.get('type')} по набору {c.get('dataset_id')}, "
                f"ряды {', '.join(c.get('series') or []) or '—'}"
            )
        elif kind == "image" and b.get("image"):
            lines.append(f"  изображение ({slot}): {b['image'].get('asset_id') or 'по описанию'}")
        elif kind == "diagram" and b.get("diagram"):
            d = b["diagram"]
            items = "; ".join(_clean(it.get("text"), 120) for it in d.get("items") or [])
            lines.append(f"  схема ({slot}): {d.get('kind')} — {items}")
    return lines


def edit_digest(
    ctx: Context,
    plan: JsonDict,
    slide: JsonDict,
    theses: list[Thesis],
    candidates: list[PatternInfo],
    instruction: str,
) -> str:
    story = ctx.story
    brief = story.get("effective_brief") or {}
    slides = ordered_slides(plan)
    index = next((i for i, s in enumerate(slides) if s["slide_id"] == slide["slide_id"]), 0)
    lines: list[str] = []
    lines.append(f"Просьба пользователя: «{_clean(instruction, MAX_INSTRUCTION_CHARS)}»")
    lines.append(f"Вариант: {ctx.variant_id}. {VARIANT_RULES.get(ctx.variant_id, '')}")
    lines.append(
        "Бриф: "
        + "; ".join(
            s
            for s in (
                f"тема — «{brief.get('title', '')}»" if brief.get("title") else "",
                f"аудитория — {brief['audience']}" if brief.get("audience") else "",
                f"цель — {brief['goal']}" if brief.get("goal") else "",
                f"тон — {brief['tone']}" if brief.get("tone") else "",
                f"язык — {story.get('language', 'ru')}",
            )
            if s
        )
        + "."
    )
    current = next((c for c in candidates if c.pattern_id == slide.get("pattern_id")), None)
    fact_ids = list(slide.get("fact_refs") or [])
    lines.append(
        f"Слайд {index + 1} из {len(slides)}, роль {slide.get('role')}, композиция "
        f"{current.summary() if current else slide.get('pattern_id')}"
    )
    lines.append(f"  заголовок: {_unsubstitute(str(slide.get('title', '')), ctx, fact_ids)}")
    if slide.get("key_message"):
        lines.append(f"  ключевая мысль: {_unsubstitute(str(slide['key_message']), ctx, fact_ids)}")
    lines.extend(describe_blocks(slide))
    if slide.get("notes"):
        lines.append(f"  заметки: {_clean(slide['notes'], 300)}")
    neighbours = []
    if index > 0:
        neighbours.append(f"слайд {index} «{_clean(slides[index - 1].get('title'), 80)}»")
    if index + 1 < len(slides):
        neighbours.append(f"слайд {index + 2} «{_clean(slides[index + 1].get('title'), 80)}»")
    if neighbours:
        lines.append("Соседи: " + "; ".join(neighbours) + ".")
    fact_list: list[str] = []
    ds_ids: list[str] = []
    asset_ids: list[str] = []
    if theses:
        lines.append(
            "Тезисы слайда (идентификатор · вид · обязательный · формулировка · пояснение · "
            "факты · данные · изображения):"
        )
        for t in theses:
            lines.append(
                f"{t.id} · {t.kind} · {'да' if t.required else 'нет'} · {t.statement} · "
                f"{t.explanation or '—'} · {', '.join(t.fact_refs) or '—'} · "
                f"{', '.join(t.dataset_refs) or '—'} · {', '.join(t.asset_refs) or '—'}"
            )
            fact_list.extend(f for f in t.fact_refs if f in ctx.facts and f not in fact_list)
            ds_ids.extend(d for d in t.dataset_refs if d in ctx.datasets and d not in ds_ids)
            asset_ids.extend(a for a in t.asset_refs if a in ctx.assets and a not in asset_ids)
    fact_list.extend(f for f in fact_ids if f in ctx.facts and f not in fact_list)
    if fact_list:
        lines.append("Факты слайда (только ссылками {fact:id}; значение подставит код):")
        lines.extend(_fact_line(ctx.facts[f]) for f in fact_list)
    others = [f for f in ctx.facts if f not in fact_list][:OTHER_FACTS_LIMIT]
    if others:
        lines.append("Другие факты пакета, если просьба про них:")
        lines.extend(_fact_line(ctx.facts[f]) for f in others)
    for d in ds_ids or list(ctx.datasets)[:3]:
        ds = ctx.datasets[d]
        cols = "; ".join(
            f"{c['name']}" + (f" ({c['unit']})" if c.get("unit") else "") for c in ds["columns"]
        )
        rows = " / ".join(" | ".join(str(v) for v in row) for row in ds["rows"][:3])
        total = ds.get("total_rows", len(ds["rows"]))
        lines.append(
            f"Набор данных {d}"
            + (f" «{ds['title']}»" if ds.get("title") else "")
            + f": колонки {cols}; строк {total}; первые: {rows}"
        )
    for a in asset_ids or list(ctx.assets)[:3]:
        asset = ctx.assets[a]
        lines.append(
            f"Изображение {a} ({asset.get('kind', 'image')}, "
            f"{asset.get('width_px', '?')}×{asset.get('height_px', '?')})"
            + (f" «{asset['caption']}»" if asset.get("caption") else "")
        )
    lines.append("Композиции шаблона (текущая первой; идентификатор · роль · что вмещает):")
    lines.extend(c.summary() for c in candidates)
    return "\n".join(lines)


# ---------- ответ модели → черновик ----------


def _items_from_answer(ctx: Context, raw: JsonDict) -> list[JsonDict]:
    items: list[JsonDict] = []
    for it in raw.get("items") or []:
        if isinstance(it, dict) and _clean(it.get("text")):
            item: JsonDict = {"text": _clean(it.get("text"), 300)}
            if _clean(it.get("sub")):
                item["sub"] = _clean(it.get("sub"), 300)
            facts = [str(f) for f in (it.get("facts") or []) if str(f) in ctx.facts] + [
                f for f in FACT_REF.findall(item["text"]) if f in ctx.facts
            ]
            if facts:
                item["fact_refs"] = list(dict.fromkeys(facts))
            items.append(item)
        elif isinstance(it, str) and it.strip():
            items.append({"text": _clean(it, 300)})
    return items


def service_draft(
    ctx: Context, slide: JsonDict, raw: JsonDict, candidates: list[PatternInfo]
) -> Draft:
    """Черновик служебного слайда (титул, разделитель, оглавление, финал) из ответа модели:
    композиция из пула роли, тезисы прежние."""
    by_id = {c.pattern_id: c for c in candidates}
    pattern = by_id.get(str(raw.get("pattern", ""))) or candidates[0]
    if pattern.pattern_id != str(raw.get("pattern", "")) and raw.get("pattern"):
        ctx.fix("pattern_remapped", f"«{_clean(slide.get('title'), 40)}»: {raw.get('pattern')}")
    title = _clean(raw.get("title"), 200) or _clean(slide.get("title"), 200)
    if not title:
        raise ValueError("нужен непустой title")
    visual = str(raw.get("visual") or "text")
    if visual not in CONTENT_VISUALS:
        visual = "text"
    facts = [str(f) for f in (raw.get("facts") or []) if str(f) in ctx.facts]
    text = _clean(raw.get("text"), 1200)
    for ref in FACT_REF.findall(text + " " + title):
        if ref in ctx.facts and ref not in facts:
            facts.append(ref)
    theses = [str(t) for t in slide.get("thesis_refs") or []]
    section = next((t.id for tid in theses if (t := ctx.thesis(tid)) and t.kind == "section"), None)
    return Draft(
        kind=SERVICE_KINDS.get(str(slide.get("role")), "content"),
        theses=theses,
        pattern=pattern,
        title=title,
        message=_clean(raw.get("message"), 300),
        visual=visual,
        text=text,
        items=_items_from_answer(ctx, raw),
        facts=list(dict.fromkeys(facts)),
        notes=_clean(raw.get("notes"), 600),
        section=section,
        candidates=[c for c in candidates if c.pattern_id != pattern.pattern_id],
    )


def make_edit_validator(
    ctx: Context,
    slide: JsonDict,
    packet: Packet | None,
    candidates: list[PatternInfo],
) -> Any:
    """Структура ответа: отказ с причиной или ровно один слайд с композицией из списка.
    Детерминирована, поэтому совместима с кэшем и replay; ёмкость проверяется отдельно."""

    def validate(value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("ответ должен быть объектом")
        if value.get("unchanged"):
            reason = _clean(value.get("reason"), 300)
            if not reason:
                raise ValueError("при unchanged нужна причина в reason")
            return {"unchanged": True, "reason": reason}
        slides = value.get("slides")
        if not isinstance(slides, list) or len(slides) != 1 or not isinstance(slides[0], dict):
            raise ValueError("нужен ровно один слайд в slides")
        if packet is not None:
            raw = dict(slides[0])
            if not raw.get("theses"):
                raw["theses"] = [t.id for t in packet.theses]
            draft = drafts_from_answer(ctx, packet, {"slides": [raw]})[0]
        else:
            draft = service_draft(ctx, slide, slides[0], candidates)
        return {
            "unchanged": False,
            "draft": draft,
            "change_note": _clean(value.get("change_note"), 200),
        }

    return validate


# ---------- новый план ----------


def _fit_edit(ctx: Context, draft: Draft) -> Draft:
    fitted = fit_draft(ctx, draft)
    if fitted.kind != "content":
        _drop_overflowing_optional(fitted)
    return fitted


def apply_slide(
    ctx: Context,
    plan: JsonDict,
    slide: JsonDict,
    draft: Draft,
    *,
    change_note: str,
    meta_update: JsonDict | None = None,
) -> JsonDict:
    """Новый план с заменённым слайдом: slide_id и order прежние, заметки сохраняются, если
    правка их не касалась; покрытие и сравнение пересчитываются."""
    new_slide = slide_from_draft(ctx, draft, slide_id=slide["slide_id"], order=int(slide["order"]))
    if not draft.notes and slide.get("notes"):
        new_slide["notes"] = slide["notes"]
    if change_note:
        new_slide["revision_note"] = change_note
    doc = copy.deepcopy(plan)
    slides = doc["slides"]
    pos = next(i for i, s in enumerate(slides) if s["slide_id"] == slide["slide_id"])
    slides[pos] = new_slide
    ordered = ordered_slides(doc)
    required = [t.id for t in ctx.theses if t.required]
    covered: list[JsonDict] = []
    missing: list[str] = []
    for tid in required:
        sids = [s["slide_id"] for s in ordered if tid in (s.get("thesis_refs") or [])]
        if sids:
            entry: JsonDict = {"thesis_id": tid, "slide_ids": sids}
            old = next(
                (
                    c
                    for c in (doc.get("coverage") or {}).get("covered", [])
                    if c.get("thesis_id") == tid and c.get("reduction")
                ),
                None,
            )
            if old:
                entry["reduction"] = old["reduction"]
            covered.append(entry)
        else:
            missing.append(tid)
    doc["coverage"] = {"required_thesis_ids": required, "covered": covered, "missing": missing}
    comparison = dict(doc.get("comparison") or {})
    kinds = list(comparison.get("visual_kinds") or [])
    index = next(i for i, s in enumerate(ordered) if s["slide_id"] == slide["slide_id"])
    if len(kinds) == len(ordered):
        kinds[index] = _visual_kind(draft)
    else:
        kinds = [_visual_kind(draft) if i == index else "text" for i in range(len(ordered))]
    comparison["pattern_sequence"] = [s["pattern_id"] for s in ordered]
    comparison["visual_kinds"] = kinds
    comparison["text_chars_total"] = sum(slide_chars(ctx, s.get("blocks") or []) for s in ordered)
    doc["comparison"] = comparison
    old_title = _clean(slide.get("title"), 60)
    warnings = [
        w
        for w in doc.get("warnings") or []
        if not (
            w.get("code") == "capacity_overflow" and old_title and old_title in w.get("message", "")
        )
    ]
    if draft.overflow:
        warnings.append(
            {
                "code": "capacity_overflow",
                "message": f"«{new_slide['title'][:60]}» ({draft.pattern.pattern_id}): текст не "
                "помещается и после лестницы ёмкости — проверит аудит",
            }
        )
    doc["warnings"] = warnings
    if meta_update:
        meta = dict(doc.get("generation_meta") or {"skills": [], "models": []})
        for key in ("skills", "prompts", "models"):
            merged = list(meta.get(key) or [])
            for ref in meta_update.get(key) or []:
                if ref not in merged:
                    merged.append(ref)
            meta[key] = merged
        for key in ("prompt_tokens", "completion_tokens"):
            if key in meta_update:
                meta[key] = int(meta.get(key) or 0) + int(meta_update[key])
        meta["created_at"] = now_iso()
        doc["generation_meta"] = meta
    return doc


def validate_plan(doc: JsonDict, profile: JsonDict, package: JsonDict, story: JsonDict) -> None:
    validated = SlidePlan.model_validate(doc)
    violations = check_slide_plan(
        validated,
        TemplateProfile.model_validate(profile),
        ContentPackage.model_validate(package),
        StoryPlan.model_validate(story),
    )
    violations = [
        v
        for v in violations
        if v.code != "package_mismatch" or story.get("package_id") == package.get("package_id")
    ]
    if violations:
        raise EditError(
            "edit_invalid",
            "Правка нарушает связи контракта: " + "; ".join(str(v) for v in violations[:6]),
            details={"violations": [str(v) for v in violations[:20]]},
        )


def edit_slide(
    plan: JsonDict,
    story: JsonDict,
    profile: JsonDict,
    package: JsonDict,
    *,
    slide_index: int,
    instruction: str,
    client: Any,
    skill: Any,
    settings: JsonDict | None = None,
    app_settings: Settings | None = None,
    deadline_s: float | None = None,
    nonce: str | None = None,
) -> EditResult:
    """Правка слайда `slide_index` (с нуля, по порядку) плана по инструкции. Модель обязательна:
    без неё — `edit_llm_not_configured`; отказ модели — `changed=False` с причиной."""
    from presentation_designer.llm.types import Deadline, LlmError

    app = app_settings or get_settings()
    started = time.perf_counter()
    instruction = _clean(instruction, MAX_INSTRUCTION_CHARS)
    if not instruction:
        raise EditError("instruction_required", "Напишите, что изменить на слайде")
    if client is None or skill is None:
        raise EditError(
            "edit_llm_not_configured", "Провайдер моделей не настроен: правка слайда невозможна"
        )
    variant_id = str((plan.get("variant") or {}).get("variant_id") or "balanced")
    ctx = build_context(story, profile, package, variant_id, settings or {}, app, None)
    slide = slide_at(plan, slide_index)
    theses = slide_theses(ctx, slide)
    content = not is_service(slide) and bool(theses)
    candidates = edit_candidates(ctx, slide, theses, instruction)
    if not candidates:
        raise EditError("edit_no_patterns", "В шаблоне нет композиций для этого слайда")
    packet: Packet | None = None
    if content:
        packet = Packet(0, theses, 1, 1, 1, candidates={t.id: list(candidates) for t in theses})
    digest = edit_digest(ctx, plan, slide, theses, candidates, instruction)
    params = skill.manifest.params or {}
    budget = float(deadline_s if deadline_s is not None else params.get("time_budget_s", 60))
    req = skill.request("edit.slide", digest, schema=EDIT_MODEL_SCHEMA, stage="plan")
    req.schema_name = "edit_slide"
    req.deadline = Deadline.after(budget)
    req.variant_id = variant_id
    req.slide_ids = [str(slide["slide_id"])]
    if (settings or {}).get("seed") is not None:
        req.seed = int((settings or {})["seed"])
    if nonce:
        req.regenerate_nonce = nonce
    validator = make_edit_validator(ctx, slide, packet, candidates)
    report: JsonDict = {
        "slide_id": slide["slide_id"],
        "slide_index": slide_index,
        "pattern_before": slide.get("pattern_id"),
        "candidates": [c.pattern_id for c in candidates],
        "instruction": instruction,
    }

    async def run() -> tuple[Any, list[Any]]:
        responses: list[Any] = []
        resp = await client.complete(req, validator=validator)
        responses.append(resp)
        parsed = resp.parsed
        if parsed.get("unchanged"):
            return parsed, responses
        draft = _fit_edit(ctx, parsed["draft"])
        hint = overflow_hint([draft])
        if hint and req.deadline is not None and req.deadline.remaining() >= 5:
            retry = with_capacity_hint(req, resp.text, hint)
            resp2 = await client.complete(retry, validator=validator)
            responses.append(resp2)
            parsed2 = resp2.parsed
            if not parsed2.get("unchanged"):
                draft2 = _fit_edit(ctx, parsed2["draft"])
                if len(draft2.overflow) <= len(draft.overflow):
                    draft = draft2
                    parsed = parsed2
            ctx.fix("edit_retried", "повтор из-за переполнения")
        return {**parsed, "draft": draft}, responses

    try:
        parsed, responses = asyncio.run(run())
    except LlmError as e:
        raise EditError(e.code, str(e), retryable=e.retryable) from e
    from presentation_designer.generation.story import llm_model_ref

    model_ref = llm_model_ref(client, skill)
    report["llm"] = {
        "latency_ms": sum(r.latency_ms for r in responses),
        "quota_wait_ms": sum(r.quota_wait_ms for r in responses),
        "attempts": sum(r.attempts for r in responses),
        "calls": len(responses),
        "cache_hit": all(r.cache_hit for r in responses),
        "prompt_tokens": sum(r.usage.prompt_tokens for r in responses),
        "completion_tokens": sum(r.usage.completion_tokens for r in responses),
    }
    if parsed.get("unchanged"):
        report["fixes"] = list(ctx.fixes)
        report["total_ms"] = int((time.perf_counter() - started) * 1000)
        return EditResult(
            plan=None,
            changed=False,
            slide_id=str(slide["slide_id"]),
            reason=str(parsed.get("reason") or ""),
            report=report,
        )
    draft: Draft = parsed["draft"]
    change_note = str(parsed.get("change_note") or "").strip() or "Слайд переделан по просьбе"
    meta_update: JsonDict = {
        "skills": [{"name": skill.name, "version": skill.version}],
        "prompts": [{"name": p.id, "version": p.version} for p in skill.prompts.values()],
        "models": [model_ref] if model_ref else [],
        "prompt_tokens": report["llm"]["prompt_tokens"],
        "completion_tokens": report["llm"]["completion_tokens"],
    }
    doc = apply_slide(ctx, plan, slide, draft, change_note=change_note, meta_update=meta_update)
    validate_plan(doc, profile, package, story)
    report["pattern_after"] = draft.pattern.pattern_id
    report["actions"] = list(draft.actions)
    report["overflow"] = list(draft.overflow)
    report["fixes"] = list(ctx.fixes)
    report["total_ms"] = int((time.perf_counter() - started) * 1000)
    log.info(
        "правка %s: %s → %s, переполнений %d, %d мс",
        slide["slide_id"],
        slide.get("pattern_id"),
        draft.pattern.pattern_id,
        len(draft.overflow),
        report["total_ms"],
    )
    return EditResult(
        plan=doc,
        changed=True,
        slide_id=str(slide["slide_id"]),
        change_note=change_note,
        report=report,
    )


__all__ = [
    "EDIT_MODEL_SCHEMA",
    "EDIT_VERSION",
    "EditError",
    "EditResult",
    "apply_slide",
    "edit_candidates",
    "edit_digest",
    "edit_slide",
    "make_edit_validator",
    "ordered_slides",
    "requested_visuals",
    "slide_at",
]
