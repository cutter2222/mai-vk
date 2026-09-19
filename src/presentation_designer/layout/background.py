"""Фон слайда: чтение и запись `p:cSld/p:bg`.

До этапа 22 фон только читался (ComposedDeck): слайд наследует его от клонированного
образца и макета. Ручная правка из редактора умеет поставить сплошной цвет, картинку
(с обрезкой `cover` или вписыванием `contain`) или вернуть наследование от макета. `p:bg`
по схеме — первый ребёнок `p:cSld`, до `p:spTree`.
"""

from __future__ import annotations

import hashlib
from typing import Any

from lxml import etree

from presentation_designer.layout.images import cover_crop, image_size
from presentation_designer.layout.ooxml import NS_A, NS_P, NS_R
from presentation_designer.layout.shapes import NS, drop_unreferenced_rels

JsonDict = dict[str, Any]
P_CSLD = f"{{{NS_P}}}cSld"
P_BG = f"{{{NS_P}}}bg"
P_BGPR = f"{{{NS_P}}}bgPr"


def _csld(slide_element: Any) -> Any:
    csld = slide_element.find(P_CSLD)
    if csld is None:
        raise ValueError("у слайда нет p:cSld")
    return csld


def read(slide_element: Any, part: Any | None = None) -> JsonDict:
    """Описание фона: вид, цвет сплошной заливки; для картинки — часть и sha256 медиа
    (через `part`, если он передан)."""
    bg = slide_element.find(f"{P_CSLD}/{P_BG}")
    if bg is None:
        return {"kind": "inherited"}
    blip_fill = bg.find(".//a:blipFill", NS)
    if blip_fill is not None:
        out: JsonDict = {"kind": "image"}
        blip = blip_fill.find("a:blip", NS)
        rid = blip.get(f"{{{NS_R}}}embed") if blip is not None else None
        if rid and part is not None:
            try:
                media = part.related_part(rid)
            except KeyError:
                media = None
            if media is not None:
                blob = bytes(media.blob)
                out["media_part"] = str(media.partname).lstrip("/")
                out["sha256"] = hashlib.sha256(blob).hexdigest()
                out["blob"] = blob
        return out
    if bg.find(".//a:gradFill", NS) is not None:
        return {"kind": "gradient"}
    solid = bg.find(".//a:solidFill", NS)
    if solid is not None:
        srgb = solid.find("a:srgbClr", NS)
        out = {"kind": "solid"}
        if srgb is not None:
            out["color"] = f"#{srgb.get('val', '000000').upper()}"
        return out
    return {"kind": "inherited"}


def clear(slide: Any) -> None:
    """Снимает собственный фон слайда: фон наследуется от макета."""
    csld = _csld(slide._element)
    for bg in csld.findall(P_BG):
        csld.remove(bg)
    drop_unreferenced_rels(slide)


def _fresh_bgpr(slide: Any) -> Any:
    csld = _csld(slide._element)
    for old in csld.findall(P_BG):
        csld.remove(old)
    bg = etree.Element(P_BG)
    csld.insert(0, bg)
    return etree.SubElement(bg, P_BGPR)


def set_solid(slide: Any, color: str) -> None:
    """Сплошной цвет фона `#RRGGBB`."""
    bgpr = _fresh_bgpr(slide)
    solid = etree.SubElement(bgpr, f"{{{NS_A}}}solidFill")
    etree.SubElement(solid, f"{{{NS_A}}}srgbClr", val=color.lstrip("#").upper())
    etree.SubElement(bgpr, f"{{{NS_A}}}effectLst")
    drop_unreferenced_rels(slide)


def set_image(
    slide: Any, blob: bytes, *, fit: str = "cover", slide_w: int = 0, slide_h: int = 0
) -> str:
    """Картинка фоном: `cover` режет по центру (`a:srcRect`), `contain` вписывает с полями
    (`a:stretch/a:fillRect`). Возвращает rId медиа-части."""
    _image_part, rid = slide.part.get_or_add_image_part(_bytes_io(blob))
    bgpr = _fresh_bgpr(slide)
    blip_fill = etree.SubElement(bgpr, f"{{{NS_A}}}blipFill", dpi="0", rotWithShape="1")
    etree.SubElement(blip_fill, f"{{{NS_A}}}blip", {f"{{{NS_R}}}embed": rid})
    width, height = image_size(blob)
    if fit == "cover" and width and height and slide_w and slide_h:
        crop = cover_crop(width, height, slide_w, slide_h)
        if crop:
            src = etree.SubElement(blip_fill, f"{{{NS_A}}}srcRect")
            for side, key in (("l", "left"), ("t", "top"), ("r", "right"), ("b", "bottom")):
                value = round(float(crop.get(key, 0)) * 100000)
                if value:
                    src.set(side, str(value))
    stretch = etree.SubElement(blip_fill, f"{{{NS_A}}}stretch")
    fill_rect = etree.SubElement(stretch, f"{{{NS_A}}}fillRect")
    if fit == "contain" and width and height and slide_w and slide_h:
        scale = min(slide_w / width, slide_h / height)
        margin_x = (slide_w - width * scale) / 2 / slide_w
        margin_y = (slide_h - height * scale) / 2 / slide_h
        margins = (("l", margin_x), ("r", margin_x), ("t", margin_y), ("b", margin_y))
        for side, margin in margins:
            percent = round(margin * 100000)
            if percent:
                fill_rect.set(side, str(percent))
    etree.SubElement(bgpr, f"{{{NS_A}}}effectLst")
    drop_unreferenced_rels(slide)
    return str(rid)


def _bytes_io(blob: bytes) -> Any:
    import io

    return io.BytesIO(blob)


__all__ = ["clear", "read", "set_image", "set_solid"]
