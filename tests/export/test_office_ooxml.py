import io
from zipfile import ZipFile

import pytest
from lxml import etree

from presentation_designer.export.office_ooxml import P, validate_saved_pptx

PART = "ppt/slideLayouts/slideLayout3.xml"


def archive(xml, extra=b"<rels/>"):
    output = io.BytesIO()
    with ZipFile(output, "w") as z:
        z.writestr(PART, xml)
        z.writestr("_rels/.rels", extra)
        z.writestr("ppt/media/test.png", b"unchanged")
    return output.getvalue()


@pytest.mark.parametrize("name", ['Слайд "Спасибо!"', "A & B < C > D", 'A &quot; "B"'])
def test_exact_source_name_is_escaped_without_changing_other_bytes(name):
    root = etree.Element(f"{{{P}}}sldLayout", nsmap={"p": P}, matchingName=name)
    valid = etree.tostring(root)
    source = archive(valid)
    raw = archive(f'<p:sldLayout xmlns:p="{P}" matchingName="{name}"/>'.encode())
    result, parts = validate_saved_pptx(raw, source)
    assert parts == [PART]
    with ZipFile(io.BytesIO(result)) as z:
        assert etree.fromstring(z.read(PART)).get("matchingName") == name
        assert z.read("ppt/media/test.png") == b"unchanged"
        assert z.read("_rels/.rels") == b"<rels/>"
    assert validate_saved_pptx(result, source) == (result, [])
    assert validate_saved_pptx(source, source) == (source, [])


@pytest.mark.parametrize("other", [b"<broken", b'<!DOCTYPE x [<!ENTITY a "x">]><x/>'])
def test_unrelated_invalid_parts_fail_closed(other):
    source = archive(f'<p:sldLayout xmlns:p="{P}" matchingName="A &quot;B&quot;"/>'.encode())
    raw = archive(f'<p:sldLayout xmlns:p="{P}" matchingName="A "B""/>'.encode(), other)
    with pytest.raises(ValueError):
        validate_saved_pptx(raw, source)


def test_unknown_or_additionally_broken_layout_is_rejected():
    source = archive(f'<p:sldLayout xmlns:p="{P}" matchingName="A &quot;B&quot;"/>'.encode())
    for name, tail in [('different "name"', "/>"), ('A "B"', "><broken")]:
        with pytest.raises(ValueError):
            validate_saved_pptx(
                archive(f'<p:sldLayout xmlns:p="{P}" matchingName="{name}"{tail}'.encode()), source
            )
