"""Content → native diagrams, safe fitting and mode boundaries."""

from copy import deepcopy
from typing import Any

import pytest

from presentation_designer.generation import matching as mt
from presentation_designer.generation import variants as vr
from presentation_designer.generation.design_mode import profile_for_mode
from presentation_designer.layout import diagrams
from tests.generation.test_content_first import _setup


def _diagram() -> dict[str, Any]:
    return {
        "pattern_id": "diagram",
        "role": "process",
        "source": {"kind": "builtin"},
        "slots": [
            {"slot_id": "title", "kind": "title"},
            {"slot_id": "lead", "kind": "body"},
            {
                "slot_id": "diagram",
                "kind": "diagram",
                "required": True,
                "bbox": {"x": 0.05, "y": 0.25, "width": 0.9, "height": 0.65},
            },
        ],
    }


def _answer(kind: str = "process") -> tuple[vr.Context, vr.Packet, dict[str, Any]]:
    ctx, packet, answer = _setup()
    ctx.patterns.insert(0, mt.pattern_info(_diagram()))
    answer["slides"][0].update(
        visual="timeline" if kind == "timeline" else "diagram",
        diagram_kind=kind,
        items=[
            {"sub": "Старт", "text": "Согласие"},
            {"sub": "Пилот", "text": "Проверка"},
            {"sub": "Итог", "text": "Решение"},
        ],
    )
    return ctx, packet, answer


@pytest.mark.parametrize("kind", diagrams.KINDS)
def test_structured_content_becomes_native_diagram(kind: str) -> None:
    ctx, packet, answer = _answer(kind)
    before = deepcopy(answer)
    draft = vr.make_validator(ctx, packet)(answer)["drafts"][0]
    vr.fit_draft(ctx, draft)
    block = next(b for b in draft.blocks if b["kind"] == "diagram")
    assert block["diagram"]["kind"] == kind
    assert block["diagram"]["items"] == [
        {"text": it["sub"], "sub": it["text"]} for it in answer["slides"][0]["items"]
    ]
    assert not draft.unplaced_text and not draft.overflow
    assert not draft.splittable, "Do not sever cycles or orphan hierarchy children"
    assert answer == before


@pytest.mark.parametrize(
    "mode,expected", [("mixed", True), ("all_new", True), ("template_only", False)]
)
def test_diagram_candidates_respect_mode(mode: str, expected: bool) -> None:
    raw = _diagram()
    profile = profile_for_mode({"patterns": [raw]}, {"design_mode": mode})
    candidates = mt.candidates_for(
        mt.profile_patterns(profile), mt.Need("diagram", items=3, has_diagram=True), "balanced"
    )
    assert bool(candidates) == expected


@pytest.mark.parametrize("kind", [None, "invented"])
def test_missing_relationship_kind_does_not_invent_a_process(kind: str | None) -> None:
    ctx, packet, answer = _answer()
    answer["slides"][0]["diagram_kind"] = kind
    draft = vr.make_validator(ctx, packet)(answer)["drafts"][0]
    assert draft.visual == "bullets" and not draft.pattern.single("diagram")


@pytest.mark.parametrize(
    "kind,count,long", [("venn", 4, False), ("process", 7, False), ("cycle", 3, True)]
)
def test_unsafe_diagram_falls_back_without_losing_items(kind: str, count: int, long: bool) -> None:
    ctx, packet, answer = _answer(kind)
    ctx.patterns.append(
        mt.pattern_info(
            {
                "pattern_id": "list",
                "role": "bullets",
                "slots": [{"slot_id": "list", "kind": "bullets"}],
            }
        )
    )
    items = [
        {"text": f"Пункт {i}" + (" Подробное пояснение" * 10 if long else "")} for i in range(count)
    ]
    answer["slides"][0]["items"] = items
    draft = vr.make_validator(ctx, packet)(answer)["drafts"][0]
    vr.fit_draft(ctx, draft)
    assert not any(b["kind"] == "diagram" for b in draft.blocks)
    assert not draft.unplaced_text
    visible = str(draft.blocks)
    assert all(it["text"] in visible for it in items)


