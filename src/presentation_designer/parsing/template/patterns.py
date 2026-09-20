"""Композиционные паттерны из образцов содержания: слоты, повторяющиеся группы, роль, ёмкость.

Каждый образец даёт паттерн: текстовые объекты становятся слотами с видом (заголовок, тезисы,
показатель, имя…), картинки — слотами изображений или иконок либо статикой, таблицы и диаграммы —
слотами своего вида. Объекты одинакового размера, стоящие в ряд, объединяются в повторяющуюся
группу (карточки): их можно клонировать или убирать при меньшем числе элементов. Роль паттерна
сначала определяется эвристиками по составу слотов, тексту и имени макета; VLM уточняет роль по
миниатюрам сгруппированных образцов. Ёмкость текстовых слотов считается по метрикам шрифта
через `shared/text_metrics.py`, запас записывается в `measured_with`.
"""

from __future__ import annotations

import collections
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.parsing.template.assets import Asset
from presentation_designer.parsing.template.classify import Classification, is_marker
from presentation_designer.parsing.template.drawn_charts import find_drawn_chart
from presentation_designer.parsing.template.geometry import ShapeInfo, normalize_text
from presentation_designer.parsing.template.layouts import layout_family
from presentation_designer.parsing.template.package import SlideInfo, TemplatePackage
from presentation_designer.parsing.template.styles import ResolvedText, StyleResolver
from presentation_designer.parsing.template.tokens import text_role_for
from presentation_designer.parsing.template.tone import UNKNOWN, Tone, slide_tone
from presentation_designer.shared import text_metrics

log = logging.getLogger(__name__)

ROLES = (
    "title",
    "agenda",
    "section_divider",
    "bullets",
    "text",
    "two_column",
    "cards",
    "kpi",
    "numbers",
    "table",
    "chart",
    "timeline",
    "process",
    "comparison",
    "quote",
    "team",
    "speaker",
    "screenshot",
    "mockup",
    "pricing",
    "code",
    "image_full",
    "qr",
    "thanks",
    "freeform",
)
SLOT_KINDS_SUPPORT = {
    "image": "image",
    "icon": "icon",
    "table": "table",
    "chart": "chart",
    "diagram": "diagram",
    "number": "number",
    "qr": "qr",
}
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_NUMBERISH = re.compile(r"^[\s\d.,%+\-–x×хХXк$₽€млнмлрдтыс.]+$", re.IGNORECASE)


@dataclass
class Slot:
    slot_id: str
    kind: str
    shape: ShapeInfo
    style: ResolvedText | None = None
    repeat_group: str | None = None
    required: bool = False
    paragraphs: int = 1
    capacity: dict[str, Any] | None = None
    # Слот шире своего объекта: у нарисованной диаграммы это вся область ряда, а `shape` —
    # только один столбик, с которого вёрстка начинает замену.
    bbox_override: dict[str, float] | None = None

    def as_dict(self, slide_w: int, slide_h: int) -> dict[str, Any]:
        s = self.shape
        out: dict[str, Any] = {
            "slot_id": self.slot_id,
            "kind": self.kind,
            "bbox": self.bbox_override or s.bbox,
            "z_order": s.z_order,
            "required": self.required,
            "element_ref": s.element_id,
        }
        if s.group_path:
            out["group_path"] = list(s.group_path)
        if abs(s.rotation_deg) > 0.01:
            out["rotation_deg"] = round(s.rotation_deg, 2)
        if self.repeat_group:
            out["repeat_group"] = self.repeat_group
        if s.text:
            out["sample_text"] = s.text[:200]
        if self.style is not None:
            out["font"] = self.style.font_spec()
            out["computed_style"] = self.style.computed_style()
            align = next((p.align for p in s.paragraphs if p.align), None)
            if align:
                out["align"] = {"l": "left", "ctr": "center", "r": "right", "just": "justify"}.get(
                    align, "left"
                )
            if s.anchor:
                out["valign"] = {"t": "top", "ctr": "middle", "b": "bottom"}.get(s.anchor, "top")
            params: dict[str, Any] = {}
            if self.style.line_spacing:
                params["line_spacing"] = round(self.style.line_spacing, 2)
            if self.style.space_before_pt is not None:
                params["space_before_pt"] = self.style.space_before_pt
            if self.style.space_after_pt is not None:
                params["space_after_pt"] = self.style.space_after_pt
            if self.style.indent_emu is not None:
                params["indent_emu"] = self.style.indent_emu
            if s.insets_emu:
                left, top, right, bottom = s.insets_emu
                params["insets"] = {
                    "left": round(left / slide_w, 4),
                    "top": round(top / slide_h, 4),
                    "right": round(right / slide_w, 4),
                    "bottom": round(bottom / slide_h, 4),
                }
            if s.autofit:
                params["autofit"] = s.autofit
            if params:
                out["paragraph_params"] = params
        if self.capacity:
            out["capacity"] = self.capacity
        if s.crop and any(c > 0 for c in s.crop):
            out["crop"] = {
                "left": s.crop[0],
                "top": s.crop[1],
                "right": s.crop[2],
                "bottom": s.crop[3],
            }
        return out


