"""Оформление содержательного слайда шаблона для собственной композиции: фон и декор.

Собственная композиция строится на макете шаблона и рассчитывает, что фон, логотип и
колонтитул придут наследованием. Это верно для шаблонов с размеченными макетами, но не для
шаблонов, где всё оформление лежит на самих слайдах: один пустой макет, а фон, цветная полоса
под заголовком и номер страницы нарисованы на каждом образце. Там композиция получала белый
лист среди оформленных слайдов.

«Кожа» снимается с содержательных образцов преобладающего тона (без обложки, разделителей и
финала):

* фон — самый частый собственный фон образцов (`p:cSld/p:bg`), если он отличается от того,
  что даёт макет;
* декор — фигуры без текста, которые стоят на одном и том же месте у большинства образцов
  (полоса-прогресс под заголовком, линии, плашки колонтитула). Они статичны по разбору
  шаблона, поэтому это оформление, а не содержание;
* место заголовка — рамка заголовка образца: декор часто стоит сразу под ней, и заголовок
  композиции поднимается туда же, чтобы не лечь на полосу.
"""

from __future__ import annotations

import copy
import hashlib
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.layout.ooxml import NS_A, NS_P, NS_R
from presentation_designer.library.tokens import content_samples

JsonDict = dict[str, Any]
Box = tuple[float, float, float, float]

P_BG = f"{{{NS_P}}}bg"
P_CSLD = f"{{{NS_P}}}cSld"
CNVPR = f"{{{NS_P}}}cNvPr"
DECOR_TAGS = ("sp", "cxnSp")
# Декор считается общим, если стоит на месте у такой доли образцов (и не меньше чем у двух).
SHARE = 0.6
# Совпадение положения — с точностью до полупроцента холста.
GRID = 200
# Зазор между заголовком и декором под ним.
GAP = 0.005


@dataclass
class Skin:
    """Что собственная композиция берёт у содержательных образцов шаблона."""

    background: str | None = None
    bg_element: Any | None = None
    bg_blob: bytes | None = None
    decor: list[tuple[Any, Box]] = field(default_factory=list)
    title_box: Box | None = None

    @property
    def empty(self) -> bool:
        return self.bg_element is None and self.bg_blob is None and not self.decor


def template_skin(samples: list[Any], profile: JsonDict, layout_id: str | None = None) -> Skin:
    """Кожа по слайдам образцов (`samples` — слайды шаблона в исходном порядке)."""
    donors: list[tuple[JsonDict, Any]] = []
    for pattern in content_samples(profile):
        source = pattern.get("source") or {}
        index = int(source.get("slide_index") or 0)
        if not 1 <= index <= len(samples):
            continue
        if layout_id and source.get("layout_id") and source.get("layout_id") != layout_id:
            continue
        donors.append((pattern, samples[index - 1]))
    skin = Skin()
    if not donors:
        return skin
    need = max(2, round(len(donors) * SHARE))
    _read_background(skin, donors, need)
    _read_decor(skin, donors, need, profile)
    _read_title(skin, donors)
    return skin


def _read_background(skin: Skin, donors: list[tuple[JsonDict, Any]], need: int) -> None:
    keyed: dict[str, tuple[Any, bytes | None]] = {}
    counts: Counter[str] = Counter()
    for _pattern, slide in donors:
        bg = slide._element.find(f"{P_CSLD}/{P_BG}")
        if bg is None:
            continue
        blob = _bg_blob(bg, slide)
        if bg.find(f".//{{{NS_A}}}blip") is not None and blob is None:
            continue
        key = hashlib.sha256(blob).hexdigest() if blob is not None else _xml_key(bg)
        keyed.setdefault(key, (bg, blob))
        counts[key] += 1
    if not counts:
        return
    key, count = counts.most_common(1)[0]
    if count < need and count * 2 <= len(donors):
        return
    bg, blob = keyed[key]
    if blob is not None:
        skin.bg_blob = blob
    else:
        skin.bg_element = copy.deepcopy(bg)
        colors = [
            f"#{c.get('val', '').upper()}"
            for c in bg.iter(f"{{{NS_A}}}srgbClr")
            if len(c.get("val", "")) == 6
        ]
        if colors:
            skin.background = _mean(colors)


def _bg_blob(bg: Any, slide: Any) -> bytes | None:
    blip = bg.find(f".//{{{NS_A}}}blip")
    if blip is None:
        return None
    rid = blip.get(f"{{{NS_R}}}embed")
    try:
        return bytes(slide.part.related_part(rid).blob) if rid else None
    except (KeyError, AttributeError):
        return None


