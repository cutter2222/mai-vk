"""Content-first generation: model content is bound to layouts by code."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from presentation_designer.generation import matching as mt
from presentation_designer.generation import variants as vr
from tests.generation.test_number_content import _context


def _cards(count: int) -> mt.PatternInfo:
    return mt.pattern_info(
        {
            "pattern_id": f"cards_{count}",
            "role": "cards",
            "slots": [
                {"slot_id": "title", "kind": "title"},
                *[
                    {
                        "slot_id": f"body_{i}",
                        "kind": "body",
                        "repeat_group": "cards",
                        "capacity": {"max_chars": 250},
                    }
                    for i in range(count)
                ],
            ],
        }
    )


def _setup() -> tuple[vr.Context, vr.Packet, dict[str, Any]]:
    ctx = _context("balanced")
    ctx.patterns = [_cards(6), _cards(2), _cards(3)]
    ctx.theses = [
        vr.Thesis("t1", 1, "claim", "Условия пилота", "", True, None, [], [], [], [], "cards")
    ]
    packet = vr.Packet(0, ctx.theses, 1, 1, 2, {"t1": [ctx.patterns[0]]})
    answer = {
        "slides": [
            {
                "theses": ["t1"],
                "title": "Проверяем условия",
                "visual": "cards",
                "items": [
                    {"sub": "Согласие", "text": "Участие добровольное"},
                    {"sub": "Поддержка", "text": "Проверяем нагрузку"},
                ],
            }
        ]
    }
    return ctx, packet, answer


@pytest.mark.parametrize("pattern", [None, "cards_6", "invented"])
def test_generation_selects_layout_after_content_not_model_hint(pattern: str | None) -> None:
    ctx, packet, answer = _setup()
    if pattern:
        answer["slides"][0]["pattern"] = pattern
    before = copy.deepcopy(answer)
    draft = vr.make_validator(ctx, packet)(answer)["drafts"][0]
    assert draft.pattern.pattern_id == "cards_2"
    assert draft.items == answer["slides"][0]["items"]
    assert answer == before
    assert "pattern" not in vr.PLAN_MODEL_SCHEMA["properties"]["slides"]["items"]["required"]


def test_manual_ai_edit_can_still_request_specific_layout() -> None:
    ctx, packet, answer = _setup()
    answer["slides"][0]["pattern"] = "cards_6"
    assert vr.drafts_from_answer(ctx, packet, answer)[0].pattern.pattern_id == "cards_6"


@pytest.mark.parametrize("visual", ["image", "chart", "table", "number"])
def test_missing_resources_fall_back_to_real_text(visual: str) -> None:
    ctx, packet, answer = _setup()
    answer["slides"][0].update(visual=visual, image="unknown", dataset="unknown")
    draft = vr.make_validator(ctx, packet)(answer)["drafts"][0]
    assert draft.visual == "bullets"
    assert not draft.image and not draft.dataset
    assert len(draft.items) == 2


@pytest.mark.parametrize("role", ["screenshot", "mockup", "image_full"])
def test_product_image_layout_requires_provided_image(role: str) -> None:
    pattern = mt.pattern_info(
        {
            "pattern_id": "product",
            "role": role,
            "slots": [
                {"slot_id": "title", "kind": "title"},
                {"slot_id": "image", "kind": "image"},
            ],
        }
    )
    assert mt.score_pattern(pattern, mt.Need("image"), "balanced") == 0
    assert mt.score_pattern(pattern, mt.Need("image", has_image=True), "balanced") > 0


def test_required_image_layout_is_available_with_a_real_image() -> None:
    pattern = mt.pattern_info(
        {
            "pattern_id": "product",
            "role": "screenshot",
            "slots": [{"slot_id": "image", "kind": "image", "required": True}],
        }
    )
    assert mt.candidates_for([pattern], mt.Need("image"), "balanced") == []
    assert mt.candidates_for([pattern], mt.Need("image", has_image=True), "balanced") == [pattern]


def test_block_count_changes_layout_deterministically() -> None:
    ctx, packet, answer = _setup()
    answer["slides"][0]["items"].append({"text": "Проверяем результат"})
    first = vr.make_validator(ctx, packet)(answer)["drafts"][0]
    second = vr.make_validator(ctx, packet)(answer)["drafts"][0]
    assert first.pattern.pattern_id == second.pattern.pattern_id == "cards_3"


def test_no_safe_layout_is_an_error_not_a_foreign_screenshot() -> None:
    ctx, packet, answer = _setup()
    ctx.patterns = [
        mt.pattern_info(
            {
                "pattern_id": "photo",
                "role": "screenshot",
                "slots": [{"slot_id": "image", "kind": "image"}],
            }
        )
    ]
    with pytest.raises(ValueError, match="нет подходящей композиции"):
        vr.make_validator(ctx, packet)(answer)


def test_block_count_does_not_override_comparison_semantics() -> None:
    ctx, packet, answer = _setup()
    comparison = _cards(3)
    comparison.role = "comparison"
    comparison.pattern_id = "comparison"
    ctx.patterns.append(comparison)
    answer["slides"][0]["visual"] = "comparison"
    draft = vr.make_validator(ctx, packet)(answer)["drafts"][0]
    assert draft.pattern.pattern_id == "comparison"


def test_variable_list_does_not_displace_better_ranked_cards() -> None:
    ctx, packet, answer = _setup()
    ctx.patterns = [
        _cards(3),
        mt.pattern_info(
            {
                "pattern_id": "list",
                "role": "bullets",
                "slots": [{"slot_id": "body", "kind": "bullets"}],
            }
        ),
    ]
    draft = vr.make_validator(ctx, packet)(answer)["drafts"][0]
    assert draft.pattern.pattern_id == "cards_3"


@pytest.mark.parametrize("location", ["facts", "text", "sub"])
def test_item_facts_participate_in_number_layout_selection(location: str) -> None:
    ctx, packet, answer = _setup()
    ctx.patterns = _context("balanced").patterns
    item: dict[str, Any] = {"text": "Охват пилота"}
    item[location] = ["f1"] if location == "facts" else "Охват {fact:f1}"
    answer["slides"][0].update(visual="number", items=[item])
    draft = vr.make_validator(ctx, packet)(answer)["drafts"][0]
    assert draft.visual == "number"
    assert draft.facts == ["f1"]
    assert draft.pattern.pattern_id == "metrics"


def test_required_image_slot_and_dataset_backed_chart() -> None:
    pattern = mt.pattern_info(
        {
            "pattern_id": "chart",
            "role": "chart",
            "slots": [{"slot_id": "image", "kind": "image", "required": True}],
        }
    )
    assert mt.candidates_for([pattern], mt.Need("chart"), "balanced") == []
    assert mt.candidates_for(
        [pattern], mt.Need("chart", has_dataset=True), "balanced", has_datasets=True
    ) == [pattern]
    pattern.role = "text"
    assert mt.score_pattern(pattern, mt.Need("text"), "balanced") == 0


def test_prompt_digest_does_not_expose_template_layouts() -> None:
    ctx, packet, _ = _setup()
    structure = vr.deck_structure(ctx)
    digest = vr.packet_digest(ctx, structure, packet)
    assert not any(p.pattern_id in digest for p in ctx.patterns)
    assert "Дизайн-код" not in digest
    assert "Не возвращай pattern" in digest
