"""Подпись к числу не повторяет само число."""

from __future__ import annotations

from presentation_designer.design.labels import trim_repeated_values


def _slide(label: str) -> dict:
    return {
        "slide_id": "s1",
        "blocks": [
            {
                "slot_id": "value",
                "kind": "number",
                "number": {"fact_id": "f13"},
                "text": "420 тыс. руб.",
                "fact_refs": ["f13"],
            },
            {"slot_id": "value_label", "kind": "label", "text": label, "fact_refs": ["f13"]},
        ],
    }


def test_fact_repeated_at_label_start_is_trimmed() -> None:
    plan, changed = trim_repeated_values({"slides": [_slide("{fact:f13} на остановку")]})
    label = plan["slides"][0]["blocks"][1]
    assert changed == 1
    assert label["text"] == "на остановку"
    assert "fact_refs" not in label


def test_label_equal_to_number_is_dropped_and_others_kept() -> None:
    plan, _ = trim_repeated_values({"slides": [_slide("420 тыс. руб.")]})
    assert [b["slot_id"] for b in plan["slides"][0]["blocks"]] == ["value"]
    plan, changed = trim_repeated_values({"slides": [_slide("Оснащение одной остановки")]})
    assert changed == 0 and plan["slides"][0]["blocks"][1]["text"] == "Оснащение одной остановки"


def test_locked_slides_are_not_touched() -> None:
    _plan, changed = trim_repeated_values(
        {"slides": [_slide("{fact:f13} на остановку")]}, locked={"s1"}
    )
    assert changed == 0
