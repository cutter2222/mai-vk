"""Заголовок и описание процесса остаются в одной физической карточке."""

import pytest

from presentation_designer.generation.matching import pattern_info
from presentation_designer.library.spec import find_composition


@pytest.mark.parametrize("legacy", [False, True])
def test_process_has_three_cards_not_six_headings(legacy):
    composition = find_composition("process@count=3")
    assert composition is not None
    slots = [slot.as_profile_slot() for slot in composition.slots]
    if legacy:
        for slot in slots:
            if slot["slot_id"].endswith("_number"):
                slot["kind"] = "label"
    pattern = pattern_info(
        {
            "pattern_id": "pat_builtin_process_count3",
            "role": "process",
            "slots": slots,
        }
    )
    assert pattern.cards is not None
    assert pattern.cards.count == 3
    for index in range(3):
        card = pattern.cards.card(index)
        assert card["label"].slot_id == f"step_{index + 1}_title"
        assert card["caption"].slot_id == f"step_{index + 1}_body"
        assert card["subtitle"].slot_id == f"step_{index + 1}_number"
