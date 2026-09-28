"""Page occupancy is content-aware, geometric and never a reason to delete content."""

import copy

import pytest

from presentation_designer.generation import density
from presentation_designer.generation import variants as vr
from presentation_designer.generation.matching import pattern_info
from tests.generation.test_number_content import _context


def pattern():
    return pattern_info(
        {
            "pattern_id": "body",
            "role": "text",
            "slots": [
                {
                    "slot_id": "title",
                    "kind": "title",
                    "bbox": {"x": 0.1, "y": 0.05, "width": 0.8, "height": 0.1},
                },
                {
                    "slot_id": "body",
                    "kind": "body",
                    "bbox": {"x": 0.1, "y": 0.2, "width": 0.8, "height": 0.65},
                },
            ],
        }
    )


def assess(blocks, **kwargs):
    p = pattern()
    p.slots["body"].size_pt = 24
    return density.assess(p, blocks, {}, 12192000, 6858000, **kwargs)


def test_union_clips_and_does_not_double_count():
    assert density.union_area([(0, 0, 0.8, 1), (0.5, 0, 0.8, 1)]) == pytest.approx(1)
    assert density.union_area([(-0.5, -0.5, 1, 1)]) == pytest.approx(0.25)
    assert density.union_area([(2, 2, 1, 1), (0, 0, -1, 1)]) == 0


def test_title_cannot_disguise_empty_body():
    score = assess([{"slot_id": "title", "kind": "title", "text": "Заголовок " * 50}])
    assert score["underfilled"] and score["text_chars"] == 0


def test_short_body_is_sparse_but_substantial_paragraph_is_not():
    block = {"slot_id": "body", "kind": "body", "text": "Короткое пояснение"}
    short = assess([block])
    full = assess([{**block, "text": "Содержательный аргумент из источника. " * 12}])
    assert short["underfilled"]
    assert not full["underfilled"]
    assert full["body_fill_ratio"] > short["body_fill_ratio"]


@pytest.mark.parametrize("kind", ["title", "divider", "final"])
def test_service_slides_are_exempt(kind):
    assert not assess([], kind=kind)["underfilled"]


def test_quote_and_meaningful_visual_are_not_forced_into_bullets():
    assert not assess([], visual="quote")["underfilled"]
    p = pattern()
    p.slots["body"].kind = "chart"
    score = density.assess(
        p,
        [{"kind": "chart", "slot_id": "body", "chart": {"dataset_id": "ds1"}}],
        {},
        12192000,
        6858000,
    )
    assert not score["underfilled"] and score["visual_blocks"] == 1


def test_unknown_font_does_not_produce_false_precision_or_sparse_warning():
    p = pattern()
    p.slots["body"].size_pt = None
    p.slots["body"].family = None
    score = density.assess(
        p, [{"slot_id": "body", "kind": "body", "text": "Смысл"}], {}, 12192000, 6858000
    )
    assert score["measurement_incomplete"] and not score["underfilled"]


def test_filler_is_not_content():
    assert assess(
        [{"slot_id": "body", "kind": "body", "text": "Повтор " * 100}], filler_slots={"body"}
    )["underfilled"]


def test_three_concrete_bullets_are_not_forced_to_expand():
    score = assess(
        [
            {
                "slot_id": "body",
                "kind": "bullets",
                "items": [
                    {"text": "Приоритет по контексту"},
                    {"text": "Единый центр уведомлений"},
                    {"text": "Тихие часы"},
                ],
            }
        ]
    )
    assert score["body_fill_ratio"] < 0.28
    assert not score["underfilled"]


def test_native_number_is_content_not_an_empty_slide():
    p = pattern()
    p.slots["body"].kind = "number"
    p.slots["body"].size_pt = 100
    score = density.assess(
        p,
        [{"kind": "number", "slot_id": "body", "number": {"fact_id": "f1"}}],
        {"f1": {"raw": "44 %"}},
        12192000,
        6858000,
    )
    assert score["text_chars"] == 4 and score["text_units"] == 1


def test_two_page_target_is_audited_without_mutating_or_dropping_content():
    ctx = _context("balanced")
    draft = vr.Draft("content", ["t1"], pattern(), "Риски", text="Важное условие")
    decks = [copy.deepcopy(draft) for _ in range(3)]
    before = copy.deepcopy(decks)
    assert vr._thesis_page_excess(decks) == 1
    assert "t1: 3 страниц" in vr.page_quality_hint(ctx, decks)
    assert decks == before


def test_model_receives_page_contract_and_structured_list_instructions():
    ctx = _context("balanced")
    packet = vr.Packet(0, [], 1, 1, 2, {})
    digest = vr.packet_digest(ctx, vr.deck_structure(ctx), packet)
    assert "предпочтительно 1 страница, максимум 2" in digest
    assert "Списки передавай в items" in digest
    assert "сохрани их и оговорки" in digest
    assert "**жирное выделение**" in digest