@dataclass
class Pattern:
    pattern_id: str
    slide: SlideInfo
    role: str
    role_source: str
    confidence: float
    name: str
    slots: list[Slot]
    static_ids: list[str]
    removable_ids: list[str]
    repeat_counts: dict[str, int]
    # Части нарисованной диаграммы образца: убираются, когда на их месте построена нативная.
    chart_parts: list[str] = field(default_factory=list)
    notes: str = ""
    group_id: str = ""
    tags: list[str] = field(default_factory=list)
    preview_path: str | None = None
    layout_kind: str = "sample_slide"
    tone: Tone = field(default_factory=lambda: UNKNOWN)
    layout_name: str = ""

    def signature(self) -> tuple[Any, ...]:
        kinds = tuple(sorted(collections.Counter(s.kind for s in self.slots).items()))
        return (self.role, kinds, tuple(sorted(self.repeat_counts.values())))

    @property
    def style_key(self) -> str:
        """Стиль служебного слайда: тон | семейство макета | photo или plain. Образцы одного
        стиля взаимозаменяемы внутри колоды; `photo` появится у образцов с фотослотом макета
        (этап 16), пока все образцы `plain`."""
        return f"{self.tone.background}|{layout_family(self.layout_name)}|plain"

    def as_dict(self, pkg: TemplatePackage) -> dict[str, Any]:
        supports = sorted(
            {SLOT_KINDS_SUPPORT[s.kind] for s in self.slots if s.kind in SLOT_KINDS_SUPPORT}
        )
        max_items = max(self.repeat_counts.values(), default=0)
        constraints: dict[str, Any] = {"supports": supports}
        if max_items:
            constraints["min_items"] = 1
            constraints["max_items"] = max_items
        out: dict[str, Any] = {
            "pattern_id": self.pattern_id,
            "name": self.name,
            "role": self.role,
            "source": {
                "kind": self.layout_kind,
                "slide_index": self.slide.index,
                "layout_id": self.slide.layout_id or "layout",
                "pptx_slide_part": self.slide.part,
            },
            "slots": [s.as_dict(pkg.width_emu, pkg.height_emu) for s in self.slots],
            "constraints": constraints,
            "tags": self.tags,
            "confidence": round(self.confidence, 2),
            "role_source": self.role_source,
            "static_object_ids": self.static_ids,
            "removable_object_ids": self.removable_ids,
            **({"chart_parts": self.chart_parts} if self.chart_parts else {}),
            "sequence_hints": sequence_hints(self.role),
            "tone": self.tone.as_dict(),
            "style_key": self.style_key,
        }
        if self.group_id:
            out["group_id"] = self.group_id
        if self.preview_path:
            out["preview_path"] = self.preview_path
        if self.notes:
            out["notes"] = self.notes[:300]
        return out


def sequence_hints(role: str) -> dict[str, Any]:
    position = {
        "title": "first",
        "agenda": "early",
        "thanks": "last",
        "qr": "last",
        "section_divider": "middle",
        "speaker": "early",
        "team": "early",
    }.get(role, "any")
    max_consecutive = (
        1
        if role in ("title", "thanks", "section_divider", "quote", "agenda", "qr")
        else 2
        if role in ("kpi", "numbers", "image_full")
        else 3
    )
    return {"typical_position": position, "max_consecutive": max_consecutive}


# ---------- слоты ----------


