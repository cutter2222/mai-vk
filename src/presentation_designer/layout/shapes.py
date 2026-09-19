"""Поиск и правка объектов слайда по идентификатору `p:cNvPr@id`: слоты профиля ссылаются
на объекты образца именно так, вложенность в группы при этом не важна.

Здесь только операции над деревом фигур: найти элемент, обойти дерево с прокси python-pptx,
удалить объект (с чисткой опустевших групп и осиротевших связей), выдать свободный
идентификатор, перевести доли слайда в EMU.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from lxml import etree
from pptx.opc.constants import RELATIONSHIP_TYPE as RT

from presentation_designer.layout.ooxml import _REL_ATTRS, NS_A, NS_P, NS_R

NS = {"a": NS_A, "p": NS_P, "r": NS_R}
CNVPR = f"{{{NS_P}}}cNvPr"
GRPSP = f"{{{NS_P}}}grpSp"
SPTREE = f"{{{NS_P}}}spTree"


def sp_tree(slide: Any) -> Any:
    return slide.shapes._spTree


def shape_element(slide: Any, element_id: str) -> Any | None:
    """Элемент фигуры (`p:sp`, `p:pic`, `p:graphicFrame`, `p:grpSp`, `p:cxnSp`) по id."""
    for cnvpr in sp_tree(slide).iter(CNVPR):
        if cnvpr.get("id") == str(element_id):
            return cnvpr.getparent().getparent()
    return None


def iter_shapes(container: Any) -> Iterator[Any]:
    """Прокси python-pptx для всех фигур слайда, включая вложенные в группы."""
    for shape in container.shapes:
        yield shape
        if shape._element.tag == GRPSP:
            yield from iter_shapes(shape)


def shape_map(slide: Any) -> dict[str, Any]:
    return {str(s.shape_id): s for s in iter_shapes(slide)}


def element_ids(slide: Any) -> list[str]:
    return [c.get("id", "") for c in sp_tree(slide).iter(CNVPR)]


def next_shape_id(slide: Any) -> int:
    ids = [int(i) for i in element_ids(slide) if i.isdigit()]
    return max(ids, default=0) + 1


def remove_shape(slide: Any, element: Any) -> list[str]:
    """Удаляет элемент из дерева; опустевшие группы удаляются следом, связи, на которые
    больше никто не ссылается, снимаются. Возвращает удалённые идентификаторы."""
    removed = [c.get("id", "") for c in element.iter(CNVPR)]
    parent = element.getparent()
    if parent is None:
        return []
    parent.remove(element)
    while parent is not None and parent.tag == GRPSP and not _has_shapes(parent):
        grand = parent.getparent()
        removed.extend(c.get("id", "") for c in parent.iter(CNVPR))
        if grand is not None:
            grand.remove(parent)
        parent = grand
    drop_unreferenced_rels(slide)
    return removed


def _has_shapes(group: Any) -> bool:
    return any(
        child.tag.split("}")[-1] in ("sp", "pic", "graphicFrame", "grpSp", "cxnSp")
        for child in group
    )


def referenced_rids(part: Any) -> set[str]:
    out: set[str] = set()
    for node in part._element.iter():
        for name in _REL_ATTRS:
            value = node.get(f"{{{NS_R}}}{name}")
            if value:
                out.add(value)
    return out


def drop_unreferenced_rels(slide: Any) -> list[str]:
    """Снимает связи слайда с картинками, диаграммами и схемами, на которые XML больше не
    ссылается; макет, заметки и прочие структурные связи не трогаются."""
    part = slide.part
    used = referenced_rids(part)
    dropped: list[str] = []
    for rid, rel in list(part.rels.items()):
        if rid in used or rel.is_external:
            continue
        if rel.reltype in (
            RT.IMAGE,
            RT.CHART,
            RT.DIAGRAM_DATA,
            RT.DIAGRAM_LAYOUT,
            RT.DIAGRAM_QUICK_STYLE,
            RT.DIAGRAM_COLORS,
            RT.OLE_OBJECT,
            RT.PACKAGE,
            RT.MEDIA,
            RT.VIDEO,
            RT.AUDIO,
            RT.HYPERLINK,
        ):
            part.rels.pop(rid)
            dropped.append(rid)
    return dropped


def emu_box(bbox: dict[str, float], slide_w: int, slide_h: int) -> tuple[int, int, int, int]:
    """bbox в долях слайда → (x, y, cx, cy) в EMU."""
    return (
        round(float(bbox.get("x", 0)) * slide_w),
        round(float(bbox.get("y", 0)) * slide_h),
        round(float(bbox.get("width", 0)) * slide_w),
        round(float(bbox.get("height", 0)) * slide_h),
    )


def element_box(element: Any) -> tuple[int, int, int, int] | None:
    """Координаты элемента из его собственного xfrm в EMU (без пересчёта групп)."""
    for path in ("p:spPr/a:xfrm", "p:grpSpPr/a:xfrm", "p:xfrm"):
        xfrm = element.find(path, NS)
        if xfrm is not None:
            off, ext = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
            if off is not None and ext is not None:
                return (
                    int(off.get("x", 0)),
                    int(off.get("y", 0)),
                    int(ext.get("cx", 0)),
                    int(ext.get("cy", 0)),
                )
    return None


def set_element_box(element: Any, box: tuple[int, int, int, int]) -> None:
    x, y, cx, cy = box
    for path in ("p:spPr/a:xfrm", "p:grpSpPr/a:xfrm", "p:xfrm"):
        xfrm = element.find(path, NS)
        if xfrm is not None:
            off, ext = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
            if off is None:
                off = etree.SubElement(xfrm, f"{{{NS_A}}}off")
            if ext is None:
                ext = etree.SubElement(xfrm, f"{{{NS_A}}}ext")
            off.set("x", str(x))
            off.set("y", str(y))
            ext.set("cx", str(cx))
            ext.set("cy", str(cy))
            return


def ensure_xfrm(element: Any) -> Any | None:
    """`a:xfrm` фигуры; у плейсхолдера без своих координат создаётся первым в `p:spPr`."""
    for path in ("p:spPr/a:xfrm", "p:grpSpPr/a:xfrm", "p:xfrm"):
        xfrm = element.find(path, NS)
        if xfrm is not None:
            return xfrm
    sppr = element.find("p:spPr", NS)
    if sppr is None:
        return None
    xfrm = etree.Element(f"{{{NS_A}}}xfrm")
    etree.SubElement(xfrm, f"{{{NS_A}}}off", x="0", y="0")
    etree.SubElement(xfrm, f"{{{NS_A}}}ext", cx="0", cy="0")
    sppr.insert(0, xfrm)
    return xfrm


def in_group(element: Any) -> bool:
    """Фигура внутри группы: её xfrm в координатах группы, а не слайда."""
    parent = element.getparent()
    return parent is not None and parent.tag == GRPSP


@dataclass
class GroupTransform:
    """Отображение координат детей группы в координаты слайда (без поворота группы), то же,
    что строит анализ (`geometry._Transform`)."""

    off_x: float = 0.0
    off_y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    ch_off_x: float = 0.0
    ch_off_y: float = 0.0

    def to_slide(self, x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
        return (
            self.off_x + (x - self.ch_off_x) * self.scale_x,
            self.off_y + (y - self.ch_off_y) * self.scale_y,
            w * self.scale_x,
            h * self.scale_y,
        )

    def to_local(self, x: float, y: float, w: float, h: float) -> tuple[float, float, float, float]:
        sx = self.scale_x or 1.0
        sy = self.scale_y or 1.0
        return (
            self.ch_off_x + (x - self.off_x) / sx,
            self.ch_off_y + (y - self.off_y) / sy,
            w / sx,
            h / sy,
        )

    def compose(self, xfrm: Any) -> GroupTransform:
        off = xfrm.find("a:off", NS)
        ext = xfrm.find("a:ext", NS)
        ch_off = xfrm.find("a:chOff", NS)
        ch_ext = xfrm.find("a:chExt", NS)
        if off is None or ext is None:
            return self
        gx, gy = float(off.get("x", 0)), float(off.get("y", 0))
        gw, gh = float(ext.get("cx", 0)), float(ext.get("cy", 0))
        cx = float(ch_off.get("x", 0)) if ch_off is not None else 0.0
        cy = float(ch_off.get("y", 0)) if ch_off is not None else 0.0
        cw = float(ch_ext.get("cx", 0)) if ch_ext is not None else gw
        ch = float(ch_ext.get("cy", 0)) if ch_ext is not None else gh
        sx = gw / cw if cw else 1.0
        sy = gh / ch if ch else 1.0
        px, py, pw, ph = self.to_slide(gx, gy, gw, gh)
        return GroupTransform(
            px, py, (pw / gw if gw else 1.0) * sx, (ph / gh if gh else 1.0) * sy, cx, cy
        )


def group_transform(element: Any) -> GroupTransform:
    """Преобразование координат самого элемента (его xfrm) в координаты слайда: композиция
    xfrm групп-предков от внешней к внутренней; для фигуры вне групп — тождественное."""
    chain: list[Any] = []
    parent = element.getparent()
    while parent is not None and parent.tag == GRPSP:
        chain.append(parent)
        parent = parent.getparent()
    transform = GroupTransform()
    for group in reversed(chain):
        xfrm = group.find("p:grpSpPr/a:xfrm", NS)
        if xfrm is not None:
            transform = transform.compose(xfrm)
    return transform


def slide_box_to_local(element: Any, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Рамка в координатах слайда (EMU) → координаты для собственного xfrm элемента."""
    x, y, cx, cy = group_transform(element).to_local(*box)
    return (round(x), round(y), round(cx), round(cy))


