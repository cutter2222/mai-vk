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


@pytest.mark.parametrize("round_coordinates", [False, True])
def test_selected_object_move_preserves_other_bytes(manual_deck, round_coordinates):
    from presentation_designer.generation.office_object_edit import (
        ObjectEditPlan,
        Position,
        patch_object,
    )
    from presentation_designer.generation.office_objects import objects

    obj = objects(manual_deck)[0]
    assert obj.runs == [0, 1]
    assert obj.label.startswith("Ручной заголовок")
    updated = patch_object(
        manual_deck,
        obj,
        ObjectEditPlan(
            explanation="Правее",
            patches=[],
            position=Position(x=0.4, y=round(obj.bbox.y, 6) if round_coordinates else obj.bbox.y),
        ),
    )
    with ZipFile(io.BytesIO(manual_deck)) as before, ZipFile(io.BytesIO(updated)) as after:
        for name in before.namelist():
            if name != "ppt/slides/slide1.xml":
                assert before.read(name) == after.read(name)
        old = etree.fromstring(before.read("ppt/slides/slide1.xml"))
        new = etree.fromstring(after.read("ppt/slides/slide1.xml"))
        new.find(".//a:off", NS).set("x", old.find(".//a:off", NS).get("x"))
        assert etree.tostring(old, method="c14n") == etree.tostring(new, method="c14n")
    shape = Presentation(io.BytesIO(updated)).slides[0].shapes[0]
    assert shape.left == Inches(4)
    assert shape.text_frame.paragraphs[0].runs[0].font.bold


def test_selected_object_rejects_other_text_and_off_slide_position(manual_deck):
    from presentation_designer.generation.office_object_edit import (
        ObjectEditPlan,
        Position,
        patch_object,
    )
    from presentation_designer.generation.office_objects import ObjectTarget, objects

    obj = objects(manual_deck)[0]
    with pytest.raises(ValueError, match="вне выбранного"):
        patch_object(
            manual_deck,
            obj,
            ObjectEditPlan(
                explanation="",
                patches=[
                    TextPatch(
                        slide=2,
                        run=0,
                        before="Ручной соседний слайд",
                        after="Нельзя",
                    )
                ],
            ),
        )
    with pytest.raises(ValueError, match="границы"):
        patch_object(
            manual_deck,
            obj,
            ObjectEditPlan(explanation="", patches=[], position=Position(x=0.9, y=0.2)),
        )
    with pytest.raises(ValueError, match="не найден"):
        patch_object(
            manual_deck,
            ObjectTarget(slide=1, shape_id="999"),
            ObjectEditPlan(explanation="", patches=[]),
        )
    assert (
        patch_object(
            manual_deck,
            obj,
            ObjectEditPlan(
                explanation="", patches=[], position=Position(x=obj.bbox.x, y=obj.bbox.y)
            ),
        )
        == manual_deck
    )


def test_object_map_omits_groups_and_rotated_shapes(manual_deck):
    from presentation_designer.generation.office_objects import objects

    deck = Presentation(io.BytesIO(manual_deck))
    deck.slides[0].shapes[0].rotation = 45
    group = deck.slides[0].shapes.add_group_shape()
    group.shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(1)).text = "В группе"
    output = io.BytesIO()
    deck.save(output)
    assert all(obj.slide == 2 for obj in objects(output.getvalue()))


async def test_object_proposal_receives_only_selected_text(manual_deck, monkeypatch):
    import json
    from types import SimpleNamespace

    from presentation_designer.generation import office_object_edit
    from presentation_designer.generation.office_objects import objects
    from presentation_designer.shared.settings import Settings

    obj = objects(manual_deck)[0]
    calls = []

    class Client:
        async def complete(self, request, validator):
            context = json.loads(request.messages[-1].text)
            assert context["object"]["shape_id"] == obj.shape_id
            assert context["instruction"] == "Правее"
            assert context["runs"] == [
                {"run": 0, "text": "Ручной заголовок"},
                {"run": 1, "text": "Не менять вручную добавленный текст"},
            ]
            plan = validator(
                {"explanation": "Правее", "patches": [], "position": {"x": 0.3, "y": 0.4}}
            )
            return SimpleNamespace(parsed=plan.model_dump())

        async def aclose(self):
            calls.append("closed")

    monkeypatch.setattr(office_object_edit, "build_client", lambda settings: Client())
    plan = await office_object_edit.propose(manual_deck, "Правее", Settings(), obj)
    assert plan.position.x == 0.3
    assert calls == ["closed"]


async def test_multi_object_proposal_has_shared_context_and_rejects_escape(
    manual_deck, monkeypatch
):
    import json
    from types import SimpleNamespace

    from presentation_designer.generation import office_object_edit as edit
    from presentation_designer.generation.office_objects import ObjectTarget, objects
    from presentation_designer.shared.settings import Settings

    chosen = [ObjectTarget(slide=obj.slide, shape_id=obj.shape_id) for obj in objects(manual_deck)]
    closed = []

    class Client:
        async def complete(self, request, validator):
            content = json.loads(request.messages[-1].text)
            assert content["instruction"] == "Оба правее"
            assert len(content["objects"]) == 2
            assert content["objects"][0]["runs"][0]["text"] == "Ручной заголовок"
            return SimpleNamespace(parsed=validator({"explanation": "Без изменений", "edits": []}))

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(edit, "build_client", lambda settings: Client())
    plan = await edit.propose_many(manual_deck, "Оба правее", Settings(), chosen)
    assert edit.patch_objects(manual_deck, chosen, plan) == manual_deck
    assert closed == [True]
    change = edit.SelectedObjectEdit(
        target=chosen[1], plan=edit.ObjectEditPlan(explanation="", patches=[])
    )
    with pytest.raises(ValueError, match="вне выбора"):
        edit.patch_objects(
            manual_deck, chosen[:1], edit.ObjectsEditPlan(explanation="", edits=[change])
        )
    with pytest.raises(ValueError, match="повторно"):
        edit.patch_objects(
            manual_deck, chosen, edit.ObjectsEditPlan(explanation="", edits=[change, change])
        )
