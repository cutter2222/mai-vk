"""Изображения контент-пакета в слоты image образца.

Картинка образца остаётся тем же объектом `p:pic` (положение, рамка, скругление, тень —
всё оформление образца), меняется только ссылка на медиа-часть. Режим `cover` подбирает
`a:srcRect` так, чтобы картинка заполнила рамку без искажения (обрезка по центру), `contain`
вписывает рамку в слот по пропорциям картинки. Осиротевшая медиа-часть образца снимается со
слайда; если её использует другой слайд, она остаётся в пакете.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from lxml import etree
from pptx.opc.constants import RELATIONSHIP_TYPE as RT

from presentation_designer.layout.ooxml import NS_A, NS_R
from presentation_designer.layout.shapes import (
    NS,
    drop_unreferenced_rels,
    element_box,
    set_element_box,
)


@dataclass
class PictureResult:
    rid: str
    crop: dict[str, float] | None
    natural_width_px: int | None
    natural_height_px: int | None
    box: tuple[int, int, int, int] | None
    fit: str


def image_size(blob: bytes) -> tuple[int | None, int | None]:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(blob)) as img:
            return int(img.width), int(img.height)
    except Exception:
        return None, None


def _blip(element: Any) -> Any | None:
    return element.find(".//a:blip", NS)


def cover_crop(img_w: int, img_h: int, box_w: int, box_h: int) -> dict[str, float] | None:
    """Доли обрезки по центру, чтобы пропорции картинки совпали с рамкой."""
    if img_w <= 0 or img_h <= 0 or box_w <= 0 or box_h <= 0:
        return None
    image_ratio = img_w / img_h
    box_ratio = box_w / box_h
    if abs(image_ratio - box_ratio) < 1e-3:
        return None
    if image_ratio > box_ratio:
        # Картинка шире рамки: режем бока.
        keep = box_ratio / image_ratio
        side = (1 - keep) / 2
        return {"left": round(side, 4), "top": 0.0, "right": round(side, 4), "bottom": 0.0}
    keep = image_ratio / box_ratio
    side = (1 - keep) / 2
    return {"left": 0.0, "top": round(side, 4), "right": 0.0, "bottom": round(side, 4)}


def contain_box(
    img_w: int, img_h: int, box: tuple[int, int, int, int]
) -> tuple[int, int, int, int]:
    """Рамка внутри слота по пропорциям картинки, по центру."""
    x, y, cx, cy = box
    if img_w <= 0 or img_h <= 0 or cx <= 0 or cy <= 0:
        return box
    scale = min(cx / img_w, cy / img_h)
    w, h = round(img_w * scale), round(img_h * scale)
    return (x + (cx - w) // 2, y + (cy - h) // 2, w, h)


def set_crop(element: Any, crop: dict[str, float] | None) -> None:
    blip_fill = element.find(".//p:blipFill", NS)
    if blip_fill is None:
        blip_fill = element.find(".//a:blipFill", NS)
    if blip_fill is None:
        return
    src = blip_fill.find("a:srcRect", NS)
    if crop is None:
        if src is not None:
            blip_fill.remove(src)
        return
    if src is None:
        src = etree.Element(f"{{{NS_A}}}srcRect")
        blip = blip_fill.find("a:blip", NS)
        index = list(blip_fill).index(blip) + 1 if blip is not None else 0
        blip_fill.insert(index, src)
    for side, key in (("l", "left"), ("t", "top"), ("r", "right"), ("b", "bottom")):
        value = round(float(crop.get(key, 0)) * 100000)
        if value:
            src.set(side, str(value))
        elif side in src.attrib:
            del src.attrib[side]


def read_crop(element: Any) -> dict[str, float] | None:
    src = element.find(".//a:srcRect", NS)
    if src is None:
        return None
    crop = {
        key: round(float(src.get(side, 0)) / 100000, 4)
        for side, key in (("l", "left"), ("t", "top"), ("r", "right"), ("b", "bottom"))
    }
    return crop if any(crop.values()) else None


def place_image(
    slide: Any,
    element: Any,
    blob: bytes,
    *,
    fit: str = "cover",
    box: tuple[int, int, int, int] | None = None,
) -> PictureResult:
    """Подставляет картинку в объект `p:pic` образца. `box` — положение слота в EMU (для
    объектов внутри групп — координаты самого элемента)."""
    blip = _blip(element)
    if blip is None:
        raise ValueError("объект образца не является картинкой")
    _image_part, rid = slide.part.get_or_add_image_part(io.BytesIO(blob))
    blip.set(f"{{{NS_R}}}embed", rid)
    for attr in (f"{{{NS_R}}}link",):
        if attr in blip.attrib:
            del blip.attrib[attr]
    width, height = image_size(blob)
    own = element_box(element)
    target = box or own
    crop: dict[str, float] | None = None
    if fit == "contain" and width and height and own is not None:
        new_box = contain_box(width, height, own)
        set_element_box(element, new_box)
        set_crop(element, None)
        target = new_box
    elif width and height and target is not None:
        crop = cover_crop(width, height, target[2], target[3])
        set_crop(element, crop)
    else:
        set_crop(element, None)
    drop_unreferenced_rels(slide)
    return PictureResult(rid, crop, width, height, target, fit)


def picture_sha256(slide: Any, element: Any) -> str | None:
    """sha256 медиа-части, на которую ссылается картинка образца."""
    blip = _blip(element)
    if blip is None:
        return None
    rid = blip.get(f"{{{NS_R}}}embed")
    if not rid:
        return None
    try:
        part = slide.part.related_part(rid)
    except KeyError:
        return None
    import hashlib

    return hashlib.sha256(bytes(part.blob)).hexdigest()


def relate_image_part(slide: Any, image_part: Any) -> str:
    """Связь слайда с уже существующей медиа-частью пакета (иконка шаблона)."""
    return str(slide.part.relate_to(image_part, RT.IMAGE))


__all__ = [
    "PictureResult",
    "contain_box",
    "cover_crop",
    "image_size",
    "picture_sha256",
    "place_image",
    "read_crop",
    "relate_image_part",
    "set_crop",
]
