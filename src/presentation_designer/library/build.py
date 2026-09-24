"""Построение слайда собственной композиции внутри пакета загруженного шаблона.

Слайд создаётся на макете шаблона, поэтому фон, логотипы и колонтитулы приходят
наследованием — их не нужно ни копировать, ни рисовать. Поверх макета ставятся плашки и
текстовые рамки композиции, одетые в дизайн-код: заливка акцентом шаблона, скругление его
пластики, шрифт и кегль его типографики.

Пустые плейсхолдеры макета убираются: иначе PowerPoint покажет поверх композиции подсказки
вида «Заголовок слайда».
"""

from __future__ import annotations

from typing import Any

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Pt

from presentation_designer.layout.ooxml import NS_A, NS_P, NS_R
from presentation_designer.library.spec import CardSpec, Composition, CompositionSlot
from presentation_designer.library.tokens import DesignCode
from presentation_designer.parsing.template.tone import relative_luminance

JsonDict = dict[str, Any]

_ALIGN = {
    "left": PP_ALIGN.LEFT,
    "center": PP_ALIGN.CENTER,
    "right": PP_ALIGN.RIGHT,
    "justify": PP_ALIGN.JUSTIFY,
}
_ANCHOR = {"top": MSO_ANCHOR.TOP, "middle": MSO_ANCHOR.MIDDLE, "bottom": MSO_ANCHOR.BOTTOM}
# Доля заливки акцента у «мягкой» плашки: сам акцент под текстом читается плохо.
SURFACE_TINT = 0.12
# Слоты, которым нужен объект-якорь: вёрстка строит на его месте свой объект.
ANCHOR_KINDS = ("table", "chart", "image", "icon", "diagram")


def build_slide(
    prs: Any,
    layout: Any,
    composition: Composition,
    code: DesignCode,
) -> tuple[Any, dict[str, str], list[str]]:
    """Создаёт слайд по композиции.

    Возвращает слайд, карту `slot_id → id объекта` и id плашек, которые можно убрать вместе с
    незаполненной карточкой. Текст не пишется: рамки создаются пустыми, а заполняет их общий
    путь вёрстки по `element_ref`, как и у паттернов из образцов шаблона.
    """
    slide = prs.slides.add_slide(layout)
    _drop_placeholders(slide)
    width, height = int(prs.slide_width or 0), int(prs.slide_height or 0)
    # Цвет текста считается от того, на чём текст лежит на самом деле: фон макета может
    # не совпадать с фоном дизайн-кода (у ЛЦТ все макеты фиолетовые, а белые слайды
    # образцов получают фон на самом слайде) — тёмный текст палитры там не читается.
    backdrop = layout_background(layout, code, width, height) or code.background
    cards = {card.index: card for card in composition.cards}
    card_ids: list[str] = []
    for card in composition.cards:
        shape = _add_card(slide, card, code, width, height)
        card_ids.append(str(shape.shape_id))
    refs: dict[str, str] = {}
    anchor_ids: list[str] = []
    for slot in composition.slots:
        if slot.kind in ANCHOR_KINDS:
            # Таблице, диаграмме, картинке и схеме нужен объект-якорь: вёрстка строит свой
            # объект на его месте и удаляет якорь (`compose._apply_chart`, `_apply_table`,
            # `_apply_image`). Без якоря слот считается ненайденным и блок теряется.
            shape = _add_anchor(slide, slot, code, width, height)
            anchor_ids.append(str(shape.shape_id))
        else:
            shape = _add_text_box(
                slide, slot, code, width, height, cards.get(slot.card_index), backdrop
            )
        refs[slot.slot_id] = str(shape.shape_id)
    # Незаполненный якорь убирается вместе с пустой карточкой, как и текстовые рамки.
    return slide, refs, card_ids + anchor_ids


def _rgb(hex_color: str) -> Any:
    """Цвет для python-pptx (как в `layout/diagrams.py`)."""
    return RGBColor.from_string(hex_color.lstrip("#"))  # type: ignore[no-untyped-call]


def _add_anchor(
    slide: Any, slot: CompositionSlot, code: DesignCode, width: int, height: int
) -> Any:
    """Место под диаграмму, таблицу, картинку или схему.

    Пока слот не заполнен, это видимая рамка в цвете шаблона: так пустое место читается как
    задуманное, а не как потерянный объект. Вёрстка ставит на него свой объект и удаляет
    якорь, а если содержания не нашлось — якорь убирается вместе с пустой карточкой.
    """
    left, top, box_w, box_h = _emu_box(slot.bbox, width, height)
    rounded = code.card_geometry == "roundRect" and code.corner_ratio > 0
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
        Emu(left),
        Emu(top),
        Emu(box_w),
        Emu(box_h),
    )
    if rounded:
        _set_corner(shape, code.corner_ratio)
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(_tint(code.accent, code.background))
    shape.line.fill.background()
    _set_shadow(shape, False)
    if shape.has_text_frame:
        shape.text_frame.text = ""
    return shape


