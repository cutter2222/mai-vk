"""Показатели не должны вытеснять условия, риски и пояснения из ответа модели."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from presentation_designer.generation import variants as vr
from presentation_designer.shared.settings import get_settings


def _context(variant: str, captions: int = 1, numbers: int = 1) -> vr.Context:
    kinds = ["title"] + ["number"] * numbers + ["caption"] * captions + ["bullets"]
    profile = {
        "patterns": [
            {
                "pattern_id": "metrics",
                "role": "numbers",
                "slots": [
                    {
                        "slot_id": f"{kind}_{i}",
                        "kind": kind,
                        "bbox": {"x": 0.1, "y": i * 0.1, "width": 0.8, "height": 0.1},
                    }
                    for i, kind in enumerate(kinds)
                ],
            }
        ]
    }
    package = {
        "facts": [
            {"fact_id": "f1", "raw": "60 %", "value": 60, "label": "Охват пилота"},
            {"fact_id": "f2", "raw": "12 млн ₽", "value": 12, "label": "Экономия за год"},
        ]
    }
    return vr.build_context({}, profile, package, variant, {}, get_settings(), 10)


def _fill(ctx: vr.Context, items: list[dict[str, Any]], facts: list[str]) -> list[dict[str, Any]]:
    draft = vr.Draft(
        kind="content",
        theses=[],
        pattern=ctx.patterns[0],
        title="Результаты пилота",
        visual="number",
        facts=facts,
        items=items,
    )
    before = copy.deepcopy(items)
    blocks = vr.fill_blocks(ctx, draft)
    assert draft.items == before, "Сборка не должна менять исходный ответ модели"
    return blocks


def _bullets(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for block in blocks if block["kind"] == "bullets" for item in block["items"]]


@pytest.mark.parametrize("variant", vr.VARIANTS)
def test_numbers_keep_non_numeric_conditions_and_risks(variant: str) -> None:
    ctx = _context(variant)
    metric = {"text": "Охват пилота", "fact_refs": ["f1"]}
    conditions = [
        {"text": "Только после согласия пользователя"},
        {"text": "Риск нагрузки поддержки"},
    ]
    blocks = _fill(ctx, [metric, *conditions], ["f1"])
    assert _bullets(blocks) == conditions
    assert [b["text"] for b in blocks if b["kind"] == "caption"] == [metric["text"]]
    assert [b["number"]["fact_id"] for b in blocks if b["kind"] == "number"] == ["f1"]


@pytest.mark.parametrize("variant", vr.VARIANTS)
def test_number_without_caption_keeps_its_explanation(variant: str) -> None:
    ctx = _context(variant, captions=0)
    item = {"text": "Охват только среди участников пилота", "fact_refs": ["f1"]}
    assert _bullets(_fill(ctx, [item], ["f1"])) == [item]


@pytest.mark.parametrize("variant", vr.VARIANTS)
def test_shared_fact_does_not_make_different_statements_duplicates(variant: str) -> None:
    ctx = _context(variant)
    metric = {"text": "Охват пилота", "fact_refs": ["f1"]}
    qualification = {"text": "Охват исключает отказавшихся пользователей", "fact_refs": ["f1"]}
    assert _bullets(_fill(ctx, [metric, qualification], ["f1"])) == [qualification]


@pytest.mark.parametrize("variant", vr.VARIANTS)
def test_partial_numbers_keep_unplaced_fact_and_non_numeric_item(variant: str) -> None:
    ctx = _context(variant)
    metric = {"text": "Охват пилота", "fact_refs": ["f1"]}
    remaining = [
        {"text": "Экономия {fact:f2} за год", "fact_refs": ["f2"]},
        {"text": "Масштабирование после проверки рисков"},
    ]
    assert _bullets(_fill(ctx, [metric, *remaining], ["f1", "f2"])) == remaining


def test_all_single_number_captions_are_used_in_order() -> None:
    ctx = _context("balanced", captions=2, numbers=2)
    items = [
        {"text": "Охват пилота", "fact_refs": ["f1"]},
        {"text": "Экономия за год", "fact_refs": ["f2"]},
    ]
    blocks = _fill(ctx, items, ["f1", "f2"])
    assert [b["text"] for b in blocks if b["kind"] == "caption"] == [i["text"] for i in items]
    assert _bullets(blocks) == []


@pytest.mark.parametrize("text", ["{fact:f1}", "60 %"])
def test_bare_value_is_not_duplicated_in_bullets(text: str) -> None:
    ctx = _context("balanced")
    blocks = _fill(ctx, [{"text": text, "fact_refs": ["f1"]}], ["f1"])
    assert _bullets(blocks) == []
    assert [b["text"] for b in blocks if b["kind"] == "number"] == ["60 %"]


def test_two_fact_statement_stays_when_only_one_number_is_shown() -> None:
    ctx = _context("balanced")
    item = {"text": "Охват {fact:f1}, экономия {fact:f2}", "fact_refs": ["f1", "f2"]}
    assert _bullets(_fill(ctx, [item], ["f1", "f2"])) == [item]


def test_existing_number_card_groups_keep_caption_pairing() -> None:
    ctx = _context("balanced", captions=2, numbers=2)
    for slot in ctx.profile["patterns"][0]["slots"]:
        if slot["kind"] in ("number", "caption"):
            slot["repeat_group"] = "metrics"
    from presentation_designer.generation.matching import profile_patterns

    ctx.patterns = profile_patterns(ctx.profile)
    items = [
        {"text": "Охват пилота", "fact_refs": ["f1"]},
        {"text": "Экономия за год", "fact_refs": ["f2"]},
    ]
    blocks = _fill(ctx, items, ["f1", "f2"])
    captions = {b["slot_id"]: b["text"] for b in blocks if b["kind"] == "caption"}
    assert captions == {"caption_3": "Охват пилота", "caption_4": "Экономия за год"}


def test_new_plans_do_not_reuse_previous_content_loss_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _context("balanced")
    current = vr.plan_key(ctx.story, ctx.profile, ctx.variant_id, {})
    monkeypatch.setattr(vr, "PLAN_VERSION", "0.3.1")
    assert vr.plan_key(ctx.story, ctx.profile, ctx.variant_id, {}) != current
