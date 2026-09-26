"""Рабочий пакет результата: макеты, плейсхолдеры, номера слайдов, очистка.

Рабочая основа варианта — сам исходный PPTX, открытый python-pptx: темы, мастера, макеты и
ресурсы остаются, новые слайды добавляются клонированием образцов, образцы удаляются в конце.
При сохранении python-pptx записывает только части, достижимые от корня пакета, поэтому
ресурсы удалённых образцов в файл не попадают, а общие части (макеты, мастера, шрифты) —
остаются. Удаление неиспользуемых макетов — отдельная настройка: оно уменьшает файл, но
меняет набор макетов, доступный пользователю в PowerPoint.
"""

from __future__ import annotations

import hashlib
import io
from copy import deepcopy
from typing import Any

from PIL import Image

from presentation_designer.layout.ooxml import _REL_ATTRS, NS_A, NS_P, NS_R, _remap_rel_ids
from presentation_designer.layout.shapes import (
    NS,
    element_box_absolute,
    next_shape_id,
    remove_shape,
    set_element_box,
    shape_element,
    sp_tree,
)
from presentation_designer.layout.text import set_field_text

# Начало p:spTree до фигур: свойства самой группы-дерева.
_TREE_HEAD = {"nvGrpSpPr", "grpSpPr"}


def layout_stem(layout: Any) -> str:
    """`ppt/slideLayouts/slideLayout3.xml` → `slideLayout3` (идентификатор макета в профиле)."""
    return str(layout.part.partname).rsplit("/", 1)[-1].rsplit(".", 1)[0]


def layout_by_id(prs: Any, layout_id: str) -> Any | None:
    for master in prs.slide_masters:
        for layout in master.slide_layouts:
            if layout_stem(layout) == layout_id:
                return layout
    return None


def ensure_placeholder(slide: Any, layout: Any, layout_shape_id: str) -> Any | None:
    """Плейсхолдер слайда, соответствующий плейсхолдеру макета с данным id: по `idx`.
    Если на слайде его нет, клонируется из макета (как делает python-pptx при добавлении
    слайда)."""
    layout_ph = next(
        (p for p in layout.placeholders if str(p.shape_id) == str(layout_shape_id)), None
    )
    if layout_ph is None:
        return None
    idx = layout_ph.placeholder_format.idx
    for ph in slide.placeholders:
        if ph.placeholder_format.idx == idx:
            return ph._element
    slide.shapes.clone_placeholder(layout_ph)
    for ph in slide.placeholders:
        if ph.placeholder_format.idx == idx:
            return ph._element
    return None


def update_slide_numbers(prs: Any) -> int:
    """Подставляет актуальный номер в поля `a:fld type="slidenum"` на слайдах."""
    updated = 0
    for index, slide in enumerate(prs.slides, start=1):
        updated += set_field_text(slide.shapes._spTree, "slidenum", str(index))
    return updated


def used_layout_partnames(prs: Any) -> set[str]:
    return {str(slide.slide_layout.part.partname) for slide in prs.slides}


def prune_unused_layouts(prs: Any) -> int:
    """Удаляет макеты, которые не использует ни один слайд; у мастера остаётся хотя бы один
    макет. Возвращает число удалённых."""
    used = used_layout_partnames(prs)
    removed = 0
    for master in prs.slide_masters:
        layouts = list(master.slide_layouts)
        keep_one = layouts[0] if layouts else None
        for layout in layouts:
            if str(layout.part.partname) in used:
                continue
            if layout is keep_one and not any(str(lay.part.partname) in used for lay in layouts):
                continue
            master.slide_layouts.remove(layout)
            removed += 1
    return removed


def slide_part_name(slide: Any) -> str:
    return str(slide.part.partname).lstrip("/")


def section_list(prs: Any) -> Any | None:
    return prs.part._element.find(f".//{{{NS_P}}}sectionLst")


__all__ = [
    "ensure_placeholder",
    "layout_by_id",
    "layout_stem",
    "prune_unused_layouts",
    "slide_part_name",
    "update_slide_numbers",
    "used_layout_partnames",
]


def drop_template_logos(prs: Any, profile: dict[str, Any]) -> int:
    """Снимает знак шаблона со всех макетов, мастеров и слайдов колоды.

    Логотип шаблона живёт не на слайде, а на макете: в колоде объекта с ним нет, и правкой
    слайда его не убрать. Другому подразделению чужой знак делает шаблон непригодным, поэтому
    снимается он целиком по профилю (`fixed_elements` вида `logo`): часть пакета и id объекта
    известны, остальное — обычное удаление фигуры со снятием осиротевших связей.
    """
    by_part: dict[str, set[str]] = {}
    for item in profile.get("fixed_elements") or []:
        if str(item.get("kind")) != "logo" or not item.get("element_ref"):
            continue
        by_part.setdefault(str(item.get("source_part") or ""), set()).add(str(item["element_ref"]))
    if not by_part:
        return 0
    everywhere = {ref for refs in by_part.values() for ref in refs}
    removed = 0

    def strip(container: Any, refs: set[str]) -> None:
        nonlocal removed
        for ref in sorted(refs):
            element = shape_element(container, ref)
            if element is None:
                continue
            # Декор с приметами знака (место, размер) остаётся: см. `logo_like`.
            if element.tag == f"{{{NS_P}}}pic" and not logo_like(picture_blob(container, element)):
                continue
            removed += len(remove_shape(container, element))

    for master in prs.slide_masters:
        strip(master, by_part.get(str(master.part.partname).lstrip("/"), set()))
        for layout in master.slide_layouts:
            strip(layout, by_part.get(str(layout.part.partname).lstrip("/"), set()))
    # Клон образца сохраняет id объектов, поэтому логотип, попавший на слайд, снимается тоже.
    for slide in prs.slides:
        strip(slide, everywhere)
    return removed


