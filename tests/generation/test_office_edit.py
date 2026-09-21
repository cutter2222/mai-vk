"""Patches preserve manual work without reconstructing a deck from its old plan."""

import io
from zipfile import ZipFile

import pytest
from lxml import etree
from pptx import Presentation
from pptx.util import Inches, Pt

from presentation_designer.generation.office_edit import NS, EditPlan, TextPatch, patch_pptx


@pytest.fixture
def manual_deck():
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    shape = slide.shapes.add_textbox(Inches(2), Inches(3), Inches(4), Inches(1))
    run = shape.text_frame.paragraphs[0].add_run()
    run.text = "Ручной заголовок"
    run.font.bold = True
    run.font.size = Pt(27)
    shape.text_frame.add_paragraph().text = "Не менять вручную добавленный текст"
    deck.slides.add_slide(deck.slide_layouts[6]).shapes.add_textbox(
        Inches(1), Inches(1), Inches(3), Inches(1)
    ).text = "Ручной соседний слайд"
    output = io.BytesIO()
    deck.save(output)
    return output.getvalue()


def test_only_requested_text_changes(manual_deck):
    plan = EditPlan(
        explanation="Заголовок изменён",
        patches=[TextPatch(slide=1, run=0, before="Ручной заголовок", after="Новый заголовок")],
    )
    result = patch_pptx(manual_deck, plan)
    with ZipFile(io.BytesIO(manual_deck)) as before, ZipFile(io.BytesIO(result)) as after:
        assert before.namelist() == after.namelist()
        for name in before.namelist():
            if name != "ppt/slides/slide1.xml":
                assert before.read(name) == after.read(name), name
        old = etree.fromstring(before.read("ppt/slides/slide1.xml"))
        new = etree.fromstring(after.read("ppt/slides/slide1.xml"))
        new.find(".//a:t", NS).text = "Ручной заголовок"
        assert etree.tostring(old, method="c14n") == etree.tostring(new, method="c14n")
    deck = Presentation(io.BytesIO(result))
    shape = deck.slides[0].shapes[0]
    assert shape.left == Inches(2)
    assert shape.top == Inches(3)
    assert shape.text_frame.paragraphs[0].runs[0].font.bold
    assert shape.text_frame.paragraphs[0].runs[0].font.size == Pt(27)
    assert deck.slides[1].shapes[0].text == "Ручной соседний слайд"


@pytest.mark.parametrize(
    "changes",
    [
        {"before": "Устаревший текст"},
        {"slide": 3},
        {"run": 100},
    ],
)
def test_wrong_patch_rejected(manual_deck, changes):
    patch = {"slide": 1, "run": 0, "before": "Ручной заголовок", "after": "Новый"}
    with pytest.raises(ValueError):
        patch_pptx(manual_deck, EditPlan(explanation="", patches=[TextPatch(**(patch | changes))]))


def test_noop_is_byte_identical(manual_deck):
    assert (
        patch_pptx(manual_deck, EditPlan(explanation="Не поддерживается", patches=[]))
        == manual_deck
    )
