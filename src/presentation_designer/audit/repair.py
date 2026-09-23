"""Исправления по находкам аудита: правки к плану, которые код делает сам.

Правки выражены теми же `overrides`, что и ручная правка из редактора. Это не экономия кода
ради экономии: у колоды остаётся один способ измениться (план → вёрстка → экспорт → аудит), и
исправление аудита проверяется тем же путём, что и правка руками, а не своей веткой.

Чинится то, у чего есть однозначный правильный ответ: вылез за холст — вернуть в холст, кегль
не из шкалы — ближайшая ступень шкалы, цвет не из палитры — ближайший цвет палитры, знак
шаблона сдвинут — вернуть на место из профиля. Там, где правильных ответов много (переставить
композицию, переписать текст, разделить слайд), код не угадывает: находка остаётся, а в отчёте
написано, почему её не тронули.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.audit.deterministic import Context

JsonDict = dict[str, Any]

# Ниже этого кегля не опускаемся даже ради того, чтобы текст влез: нечитаемый слайд — не
# исправленный слайд.
MIN_SIZE_PT = 9.0


@dataclass
class Fix:
    """Что сделали с находкой: применили правку или оставили как есть и почему."""

    issue_id: str
    check_id: str
    applied: bool
    note: str = ""


@dataclass
class RepairPlan:
    """Патч к плану базовой ревизии и судьба каждой выбранной находки."""

    patch: JsonDict = field(default_factory=lambda: {"slides": []})
    fixes: list[Fix] = field(default_factory=list)

    @property
    def applied(self) -> list[str]:
        return [f.issue_id for f in self.fixes if f.applied]

    @property
    def changed_slide_ids(self) -> list[str]:
        return [str(s["slide_id"]) for s in self.patch.get("slides") or []]


def build_repair(
    report: JsonDict,
    issue_ids: list[str],
    deck: JsonDict,
    profile: JsonDict,
    plan: JsonDict | None = None,
    *,
    settings: JsonDict | None = None,
) -> RepairPlan:
    """Патч, исправляющий выбранные находки, и отчёт по каждой из них."""
    wanted = list(dict.fromkeys(issue_ids))
    issues = {str(i.get("issue_id")): i for i in report.get("issues") or []}
    ctx = Context(deck=deck, profile=profile)
    slides = {int(s.get("index", 0)): s for s in deck.get("slides") or []}
    # Ручные правки слайда сохраняются: список overrides заменяется целиком, и потерять их
    # значило бы откатить работу пользователя вместе с исправлением находки.
    existing = {
        str(s.get("slide_id")): list(s.get("overrides") or [])
        for s in ((plan or {}).get("slides") or [])
    }

    out = RepairPlan()
    added: dict[str, list[JsonDict]] = {}
    for issue_id in wanted:
        issue = issues.get(issue_id)
        if issue is None:
            out.fixes.append(Fix(issue_id, "", False, "находки нет в отчёте базовой ревизии"))
            continue
        check_id = str(issue.get("check_id"))
        slide = slides.get(int(issue.get("slide_index", -1)))
        if slide is None:
            out.fixes.append(Fix(issue_id, check_id, False, "слайд находки не найден в колоде"))
            continue
        obj = _object_of(issue, slide)
        fixer = FIXERS.get(check_id)
        if fixer is None:
            out.fixes.append(Fix(issue_id, check_id, False, _skip_note(check_id)))
            continue
        if obj is None:
            out.fixes.append(Fix(issue_id, check_id, False, "находка не указывает на объект"))
            continue
        override = fixer(issue, obj, slide, ctx)
        if (
            override
            and override.get("op") == "geometry"
            and (settings or {}).get("design_mode") == "template_only"
        ):
            out.fixes.append(
                Fix(
                    issue_id,
                    check_id,
                    False,
                    "По шаблону: расположение блоков сохранено. "
                    "Разделите слайд или сократите текст.",
                )
            )
            continue
        if override is None:
            out.fixes.append(Fix(issue_id, check_id, False, "нечего менять: значение уже верное"))
            continue
        slide_id = str(slide.get("slide_id"))
        added.setdefault(slide_id, []).append(override)
        out.fixes.append(Fix(issue_id, check_id, True))

    out.patch = {
        "slides": [
            {"slide_id": slide_id, "overrides": [*existing.get(slide_id, []), *overrides]}
            for slide_id, overrides in added.items()
        ]
    }
    return out


# ---------- правила исправления ----------


def _fix_out_of_bounds(
    issue: JsonDict, obj: JsonDict, slide: JsonDict, ctx: Context
) -> JsonDict | None:
    bbox = dict(obj.get("bbox") or {})
    fixed = _clamp(bbox, 0.0, 0.0, 1.0, 1.0)
    return _geometry(obj, fixed) if fixed != bbox else None


def _fix_in_margins(
    issue: JsonDict, obj: JsonDict, slide: JsonDict, ctx: Context
) -> JsonDict | None:
    margins = ctx.margins or {}
    left = float(margins.get("left") or 0)
    top = float(margins.get("top") or 0)
    right = 1.0 - float(margins.get("right") or 0)
    bottom = 1.0 - float(margins.get("bottom") or 0)
    if right <= left or bottom <= top:
        return None
    bbox = dict(obj.get("bbox") or {})
    fixed = _clamp(bbox, left, top, right, bottom)
    return _geometry(obj, fixed) if fixed != bbox else None


def _fix_fixed_element(
    issue: JsonDict, obj: JsonDict, slide: JsonDict, ctx: Context
) -> JsonDict | None:
    """Знак шаблона или колонтитул возвращается на место, записанное в профиле."""
    place = (issue.get("evidence") or {}).get("place")
    if not isinstance(place, dict) or not {"x", "y", "width", "height"} <= set(place):
        return None
    bbox = {k: round(float(place[k]), 4) for k in ("x", "y", "width", "height")}
    return _geometry(obj, bbox) if bbox != (obj.get("bbox") or {}) else None


def _fix_size_not_in_scale(
    issue: JsonDict, obj: JsonDict, slide: JsonDict, ctx: Context
) -> JsonDict | None:
    current = _font(obj).get("size_pt")
    allowed = _numbers((issue.get("evidence") or {}).get("threshold")) or ctx.sizes
    if current is None or not allowed:
        return None
    nearest = min(allowed, key=lambda s: abs(s - float(current)))
    return _style(obj, {"size_pt": round(float(nearest), 1)}) if nearest != current else None


def _fix_font_not_in_template(
    issue: JsonDict, obj: JsonDict, slide: JsonDict, ctx: Context
) -> JsonDict | None:
    family = _template_family(ctx, obj)
    if not family or family.strip().lower() == str(_font(obj).get("family") or "").strip().lower():
        return None
    return _style(obj, {"family": family})


def _fix_color_not_in_palette(
    issue: JsonDict, obj: JsonDict, slide: JsonDict, ctx: Context
) -> JsonDict | None:
    current = str(_font(obj).get("color") or "")
    if not current.startswith("#") or not ctx.palette:
        return None
    rgb = _rgb(current)
    nearest = min(ctx.palette, key=lambda c: _distance(rgb, c))
    hex_color = "#{:02X}{:02X}{:02X}".format(*nearest)
    return _style(obj, {"color": hex_color}) if hex_color.lower() != current.lower() else None


def _fix_text_overflow(
    issue: JsonDict, obj: JsonDict, slide: JsonDict, ctx: Context
) -> JsonDict | None:
    """Текст не помещается: ступень кегля вниз по шкале шаблона, но не в нечитаемое."""
    current = _font(obj).get("size_pt")
    if current is None:
        return None
    smaller = [s for s in ctx.sizes if s < float(current) and s >= MIN_SIZE_PT]
    if not smaller:
        return None
    return _style(obj, {"size_pt": round(max(smaller), 1)})


FIXERS: dict[str, Any] = {
    "layout.out_of_bounds": _fix_out_of_bounds,
    "layout.in_margins": _fix_in_margins,
    "template.fixed_element_moved": _fix_fixed_element,
    "template.size_not_in_scale": _fix_size_not_in_scale,
    "template.font_not_in_template": _fix_font_not_in_template,
    "template.color_not_in_palette": _fix_color_not_in_palette,
    "layout.text_overflow": _fix_text_overflow,
}

# Почему находку не трогаем. Молчаливый пропуск хуже невыполненного исправления: по отчёту
# должно быть видно, что код не смог, а не что он «починил».
SKIP_NOTES: dict[str, str] = {
    "layout.overlap": "развести блоки без перекладки композиции нельзя",
    "density.fill_ratio": "нужна другая композиция под объём содержания",
    "density.too_many_bullets": "нужно переписать или разделить содержание",
    "density.bullet_too_long": "нужно переписать текст моделью",
    "integrity.empty_slide": "нужно наполнить слайд или убрать его из колоды",
    "integrity.duplicate_slides": "нужно решить, какой из слайдов лишний",
    "integrity.placeholder_text": "нужно дописать содержание вместо образца",
}


def _skip_note(check_id: str) -> str:
    if check_id.startswith("content."):
        return "смысловая находка: исправляется переписыванием текста моделью"
    return SKIP_NOTES.get(check_id, "автоматическое исправление для этой проверки не сделано")


# ---------- вспомогательное ----------


def _object_of(issue: JsonDict, slide: JsonDict) -> JsonDict | None:
    ids = [str(x) for x in (issue.get("element_ids") or [])]
    if not ids:
        return None
    for obj in slide.get("objects") or []:
        if str(obj.get("object_id")) in ids:
            return dict(obj)
    return None


def _target(obj: JsonDict) -> JsonDict:
    target: JsonDict = {"object_id": str(obj.get("object_id"))}
    if obj.get("source_object_id"):
        target["source_object_id"] = str(obj["source_object_id"])
    if obj.get("slot_id"):
        target["slot_id"] = str(obj["slot_id"])
    return target


def _geometry(obj: JsonDict, bbox: JsonDict) -> JsonDict:
    return {"op": "geometry", "target": _target(obj), "geometry": {"bbox": bbox}}


def _style(obj: JsonDict, font: JsonDict) -> JsonDict:
    return {"op": "style", "target": _target(obj), "style": {"font": font}}


def _font(obj: JsonDict) -> JsonDict:
    return ((obj.get("text") or {}).get("computed_style") or {}).get("font") or {}


def _clamp(bbox: JsonDict, left: float, top: float, right: float, bottom: float) -> JsonDict:
    width = min(float(bbox.get("width") or 0), right - left)
    height = min(float(bbox.get("height") or 0), bottom - top)
    x = min(max(float(bbox.get("x") or 0), left), right - width)
    y = min(max(float(bbox.get("y") or 0), top), bottom - height)
    return {
        "x": round(x, 4),
        "y": round(y, 4),
        "width": round(width, 4),
        "height": round(height, 4),
    }


def _numbers(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    out: list[float] = []
    for item in value:
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            continue
    return out


def _template_family(ctx: Context, obj: JsonDict) -> str | None:
    typography = (ctx.profile.get("design_tokens") or {}).get("typography") or {}
    fonts = typography.get("fonts") or []
    families = [str(f.get("family")) for f in fonts if f.get("family")]
    if not families:
        return None
    # Заголовку — первая гарнитура шаблона (в профиле она идёт как основная), остальному —
    # последняя: в двухшрифтовых системах это текстовая пара к заголовочной.
    if str(obj.get("slot_kind") or "") == "title":
        return families[0]
    return families[-1]


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))


__all__ = ["Fix", "RepairPlan", "build_repair"]