def logo_hashes(profile: dict[str, Any] | None) -> set[str]:
    """sha256 картинок знака шаблона по профилю: только логотипы, закреплённые на макетах и
    образцах (`fixed_elements`). Логотипы на слайдах — партнёры, соцсети — чужие знаки, их
    замена логотипа шаблона не касается."""
    if not profile:
        return set()
    wanted = {
        str(f.get("asset_id"))
        for f in profile.get("fixed_elements") or []
        if f.get("kind") == "logo" and f.get("asset_id")
    }
    return {
        str(a["sha256"])
        for a in profile.get("assets") or []
        if a.get("sha256") and a.get("asset_id") in wanted
    }


def picture_blob(container: Any, pic: Any) -> bytes | None:
    """Байты картинки `p:pic` (растровая `a:blip`, у SVG — её PNG-замена)."""
    blip = pic.find(f".//{{{NS_A}}}blip")
    rid = blip.get(f"{{{NS_R}}}embed") if blip is not None else None
    if not rid or rid not in container.part.rels:
        return None
    rel = container.part.rels[rid]
    return None if rel.is_external else rel.target_part.blob


def logo_like(blob: bytes | None) -> bool:
    """Знак — картинка с прозрачным фоном: он лежит поверх любого фона шаблона. По месту и
    размеру анализатор относит к логотипам и мелкий декор макетов, а у VK WorkSpace это
    непрозрачные вырезки фона (точки, полосы; 0 % прозрачных пикселей), тогда как у знаков
    VK Tech, VK Education и VK WorkSpace прозрачно 56–69 % картинки, у квадрата VK — углы (4 %)."""
    if not blob:
        return False
    try:
        with Image.open(io.BytesIO(blob)) as raw:
            if "A" not in raw.getbands() and "transparency" not in raw.info:
                return False
            alpha = raw.convert("RGBA").getchannel("A")
    except (OSError, SyntaxError, ValueError):
        return False
    hist = alpha.histogram()
    return sum(hist[:16]) >= 0.02 * max(1, sum(hist))


Box = tuple[int, int, int, int]  # x, y, ширина, высота в EMU
_LEAVES = {"sp", "pic", "cxnSp", "graphicFrame"}
_FILLS = ("a:solidFill", "a:gradFill", "a:blipFill", "a:pattFill")


def _opaque(el: Any) -> bool:
    """Фигура что-то закрывает собой: картинка, таблица, диаграмма или фигура с заливкой
    (своей или из стиля). Пустая рамка без заливки — прозрачная (у VK Tech такая лежит поверх
    знака на макетах 4 и 11)."""
    if el.tag.split("}")[-1] in ("pic", "graphicFrame"):
        return True
    sp_pr = el.find("p:spPr", NS)
    if sp_pr is not None:
        if sp_pr.find("a:noFill", NS) is not None:
            return False
        if any(sp_pr.find(tag, NS) is not None for tag in _FILLS):
            return True
    ref = el.find("p:style/a:fillRef", NS)
    return ref is not None and ref.get("idx", "0") != "0"


def _drawn(container: Any) -> list[tuple[Any, Box]]:
    """Непрозрачные фигуры образца или макета в порядке рисования (снизу вверх) с рамками на
    слайде. Плейсхолдеры пропускаются: на слайдах они не рисуются."""
    out = []
    for el in sp_tree(container).iter():
        if el.tag.split("}")[-1] not in _LEAVES or el.find("./*/p:nvPr/p:ph", NS) is not None:
            continue
        if not _opaque(el):
            continue
        box = element_box_absolute(el)
        if box is not None:
            out.append((el, box))
    return out


def _covered(box: Box, above: list[Box]) -> bool:
    """Картинку почти целиком (от 90 %) закрывает фигура, нарисованная поверх неё: знак
    спрятан. Снять его с образца можно и на других макетах — там он так же не виден."""
    x, y, w, h = box
    for ox, oy, ow, oh in above:
        iw = min(x + w, ox + ow) - max(x, ox)
        ih = min(y + h, oy + oh) - max(y, oy)
        if iw > 0 and ih > 0 and iw * ih >= 0.9 * w * h:
            return True
    return False


