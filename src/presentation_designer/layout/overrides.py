"""Ручные правки объектов слайда из визуального редактора (`slides[].overrides` плана).

Применяются в конце заполнения слайда — после блоков плана и чистки карточек, до заметок, —
поэтому ревизия по-прежнему воспроизводится из плана командой `compose`. Адрес правки —
`object_id` (`p:cNvPr@id`) из ComposedDeck базовой ревизии: у объектов клона образца он
совпадает с образцом, у новых объектов детерминирован (`_Context.fresh_ids`). Охрана
адреса — `source_object_id` и `slot_id`: при несовпадении правка отбрасывается с
предупреждением, а не применяется к чужому объекту. Порядок применения: text → style →
geometry → picture, фон слайда последним; внутри одного вида — порядок списка.

Виды операций:

* `text` — замена текста с сохранением оформления (`fill_text`/`fill_bullets`);
* `style` — кегль, начертание, цвет, гарнитура всех фрагментов и выравнивание абзацев;
* `geometry` — положение и размер в долях слайда с учётом вложенности в группы; у картинок
  с обрезкой `cover` обрезка пересчитывается под новую рамку;
* `picture` — картинка или иконка из ресурсов шаблона, пакета или файла проекта, с
  режимом `cover`/`contain` и перекраской монохромной иконки;
* `background` — сплошной цвет, картинка или наследование от макета.

Значения вне токенов шаблона (гарнитура, кегль, цвет) применяются, но помечаются
предупреждением `override_off_template`; текст, не помещающийся в рамку по метрикам
шрифта, — `override_overflow`.
"""

from __future__ import annotations

import dataclasses
import logging
import pathlib
from typing import Any

from presentation_designer.generation import capacity
from presentation_designer.generation.matching import SlotInfo
from presentation_designer.layout import background as bg
from presentation_designer.layout import icons, images
from presentation_designer.layout import text as tx
from presentation_designer.layout.composed import SlideRecord, SlotFill
from presentation_designer.layout.images import cover_crop, image_size, read_crop, set_crop
from presentation_designer.layout.ooxml import NS_R
from presentation_designer.layout.shapes import (
    element_box,
    element_box_absolute,
    emu_box,
    part_by_name,
    set_element_box_absolute,
    shape_element,
)

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]
OP_ORDER = {"text": 0, "style": 1, "geometry": 2, "picture": 3, "background": 4}
A_BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"


