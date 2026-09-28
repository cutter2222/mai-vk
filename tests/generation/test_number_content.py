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


@pytest.mark.parametrize("variant", vr.VARIANTS)
def test_metric_heading_and_explanation_are_placed_once_together(variant: str) -> None:
    ctx = _context(variant)
    item = {"sub": "Охват", "text": "Только участники пилота", "fact_refs": ["f1"]}
    qualification = {"text": "Не включает отказавшихся пользователей", "fact_refs": ["f1"]}
    blocks = _fill(ctx, [item, qualification], ["f1"])
    assert [b["text"] for b in blocks if b["kind"] == "caption"] == [
        "Охват: Только участники пилота"
    ]
    assert _bullets(blocks) == [qualification]


def test_metric_heading_without_caption_stays_in_bullets() -> None:
    ctx = _context("balanced", captions=0)
    item = {"sub": "Охват", "text": "Только участники пилота", "fact_refs": ["f1"]}
    assert _bullets(_fill(ctx, [item], ["f1"])) == [
        {"text": "Охват: Только участники пилота", "fact_refs": ["f1"]}
    ]


def test_new_plans_do_not_reuse_previous_content_loss_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = _context("balanced")
    current = vr.plan_key(ctx.story, ctx.profile, ctx.variant_id, {})
    monkeypatch.setattr(vr, "PLAN_VERSION", "0.3.1")
    assert vr.plan_key(ctx.story, ctx.profile, ctx.variant_id, {}) != current


def test_generated_comparison_is_one_line_with_both_fact_references():
    ctx = _context("balanced", numbers=0, captions=0)
    ctx.facts = {
        fid: {
            "fact_id": fid,
            "raw": raw,
            "block_id": "b1",
            "source_id": "s1",
            "context": {"metric": "Отписки", "comparison": "с 4,1 % до 2,7 %"},
        }
        for fid, raw in [("f1", "4,1 %"), ("f2", "2,7 %")]
    }
    blocks = _fill(ctx, [], ["f1", "f2"])
    assert _bullets(blocks) == [
        {
            "text": "Отписки: с {fact:f1} до {fact:f2}",
            "fact_refs": ["f1", "f2"],
        }
    ]
    ctx.facts["f2"]["block_id"] = "another_source_block"
    assert "{fact:f2}" not in vr._fact_phrase(ctx, "f1")


def test_comparison_substitution_does_not_replace_part_of_a_larger_number():
    ctx = _context("balanced", numbers=0, captions=0)
    ctx.facts = {
        fid: {
            "fact_id": fid,
            "raw": raw,
            "block_id": "b1",
            "source_id": "s1",
            "context": {"metric": "Охват", "comparison": "с 4 % до 44 %"},
        }
        for fid, raw in [("f1", "4 %"), ("f2", "44 %")]
    }
    assert vr._fact_phrase(ctx, "f1") == "Охват: с {fact:f1} до {fact:f2}"


@pytest.mark.parametrize(
    ("fact", "expected"),
    [
        (
            {
                "raw": "27",
                "label": "уведомлений (в день)",
                "context": {"metric": "уведомлений", "period": "в день"},
                "source_location": {
                    "fragment": "…без приоритизации: в среднем 27 уведомлений в день на человека."
                },
            },
            "В среднем {fact:f1} уведомлений в день на человека",
        ),
        (
            {
                "raw": "2,4 млн ₽",
                "unit": "млн ₽",
                "label": "Сумма — Затраты на пилот",
                "context": {
                    "metric": "Сумма",
                    "period": "Экономика",
                    "subject": "Затраты на пилот",
                },
                "source_location": {
                    "cell": "B2",
                    "fragment": "Сумма: Затраты на пилот = 2.4 млн ₽",
                },
            },
            "Затраты на пилот: {fact:f1}",
        ),
        (
            {
                "raw": "31 %",
                "unit": "%",
                "label": "Открываемость — Май",
                "context": {"metric": "Открываемость", "period": "Май", "subject": "Метрики"},
                "source_location": {"cell": "B2", "fragment": "Открываемость: Май = 31 %"},
            },
            "Открываемость, май: {fact:f1}",
        ),
        (
            {
                "raw": "12,5 млн ₽",
                "unit": "₽",
                "label": "Экономия на поддержке (за год)",
                "context": {"metric": "Экономия на поддержке", "period": "за год"},
                "source_location": {
                    "fragment": "Экономия на поддержке составила 12,5 млн ₽ за год"
                },
            },
            "Экономия на поддержке: {fact:f1}",
        ),
    ],
)
def test_fact_phrase_reads_like_a_caption(fact, expected):
    # Число, считающее слово после себя, остаётся во фразе источника; ячейку таблицы
    # называет строка (или колонка с периодом), а не общее имя колонки «Сумма».
    ctx = _context("balanced", numbers=0, captions=0)
    ctx.facts = {"f1": {"fact_id": "f1", **fact}}
    assert vr._fact_phrase(ctx, "f1") == expected


def test_answer_items_lose_manual_markers_and_concept_notice():
    # Sonnet ставил «• » в тексте пунктов уже маркированного списка и повторял оговорку
    # концепции абзацем на половине слайдов (зоопарк шаблонов, 28.09.2026).
    assert vr._clean("• Сценарий риска: клиент не получает ответ") == (
        "Сценарий риска: клиент не получает ответ"
    )
    assert vr._clean("— Пункт") == "Пункт" and vr._clean("2024 год") == "2024 год"
    notice = "Концепция по теме: источники не предоставлены; утверждения требуют проверки."
    text = f"Предлагаем выбирать отдел не произвольно. {notice} Устоявшиеся процессы важны."
    assert vr._without_notice(text, notice) == (
        "Предлагаем выбирать отдел не произвольно. Устоявшиеся процессы важны."
    )
    assert vr._without_notice(notice.rstrip("."), notice) == ""