def promote_logos(prs: Any, hashes: set[str], reserved: dict[Any, list[str]] | None = None) -> int:
    """Знак шаблона с образцов и макетов переносится на сами слайды обычными картинками.

    На макете знак виден на каждом слайде, но в редакторе его не выделить: ни сдвинуть, ни
    уменьшить, ни удалить на одном слайде. Картинки с хешами `hashes`, похожие на знак
    (`logo_like`), копируются на каждый слайд, где они видны, в той же рамке и под всеми
    фигурами слайда (как и рисовался макет), а с образцов и макетов снимаются, иначе знак
    удвоится. Знак, который макет прячет под своей фигурой (VK WorkSpace закрывает тёмный
    знак образца чёрными плашками), остаётся на месте: на слайде он оказался бы поверх них.
    Слайд, добавленный потом кнопкой редактора, знака не получит. `reserved` — занятые id по
    части слайда (удалённые сборкой объекты): новые картинки их не берут. Возвращает число
    картинок, поставленных на слайды."""
    wanted = {h.split(":", 1)[-1] for h in hashes}
    if not wanted:
        return 0
    verdicts: dict[str, bool] = {}
    drawn: dict[Any, list[tuple[Any, Box]]] = {}
    found: dict[Any, list[tuple[Any, Any, Box]]] = {}
    for master in prs.slide_masters:
        for container in (master, *master.slide_layouts):
            drawn[container.part] = _drawn(container)
            for pic, box in drawn[container.part]:
                if pic.tag != f"{{{NS_P}}}pic":
                    continue
                blob = picture_blob(container, pic)
                sha = hashlib.sha256(blob).hexdigest() if blob else ""
                if sha not in wanted:
                    continue
                if sha not in verdicts:
                    verdicts[sha] = logo_like(blob)
                if verdicts[sha]:
                    found.setdefault(container.part, []).append((container, pic, box))
    if not found:
        return 0
    marks = {id(pic) for items in found.values() for _, pic, _ in items}

    def above(part: Any, pic: Any | None) -> list[Box]:
        """Рамки фигур части `part`, нарисованных поверх `pic` (все — при None), без знаков:
        знаки переносятся вместе и сохраняют порядок между собой."""
        shapes = drawn[part]
        start = next((i + 1 for i, (el, _) in enumerate(shapes) if el is pic), 0)
        return [box for el, box in shapes[start:] if id(el) not in marks]

    by_layout: dict[Any, list[tuple[Any, Any, Box]]] = {}

    def sources(layout: Any) -> list[tuple[Any, Any, Box]]:
        if layout.part not in by_layout:
            out = []
            if layout._element.get("showMasterSp") != "0":
                master = layout.slide_master.part
                cover = above(layout.part, None)
                out = [
                    item
                    for item in found.get(master, [])
                    if not _covered(item[2], [*above(master, item[1]), *cover])
                ]
            out += [
                item
                for item in found.get(layout.part, [])
                if not _covered(item[2], above(layout.part, item[1]))
            ]
            by_layout[layout.part] = out
        return by_layout[layout.part]

    placed = 0
    moved: dict[int, tuple[Any, Any]] = {}
    for slide in prs.slides:
        if slide._element.get("showMasterSp") == "0":
            continue
        tree = sp_tree(slide)
        at = next(
            (i for i, child in enumerate(tree) if child.tag.split("}")[-1] not in _TREE_HEAD),
            len(tree),
        )
        taken = [int(i) for i in (reserved or {}).get(slide.part, []) if str(i).isdigit()]
        next_id = max(next_shape_id(slide), max(taken, default=0) + 1)
        # Экспорт из Google Slides кладёт знак дважды в одну рамку (VK Tech): на слайде нужен
        # один, иначе сдвинутый знак оставляет под собой такой же.
        seen: set[tuple[bytes | None, Box]] = set()
        for container, pic, box in sources(slide.slide_layout):
            moved[id(pic)] = (container, pic)
            key = (picture_blob(container, pic), box)
            if key in seen:
                continue
            seen.add(key)
            copy = deepcopy(pic)
            set_element_box(copy, box)
            mapping: dict[str, str] = {}
            for node in copy.iter():
                for name in _REL_ATTRS:
                    rid = node.get(f"{{{NS_R}}}{name}")
                    if rid is None or rid in mapping:
                        continue
                    rel = container.part.rels.get(rid)
                    if rel is None:
                        mapping[rid] = ""
                    elif rel.is_external:
                        mapping[rid] = slide.part.relate_to(
                            rel.target_ref, rel.reltype, is_external=True
                        )
                    else:
                        mapping[rid] = slide.part.relate_to(rel.target_part, rel.reltype)
            _remap_rel_ids(copy, mapping)
            copy.find("p:nvPicPr/p:cNvPr", NS).set("id", str(next_id))
            next_id += 1
            tree.insert(at, copy)
            at += 1
            placed += 1
    # Снимаются только перенесённые: спрятанный или ни на одном слайде не видный знак остаётся.
    for container, pic in moved.values():
        remove_shape(container, pic)
    return placed
