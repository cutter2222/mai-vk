"""Read-only precision diagnostics for an editor PPTX round trip.

Addresses are structural (QName + same-tag sibling index), not persistent object
identities. Use this report to investigate a controlled edit, never to restore
values automatically: reordering objects can pair unrelated elements.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from zipfile import ZipFile

from lxml import etree

_DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PRESENTATION = "http://schemas.openxmlformats.org/presentationml/2006/main"
_ATTRIBUTES = {
    **{
        f"{{{_DRAWING}}}{tag}": ("geometry", attrs)
        for tag, attrs in {
            "off": ("x", "y"),
            "ext": ("cx", "cy"),
            "chOff": ("x", "y"),
            "chExt": ("cx", "cy"),
            "xfrm": ("rot",),
        }.items()
    },
    **{
        f"{{{_DRAWING}}}{tag}": ("typography", attrs)
        for tag, attrs in {
            "rPr": ("sz", "spc", "kern", "baseline"),
            "defRPr": ("sz", "spc", "kern", "baseline"),
            "endParaRPr": ("sz", "spc", "kern", "baseline"),
            "spcPts": ("val",),
            "spcPct": ("val",),
            "bodyPr": ("lIns", "rIns", "tIns", "bIns", "spcCol"),
            "pPr": ("marL", "marR", "indent", "defTabSz"),
            "tab": ("pos",),
        }.items()
    },
    f"{{{_PRESENTATION}}}sldSz": ("geometry", ("cx", "cy")),
}


def numeric_xml_deltas(before: Path, after: Path) -> list[dict[str, str]]:
    """Compare explicit numeric properties at shared XML addresses in all parts.

    Missing/added properties, inheritance, colors, fonts and relationships are
    outside this diagnostic. XML errors propagate; recovery is never enabled.
    """
    changes: list[dict[str, str]] = []
    with ZipFile(before) as old, ZipFile(after) as new:
        for part in sorted(set(old.namelist()) & set(new.namelist())):
            if not part.endswith(".xml") or old.read(part) == new.read(part):
                continue
            roots = [
                etree.fromstring(
                    archive.read(part), etree.XMLParser(resolve_entities=False, no_network=True)
                )
                for archive in (old, new)
            ]
            if roots[0].tag != roots[1].tag:
                continue
            trees = [root.getroottree() for root in roots]
            right = {trees[1].getelementpath(element): element for element in roots[1].iter()}
            for element in roots[0].iter():
                if element.tag not in _ATTRIBUTES:
                    continue
                path = trees[0].getelementpath(element)
                other = right.get(path)
                if other is None:
                    continue
                category, attributes = _ATTRIBUTES[element.tag]
                for attribute in attributes:
                    a, b = element.get(attribute), other.get(attribute)
                    if a is None or b is None or a == b:
                        continue
                    try:
                        x, y = Decimal(a), Decimal(b)
                    except InvalidOperation:
                        continue
                    if not (x.is_finite() and y.is_finite()) or x == y:
                        continue
                    changes.append(
                        {
                            "part": part,
                            "path": path,
                            "element": etree.QName(element).localname,
                            "attribute": attribute,
                            "category": category,
                            "before": a,
                            "after": b,
                            "delta": str(y - x),
                        }
                    )
    return changes