def _slot_kind(
    shape: ShapeInfo, style: ResolvedText | None, slide_h_pt: float, *, largest_title: bool
) -> str:
    text = normalize_text(shape.text)
    if shape.placeholder_type in ("title", "ctrTitle"):
        return "title"
    if shape.placeholder_type == "subTitle":
        return "subtitle"
    if shape.placeholder_type == "pic":
        return "image"
    if shape.placeholder_type == "tbl":
        return "table"
    if shape.placeholder_type == "chart":
        return "chart"
    if shape.placeholder_type in ("dt",):
        return "date"
    if shape.placeholder_type == "ftr":
        return "footer"
    if "qr" in text and len(text) <= 30:
        return "qr"
    if any(m in text for m in ("имя фамилия", "имя спикера", "фио")) and len(text) <= 60:
        return "name"
    if (
        text.startswith("должность")
        or text == "роль в команде"
        or ("должность" in text and len(text) <= 40)
    ):
        return "position"
    if (
        any(m in text for m in ("вставить фото", "иллюстрац", "фото", "изображение"))
        and len(text) <= 40
    ):
        return "image"
    if text in ("дата",) or _YEAR.fullmatch(text or "") is not None:
        return "date"
    if (
        style is not None
        and text
        and (_NUMBERISH.match(text) or text in ("показатель", "ххх%", "хх%", "x%", "ххх"))
        and len(text) <= 24
    ):
        return "number"
    if any(tok in shape.text for tok in ("{", "};", "padding:", "def ", "import ", "</")):
        return "code"
    paragraphs = [p for p in shape.paragraphs if p.text.strip()]
    bulleted = sum(
        1
        for p in paragraphs
        if p.bullet in ("char", "number", "picture") or normalize_text(p.text).startswith("пункт")
    )
    if len(paragraphs) >= 2 and bulleted >= max(2, len(paragraphs) // 2):
        return "bullets"
    if (
        len(paragraphs) >= 3
        and all(len(p.text) <= 60 for p in paragraphs)
        and text.startswith("пункт")
    ):
        return "bullets"
    role = text_role_for(shape, style, slide_h_pt) if style else "body"
    if role in ("display",) or (role == "title" and largest_title):
        return "title"
    if role == "title":
        return "subtitle" if shape.y < 0.4 else "label"
    if role == "subtitle":
        return "subtitle" if shape.y < 0.35 and shape.width > 0.4 else "label"
    if role == "caption":
        return "caption"
    if role == "kpi":
        return "number"
    if text in ("подпись", "заметка"):
        return "caption"
    return "body"


def _single_title(slots: list[Slot]) -> None:
    """В паттерне один заголовок: планировщик и вёрстка рассчитаны ровно на один слот kind
    «title», и только он обязателен. Остальные крупные надписи — подписи карточек: у
    шаблонов вне партнёрских встречаются номера шагов и заголовки карточек тем же кеглем,
    что и заголовок слайда (или крупнее). Остаётся плейсхолдер заголовка, а без него —
    самая верхняя и широкая надпись."""
    titles = [sl for sl in slots if sl.kind == "title"]
    if len(titles) <= 1:
        return
    keep = next(
        (sl for sl in titles if sl.shape.placeholder_type in ("title", "ctrTitle")),
        None,
    )
    if keep is None:
        keep = min(titles, key=lambda sl: (round(sl.shape.y, 2), -sl.shape.width))
    for sl in titles:
        if sl is not keep:
            sl.kind = "label"


def _capacity(slot: Slot, pkg: TemplatePackage, margin_ratio: float) -> dict[str, Any] | None:
    style, shape = slot.style, slot.shape
    if style is None or shape.width_emu <= 0 or shape.height_emu <= 0:
        return None
    font = text_metrics.resolve_font(style.family, bold=style.bold, italic=style.italic)
    paragraphs = max(1, len([p for p in shape.paragraphs if p.text.strip()]))
    cap = text_metrics.capacity(
        width_emu=shape.width_emu,
        height_emu=shape.height_emu,
        size_pt=style.size_pt,
        font=font,
        line_spacing=style.line_spacing if style.line_spacing and style.line_spacing > 0 else 1.0,
        space_before_pt=style.space_before_pt or 0.0,
        space_after_pt=style.space_after_pt or 0.0,
        insets_emu=shape.insets_emu or (91440, 45720, 91440, 45720),
        bullet_indent_emu=abs(style.indent_emu or 0) if slot.kind == "bullets" else 0,
        margin_ratio=margin_ratio,
        paragraphs=paragraphs,
    )
    # Текст образца в рамке помещается по построению (у рамок с autofit resize_shape высота уже
    # равна тексту): ёмкость не меньше длины образца, минимум одна строка.
    sample_len = len(shape.text.strip())
    max_lines = max(
        cap.max_lines, 1, shape.text.count("\n") + 1 if shape.autofit == "resize_shape" else 1
    )
    max_chars = max(cap.max_chars, sample_len, cap.chars_per_line if max_lines >= 1 else 0)
    if shape.autofit == "resize_shape":
        # Рамка растёт по высоте: ограничение задаёт ширина, число строк — по образцу с запасом.
        max_lines = max(max_lines, shape.text.count("\n") + 1)
        max_chars = max(max_chars, cap.chars_per_line * max_lines)
    out: dict[str, Any] = {
        "max_chars": int(max_chars),
        "max_lines": int(max_lines),
        "measured_with": {
            "method": cap.method,
            "margin_ratio": cap.margin_ratio,
            **({"font_file": cap.font_file} if cap.font_file else {}),
        },
        "confidence": 0.8
        if cap.method == "font_metrics" and not cap.substituted
        else 0.6
        if cap.method == "font_metrics"
        else 0.4,
    }
    if sample_len > cap.max_chars and cap.max_chars:
        out["confidence"] = round(out["confidence"] - 0.2, 2)
    if slot.kind == "bullets":
        out["max_items"] = max(1, min(max_lines, 8))
        out["max_words_per_item"] = max(2, cap.chars_per_line // 7)
    return out


def _repeat_groups(shapes: list[ShapeInfo]) -> dict[str, str]:
    """Объекты одинакового размера и вида, стоящие в ряд/колонку, — одна группа; группы с равным
    числом членов, соседствующие покомпонентно, объединяются в карточки."""
    clusters: dict[tuple[str, int, int], list[ShapeInfo]] = collections.defaultdict(list)
    for s in shapes:
        if s.area < 0.0005:
            continue
        clusters[(s.kind, round(s.width * 60), round(s.height * 60))].append(s)
    groups: list[list[ShapeInfo]] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: (round(m.y, 2), m.x))
        rows = collections.Counter(round(m.y, 2) for m in members)
        cols = collections.Counter(round(m.x, 2) for m in members)
        aligned = max(rows.values()) >= 2 or max(cols.values()) >= 2
        if aligned:
            groups.append(members)
    # Группы с равным числом членов, стоящие рядом, — одна карточка (иконка + заголовок + текст).
    merged: list[list[list[ShapeInfo]]] = []
    for g in groups:
        placed = False
        for bundle in merged:
            ref = bundle[0]
            if len(ref) == len(g) and _componentwise_close(ref, g):
                bundle.append(g)
                placed = True
                break
        if not placed:
            merged.append([g])
    assignment: dict[str, str] = {}
    for i, bundle in enumerate(merged, start=1):
        for g in bundle:
            for s in g:
                assignment[s.element_id] = f"g{i}"
    return assignment


def _componentwise_close(a: list[ShapeInfo], b: list[ShapeInfo], tol: float = 0.2) -> bool:
    b_sorted = sorted(b, key=lambda m: (round(m.y, 1), m.x))
    a_sorted = sorted(a, key=lambda m: (round(m.y, 1), m.x))
    for x, y in zip(a_sorted, b_sorted, strict=False):
        if abs(x.center[0] - y.center[0]) > tol or abs(x.center[1] - y.center[1]) > tol:
            return False
    return True


def build_pattern(
    slide: SlideInfo,
    pkg: TemplatePackage,
    resolver: StyleResolver,
    cls: Classification,
    assets_by_sha: dict[str, Asset],
    fixed_refs: set[tuple[str, str]],
    *,
    margin_ratio: float = 0.92,
) -> Pattern | None:
    slide_h_pt = pkg.height_emu / text_metrics.EMU_PER_PT
    shapes = [s for s in slide.shapes if s.kind != "group"]
    layout = pkg.layout(slide.layout_id)
    # Слайд без собственных объектов содержания берёт слоты из плейсхолдеров макета.
    layout_kind = "sample_slide"
    own_content = [
        s
        for s in shapes
        if s.text
        or s.kind in ("picture", "table", "chart", "smartart")
        or (s.is_placeholder and s.placeholder_type not in ("sldNum", "dt", "ftr"))
    ]
    if not own_content and layout is not None:
        shapes = [
            p for p in layout.placeholders if p.placeholder_type not in ("sldNum", "dt", "ftr")
        ]
        layout_kind = "layout"
        if not shapes:
            return None
    styles: dict[str, ResolvedText] = {}
    for s in shapes:
        if s.has_text_frame and s.paragraphs:
            para = next((p for p in s.paragraphs if p.text.strip()), s.paragraphs[0])
            run = next((r for r in para.runs if r.get("text", "").strip()), None)
            styles[s.element_id] = resolver.resolve(s, para, run)
        elif s.is_placeholder and s.placeholder_type in (
            "title",
            "ctrTitle",
            "subTitle",
            "body",
            "obj",
        ):
            from presentation_designer.parsing.template.geometry import Paragraph

            styles[s.element_id] = resolver.resolve(s, Paragraph(""), None)
    text_shapes = [s for s in shapes if s.element_id in styles]
    largest = max((styles[s.element_id].size_pt for s in text_shapes), default=0.0)

    slots: list[Slot] = []
    static_ids: list[str] = []
    candidates: list[ShapeInfo] = []
    for s in shapes:
        if (slide.part, s.element_id) in fixed_refs or s.placeholder_type in (
            "sldNum",
            "dt",
            "ftr",
        ):
            static_ids.append(s.element_id)
            continue
        style = styles.get(s.element_id)
        if (
            s.kind == "text"
            or (s.has_text_frame and s.text)
            or (s.is_placeholder and style is not None)
        ):
            if not s.text and not s.is_placeholder:
                static_ids.append(s.element_id)
                continue
            kind = _slot_kind(
                s,
                style,
                slide_h_pt,
                largest_title=style is not None and abs(style.size_pt - largest) < 0.5,
            )
            slots.append(
                Slot(
                    "",
                    kind,
                    s,
                    style,
                    paragraphs=max(1, len([p for p in s.paragraphs if p.text.strip()])),
                )
            )
            candidates.append(s)
        elif s.kind == "picture":
            asset = assets_by_sha.get(s.media_sha256 or "")
            akind = asset.kind if asset else "image"
            if akind in ("icon",) or (
                akind == "logo" and s.area <= 0.012 and cls.kind == "content_sample" and s.y > 0.12
            ):
                slots.append(Slot("", "icon", s))
                candidates.append(s)
            elif (
                akind in ("photo", "image", "screenshot", "mockup", "chart_image")
                and s.area >= 0.02
            ):
                slots.append(Slot("", "image", s))
                candidates.append(s)
            else:
                static_ids.append(s.element_id)
        elif s.kind == "table":
            slots.append(Slot("", "table", s))
            candidates.append(s)
        elif s.kind == "chart":
            slots.append(Slot("", "chart", s))
            candidates.append(s)
        elif s.kind == "smartart":
            slots.append(Slot("", "diagram", s))
            candidates.append(s)
        else:
            static_ids.append(s.element_id)
            candidates.append(s)
    if not slots:
        return None

    groups = _repeat_groups(candidates)
    removable: list[str] = []
    seen_first: set[str] = set()
    for s in sorted(candidates, key=lambda m: (round(m.y, 2), m.x)):
        g = groups.get(s.element_id)
        if not g:
            continue
        if g in seen_first:
            removable.append(s.element_id)
        else:
            seen_first.add(g)
    for slot in slots:
        slot.repeat_group = groups.get(slot.shape.element_id)
    repeat_counts: dict[str, int] = collections.Counter(
        groups[s.element_id] for s in candidates if s.element_id in groups
    )
    # Число элементов группы — число карточек, а не объектов: делим на объектов в карточке.
    per_card: dict[str, int] = {}
    for g in set(groups.values()):
        members = [s for s in candidates if groups.get(s.element_id) == g]
        sizes = collections.Counter(
            (s.kind, round(s.width * 60), round(s.height * 60)) for s in members
        )
        per_card[g] = max(sizes.values()) if sizes else len(members)
    repeat_counts = {g: per_card.get(g, n) for g, n in repeat_counts.items()}

    # Нарисованная диаграмма: ряд столбиков вместо нативного графика. Его части перестают
    # быть слотами (иначе план запишет числа в картинки), а на их месте появляется один слот
    # chart на всю область ряда — вёрстка построит там настоящую диаграмму.
    own = [s for s in shapes if (slide.part, s.element_id) not in fixed_refs]
    chart_parts = _attach_chart_slot(own, slots, static_ids, removable, candidates)

    _single_title(slots)
    slots.sort(key=lambda sl: (round(sl.shape.y, 2), sl.shape.x))
    counters: collections.Counter[str] = collections.Counter()
    for slot in slots:
        counters[slot.kind] += 1
        slot.slot_id = f"{slot.kind}_{counters[slot.kind]}"
        slot.required = slot.kind in ("title",) or (
            slot.repeat_group is None and slot.kind in ("body", "bullets", "table", "chart")
        )
        if slot.style is not None and slot.kind not in (
            "image",
            "icon",
            "table",
            "chart",
            "diagram",
            "qr",
        ):
            slot.capacity = _capacity(slot, pkg, margin_ratio)

    role, source, conf, notes = heuristic_role(
        slide, slots, repeat_counts, layout.name if layout else ""
    )
    name = _pattern_name(slide, layout.name if layout else "", role, slots, repeat_counts)
    return Pattern(
        pattern_id=f"pat_s{slide.index}",
        slide=slide,
        role=role,
        role_source=source,
        confidence=conf,
        name=name,
        slots=slots,
        static_ids=static_ids,
        removable_ids=removable,
        chart_parts=chart_parts,
        repeat_counts=repeat_counts,
        notes=notes,
        layout_kind=layout_kind,
        tone=slide_tone(slide, layout, pkg.master(slide.master_id), assets_by_sha, pkg=pkg),
        layout_name=layout.name if layout else "",
    )




def _attach_chart_slot(
    shapes: list[ShapeInfo],
    slots: list[Slot],
    static_ids: list[str],
    removable: list[str],
    candidates: list[ShapeInfo],
    *,
    relaxed: bool = False,
) -> list[str]:
    """Заменяет ряд нарисованной диаграммы одним слотом `chart` на всю его область.

    Списки правятся на месте (они собираются по ходу разбора образца). Возвращает части ряда,
    которые вёрстка уберёт, построив настоящую диаграмму; пустой список — ряда нет.
    """
    if any(sl.kind == "chart" for sl in slots):
        return []
    drawn = find_drawn_chart(shapes, relaxed=relaxed)
    if drawn is None:
        return []
    anchor = max(
        (s for s in shapes if s.element_id in drawn.parts), key=lambda s: s.area, default=None
    )
    if anchor is None:
        return []
    slots[:] = [sl for sl in slots if sl.shape.element_id not in drawn.element_ids]
    static_ids[:] = [i for i in static_ids if i not in drawn.element_ids]
    removable[:] = [i for i in removable if i not in drawn.element_ids]
    candidates[:] = [c for c in candidates if c.element_id not in drawn.element_ids]
    slots.append(Slot("chart_1", "chart", anchor, bbox_override=drawn.bbox, required=True))
    return [i for i in sorted(drawn.element_ids) if i != anchor.element_id]


def attach_drawn_chart(pattern: Pattern) -> bool:
    """Подсказка зрения: на образце график. Код ищет, из чего он собран, мягкими порогами.

    Вопрос «график ли это» к этому моменту уже решён моделью, поэтому пороги ослаблены; если
    ряда всё равно нет, роль chart не подтверждается и остаётся эвристика.
    """
    if pattern.chart_parts:
        return True
    shapes = [s for s in pattern.slide.shapes if s.kind != "group"]
    parts = _attach_chart_slot(
        shapes,
        pattern.slots,
        pattern.static_ids,
        pattern.removable_ids,
        [],
        relaxed=True,
    )
    if not parts:
        return False
    pattern.chart_parts = parts
    return True



# ---------- роль ----------


def heuristic_role(
    slide: SlideInfo, slots: list[Slot], repeat_counts: dict[str, int], layout_name: str
) -> tuple[str, str, float, str]:
    kinds = collections.Counter(s.kind for s in slots)
    text = normalize_text(slide.all_text)
    lname = normalize_text(layout_name)
    max_repeat = max(repeat_counts.values(), default=0)
    titles = kinds.get("title", 0)

    def by_layout(*words: str) -> bool:
        return any(w in lname for w in words)

    if (
        "спасибо" in text
        or by_layout("спасибо", "финальн", "thanks")
        or (kinds.get("qr", 0) and len(slots) <= 4 and titles)
    ):
        if kinds.get("qr", 0) and "спасибо" not in text and not by_layout("спасибо", "финальн"):
            return "qr", "heuristic", 0.7, ""
        return "thanks", "heuristic", 0.85, ""
    if text.startswith("q&a") or text == "q&a":
        return "thanks", "heuristic", 0.7, "вопросы и ответы"
    if any(w in text for w in ("содержание", "оглавление")) or by_layout(
        "содержание", "оглавление", "agenda"
    ):
        return (
            "agenda",
            "layout_name" if by_layout("содержание", "оглавление") else "heuristic",
            0.8,
            "",
        )
    if by_layout("разделител", "section") or ("разделител" in text and len(slots) <= 4):
        return "section_divider", "layout_name", 0.85, ""
    if kinds.get("code", 0):
        return "code", "heuristic", 0.85, ""
    if kinds.get("table", 0):
        return "table", "heuristic", 0.85, ""
    if kinds.get("chart", 0) or ("график" in text and "пример" in text):
        return "chart", "heuristic", 0.8, ""
    if kinds.get("diagram", 0):
        return "process", "heuristic", 0.6, "SmartArt в образце"
    if (
        "цитат" in text
        or by_layout("цитат", "quote")
        or (kinds.get("body", 0) == 1 and kinds.get("name", 0) and not titles)
    ):
        return "quote", "heuristic", 0.7, ""
    if (
        kinds.get("name", 0) >= 3
        or (kinds.get("name", 0) >= 2 and kinds.get("position", 0) >= 2)
        or by_layout("команд", "team")
    ):
        return "team", "heuristic", 0.8, ""
    if (
        kinds.get("name", 0) >= 1
        and kinds.get("position", 0) >= 1
        and (kinds.get("image", 0) or by_layout("спикер", "визитк"))
    ):
        return "speaker", "heuristic", 0.75, ""
    if (by_layout("титульн", "title") and slide.index <= 3) or (
        titles and len(slots) <= 3 and kinds.get("subtitle", 0) and slide.index <= 2
    ):
        return "title", "layout_name", 0.8, ""
    if kinds.get("number", 0) >= 2:
        return ("kpi" if kinds.get("number", 0) <= 4 else "numbers"), "heuristic", 0.75, ""
    if (
        "таймлайн" in text
        or _YEAR.findall(slide.all_text).__len__() >= 3
        or by_layout("таймлайн", "timeline")
    ):
        return "timeline", "heuristic", 0.75, ""
    if by_layout("стади", "процесс", "этап", "process", "steps") or kinds.get("date", 0) >= 3:
        return "process", "layout_name", 0.7, ""
    if by_layout("скриншот", "screenshot", "демо"):
        return "screenshot", "layout_name", 0.7, ""
    if by_layout("мокап", "mockup", "телефон"):
        return "mockup", "layout_name", 0.75, ""
    if (
        kinds.get("image", 0) >= 1
        and sum(kinds.values()) <= 2
        and any(s.shape.area >= 0.4 for s in slots if s.kind == "image")
    ):
        return "image_full", "heuristic", 0.7, ""
    if (
        any(
            w in text
            for w in ("проблем", "vs", "против", "сравнен", "до и после", "цели:", "решения:")
        )
        and max_repeat == 2
    ):
        return "comparison", "heuristic", 0.65, ""
    if (
        max_repeat >= 3
        and (
            kinds.get("label", 0)
            + kinds.get("subtitle", 0)
            + kinds.get("body", 0)
            + kinds.get("caption", 0)
        )
        >= 3
    ):
        return "cards", "heuristic", 0.7, ""
    if max_repeat == 2 and kinds.get("body", 0) + kinds.get("bullets", 0) >= 2:
        return "two_column", "heuristic", 0.65, ""
    if kinds.get("bullets", 0):
        return "bullets", "heuristic", 0.7, ""
    if kinds.get("body", 0) >= 1 and titles:
        return "text", "heuristic", 0.6, ""
    if kinds.get("icon", 0) >= 2:
        return "cards", "heuristic", 0.5, "иконки без подписей"
    return "freeform", "heuristic", 0.4, ""


def _pattern_name(
    slide: SlideInfo, layout_name: str, role: str, slots: list[Slot], repeat_counts: dict[str, int]
) -> str:
    human = {
        "title": "Титульный",
        "agenda": "Содержание",
        "section_divider": "Разделитель",
        "bullets": "Список",
        "text": "Текст",
        "two_column": "Две колонки",
        "cards": "Карточки",
        "kpi": "Показатели",
        "numbers": "Цифры",
        "table": "Таблица",
        "chart": "График",
        "timeline": "Таймлайн",
        "process": "Процесс",
        "comparison": "Сравнение",
        "quote": "Цитата",
        "team": "Команда",
        "speaker": "Спикер",
        "screenshot": "Скриншот",
        "mockup": "Мокап",
        "pricing": "Тарифы",
        "code": "Код",
        "image_full": "Изображение",
        "qr": "QR",
        "thanks": "Финальный",
        "freeform": "Свободная композиция",
    }.get(role, role)
    base = re.sub(r"^\d+_", "", layout_name or "").strip()
    max_repeat = max(repeat_counts.values(), default=0)
    detail = (
        f" × {max_repeat}"
        if max_repeat >= 2
        and role
        in ("cards", "kpi", "numbers", "team", "process", "timeline", "two_column", "comparison")
        else ""
    )
    if base and normalize_text(base) not in (
        "свободный дизайн",
        "заголовок",
        "титульный слайд",
        "контент",
        "default",
        "пустой с заголовком",
        "заголовок и объект",
    ):
        return f"{human} · {base}{detail}"[:80]
    return f"{human}{detail} · слайд {slide.index}"


# ---------- группировка похожих образцов и уточнение VLM ----------


def group_patterns(patterns: list[Pattern]) -> dict[str, list[Pattern]]:
    groups: dict[tuple[Any, ...], list[Pattern]] = collections.defaultdict(list)
    for p in patterns:
        groups[p.signature()].append(p)
    out: dict[str, list[Pattern]] = {}
    for i, members in enumerate(sorted(groups.values(), key=lambda m: m[0].slide.index), start=1):
        gid = f"grp_{i}"
        for m in members:
            m.group_id = gid
        out[gid] = members
    return out


VLM_ROLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "role": {"type": "string", "enum": list(ROLES)},
                    "confidence": {"type": "number"},
                    "name": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["n", "role", "confidence"],
            },
        }
    },
    "required": ["items"],
}


