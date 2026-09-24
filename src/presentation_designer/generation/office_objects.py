"""Revision-local selectable PPTX objects; no reconstruction of the presentation.

Объекты берутся тем же обходом фигур, что анализ шаблона и ComposedDeck
(`parsing/template/geometry.walk_shapes`): фигуры внутри групп (адрес — путь групп), рамка
плейсхолдера из макета, повёрнутые фигуры (рамка без поворота), соединители. Номера текстовых
узлов `a:t` — сквозные по слайду, как у текстовой правки (`office_edit.patch_pptx`).
"""

from __future__ import annotations

import io
from zipfile import ZipFile

from lxml import etree
from pptx import Presentation
from pydantic import BaseModel, ConfigDict, Field

from presentation_designer.generation.office_edit import NS
from presentation_designer.parsing.template.geometry import walk_shapes

A_T = f"{{{NS['a']}}}t"
SHAPE_TAGS = {"sp", "pic", "graphicFrame", "grpSp", "cxnSp"}


class ObjectTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    slide: int = Field(ge=1)
    shape_id: str = Field(pattern=r"^[0-9]+$", max_length=20)


class Bounds(BaseModel):
    x: float
    y: float
    width: float
    height: float


class SlideObject(ObjectTarget):
    label: str
    # Имя фигуры (`cNvPr name`): по нему живой редактор ONLYOFFICE сообщает, что выделено.
    name: str = ""
    kind: str
    bbox: Bounds
    z: int
    hollow: bool
    runs: list[int]
    # Идентификаторы групп от внешней к внутренней; пусто — объект верхнего уровня.
    group_path: list[str] = Field(default_factory=list)
    rotation: float = 0.0
    # Тип плейсхолдера (title, body, sldNum…); рамка у плейсхолдера без своей — из макета.
    placeholder: str | None = None


def xml(data: bytes) -> etree._Element:
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))


def geometry(shape: etree._Element) -> etree._Element | None:
    tag = etree.QName(shape).localname
    if tag == "graphicFrame":
        return shape.find("p:xfrm", NS)
    if tag == "grpSp":
        return shape.find("p:grpSpPr/a:xfrm", NS)
    return shape.find("p:spPr/a:xfrm", NS)


def shape_element(root: etree._Element, shape_id: str) -> etree._Element | None:
    """Фигура слайда по `cNvPr id` на любой глубине групп."""
    tree = root.find("p:cSld/p:spTree", NS)
    if tree is None:
        return None
    for element in tree.iter():
        if not isinstance(element.tag, str) or etree.QName(element).localname not in SHAPE_TAGS:
            continue
        identity = element.find("./*/p:cNvPr", NS)
        if identity is not None and identity.get("id") == shape_id:
            return element
    return None


def objects(data: bytes) -> list[SlideObject]:
    prs = Presentation(io.BytesIO(data))
    width, height = int(prs.slide_width or 0), int(prs.slide_height or 0)
    if min(width, height) <= 0:
        raise ValueError("Некорректный размер слайда")
    result = []
    for number, slide in enumerate(prs.slides, 1):
        runs = {node: n for n, node in enumerate(slide._element.iter(A_T))}
        for z, info in enumerate(walk_shapes(slide, slide.part, width, height)):
            if info.width <= 0 and info.height <= 0:
                continue
            element = info.element
            own = [runs[node] for node in element.iter(A_T) if node in runs]
            text = " ".join((node.text or "") for node in element.iter(A_T) if node in runs).strip()
            kind = etree.QName(element).localname
            result.append(
                SlideObject(
                    slide=number,
                    shape_id=info.element_id,
                    label=(text or info.name or "Объект")[:160],
                    name=info.name,
                    kind=kind,
                    bbox=Bounds(x=info.x, y=info.y, width=info.width, height=info.height),
                    z=z,
                    hollow=not text
                    and kind == "sp"
                    and element.find("p:spPr/a:noFill", NS) is not None,
                    runs=own,
                    group_path=list(info.group_path),
                    rotation=round(info.rotation_deg, 2),
                    placeholder=info.placeholder_type,
                )
            )
    return result


def selected(data: bytes, target: ObjectTarget) -> SlideObject:
    matches = [
        obj
        for obj in objects(data)
        if obj.slide == target.slide and obj.shape_id == target.shape_id
    ]
    if len(matches) != 1:
        raise ValueError(
            "Объект не найден или не поддерживает адресную правку; выберите его заново"
        )
    return matches[0]


EMU_PER_MM = 36000


class LiveBox(BaseModel):
    """Положение и размер фигуры в миллиметрах, как их отдаёт ONLYOFFICE."""

    model_config = ConfigDict(extra="forbid")
    x: float
    y: float
    width: float = Field(ge=0)
    height: float = Field(ge=0)


class LiveTarget(BaseModel):
    """Объект, выделенный в живом редакторе: номер его фигуры ONLYOFFICE наружу не отдаёт,
    поэтому он опознаётся по слайду, имени и положению в сохранённой копии. У фигуры внутри
    группы (`in_group`) положение редактор отдаёт от левого верхнего угла группы."""

    model_config = ConfigDict(extra="forbid")
    slide: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=255)
    box: LiveBox | None = None
    in_group: bool = False


def resolve_live(data: bytes, live: LiveTarget) -> ObjectTarget:
    """Фигура сохранённой копии по выделению живого редактора: с тем же именем на том же
    слайде, в группах тоже; при нескольких — ближайшая по положению."""
    on_slide = [obj for obj in objects(data) if obj.slide == live.slide]
    found = [obj for obj in on_slide if obj.name == live.name]
    if not found:
        raise ValueError(
            "выбранный объект не найден в сохранённой презентации или не поддерживает "
            "адресную правку — выделите его заново"
        )
    if len(found) > 1:
        # Сначала те, что там же по отношению к группе, что и выделение.
        found.sort(key=lambda obj: bool(obj.group_path) != live.in_group)
        if live.box is not None:
            with ZipFile(io.BytesIO(data)) as archive:
                size = xml(archive.read("ppt/presentation.xml")).find("p:sldSz", NS)
            if size is not None:
                width = int(size.attrib["cx"]) / EMU_PER_MM
                height = int(size.attrib["cy"]) / EMU_PER_MM
                groups = {obj.shape_id: obj for obj in on_slide if obj.kind == "grpSp"}
                box = live.box

                def distance(obj: SlideObject) -> tuple[bool, float]:
                    x, y = obj.bbox.x, obj.bbox.y
                    parent = groups.get(obj.group_path[-1]) if obj.group_path else None
                    if live.in_group and parent is not None:
                        x, y = x - parent.bbox.x, y - parent.bbox.y
                    return (
                        bool(obj.group_path) != live.in_group,
                        abs(x * width - box.x)
                        + abs(y * height - box.y)
                        + abs(obj.bbox.width * width - box.width)
                        + abs(obj.bbox.height * height - box.height),
                    )

                found.sort(key=distance)
    return ObjectTarget(slide=found[0].slide, shape_id=found[0].shape_id)