class OverrideDroppedError(Exception):
    """Правка не применена: код и причина попадают в предупреждения и ComposedDeck."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclasses.dataclass
class _Touched:
    """Текстовый объект, затронутый правками: измеряется на переполнение в конце."""

    element: Any
    fill: SlotFill | None
    slot: SlotInfo | None
    text: str | None = None
    family: str | None = None
    size_pt: float | None = None
    bold: bool | None = None
    italic: bool | None = None


# ---------- токены шаблона ----------


def template_tokens(profile: JsonDict) -> dict[str, set[Any]]:
    """Гарнитуры, кегли и цвета профиля, внутри которых правка считается «из шаблона»."""
    tokens = profile.get("design_tokens") or {}
    typography = tokens.get("typography") or {}
    colors = tokens.get("colors") or {}
    families = {
        str(f.get("family")).lower() for f in typography.get("fonts") or [] if f.get("family")
    }
    for value in (typography.get("theme_fonts") or {}).values():
        if value:
            families.add(str(value).lower())
    sizes = {float(s.get("size_pt")) for s in typography.get("scale") or [] if s.get("size_pt")}
    palette = {str(c.get("hex")).upper() for c in colors.get("palette") or [] if c.get("hex")}
    for value in (colors.get("theme") or {}).values():
        if value:
            palette.add(str(value).upper())
    return {"families": families, "sizes": sizes, "colors": palette}


def off_template(style: JsonDict, tokens: dict[str, set[Any]]) -> list[str]:
    """Что в правке стиля выходит за токены шаблона (пустой список — всё из шаблона)."""
    font = style.get("font") or {}
    out: list[str] = []
    family = font.get("family")
    if family and tokens["families"] and str(family).lower() not in tokens["families"]:
        out.append(f"гарнитура {family}")
    size = font.get("size_pt")
    if size is not None and tokens["sizes"] and float(size) not in tokens["sizes"]:
        out.append(f"кегль {size:g}")
    color = font.get("color")
    if color and tokens["colors"] and str(color).upper() not in tokens["colors"]:
        out.append(f"цвет {str(color).upper()}")
    return out


# ---------- применение ----------


def apply_overrides(
    ctx: Any,
    slide: Any,
    record: SlideRecord,
    plan_slide: JsonDict,
    pinfo: Any,
    slide_index: int,
) -> None:
    """Применяет `plan_slide["overrides"]` к собранному слайду, пишет в `record`, что
    применено и что отброшено, предупреждения — в контекст композера."""
    overrides = [o for o in plan_slide.get("overrides") or [] if isinstance(o, dict)]
    if not overrides:
        return
    tokens = template_tokens(ctx.profile)
    fills_by_id = {f.element_id: f for f in record.fills}
    fills_by_slot = {f.slot_id: f for f in record.fills}
    touched: dict[str, _Touched] = {}
    ordered = sorted(
        enumerate(overrides),
        key=lambda item: (OP_ORDER.get(str(item[1].get("op")), 9), item[0]),
    )
    for _, override in ordered:
        op = str(override.get("op") or "")
        target = override.get("target") or {}
        object_id = str(target.get("object_id") or "")
        try:
            if op == "background":
                _apply_background(ctx, slide, override)
                record.background_override = override
            else:
                element = _resolve_target(slide, override, fills_by_id, fills_by_slot)
                fill = fills_by_id.get(object_id)
                slot = pinfo.slots.get(fill.slot_id) if fill is not None else None
                if op == "text":
                    _apply_text(ctx, element, override, fill, touched, slot)
                elif op == "style":
                    _apply_style(ctx, element, override, fill, touched, slot, tokens, slide_index)
                elif op == "geometry":
                    _apply_geometry(ctx, slide, element, override, fill, touched, slot)
                elif op == "picture":
                    _apply_picture(
                        ctx, slide, element, override, fill, record, object_id, slide_index
                    )
                else:
                    raise OverrideDroppedError("override_unsupported", f"операция {op} неизвестна")
            record.overrides_applied.setdefault(object_id or "", []).append(override)
        except OverrideDroppedError as dropped:
            entry: JsonDict = {"op": op, "code": dropped.code, "message": str(dropped)}
            if target:
                entry["target"] = {k: v for k, v in target.items() if v is not None}
            record.overrides_dropped.append(entry)
            ctx.warn(dropped.code, f"{record.slide_id}: {dropped}", slide_index)
        except Exception as e:  # правка не имеет права ронять сборку
            log.exception("правка %s объекта %s на %s не применена", op, object_id, record.slide_id)
            message = f"{e.__class__.__name__}: {e}"[:300]
            entry = {"op": op, "code": "override_failed", "message": message}
            if target:
                entry["target"] = {k: v for k, v in target.items() if v is not None}
            record.overrides_dropped.append(entry)
            ctx.warn("override_failed", f"{record.slide_id}: правка {op} не применена: {e}")
    for object_id, item in touched.items():
        _check_overflow(ctx, record, object_id, item, slide_index)


def _resolve_target(
    slide: Any,
    override: JsonDict,
    fills_by_id: dict[str, SlotFill],
    fills_by_slot: dict[str, SlotFill],
) -> Any:
    target = override.get("target") or {}
    object_id = str(target.get("object_id") or "")
    if not object_id:
        raise OverrideDroppedError("override_target_missing", "у правки нет object_id")
    element = shape_element(slide, object_id)
    if element is None:
        raise OverrideDroppedError(
            "override_target_missing",
            f"объект {object_id} не найден на слайде (удалён чисткой или другой ревизией)",
        )
    fill = fills_by_id.get(object_id)
    expected_source = fill.source_object_id if fill is not None else object_id
    claimed_source = target.get("source_object_id")
    if claimed_source and expected_source and str(claimed_source) != str(expected_source):
        raise OverrideDroppedError(
            "override_guard_mismatch",
            f"объект {object_id} собран из другого объекта образца "
            f"({expected_source}, ожидался {claimed_source})",
        )
    claimed_slot = target.get("slot_id")
    if claimed_slot:
        by_slot = fills_by_slot.get(str(claimed_slot))
        if by_slot is None or by_slot.element_id != object_id:
            raise OverrideDroppedError(
                "override_guard_mismatch",
                f"объект {object_id} больше не заполняет слот {claimed_slot}",
            )
    return element


def _is_picture(element: Any) -> bool:
    return element.find(f".//{A_BLIP}") is not None


def _touch(
    touched: dict[str, _Touched],
    element: Any,
    fill: SlotFill | None,
    slot: SlotInfo | None,
) -> _Touched:
    key = _element_id(element)
    if key not in touched:
        touched[key] = _Touched(element=element, fill=fill, slot=slot)
    return touched[key]


def _element_id(element: Any) -> str:
    from presentation_designer.layout.shapes import CNVPR

    cnvpr = element.find(f".//{CNVPR}")
    return str(cnvpr.get("id")) if cnvpr is not None else ""


def _apply_text(
    ctx: Any,
    element: Any,
    override: JsonDict,
    fill: SlotFill | None,
    touched: dict[str, _Touched],
    slot: SlotInfo | None,
) -> None:
    if _is_picture(element) or (tx.text_body(element) is None and element.tag.endswith("}pic")):
        raise OverrideDroppedError("override_unsupported", "текст нельзя записать в картинку")
    if element.tag.endswith("}graphicFrame") or element.tag.endswith("}grpSp"):
        raise OverrideDroppedError("override_unsupported", "текст таблицы или группы не правится")
    text = str(override.get("text") or "")
    if fill is not None and fill.block_kind == "bullets":
        items = [line for line in text.split("\n")]
        result = tx.fill_bullets(element, items, size_pt=None, facts=ctx.facts)
    else:
        result = tx.fill_text(element, text, size_pt=None, facts=ctx.facts)
    if fill is not None:
        fill.text = result.plain
        fill.fact_refs = list(dict.fromkeys([*fill.fact_refs, *result.fact_refs]))
        fill.content_source = "user"
    item = _touch(touched, element, fill, slot)
    item.text = result.plain


def _apply_style(
    ctx: Any,
    element: Any,
    override: JsonDict,
    fill: SlotFill | None,
    touched: dict[str, _Touched],
    slot: SlotInfo | None,
    tokens: dict[str, set[Any]],
    slide_index: int,
) -> None:
    if _is_picture(element) or tx.text_body(element) is None:
        raise OverrideDroppedError("override_unsupported", "у объекта нет текста, стиль неприменим")
    style = override.get("style") or {}
    font = style.get("font") or {}
    count = tx.set_run_style(
        element,
        family=font.get("family") or None,
        size_pt=float(font["size_pt"]) if font.get("size_pt") else None,
        bold=font.get("bold"),
        italic=font.get("italic"),
        color=font.get("color") or None,
        align=style.get("align") or None,
    )
    if count == 0 and not style.get("align"):
        raise OverrideDroppedError("override_unsupported", "у объекта нет фрагментов текста")
    outside = off_template(style, tokens)
    if outside:
        ctx.warn(
            "override_off_template",
            f"объект {_element_id(element)}: не из шаблона — {', '.join(outside)}",
            slide_index,
        )
    if fill is not None and font.get("size_pt"):
        fill.size_pt = float(font["size_pt"])
        if fill.fit:
            fill.fit = {**fill.fit, "size_pt": float(font["size_pt"])}
    item = _touch(touched, element, fill, slot)
    if font.get("family"):
        item.family = str(font["family"])
    if font.get("size_pt"):
        item.size_pt = float(font["size_pt"])
    if font.get("bold") is not None:
        item.bold = bool(font["bold"])
    if font.get("italic") is not None:
        item.italic = bool(font["italic"])


def _apply_geometry(
    ctx: Any,
    slide: Any,
    element: Any,
    override: JsonDict,
    fill: SlotFill | None,
    touched: dict[str, _Touched],
    slot: SlotInfo | None,
) -> None:
    if element.tag.endswith("}cxnSp"):
        raise OverrideDroppedError("override_unsupported", "соединители не двигаются")
    box_raw = (override.get("geometry") or {}).get("bbox") or {}
    box = emu_box(box_raw, ctx.slide_w, ctx.slide_h)
    if box[2] <= 0 or box[3] <= 0:
        raise OverrideDroppedError("override_unsupported", "рамка нулевого размера")
    if not set_element_box_absolute(element, box):
        raise OverrideDroppedError("override_unsupported", "у объекта нет координат")
    if _is_picture(element):
        # Обрезка cover считалась под прежнюю рамку: пересчёт под новую по натуральному размеру.
        crop = read_crop(element)
        picture_fit = (fill.picture or {}).get("fit") if fill is not None else None
        if crop or picture_fit == "cover":
            blob = _picture_blob(slide, element)
            width, height = image_size(blob) if blob else (None, None)
            if width and height:
                set_crop(element, cover_crop(width, height, box[2], box[3]))
    if tx.text_body(element) is not None:
        _touch(touched, element, fill, slot)


def _picture_blob(slide: Any, element: Any) -> bytes | None:
    blip = element.find(f".//{A_BLIP}")
    rid = blip.get(f"{{{NS_R}}}embed") if blip is not None else None
    if not rid:
        return None
    try:
        return bytes(slide.part.related_part(rid).blob)
    except KeyError:
        return None


def _source_blob(ctx: Any, slide: Any, source: JsonDict) -> tuple[bytes, JsonDict]:
    """Байты картинки по источнику правки и описание для ComposedDeck."""
    kind = str(source.get("kind") or "")
    if kind == "template":
        asset_id = str(source.get("asset_id") or "")
        asset = ctx.profile_assets.get(asset_id)
        if asset is None:
            raise OverrideDroppedError("override_asset_missing", f"в шаблоне нет {asset_id}")
        part = part_by_name(slide.part.package, str(asset.get("media_path") or ""))
        if part is None:
            raise OverrideDroppedError(
                "override_asset_missing", f"медиа {asset.get('media_path')} нет в пакете"
            )
        return bytes(part.blob), {"asset_id": asset_id, "origin": "template"}
    if kind == "package":
        asset_id = str(source.get("asset_id") or "")
        asset = ctx.package_assets.get(asset_id)
        if asset is None:
            raise OverrideDroppedError("override_asset_missing", f"ресурса {asset_id} нет в пакете")
        blob = _package_asset_bytes(ctx, asset)
        if blob is None:
            raise OverrideDroppedError("override_asset_missing", f"файла ресурса {asset_id} нет")
        return blob, {"asset_id": asset_id, "origin": "content"}
    if kind == "file":
        file_id = str(source.get("file_id") or "")
        path = (getattr(ctx, "extra_assets", None) or {}).get(file_id)
        if not path or not pathlib.Path(path).is_file():
            raise OverrideDroppedError("override_file_missing", f"файл {file_id} недоступен сборке")
        return pathlib.Path(path).read_bytes(), {"origin": "content"}
    raise OverrideDroppedError("override_unsupported", f"источник {kind or '?'} неизвестен")


def _package_asset_bytes(ctx: Any, asset: JsonDict) -> bytes | None:
    if ctx.package_dir is None:
        return None
    rel = str(asset.get("path") or "")
    if not rel:
        return None
    path = pathlib.Path(ctx.package_dir) / rel
    return path.read_bytes() if path.is_file() else None


def _apply_picture(
    ctx: Any,
    slide: Any,
    element: Any,
    override: JsonDict,
    fill: SlotFill | None,
    record: SlideRecord,
    object_id: str,
    slide_index: int,
) -> None:
    if not _is_picture(element):
        raise OverrideDroppedError("override_unsupported", "объект не картинка, замена невозможна")
    picture = override.get("picture") or {}
    blob, meta = _source_blob(ctx, slide, picture.get("source") or {})
    default_fit = "contain" if (fill is not None and fill.slot_kind == "icon") else "cover"
    fit = str(picture.get("fit") or default_fit)
    color = picture.get("color")
    recolored = False
    if color:
        if icons.is_monochrome_mask(blob):
            blob = icons.recolor_mask(blob, str(color))
            recolored = True
        else:
            ctx.warn(
                "override_recolor_unsupported",
                f"объект {object_id}: картинка не одноцветная маска, перекраска пропущена",
                slide_index,
            )
    own = element_box(element)
    result = images.place_image(slide, element, blob, fit=fit, box=own)
    description: JsonDict = {**meta, "fit": fit, "recolored": recolored}
    if result.crop:
        description["crop"] = result.crop
    if fill is not None:
        fill.content_source = "user"
        fill.asset_id = meta.get("asset_id")
        fill.picture = {k: v for k, v in description.items() if k != "crop"}
    else:
        record.user_pictures[object_id] = {k: v for k, v in description.items() if k != "crop"}
    ctx.count("pictures")


def _apply_background(ctx: Any, slide: Any, override: JsonDict) -> None:
    spec = override.get("background") or {}
    kind = str(spec.get("kind") or "")
    if kind == "inherited":
        bg.clear(slide)
        return
    if kind == "solid":
        color = str(spec.get("color") or "")
        if not color:
            raise OverrideDroppedError("override_unsupported", "сплошной фон без цвета")
        bg.set_solid(slide, color)
        return
    if kind == "image":
        blob, _meta = _source_blob(ctx, slide, spec.get("source") or {})
        bg.set_image(
            slide,
            blob,
            fit=str(spec.get("fit") or "cover"),
            slide_w=ctx.slide_w,
            slide_h=ctx.slide_h,
        )
        return
    raise OverrideDroppedError("override_unsupported", f"фон вида {kind or '?'} неизвестен")


def _check_overflow(
    ctx: Any, record: SlideRecord, object_id: str, item: _Touched, slide_index: int
) -> None:
    """Переполнение текста после правок — по тем же метрикам шрифта, что у планировщика."""
    slot = item.slot
    if slot is None or (slot.family is None and slot.size_pt is None):
        return
    text = item.text if item.text is not None else (item.fill.text if item.fill else None)
    if not text:
        return
    box = element_box_absolute(item.element)
    if box is None or ctx.slide_w <= 0 or ctx.slide_h <= 0:
        return
    bbox = (
        box[0] / ctx.slide_w,
        box[1] / ctx.slide_h,
        box[2] / ctx.slide_w,
        box[3] / ctx.slide_h,
    )
    replaced = dataclasses.replace(
        slot,
        bbox=bbox,
        family=item.family or slot.family,
        bold=slot.bold if item.bold is None else item.bold,
        italic=slot.italic if item.italic is None else item.italic,
    )
    size = item.size_pt or (item.fill.size_pt if item.fill else None) or slot.size_pt
    lines = text.split("\n") if (item.fill and item.fill.block_kind == "bullets") else text
    try:
        measure = capacity.measure(lines, replaced, ctx.slide_w, ctx.slide_h, size_pt=size)
    except Exception:
        log.debug("измерение правки %s не выполнено", object_id, exc_info=True)
        return
    if not measure.fits:
        ctx.warn(
            "override_overflow",
            f"{record.slide_id}: текст объекта {object_id} не помещается после правки "
            f"({measure.lines} строк при {measure.max_lines} возможных)",
            slide_index,
        )


__all__ = ["OverrideDroppedError", "apply_overrides", "off_template", "template_tokens"]
