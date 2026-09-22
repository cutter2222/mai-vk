"""Numeric diagnostics must expose precision loss without rewriting input."""

from zipfile import ZipFile

import pytest
from lxml import etree

from presentation_designer.audit.office_roundtrip import numeric_xml_deltas

DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/main"


def package(path, content, part="ppt/slides/slide1.xml"):
    with ZipFile(path, "w") as archive:
        archive.writestr(part, content)
    return path


@pytest.mark.parametrize("part", ["ppt/slides/slide31.xml", "ppt/slideMasters/slideMaster1.xml"])
def test_reports_geometry_font_and_spacing_deltas(tmp_path, part):
    content = (
        f'<a:root xmlns:a="{DRAWING}"><a:off x="210685" y="-210685"/>'
        '<a:rPr sz="1406"/><a:rPr sz="2800"/><a:spcPts val="1406"/></a:root>'
    )
    before = package(tmp_path / "before.pptx", content, part)
    after = package(
        tmp_path / "after.pptx",
        content.replace("210685", "210684")
        .replace('sz="1406"', 'sz="1400"')
        .replace('val="1406"', 'val="1405"'),
        part,
    )
    originals = before.read_bytes(), after.read_bytes()
    changes = numeric_xml_deltas(before, after)
    assert [(c["element"], c["attribute"], c["delta"]) for c in changes] == [
        ("off", "x", "-1"),
        ("off", "y", "1"),
        ("rPr", "sz", "-6"),
        ("spcPts", "val", "-1"),
    ]
    assert changes[2]["path"].endswith("rPr[1]")
    assert {c["part"] for c in changes} == {part}
    assert {c["category"] for c in changes} == {"geometry", "typography"}
    assert originals == (before.read_bytes(), after.read_bytes())


def test_namespace_aliases_missing_attributes_and_numeric_equivalence(tmp_path):
    before = package(
        tmp_path / "before.pptx",
        f'<a:root xmlns:a="{DRAWING}">'
        '<a:off x="001" y="2"/><a:rPr sz="1200"/><a:rPr sz="1406"/></a:root>',
    )
    after = package(
        tmp_path / "after.pptx",
        f'<b:root xmlns:b="{DRAWING}"><b:off x="1"/><b:rPr sz="1200"/><b:rPr sz="1400"/></b:root>',
    )
    changes = numeric_xml_deltas(before, after)
    assert len(changes) == 1
    assert changes[0]["path"].endswith("rPr[2]")


def test_invalid_xml_is_not_recovered(tmp_path):
    before = package(tmp_path / "before.pptx", "<root/>")
    after = package(tmp_path / "after.pptx", '<root name="bad "quotes""/>')
    with pytest.raises(etree.XMLSyntaxError):
        numeric_xml_deltas(before, after)
