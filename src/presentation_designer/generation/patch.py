"""Ручные правки из визуального редактора → новый план (документ `slide_patch`, этап 22).

Слой детерминирован и не вызывает модель: у перечисленных слайдов список
`overrides` заменяется целиком (пустой список возвращает слайд к сгенерированному виду),
`order` переставляет слайды, остальное в плане не меняется. Проверки — до применения:
слайд и объект должны существовать в ComposedDeck базовой ревизии, вид операции должен
подходить виду объекта; после применения план проходит те же проверки контракта, что
у правки из чата. Сводка правок пишется в `revision_note` слайдов и уходит в карточку чата.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from presentation_designer.contracts import ComposedDeck, SlidePatch, TemplateProfile
from presentation_designer.contracts.models import ContentPackage, StoryPlan
from presentation_designer.contracts.validators import Violation, check_slide_plan
from presentation_designer.generation.edit import ordered_slides
from presentation_designer.generation.variants import upgrade_plan_schema
from presentation_designer.pipeline.state import now_iso

JsonDict = dict[str, Any]
PATCH_VERSION = "0.1.0"
PATCH_SCHEMA_VERSION = "1.0"

# Какие виды объектов ComposedDeck принимают операцию.
OP_KINDS: dict[str, tuple[str, ...]] = {
    "text": ("text", "placeholder_empty", "shape"),
    "style": ("text", "placeholder_empty", "shape"),
    "picture": ("picture",),
    "geometry": ("text", "placeholder_empty", "shape", "picture", "table", "chart", "group"),
}
SLOT_LABELS = {
    "title": "заголовок",
    "subtitle": "подзаголовок",
    "body": "текст",
    "bullets": "список",
    "number": "показатель",
    "label": "подпись",
    "caption": "подпись",
    "date": "дата",
    "name": "имя",
    "position": "должность",
    "image": "картинка",
    "icon": "иконка",
    "qr": "QR-код",
    "code": "код",
}
SOURCE_LABELS = {"template": "из шаблона", "package": "из материалов", "file": "своя"}
ALIGN_LABELS = {
    "left": "по левому краю",
    "center": "по центру",
    "right": "по правому краю",
    "justify": "по ширине",
}


class PatchError(RuntimeError):
    def __init__(self, code: str, message: str, details: JsonDict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass
class PatchResult:
    plan: JsonDict
    summary: str
    changed_slide_ids: list[str]
    warnings: list[JsonDict] = field(default_factory=list)
    report: JsonDict = field(default_factory=dict)


# ---------- нормализация и проверка ----------


def normalize_patch(raw: JsonDict) -> JsonDict:
    """Последняя правка вида `op` на объект побеждает (фон — на слайд), пустые поля
    отбрасываются, порядок остальных правок сохраняется."""
    doc = copy.deepcopy(raw)
    slides_out: list[JsonDict] = []
    seen_slides: dict[str, int] = {}
    for entry in doc.get("slides") or []:
        slide_id = str(entry.get("slide_id") or "")
        kept: dict[tuple[str, str], JsonDict] = {}
        order: list[tuple[str, str]] = []
        for override in entry.get("overrides") or []:
            if not isinstance(override, dict) or not override.get("op"):
                continue
            clean = {k: v for k, v in override.items() if v is not None}
            target = clean.get("target") or {}
            if isinstance(target, dict):
                clean_target = {k: v for k, v in target.items() if v not in (None, "")}
                if clean_target:
                    clean["target"] = clean_target
                else:
                    clean.pop("target", None)
            key = (str(clean["op"]), str((clean.get("target") or {}).get("object_id") or ""))
            if key in kept:
                order.remove(key)
            kept[key] = clean
            order.append(key)
        slide_entry = {"slide_id": slide_id, "overrides": [kept[k] for k in order]}
        if slide_id in seen_slides:
            slides_out[seen_slides[slide_id]] = slide_entry
        else:
            seen_slides[slide_id] = len(slides_out)
            slides_out.append(slide_entry)
    doc["slides"] = slides_out
    if doc.get("order") is not None:
        doc["order"] = [str(s) for s in doc["order"]]
    return doc


def _deck_objects(deck: JsonDict) -> dict[str, dict[str, JsonDict]]:
    return {
        str(s.get("slide_id")): {str(o.get("object_id")): o for o in s.get("objects") or []}
        for s in deck.get("slides") or []
    }


def validate_patch(
    patch: JsonDict,
    plan: JsonDict,
    deck: JsonDict | None,
    profile: JsonDict | None = None,
    package: JsonDict | None = None,
) -> list[Violation]:
    """Нарушения патча против плана и ComposedDeck базовой ревизии (без хранилища файлов)."""
    out: list[Violation] = []
    plan_ids = [str(s.get("slide_id")) for s in plan.get("slides") or []]
    plan_set = set(plan_ids)
    objects = _deck_objects(deck) if deck else {}
    for entry in patch.get("slides") or []:
        slide_id = str(entry.get("slide_id") or "")
        spath = f"slides[{slide_id}]"
        if slide_id not in plan_set:
            out.append(Violation("slide_unknown", f"слайда {slide_id} нет в плане", spath))
            continue
        slide_objects = objects.get(slide_id)
        for i, override in enumerate(entry.get("overrides") or []):
            op = str(override.get("op") or "")
            opath = f"{spath}.overrides[{i}]"
            if op == "background":
                continue
            object_id = str((override.get("target") or {}).get("object_id") or "")
            if slide_objects is None:
                continue
            obj = slide_objects.get(object_id)
            if obj is None:
                out.append(
                    Violation(
                        "object_unknown", f"объекта {object_id} нет на слайде {slide_id}", opath
                    )
                )
                continue
            kind = str(obj.get("kind") or "")
            allowed = OP_KINDS.get(op)
            if allowed is not None and kind not in allowed:
                out.append(
                    Violation(
                        "override_unsupported",
                        f"правка {op} не подходит объекту вида {kind} ({object_id})",
                        opath,
                    )
                )
    order = patch.get("order")
    if order is not None:
        ids = [str(s) for s in order]
        if sorted(ids) != sorted(plan_ids) or len(set(ids)) != len(ids):
            out.append(
                Violation(
                    "order_invalid",
                    "порядок должен быть перестановкой всех slide_id плана",
                    "order",
                )
            )
    return out


def patch_is_noop(patch: JsonDict, plan: JsonDict) -> bool:
    """Правки совпадают с планом базовой ревизии: ни один список overrides не меняется и
    порядок прежний — такую ревизию создавать незачем."""
    slides_by_id = {str(s.get("slide_id")): s for s in plan.get("slides") or []}
    for entry in patch.get("slides") or []:
        current = slides_by_id.get(str(entry.get("slide_id") or ""))
        if current is None:
            return False
        if (current.get("overrides") or []) != (entry.get("overrides") or []):
            return False
    order = patch.get("order")
    if order is not None:
        current_order = [
            str(s.get("slide_id"))
            for s in sorted(plan.get("slides") or [], key=lambda s: int(s.get("order", 0)))
        ]
        if [str(s) for s in order] != current_order:
            return False
    return True


# ---------- описание ----------


def _object_label(obj: JsonDict | None, object_id: str) -> str:
    if obj is None:
        return f"объект {object_id}"
    slot_kind = str(obj.get("slot_kind") or obj.get("block_kind") or "")
    if slot_kind in SLOT_LABELS:
        return SLOT_LABELS[slot_kind]
    kind = str(obj.get("kind") or "")
    if kind == "picture":
        return "картинка"
    if kind in ("text", "placeholder_empty"):
        return "текст"
    name = str(obj.get("name") or "").strip()
    return f"«{name}»" if name else f"объект {object_id}"


def _describe_override(override: JsonDict, obj: JsonDict | None) -> str:
    op = str(override.get("op") or "")
    object_id = str((override.get("target") or {}).get("object_id") or "")
    label = _object_label(obj, object_id)
    if op == "text":
        return f"{label}: текст"
    if op == "style":
        style = override.get("style") or {}
        font = style.get("font") or {}
        parts: list[str] = []
        if font.get("size_pt") is not None:
            parts.append(f"кегль {float(font['size_pt']):g}")
        if font.get("family"):
            parts.append(f"гарнитура {font['family']}")
        if font.get("bold") is not None:
            parts.append("жирный" if font["bold"] else "обычный")
        if font.get("italic") is not None:
            parts.append("курсив" if font["italic"] else "без курсива")
        if font.get("color"):
            parts.append(f"цвет {str(font['color']).upper()}")
        if style.get("align"):
            parts.append(ALIGN_LABELS.get(str(style["align"]), str(style["align"])))
        return f"{label}: " + (", ".join(parts) if parts else "стиль")
    if op == "geometry":
        return f"{label}: положение"
    if op == "picture":
        source = (override.get("picture") or {}).get("source") or {}
        kind = SOURCE_LABELS.get(str(source.get("kind") or ""), "")
        name = str(source.get("name") or source.get("asset_id") or "").strip()
        text = f"{label}: картинка {kind}".rstrip()
        if name:
            text += f" ({name})"
        if (override.get("picture") or {}).get("color"):
            text += ", перекраска"
        return text
    if op == "background":
        spec = override.get("background") or {}
        kind = str(spec.get("kind") or "")
        if kind == "solid":
            return f"фон {str(spec.get('color') or '').upper()}".rstrip()
        if kind == "image":
            source = spec.get("source") or {}
            return f"фон-картинка {SOURCE_LABELS.get(str(source.get('kind') or ''), '')}".rstrip()
        return "фон как в макете"
    return op


def describe_patch(patch: JsonDict, deck: JsonDict | None, plan: JsonDict) -> str:
    """Сводка одной строкой: «Слайд 3: заголовок: текст, кегль 28; слайд 5: сброс правок»."""
    positions = {str(s.get("slide_id")): i + 1 for i, s in enumerate(ordered_slides(plan))}
    objects = _deck_objects(deck) if deck else {}
    parts: list[str] = []
    for entry in patch.get("slides") or []:
        slide_id = str(entry.get("slide_id") or "")
        number = positions.get(slide_id)
        head = f"Слайд {number}" if number else f"Слайд {slide_id}"
        overrides = entry.get("overrides") or []
        if not overrides:
            parts.append(f"{head}: сброс правок")
            continue
        items = [
            _describe_override(
                o,
                objects.get(slide_id, {}).get(str((o.get("target") or {}).get("object_id") or "")),
            )
            for o in overrides
        ]
        parts.append(f"{head}: " + "; ".join(items))
    if patch.get("order") is not None:
        parts.append("порядок слайдов изменён")
    return " · ".join(parts) if parts else "правок нет"


# ---------- применение ----------


def apply_patch(
    plan: JsonDict,
    patch: JsonDict,
    deck: JsonDict | None,
    profile: JsonDict | None = None,
    package: JsonDict | None = None,
    story: JsonDict | None = None,
) -> PatchResult:
    """Новый план с заменёнными списками правок и порядком; проверки контракта как у
    правки из чата. Ошибки — `PatchError` с кодом (`patch_invalid`, `patch_empty`)."""
    normalized = normalize_patch(patch)
    try:
        SlidePatch.model_validate({**normalized, "schema_version": PATCH_SCHEMA_VERSION})
    except ValidationError as e:
        errors = [
            f"{'.'.join(str(x) for x in err.get('loc', ()))}: {err.get('msg')}"
            for err in e.errors()[:20]
        ]
        raise PatchError(
            "patch_invalid",
            "Документ правок не соответствует схеме: " + "; ".join(errors[:6]),
            details={"violations": errors},
        ) from e
    if not normalized.get("slides") and normalized.get("order") is None:
        raise PatchError("patch_empty", "в патче нет ни правок, ни нового порядка")
    violations = validate_patch(normalized, plan, deck, profile, package)
    if violations:
        raise PatchError(
            "patch_invalid",
            "Правки не применимы к этой ревизии: " + "; ".join(str(v) for v in violations[:6]),
            details={"violations": [str(v) for v in violations[:20]]},
        )
    doc = upgrade_plan_schema(copy.deepcopy(plan))
    slides_by_id = {str(s.get("slide_id")): s for s in doc.get("slides") or []}
    changed: list[str] = []
    summary = describe_patch(normalized, deck, plan)
    for entry in normalized.get("slides") or []:
        slide_id = str(entry["slide_id"])
        slide = slides_by_id[slide_id]
        before = slide.get("overrides") or []
        after = list(entry.get("overrides") or [])
        if before == after:
            continue
        if after:
            slide["overrides"] = after
        else:
            slide.pop("overrides", None)
        slide["revision_note"] = describe_patch({"slides": [entry]}, deck, plan)
        changed.append(slide_id)
    order = normalized.get("order")
    if order is not None:
        old_positions = {
            str(s.get("slide_id")): int(s.get("order", 0)) for s in doc.get("slides") or []
        }
        for position, slide_id in enumerate(order, start=1):
            slide = slides_by_id[slide_id]
            if int(slide.get("order", 0)) != position:
                slide["order"] = position
                if slide_id not in changed:
                    changed.append(slide_id)
        doc["slides"] = sorted(doc.get("slides") or [], key=lambda s: int(s.get("order", 0)))
        moved = [
            sid for sid in order if old_positions.get(sid) != int(slides_by_id[sid].get("order", 0))
        ]
        if moved and (doc.get("comparison") or {}).get("pattern_sequence"):
            comparison = dict(doc["comparison"])
            comparison["pattern_sequence"] = [str(s.get("pattern_id")) for s in ordered_slides(doc)]
            kinds = list(comparison.get("visual_kinds") or [])
            if len(kinds) == len(order):
                by_old = {
                    sid: kinds[old_positions[sid] - 1] for sid in order if old_positions.get(sid)
                }
                comparison["visual_kinds"] = [by_old.get(sid, "text") for sid in order]
            doc["comparison"] = comparison
    if not changed:
        raise PatchError("patch_empty", "правки совпадают с текущей ревизией: менять нечего")
    meta = dict(doc.get("generation_meta") or {"skills": [], "models": []})
    meta["created_at"] = now_iso()
    doc["generation_meta"] = meta
    warnings = _validate(doc, profile, package, story)
    return PatchResult(
        plan=doc,
        summary=summary,
        changed_slide_ids=changed,
        warnings=warnings,
        report={
            "patch_version": PATCH_VERSION,
            "slides": [str(e["slide_id"]) for e in normalized.get("slides") or []],
            "reordered": normalized.get("order") is not None,
            "changed_slide_ids": list(changed),
            "summary": summary,
        },
    )


def _validate(
    doc: JsonDict, profile: JsonDict | None, package: JsonDict | None, story: JsonDict | None
) -> list[JsonDict]:
    from presentation_designer.contracts import SlidePlan

    validated = SlidePlan.model_validate(doc)
    violations = check_slide_plan(
        validated,
        TemplateProfile.model_validate(profile) if profile else None,
        ContentPackage.model_validate(package) if package else None,
        StoryPlan.model_validate(story) if story else None,
    )
    original = str((doc.get("variant") or {}).get("variant_id")) == "original"
    blocking = [
        v
        for v in violations
        if v.code.startswith("override_") or v.code in ("asset_missing", "slide_order")
    ]
    if blocking:
        raise PatchError(
            "patch_invalid",
            "Правки нарушают контракт плана: " + "; ".join(str(v) for v in blocking[:6]),
            details={"violations": [str(v) for v in blocking[:20]]},
        )
    foreign_story = bool(story and package and story.get("package_id") != package.get("package_id"))
    rest = [
        v
        for v in violations
        if v not in blocking
        and not (v.code == "package_mismatch" and foreign_story)
        and not (original and v.code == "slot_required")
    ]
    return [{"code": v.code, "message": v.message} for v in rest]


def deck_model(deck: JsonDict) -> ComposedDeck:
    return ComposedDeck.model_validate(deck)


__all__ = [
    "PATCH_VERSION",
    "PatchError",
    "PatchResult",
    "apply_patch",
    "describe_patch",
    "normalize_patch",
    "patch_is_noop",
    "validate_patch",
]