def set_element_box_absolute(element: Any, box: tuple[int, int, int, int]) -> bool:
    """Ставит элемент в рамку, заданную в координатах слайда, с учётом вложенности в группы.
    Возвращает False, если у элемента нет и не может быть xfrm."""
    if ensure_xfrm(element) is None:
        return False
    set_element_box(element, slide_box_to_local(element, box))
    return True


def element_box_absolute(element: Any) -> tuple[int, int, int, int] | None:
    """Рамка элемента в координатах слайда (EMU) с учётом групп."""
    own = element_box(element)
    if own is None:
        return None
    x, y, cx, cy = group_transform(element).to_slide(*own)
    return (round(x), round(y), round(cx), round(cy))


def connector_endpoints(
    info: Any,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Концы соединителя в долях слайда: противоположные углы bbox с учётом flipH/flipV."""
    x, y, w, h = info.x, info.y, info.width, info.height
    flip_h = flip_v = False
    xfrm = info.element.find("p:spPr/a:xfrm", NS) if info.element is not None else None
    if xfrm is not None:
        flip_h = xfrm.get("flipH") in ("1", "true")
        flip_v = xfrm.get("flipV") in ("1", "true")
    if flip_h != flip_v:
        return (x + w, y), (x, y + h)
    return (x, y), (x + w, y + h)


def connection_ids(element: Any) -> set[str]:
    """Идентификаторы фигур, к которым соединитель привязан явно (a:stCxn, a:endCxn)."""
    out: set[str] = set()
    for tag in ("stCxn", "endCxn"):
        node = element.find(f".//a:{tag}", NS)
        if node is not None and node.get("id"):
            out.add(str(node.get("id")))
    return out


def part_by_name(package: Any, partname: str) -> Any | None:
    """Часть пакета по имени вида `ppt/media/image3.png` (с ведущим слэшем или без)."""
    wanted = "/" + partname.lstrip("/")
    for part in package.iter_parts():
        if str(part.partname) == wanted:
            return part
    return None


def relative_partname(source: str, target: str) -> str:
    return posixpath.relpath(target, posixpath.dirname(source))


__all__ = [
    "CNVPR",
    "GRPSP",
    "NS",
    "GroupTransform",
    "drop_unreferenced_rels",
    "element_box",
    "element_box_absolute",
    "element_ids",
    "emu_box",
    "group_transform",
    "iter_shapes",
    "next_shape_id",
    "part_by_name",
    "referenced_rids",
    "remove_shape",
    "set_element_box",
    "set_element_box_absolute",
    "shape_element",
    "shape_map",
    "slide_box_to_local",
    "sp_tree",
]