def test_diagram_preserves_facts_in_heading_and_does_not_add_nodes() -> None:
    ctx, packet, answer = _answer()
    answer["slides"][0]["items"][0]["sub"] = "Охват {fact:f1}"
    answer["slides"][0]["facts"] = ["f1", "f2"]
    draft = vr.make_validator(ctx, packet)(answer)["drafts"][0]
    vr.fit_draft(ctx, draft)
    block = next(b for b in draft.blocks if b["kind"] == "diagram")
    assert len(block["diagram"]["items"]) == 3
    assert block["diagram"]["items"][0]["text"] == "Охват 60 %"
    assert block["fact_refs"] == ["f1"]
    assert "{fact:f2}" in str(draft.blocks)


def test_plain_text_cannot_select_empty_diagram_pane() -> None:
    pattern = mt.pattern_info(_diagram())
    assert not mt.candidates_for([pattern], mt.Need("text"), "balanced")
    assert diagrams.DiagramStyle.from_profile({}).sub_size_pt >= 16


def test_relationships_are_not_merged_and_diagram_text_counts_toward_density() -> None:
    ctx, packet, answer = _answer("hierarchy")
    draft = vr.make_validator(ctx, packet)(answer)["drafts"][0]
    vr.fit_draft(ctx, draft)
    assert vr.merge_drafts(ctx, draft, deepcopy(draft)) is None
    block = next(b for b in draft.blocks if b["kind"] == "diagram")
    expected = sum(len(it[key]) for it in block["diagram"]["items"] for key in ("text", "sub"))
    assert vr.slide_chars(ctx, [block]) == expected


def test_unplaced_diagram_content_requests_retry_when_no_text_layout_exists() -> None:
    ctx, packet, answer = _answer("cycle")
    ctx.patterns = [ctx.patterns[0]]
    answer["slides"][0]["items"] = [{"text": f"Длинное объяснение {i} " * 15} for i in range(3)]
    draft = vr.make_validator(ctx, packet)(answer)["drafts"][0]
    vr.fit_draft(ctx, draft)
    assert draft.unplaced_text
    assert "часть содержания не размещена" in (vr.overflow_hint([draft]) or "")


def test_chart_keeps_series_of_one_scale() -> None:
    """Проценты и число пользователей на одной оси: проценты прижаты к нулю — второй
    шкалы нет, поэтому остаются ряды вида и единицы первого, сопоставимые по величине."""
    ds = {
        "columns": [
            {"name": "Месяц", "type": "date"},
            {"name": "Открываемость", "type": "percent", "unit": "%"},
            {"name": "Отписки", "type": "percent", "unit": "%"},
            {"name": "Активные пользователи", "type": "number"},
            {"name": "Доля сессий", "type": "percent", "unit": "%"},
        ],
        "rows": [["Май", 31, 4.1, 120500, 0.01], ["Июнь", 38, 3.2, 131200, 0.02]],
    }
    series = ["Открываемость", "Отписки", "Активные пользователи", "Доля сессий"]
    assert vr._one_scale(ds, series) == ["Открываемость", "Отписки"]
    assert vr._one_scale(ds, ["Активные пользователи", "Открываемость"]) == [
        "Активные пользователи"
    ]


def test_font_floor_grows_with_wide_canvas() -> None:
    """Canva и Google Slides экспортируют слайд в 20 дюймов: 12 pt там — мелкий текст."""
    from presentation_designer.generation.capacity import min_pt_for

    assert min_pt_for("body", body_pt=12, title_pt=20, slide_w_emu=9144000) == 12
    assert min_pt_for("body", body_pt=12, title_pt=20, slide_w_emu=12192000) == 12
    assert min_pt_for("body", body_pt=12, title_pt=20, slide_w_emu=18288000) == 18
    assert min_pt_for("title", body_pt=12, title_pt=20, slide_w_emu=18288000) == 30


def test_vendor_meta_slides_are_not_compositions() -> None:
    """«Credits» и «How to use this presentation» автора шаблона в подбор не идут, а слайд
    с адресом сервиса только в колонтитуле — идёт."""
    credits = {
        "slots": [
            {"kind": "title", "sample_text": "Credits"},
            {"kind": "body", "sample_text": "SlidesCarnival for the presentation template"},
        ]
    }
    content = {
        "slots": [
            {"kind": "title", "sample_text": "Who we are?"},
            {"kind": "caption", "sample_text": "SLIDESCARNIVAL.COM"},
        ]
    }
    assert mt.vendor_meta(credits) and not mt.vendor_meta(content)