def sparse_draft():
    ctx = _context("balanced")
    wide = pattern()
    wide.slots["body"].size_pt = 24
    compact = copy.deepcopy(wide)
    compact.pattern_id = "compact"
    compact.slots["body"].bbox = (0.1, 0.35, 0.8, 0.12)
    ctx.patterns = [wide, compact]
    draft = vr.Draft("content", [], wide, "Риски", text="Только после согласования")
    draft.blocks = vr.measure_blocks(ctx, draft, vr.fill_blocks(ctx, draft))
    return ctx, draft, compact


def test_sparse_composition_changes_without_changing_content():
    ctx, draft, compact = sparse_draft()
    assert vr.page_density(ctx, draft)["underfilled"]
    before = copy.deepcopy(draft)
    assert vr.compact_sparse_pattern(ctx, draft)
    assert draft.pattern is compact
    assert draft.text == before.text and draft.items == before.items
    assert vr._keeps_content(before, draft, ratio=1)
    assert not vr.page_density(ctx, draft)["underfilled"]
    assert not draft.overflow


@pytest.mark.parametrize("reason", ["smaller_font", "overflow", "different_style", "missing_text"])
def test_sparse_composition_rejects_unsafe_alternatives(reason):
    ctx, draft, compact = sparse_draft()
    if reason == "smaller_font":
        compact.slots["body"].size_pt = 12
    elif reason == "overflow":
        compact.slots["body"].bbox = (0.1, 0.35, 0.08, 0.03)
    elif reason == "different_style":
        compact.style_key = "other"
    else:
        compact.slots.pop("body")
    before = copy.deepcopy(draft)
    assert not vr.compact_sparse_pattern(ctx, draft)
    assert draft == before


def test_sparse_composition_respects_restricted_pattern_pool():
    ctx, draft, _ = sparse_draft()
    ctx.patterns = [draft.pattern]
    assert not vr.compact_sparse_pattern(ctx, draft)


@pytest.mark.parametrize("kind", ["body", "bullets", "title"])
def test_content_overflow_never_deletes_unreferenced_conditions(kind):
    ctx, draft, _ = sparse_draft()
    slot = draft.pattern.slots["body"]
    slot.kind = kind
    slot.bbox = (0.1, 0.3, 0.25, 0.08)
    text = "Ожидаемый эффект после проверки. Не является полученным результатом."
    block = {"slot_id": "body", "kind": kind}
    if kind == "bullets":
        block["items"] = [{"text": s + "."} for s in text.rstrip(".").split(". ")]
    else:
        block["text"] = text
    measured = vr.measure_blocks(ctx, draft, [copy.deepcopy(block)])
    assert draft.overflow
    assert {k: v for k, v in measured[0].items() if k != "fit"} == block


def test_intro_and_list_can_share_a_single_body_slot_without_losing_either():
    ctx, draft, _ = sparse_draft()
    draft.pattern.slots["body"].kind = "bullets"
    draft.pattern.singles = {"bullets": [draft.pattern.slots["body"]]}
    draft.visual = "bullets"
    draft.items = [{"text": "Проверка рисков"}, {"text": "Согласие пользователей"}]
    blocks = vr.fill_blocks(ctx, draft)
    items = next(b["items"] for b in blocks if b["kind"] == "bullets")
    assert items == [{"text": draft.text}, *draft.items]
    assert not draft.unplaced_text


def test_alternative_layout_cannot_remove_headline():
    ctx, draft, compact = sparse_draft()
    compact.title = None
    assert compact not in vr._alternatives(ctx, draft)


def test_only_filler_is_kept_when_it_overflows():
    # e-learning, слайд «Идея: приоритет маршруту…»: пояснение тезиса не влезло в слот на
    # 180 знаков, наполнитель убрали, и слайд остался с одним заголовком (28.09.2026).
    body = "body"
    draft = vr.Draft(kind="content", theses=["t1"], pattern=pattern(), title="Идея")
    draft.filler_slots = {body}
    measured = [
        {"slot_id": "title", "kind": "title", "text": "Идея"},
        {"slot_id": body, "kind": "body", "text": "Пояснение " * 40},
    ]
    draft.overflow = [{"kind": "body", "slot_id": body}]
    assert vr._drop_overflowing_fillers(draft, list(measured)) == measured
    assert draft.overflow == [{"kind": "body", "slot_id": body}]
    # Есть другое содержание — переполненный наполнитель по-прежнему убирается.
    with_items = [*measured, {"slot_id": "x", "kind": "bullets", "items": [{"text": "Пункт"}]}]
    assert vr._drop_overflowing_fillers(draft, with_items) == [measured[0], with_items[2]]
    assert draft.overflow == []
