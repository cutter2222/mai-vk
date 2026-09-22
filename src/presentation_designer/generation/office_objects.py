"""Revision-local selectable PPTX objects; no reconstruction of the presentation.

Only top-level objects with explicit, unrotated geometry are exposed. Inherited
placeholders, groups and connectors need separate geometry handling and are omitted.
"""

from __future__ import annotations

import io
from zipfile import ZipFile

from lxml import etree
from pydantic import BaseModel, ConfigDict, Field

from presentation_designer.generation.office_edit import NS, slides


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
    kind: str
    bbox: Bounds
    z: int
    hollow: bool
    runs: list[int]


def xml(data: bytes) -> etree._Element:
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))


def geometry(shape: etree._Element) -> etree._Element | None:
    return (
        shape.find("p:xfrm", NS)
        if shape.tag == f"{{{NS['p']}}}graphicFrame"
        else shape.find("p:spPr/a:xfrm", NS)
    )


def objects(data: bytes) -> list[SlideObject]:
    result = []
    with ZipFile(io.BytesIO(data)) as archive:
        size = xml(archive.read("ppt/presentation.xml")).find("p:sldSz", NS)
        if size is None:
            raise ValueError("Не найден размер слайда")
        width, height = int(size.attrib["cx"]), int(size.attrib["cy"])
        if min(width, height) <= 0:
            raise ValueError("Некорректный размер слайда")
        for slide, name in enumerate(slides(archive), 1):
            root = xml(archive.read(name))
            tree = root.find("p:cSld/p:spTree", NS)
            if tree is None:
                continue
            runs = root.findall(".//a:t", NS)
            for z, shape in enumerate(tree):
                kind = etree.QName(shape).localname
                if kind not in {"sp", "pic", "graphicFrame"}:
                    continue
                transform = geometry(shape)
                identity = shape.find(".//p:cNvPr", NS)
                if (
                    transform is None
                    or identity is None
                    or int(transform.get("rot", "0")) % 21600000
                ):
                    continue
                offset, extent = transform.find("a:off", NS), transform.find("a:ext", NS)
                if offset is None or extent is None:
                    continue
                x, y = int(offset.attrib["x"]), int(offset.attrib["y"])
                w, h = int(extent.attrib["cx"]), int(extent.attrib["cy"])
                if min(w, h) <= 0 or min(x, y) < 0 or x + w > width or y + h > height:
                    continue
                own_runs = shape.findall(".//a:t", NS)
                text = " ".join(node.text or "" for node in own_runs).strip()
                result.append(
                    SlideObject(
                        slide=slide,
                        shape_id=identity.attrib["id"],
                        label=(text or identity.get("name") or "Объект")[:160],
                        kind=kind,
                        bbox=Bounds(x=x / width, y=y / height, width=w / width, height=h / height),
                        z=z,
                        hollow=not text
                        and kind == "sp"
                        and shape.find("p:spPr/a:noFill", NS) is not None,
                        runs=[n for n, node in enumerate(runs) if node in own_runs],
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