def _drop_placeholders(slide: Any) -> None:
    for shape in list(slide.placeholders):
        shape._element.getparent().remove(shape._element)


def _emu_box(
    bbox: tuple[float, float, float, float], width: int, height: int
) -> tuple[int, int, int, int]:
    x, y, w, h = bbox
    return (int(x * width), int(y * height), int(w * width), int(h * height))


def _add_card(slide: Any, card: CardSpec, code: DesignCode, width: int, height: int) -> Any:
    left, top, box_w, box_h = _emu_box(card.bbox, width, height)
    rounded = code.card_geometry == "roundRect" and code.corner_ratio > 0
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE,
        Emu(left),
        Emu(top),
        Emu(box_w),
        Emu(box_h),
    )
    if rounded:
        _set_corner(shape, code.corner_ratio)
    accent = code.accent_at(card.accent_index)
    fill = shape.fill
    if card.fill == "none":
        fill.background()
    else:
        fill.solid()
        fill.fore_color.rgb = _rgb(
            accent if card.fill == "accent" else _tint(accent, code.background)
        )
    line = shape.line
    if code.stroke_pt > 0 and card.fill != "accent":
        line.color.rgb = _rgb(accent)
        line.width = Pt(code.stroke_pt)
    else:
        line.fill.background()
    _set_shadow(shape, code.shadow)
    if shape.has_text_frame:
        shape.text_frame.text = ""
    return shape


def _set_shadow(shape: Any, enabled: bool) -> None:
    """Тень плашки по пластике шаблона.

    `shadow.inherit = False` само по себе тень не убирает: python-pptx создаёт фигуру со
    ссылкой на стиль темы (`a:effectRef idx="2"`), и рендерер рисует тень оттуда. Поэтому
    эффекты гасятся и в свойствах (пустой `a:effectLst`), и в ссылке на стиль.
    """
    shape.shadow.inherit = enabled
    if enabled:
        return
    sp_pr = shape._element.spPr
    for existing in sp_pr.findall(f"{{{NS_A}}}effectLst"):
        sp_pr.remove(existing)
    sp_pr.append(sp_pr.makeelement(f"{{{NS_A}}}effectLst", {}))
    _clear_style_refs(shape)


def _clear_style_refs(shape: Any) -> None:
    """Обнуляет ссылки фигуры на стиль темы: заливку, линию и эффекты задаёт только код.

    Иначе вид плашки зависит от того, какой стиль фигуры принят в теме шаблона, и одна и та
    же композиция получает то тень, то градиент, то чужую обводку.
    """
    style = shape._element.find(f"{{{NS_P}}}style")
    if style is None:
        return
    for tag in ("lnRef", "fillRef", "effectRef"):
        ref = style.find(f"{{{NS_A}}}{tag}")
        if ref is not None:
            ref.set("idx", "0")


def _set_corner(shape: Any, ratio: float) -> None:
    """Скругление плашки в пластике шаблона: adj в тысячных долях половины меньшей стороны."""
    try:
        shape.adjustments[0] = max(0.0, min(ratio, 0.5))
    except (IndexError, AttributeError):  # pragma: no cover - у фигуры нет регулятора
        pass


def _tint(hex_color: str, background: str) -> str:
    """Мягкая подложка: акцент, разбавленный фоном шаблона."""
    try:
        fore = _rgb(hex_color)
        back = _rgb(background)
    except ValueError:
        return background
    mixed = tuple(
        round(f * SURFACE_TINT + b * (1 - SURFACE_TINT)) for f, b in zip(fore, back, strict=True)
    )
    return "#{:02X}{:02X}{:02X}".format(*mixed)


def _add_text_box(
    slide: Any,
    slot: CompositionSlot,
    code: DesignCode,
    width: int,
    height: int,
    card: CardSpec | None,
    backdrop: str | None = None,
) -> Any:
    left, top, box_w, box_h = _emu_box(slot.bbox, width, height)
    shape = slide.shapes.add_textbox(Emu(left), Emu(top), Emu(box_w), Emu(box_h))
    frame = shape.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = _ANCHOR.get(slot.valign, MSO_ANCHOR.TOP)
    paragraph = frame.paragraphs[0]
    paragraph.alignment = _ALIGN.get(slot.align, PP_ALIGN.LEFT)
    run = paragraph.add_run()
    run.text = ""
    font = run.font
    family = code.font_for(slot.text_role)
    if family:
        font.name = family
    font.size = Pt(code.size_for(slot.text_role))
    font.bold = slot.bold
    font.color.rgb = _rgb(_color_for(slot, code, card, backdrop))
    return shape