def refine_roles_with_vlm(
    groups: dict[str, list[Pattern]],
    previews: dict[int, bytes],
    client: Any,
    skill: Any,
    *,
    batch: int = 6,
    deadline_s: float = 90.0,
    min_confidence: float = 0.7,
    deadline: Any = None,
) -> dict[str, Any]:
    """Роль по миниатюрам представителей групп: до `batch` картинок в одном запросе, запросы
    идут параллельно (одновременность ограничивает лимитер клиента). Роль VLM принимается,
    если уверенность не ниже порога и она не противоречит структуре образца (table без
    таблицы, chart без диаграммы или её картинки). Возвращает сводку."""
    import asyncio

    from presentation_designer.llm.types import Deadline, Image, LlmError, Message

    summary: dict[str, Any] = {
        "groups": len(groups),
        "asked": 0,
        "changed": 0,
        "rejected": 0,
        # Образцы, где график нарисован фигурами: зрение сказало «chart», код нашёл ряд.
        "drawn_charts": 0,
        "errors": [],
    }
    if client is None or skill is None:
        return summary
    if deadline is not None and deadline.remaining() < 5:
        summary["errors"].append("бюджет времени VLM исчерпан до запроса ролей")
        return summary
    reps = [members[0] for members in groups.values() if members[0].slide.index in previews]
    chunks = [reps[i : i + batch] for i in range(0, len(reps), batch)]

    def make_request(chunk: list[Pattern]) -> Any:
        lines = []
        for i, p in enumerate(chunk, start=1):
            kinds = ", ".join(
                f"{k}×{n}" for k, n in collections.Counter(s.kind for s in p.slots).items()
            )
            lines.append(
                f"{i}. макет «{p.slide.layout_id}», слоты: {kinds}; эвристика: {p.role} "
                f"({p.confidence:.2f}); текст: {p.slide.all_text[:120]!r}"
            )
        req = skill.request(
            "analyze.classify_samples",
            "Изображения пронумерованы в порядке передачи. Структура каждого образца:\n"
            + "\n".join(lines),
            schema=VLM_ROLE_SCHEMA,
            stage="analyze",
        )
        images = tuple(Image(previews[p.slide.index], "image/png") for p in chunk)
        req.messages[1] = Message("user", req.messages[1].text, images)
        req.deadline = deadline or Deadline.after(deadline_s)
        req.schema_name = "sample_roles"
        return req

    async def run_all() -> list[Any]:
        return await asyncio.gather(
            *(client.complete(make_request(chunk)) for chunk in chunks), return_exceptions=True
        )

    outcomes = asyncio.run(run_all())
    for chunk, outcome in zip(chunks, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            summary["errors"].append(f"{type(outcome).__name__}: {outcome}"[:200])
            if not isinstance(outcome, LlmError):
                log.exception("уточнение ролей: неожиданная ошибка", exc_info=outcome)
            continue
        items = outcome.parsed.get("items", []) if isinstance(outcome.parsed, dict) else []
        summary["asked"] += len(chunk)
        for item in items:
            try:
                idx = int(item.get("n", 0)) - 1
                conf = float(item.get("confidence", 0))
            except (TypeError, ValueError):
                continue
            if not 0 <= idx < len(chunk):
                continue
            rep = chunk[idx]
            role = str(item.get("role", ""))
            if role not in ROLES or conf < min_confidence:
                continue
            if not role_matches_structure(role, rep):
                # Зрение видит график там, где код нашёл только ряд картинок: пороги поиска
                # ослабляются и роль принимается, только если ряд действительно нашёлся.
                attached = [m for m in groups[rep.group_id] if attach_drawn_chart(m)]
                if role != "chart" or rep not in attached:
                    summary["rejected"] += 1
                    continue
                summary["drawn_charts"] = summary.get("drawn_charts", 0) + len(attached)
            for member in groups[rep.group_id]:
                if member.role != role:
                    summary["changed"] += 1
                    member.notes = (member.notes + f"; эвристика: {member.role}").strip("; ")
                    member.role = role
                    member.role_source = "vlm"
                    member.confidence = round(conf, 2)
                elif conf > member.confidence:
                    member.confidence = round(conf, 2)
                    member.role_source = "vlm"
                if item.get("name") and member is rep:
                    member.name = str(item["name"])[:80]
    return summary


def role_matches_structure(role: str, pattern: Pattern) -> bool:
    """Роль не должна обещать объект, которого в образце нет: код заполняет реальные слоты."""
    kinds = collections.Counter(s.kind for s in pattern.slots)
    if role == "table":
        return kinds.get("table", 0) > 0
    if role == "chart":
        return kinds.get("chart", 0) > 0 or kinds.get("image", 0) > 0
    if role == "code":
        return kinds.get("code", 0) > 0 or kinds.get("body", 0) > 0
    if role in ("kpi", "numbers"):
        return kinds.get("number", 0) > 0 or kinds.get("label", 0) + kinds.get("body", 0) >= 2
    if role in ("screenshot", "mockup", "image_full"):
        # Картинка может быть слотом или статикой образца (рамка мокапа, фон).
        return kinds.get("image", 0) > 0 or kinds.get("icon", 0) > 0 or bool(pattern.static_ids)
    if role in ("team", "speaker"):
        return kinds.get("name", 0) > 0 or kinds.get("image", 0) > 0 or kinds.get("body", 0) > 0
    return True


def marker_slots(slots: list[Slot]) -> int:
    return sum(1 for s in slots if s.shape.text and is_marker(s.shape.text))
