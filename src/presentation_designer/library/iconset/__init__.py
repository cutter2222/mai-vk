"""Набор векторных иконок для собственных композиций и слотов `icon.query`.

Иконки — Lucide (лицензия ISC, `LICENSE-lucide`): 2000+ контурных пиктограмм на сетке 24 с
английскими тегами. На слайд иконка ставится одной фигурой `a:custGeom` в цвет дизайн-кода
(`geometry.py`), поэтому её можно перекрасить, сдвинуть или удалить в редакторе.

Подбор по тексту (`find_icon`): точное имя; английские слова — по имени и тегам; русские — по
основам из `ru.yaml` (словарь «основа слова → иконка»). Ничего не нашлось — None: вызывающий
ставит номер вместо случайной картинки.
"""

from __future__ import annotations

import functools
import gzip
import json
import pathlib
import re
from typing import Any

import yaml

from presentation_designer.library.iconset.geometry import GRID, custgeom_xml

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "lucide.json.gz"
RU = HERE / "ru.yaml"
# Толщина обводки Lucide — 2 на сетке 24.
STROKE = 2.0 / GRID

_WORD = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


@functools.cache
def _data() -> dict[str, Any]:
    with gzip.open(DATA, "rb") as f:
        return json.loads(f.read())  # type: ignore[no-any-return]


def icon_names() -> list[str]:
    return list(_data()["icons"])


def icon_nodes(name: str) -> list[list[Any]] | None:
    icon = _data()["icons"].get(name)
    return icon["n"] if icon else None


@functools.cache
def _ru() -> list[tuple[str, str]]:
    """(основа, иконка) по убыванию длины основы: «безопасн» раньше «без»."""
    raw = yaml.safe_load(RU.read_text(encoding="utf-8")) or {}
    names = set(icon_names())
    pairs = [
        (str(stem).lower(), str(icon))
        for icon, stems in raw.items()
        if icon in names
        for stem in stems or []
    ]
    return sorted(pairs, key=lambda p: -len(p[0]))


@functools.cache
def _index() -> dict[str, dict[str, float]]:
    """Английское слово → {иконка: вес}: слово имени весит больше тега."""
    out: dict[str, dict[str, float]] = {}
    for name, icon in _data()["icons"].items():
        parts = name.split("-")
        for i, part in enumerate(parts):
            weight = 3.0 if i == 0 else 2.0
            bucket = out.setdefault(part, {})
            bucket[name] = max(bucket.get(name, 0.0), weight)
        for tag in icon.get("t") or []:
            for word in _WORD.findall(str(tag).lower()):
                bucket = out.setdefault(word, {})
                bucket[name] = max(bucket.get(name, 0.0), 1.0)
    return out


def _russian(word: str) -> str | None:
    for stem, icon in _ru():
        if word.startswith(stem):
            return icon
    return None


def find_icon(text: str | None, *, exclude: set[str] | None = None) -> str | None:
    """Иконка по имени, английскому запросу или русскому тексту (заголовку карточки)."""
    if not text:
        return None
    exclude = exclude or set()
    names = _data()["icons"]
    query = str(text).strip().lower()
    if query in names and query not in exclude:
        return query
    words = _WORD.findall(query)
    # Русский текст: первое слово с известной основой; заголовок карточки обычно начинается
    # с главного слова («Безопасность данных», «Рост выручки»).
    for word in words:
        if re.search("[а-яё]", word):
            icon = _russian(word)
            if icon and icon not in exclude:
                return icon
    if re.search("[а-яё]", query):
        # Русский текст без знакомой основы: английские теги по обрывкам («IT», «SLA»)
        # дают случайную картинку — лучше номер.
        return None
    index = _index()
    scores: dict[str, float] = {}
    for word in words:
        if len(word) < 3:
            continue
        for name, weight in (index.get(word) or {}).items():
            scores[name] = scores.get(name, 0.0) + weight
    ranked = sorted(
        (item for item in scores.items() if item[0] not in exclude),
        key=lambda item: (-item[1], len(item[0])),
    )
    return ranked[0][0] if ranked else None


def pick_icons(texts: list[str], fallback: list[str] | None = None) -> list[str | None]:
    """Иконки для ряда карточек без повторов: одинаковые значки в соседних карточках
    читаются как ошибка вёрстки."""
    used: set[str] = set()
    out: list[str | None] = []
    for text in texts:
        icon = find_icon(text, exclude=used)
        if icon:
            used.add(icon)
        out.append(icon)
    return out


def add_icon(
    slide: Any,
    name: str,
    left: int,
    top: int,
    size: int,
    color: str,
    *,
    weight: float = 1.0,
) -> Any:
    """Векторная иконка на слайде: квадрат `size` EMU, обводка и заливка цветом `color`."""
    from lxml import etree
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Emu

    nodes = icon_nodes(name)
    if nodes is None:
        raise KeyError(name)
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Emu(left), Emu(top), Emu(size), Emu(size))
    element = shape._element
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    sp_pr = element.spPr
    prst = sp_pr.find("a:prstGeom", ns)
    geom = etree.fromstring(custgeom_xml(nodes))
    if prst is not None:
        prst.addprevious(geom)
        sp_pr.remove(prst)
    else:
        sp_pr.append(geom)
    style = element.find("{http://schemas.openxmlformats.org/presentationml/2006/main}style")
    if style is not None:
        element.remove(style)
    hex_color = color.lstrip("#").upper()
    width = max(3175, round(size * STROKE * weight))
    for tag in ("a:solidFill", "a:noFill", "a:ln", "a:effectLst"):
        for old in sp_pr.findall(tag, ns):
            sp_pr.remove(old)
    fill = etree.SubElement(sp_pr, f"{{{ns['a']}}}solidFill")
    etree.SubElement(fill, f"{{{ns['a']}}}srgbClr", val=hex_color)
    line = etree.SubElement(sp_pr, f"{{{ns['a']}}}ln", w=str(width), cap="rnd")
    line_fill = etree.SubElement(line, f"{{{ns['a']}}}solidFill")
    etree.SubElement(line_fill, f"{{{ns['a']}}}srgbClr", val=hex_color)
    etree.SubElement(line, f"{{{ns['a']}}}round")
    etree.SubElement(sp_pr, f"{{{ns['a']}}}effectLst")
    shape.name = f"Icon {name}"
    return shape


__all__ = ["add_icon", "find_icon", "icon_names", "icon_nodes", "pick_icons"]
