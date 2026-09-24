"""The planner sees design tokens and alternatives, but does not use step badges as KPIs."""

from __future__ import annotations

import copy
from typing import Any

from presentation_designer.generation import matching as mt
from presentation_designer.generation import variants as vr
from presentation_designer.shared.settings import get_settings


def _pattern(pid: str, *, builtin: bool = False, badges: bool = False) -> dict[str, Any]:
    return {
        "pattern_id": pid,
        "name": "Четыре этапа",
        "role": "cards",
        "confidence": 0.9,
        "source": {"kind": "builtin" if builtin else "template"},
        "slots": [
            {"slot_id": "title", "kind": "title", "capacity": {"max_chars": 80}},
            *[
                {
                    "slot_id": f"body_{i}",
                    "kind": "body",
                    "repeat_group": "cards",
                    "bbox": {"x": i * 0.22, "y": 0.4, "width": 0.2, "height": 0.4},
                    "capacity": {"max_chars": 200},
                }
                for i in range(4)
            ],
            *(
                [
                    {
                        "slot_id": f"number_{i}",
                        "kind": "number",
                        "repeat_group": "cards",
                        "sample_text": str(i + 1),
                        "capacity": {"max_chars": 3},
                        "bbox": {"x": i * 0.22, "y": 0.3, "width": 0.07, "height": 0.12},
                    }
                    for i in range(4)
                ]
                if badges
                else []
            ),
        ],
    }


def test_library_alternative_is_visible_without_losing_template_priority() -> None:
    patterns = [mt.pattern_info(_pattern(f"template_{i}")) for i in range(5)]
    patterns.append(mt.pattern_info(_pattern("library", builtin=True)))
    need = mt.Need("cards", items=4, text_chars=200)
    usual = mt.candidates_for(patterns, need, "balanced", limit=3)
    visible = mt.candidates_for(
        patterns, need, "balanced", limit=3, include_library_alternative=True
    )
    assert all(not p.builtin for p in usual)
    assert len(visible) == 3 and visible[0] == usual[0] and visible[-1].builtin
    single = mt.candidates_for(
        patterns, need, "balanced", limit=1, include_library_alternative=True
    )
    assert single == usual[:1]


def test_step_badges_are_not_metric_slots_or_filled_from_facts() -> None:
    raw = _pattern("steps", badges=True)
    profile = {"patterns": [raw]}
    package = {"facts": [{"fact_id": "f1", "raw": "3000 м", "label": "Глубина"}]}
    ctx = vr.build_context({}, profile, package, "balanced", {}, get_settings(), 8)
    p = ctx.patterns[0]
    assert p.number_capacity == 0 and "number" not in p.visuals()
    assert len(p.ordinal_slot_ids) == 4 and "номер шага" in p.summary()
    from presentation_designer.design.fit import _can_host, content_slots

    assert not _can_host(raw, {"number": 1})
    assert not any(s["kind"] == "number" for s in content_slots(raw))
    draft = vr.Draft(
        kind="content",
        theses=[],
        pattern=p,
        title="Этапы",
        visual="cards",
        facts=["f1"],
        items=[{"text": "Глубина {fact:f1}", "fact_refs": ["f1"]}],
    )
    blocks = vr.fill_blocks(ctx, draft)
    assert not any(b["kind"] == "number" for b in blocks)
    assert any("{fact:f1}" in b.get("text", "") for b in blocks)
    # The same sequence in wide number boxes is not automatically labelled navigation.
    for s in raw["slots"]:
        if s["kind"] == "number":
            s["bbox"]["width"] = 0.2
    assert mt.pattern_info(raw).number_capacity == 4


def test_unplaceable_facts_trigger_layout_change_instead_of_empty_slide() -> None:
    narrow = {
        "pattern_id": "divider",
        "role": "text",
        "slots": [
            {"slot_id": "title", "kind": "title", "capacity": {"max_chars": 80}},
        ],
    }
    roomy = {
        "pattern_id": "roomy",
        "role": "text",
        "slots": [
            *narrow["slots"],
            {"slot_id": "body", "kind": "body", "capacity": {"max_chars": 600}},
        ],
    }
    package = {"facts": [{"fact_id": "f1", "raw": "40 км²", "label": "Площадь"}]}
    ctx = vr.build_context(
        {}, {"patterns": [narrow, roomy]}, package, "balanced", {}, get_settings(), 8
    )
    draft = vr.Draft(
        kind="content",
        theses=[],
        pattern=ctx.patterns[0],
        title="Критерии",
        facts=["f1"],
        candidates=[ctx.patterns[1]],
    )
    result = vr.fit_draft(ctx, draft)
    assert result.pattern.pattern_id == "roomy"
    assert not result.unplaced_text
    assert any("{fact:f1}" in b.get("text", "") for b in result.blocks)


def test_model_cannot_claim_thesis_coverage_while_omitting_its_facts() -> None:
    package = {"facts": [{"fact_id": "f1", "raw": "40 км²", "label": "Площадь"}]}
    story = {
        "theses": [
            {
                "thesis_id": "t1",
                "order": 1,
                "kind": "claim",
                "statement": "Картирование",
                "fact_refs": ["f1"],
            }
        ]
    }
    ctx = vr.build_context(
        story, {"patterns": [_pattern("cards")]}, package, "balanced", {}, get_settings(), 8
    )
    packet = vr.Packet(0, ctx.theses, 1, 1, 1, {"t1": ctx.patterns})
    drafts = vr.drafts_from_answer(
        ctx,
        packet,
        {
            "slides": [
                {
                    "title": "Критерии",
                    "theses": ["t1"],
                    "pattern": "cards",
                    "facts": [],
                }
            ]
        },
    )
    # Факт тезиса, не написанный на слайде, к нему не привязывается (иначе на слайд
    # попадали числа из других разделов), но колода не теряет его: проверка по колоде
    # дописывает обязательный факт на слайд его тезиса.
    assert drafts[0].facts == []
    deck = vr._ensure_facts(ctx, drafts)
    assert deck[0].facts == ["f1"]
    assert any("{fact:f1}" in b.get("text", "") for b in vr.fill_blocks(ctx, deck[0]))


def test_content_packet_omits_design_but_plan_cache_tracks_it() -> None:
    profile = {
        "patterns": [_pattern("cards", builtin=True)],
        "design_tokens": {
            "typography": {"fonts": [{"family": "Play", "roles": ["title", "body"]}]},
            "colors": {"theme": {"accent1": "#0077FF"}},
        },
    }
    ctx = vr.build_context({}, profile, {}, "balanced", {}, get_settings(), 8)
    structure = vr.deck_structure(ctx)
    thesis = vr.Thesis("t1", 1, "claim", "Этапы", "", True, None, [], [], [], [], "cards")
    packet = vr.Packet(0, [thesis], 1, 1, 1, {"t1": ctx.patterns})
    digest = vr.packet_digest(ctx, structure, packet)
    assert "Дизайн-код" not in digest and "Play" not in digest and "#0077FF" not in digest
    assert "библиотека в дизайн-коде" not in digest and "x,y,w,h" not in digest
    changed = copy.deepcopy(profile)
    changed["design_tokens"]["colors"]["theme"]["accent1"] = "#FF0077"
    assert vr.profile_digest(profile) != vr.profile_digest(changed)
    changed_ctx = vr.build_context({}, changed, {}, "balanced", {}, get_settings(), 8)
    assert vr.packet_digest(changed_ctx, vr.deck_structure(changed_ctx), packet) == digest