def _xml_key(element: Any) -> str:
    from lxml import etree

    return hashlib.sha256(etree.tostring(element, method="c14n")).hexdigest()


def _read_decor(
    skin: Skin, donors: list[tuple[JsonDict, Any]], need: int, profile: JsonDict
) -> None:
    size = profile.get("slide_size") or {}
    width = float(size.get("width_emu") or 12192000)
    height = float(size.get("height_emu") or 6858000)
    seen: dict[str, tuple[Any, Box]] = {}
    counts: Counter[str] = Counter()
    for pattern, slide in donors:
        static = {str(i) for i in pattern.get("static_object_ids") or []}
        slot_refs = {str(s.get("element_ref")) for s in pattern.get("slots") or []}
        keys: set[str] = set()
        for element in slide.shapes._spTree:
            tag = element.tag.split("}")[-1]
            if tag not in DECOR_TAGS:
                continue
            cnvpr = element.find(f".//{CNVPR}")
            shape_id = cnvpr.get("id") if cnvpr is not None else ""
            if shape_id in slot_refs or (static and shape_id not in static):
                continue
            if _has_text(element) or element.find(f".//{{{NS_P}}}ph") is not None:
                continue
            if element.find(f".//{{{NS_A}}}blip") is not None:
                continue
            box = _box(element, width, height)
            if box is None:
                continue
            key = _decor_key(element, box)
            if key in keys:
                continue
            keys.add(key)
            seen.setdefault(key, (element, box))
            counts[key] += 1
    skin.decor = [
        (copy.deepcopy(seen[key][0]), seen[key][1])
        for key, count in counts.items()
        if count >= need
    ]


def _read_title(skin: Skin, donors: list[tuple[JsonDict, Any]]) -> None:
    boxes: Counter[Box] = Counter()
    for pattern, _slide in donors:
        for slot in pattern.get("slots") or []:
            if slot.get("kind") != "title":
                continue
            bbox = slot.get("bbox") or {}
            boxes[
                (
                    round(float(bbox.get("x", 0)), 3),
                    round(float(bbox.get("y", 0)), 3),
                    round(float(bbox.get("width", 0)), 3),
                    round(float(bbox.get("height", 0)), 3),
                )
            ] += 1
            break
    if boxes:
        skin.title_box = boxes.most_common(1)[0][0]


def _has_text(element: Any) -> bool:
    return any((t.text or "").strip() for t in element.iter(f"{{{NS_A}}}t"))


def _box(element: Any, width: float, height: float) -> Box | None:
    off = element.find(f".//{{{NS_A}}}xfrm/{{{NS_A}}}off")
    ext = element.find(f".//{{{NS_A}}}xfrm/{{{NS_A}}}ext")
    if off is None or ext is None:
        return None
    return (
        int(off.get("x", 0)) / width,
        int(off.get("y", 0)) / height,
        int(ext.get("cx", 0)) / width,
        int(ext.get("cy", 0)) / height,
    )


def _decor_key(element: Any, box: Box) -> str:
    """Положение, форма и цвета фигуры: имя и id у копий на разных слайдах свои."""
    geom = element.find(f".//{{{NS_A}}}prstGeom")
    colors = [c.get("val", "") for c in element.iter(f"{{{NS_A}}}srgbClr")]
    place = tuple(round(v * GRID) for v in box)
    return f"{element.tag}|{place}|{geom.get('prst') if geom is not None else ''}|{colors}"


def _mean(colors: list[str]) -> str:
    rgb = [tuple(int(c[i : i + 2], 16) for i in (1, 3, 5)) for c in colors]
    return "#{:02X}{:02X}{:02X}".format(
        *(round(sum(ch) / len(rgb)) for ch in zip(*rgb, strict=True))
    )


def overlaps(a: Box, b: Box, pad: float = 0.0) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw + pad and bx < ax + aw + pad and ay < by + bh + pad and by < ay + ah + pad


def lift_title(title: Box, skin: Skin, blocked: list[Box]) -> Box | None:
    """Место заголовка композиции, не задевающее декор: исходное, если декор не мешает; иначе
    заголовок поднимается к верху заголовка образцов с той же высотой. None — не помещается."""
    if not any(overlaps(title, box) for box in blocked):
        return title
    if skin.title_box is None:
        return None
    x, _y, w, h = title
    top = skin.title_box[1]
    moved = (x, top, w, h)
    if any(overlaps(moved, box, GAP) for box in blocked):
        return None
    return moved


__all__ = ["Skin", "lift_title", "overlaps", "template_skin"]