# Минимальный контраст текста к подложке (WCAG): крупному тексту хватает 3:1.
LARGE_ROLES = ("title", "subtitle", "number")


def _color_for(
    slot: CompositionSlot,
    code: DesignCode,
    card: CardSpec | None,
    backdrop: str | None = None,
) -> str:
    if slot.on_card and card is not None and card.fill == "accent":
        # Текст лежит на залитой плашке: цвет считается от заливки, а не от фона слайда.
        return code.on_accent(code.accent_at(card.accent_index))
    wanted = {"accent": code.accent, "muted": code.muted_color}.get(
        slot.color_role, code.text_color
    )
    if slot.on_card and card is not None and card.fill == "surface":
        surface = _tint(code.accent_at(card.accent_index), code.background)
    else:
        surface = backdrop or code.background
    need = 3.0 if slot.text_role in LARGE_ROLES else 4.5
    return legible(wanted, surface, code, need, accent=slot.color_role == "accent")


def contrast(a: str, b: str) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def legible(wanted: str, surface: str, code: DesignCode, need: float, *, accent: bool) -> str:
    """Цвет роли, если он читается на подложке; иначе ближайший по роли читаемый цвет
    дизайн-кода: для акцента — другой акцент палитры, для текста — текст или белый."""
    if contrast(wanted, surface) >= need:
        return wanted
    pool = [*code.accents, *code.light_accents] if accent else []
    pool += [code.text_color, "#FFFFFF", "#111111"]
    for color in pool:
        if contrast(color, surface) >= need:
            return color
    return max(pool, key=lambda c: contrast(c, surface))


def layout_background(layout: Any, code: DesignCode, width: int, height: int) -> str | None:
    """Средний цвет фона, который макет даёт слайду: фон макета, закрывающая его картинка,
    фон мастера — в порядке наследования PowerPoint. None — определить не удалось."""
    master = getattr(layout, "slide_master", None)
    for owner in (layout, master):
        if owner is None:
            continue
        found = _own_background(owner, code)
        if found is None:
            found = _covering_picture(owner, width, height)
        if found is not None:
            return found
    return code.theme.get("lt1")


def _own_background(owner: Any, code: DesignCode) -> str | None:
    bg = owner._element.find(f"{{{NS_P}}}cSld/{{{NS_P}}}bg")
    if bg is None:
        return None
    blip = bg.find(f".//{{{NS_A}}}blip")
    if blip is not None:
        rid = blip.get(f"{{{NS_R}}}embed")
        try:
            return _image_mean(owner.part.related_part(rid).blob) if rid else None
        except (KeyError, AttributeError):
            return None
    found = bg.iter(f"{{{NS_A}}}srgbClr", f"{{{NS_A}}}schemeClr")
    colors = [c for c in (_scheme_or_rgb(el, code) for el in found) if c]
    if not colors:
        return None
    return _mean_hex(colors)


def _covering_picture(owner: Any, width: int, height: int) -> str | None:
    if not width or not height:
        return None
    for shape in reversed(list(owner.shapes)):
        if getattr(shape, "shape_type", None) != 13:  # MSO_SHAPE_TYPE.PICTURE
            continue
        if (shape.width or 0) * (shape.height or 0) < 0.85 * width * height:
            continue
        try:
            return _image_mean(shape.image.blob)
        except Exception:
            return None
    return None


_SCHEME = {"bg1": "lt1", "bg2": "lt2", "tx1": "dk1", "tx2": "dk2"}


def _scheme_or_rgb(element: Any, code: DesignCode) -> str | None:
    value = str(element.get("val") or "")
    if element.tag.endswith("srgbClr"):
        return f"#{value.upper()}" if len(value) == 6 else None
    return code.theme.get(_SCHEME.get(value, value))


def _mean_hex(colors: list[str]) -> str:
    rgb = [tuple(int(c.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)) for c in colors]
    return "#{:02X}{:02X}{:02X}".format(
        *(round(sum(ch) / len(rgb)) for ch in zip(*rgb, strict=True))
    )


def _image_mean(blob: bytes) -> str | None:
    import io

    from PIL import Image

    try:
        with Image.open(io.BytesIO(blob)) as img:
            small = img.convert("RGB")
            small.thumbnail((32, 32))
            pixels = list(small.getdata())
    except Exception:
        return None
    if not pixels:
        return None
    return "#{:02X}{:02X}{:02X}".format(
        *(round(sum(p[i] for p in pixels) / len(pixels)) for i in range(3))
    )
