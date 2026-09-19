"""Валидаторы связей отклоняют ошибочные ссылки и содержимое, несовместимое с видом блока."""

from __future__ import annotations

import copy
from collections.abc import Callable

import pytest

from presentation_designer.contracts import models as m
from presentation_designer.contracts import validators as v

Loader = Callable[[str], dict[str, object]]


def codes(violations: list[v.Violation]) -> set[str]:
    return {x.code for x in violations}


@pytest.fixture
def docs(example: Loader) -> dict[str, object]:
    return {
        "profile": m.TemplateProfile.model_validate(example("template_profile")),
        "package": m.ContentPackage.model_validate(example("content_package")),
        "story": m.StoryPlan.model_validate(example("story_plan")),
        "plan_raw": example("slide_plan"),
    }


def _plan(docs: dict[str, object], mutate: Callable[[dict], None]) -> list[v.Violation]:
    raw = copy.deepcopy(docs["plan_raw"])
    mutate(raw)  # type: ignore[arg-type]
    plan = m.SlidePlan.model_validate(raw)
    return v.check_slide_plan(plan, docs["profile"], docs["package"], docs["story"])  # type: ignore[arg-type]


def test_unknown_slot_rejected(docs: dict[str, object]) -> None:
    def mutate(raw: dict) -> None:
        raw["slides"][2]["blocks"][0]["slot_id"] = "no_such_slot"

    assert "slot_missing" in codes(_plan(docs, mutate))


def test_unknown_pattern_rejected(docs: dict[str, object]) -> None:
    def mutate(raw: dict) -> None:
        raw["slides"][0]["pattern_id"] = "pat_missing"

    assert "pattern_missing" in codes(_plan(docs, mutate))


def test_unknown_fact_rejected(docs: dict[str, object]) -> None:
    def mutate(raw: dict) -> None:
        raw["slides"][2]["blocks"][3]["text"] = "Теряется {fact:f999} сообщений"

    assert "fact_missing" in codes(_plan(docs, mutate))


def test_kind_content_mismatch_rejected(docs: dict[str, object]) -> None:
    def mutate(raw: dict) -> None:
        block = raw["slides"][4]["blocks"][1]
        assert block["kind"] == "bullets"
        block["items"] = []

    assert "kind_content" in codes(_plan(docs, mutate))


def test_slot_kind_mismatch_rejected(docs: dict[str, object]) -> None:
    def mutate(raw: dict) -> None:
        block = raw["slides"][2]["blocks"][1]
        block["kind"] = "label"
        block.pop("number")
        block["text"] = "40 %"

    assert "slot_kind_mismatch" in codes(_plan(docs, mutate))


def test_slide_count_out_of_range_rejected(docs: dict[str, object]) -> None:
    def mutate(raw: dict) -> None:
        raw["slide_count"] = {"exact": 12}

    assert "slide_count" in codes(_plan(docs, mutate))


def test_missing_required_thesis_rejected(docs: dict[str, object]) -> None:
    def mutate(raw: dict) -> None:
        raw["coverage"]["covered"] = [
            c for c in raw["coverage"]["covered"] if c["thesis_id"] != "t6"
        ]

    assert "coverage_missing" in codes(_plan(docs, mutate))


def test_coverage_must_be_confirmed_by_slides(docs: dict[str, object]) -> None:
    def mutate(raw: dict) -> None:
        for s in raw["slides"]:
            if s["slide_id"] == "s5":
                s["thesis_refs"] = []

    assert "coverage_unconfirmed" in codes(_plan(docs, mutate))


def test_required_slot_must_be_filled(docs: dict[str, object]) -> None:
    def mutate(raw: dict) -> None:
        raw["slides"][2]["blocks"] = [
            b for b in raw["slides"][2]["blocks"] if b["slot_id"] != "title"
        ]

    assert "slot_required" in codes(_plan(docs, mutate))


def test_request_min_greater_than_max(example: Loader) -> None:
    raw = copy.deepcopy(example("generation_request"))
    raw["settings"]["slide_count"] = {"min": 15, "max": 10}  # type: ignore[index]
    req = m.GenerationRequest.model_validate(raw)
    assert "slide_count_range" in codes(v.check_generation_request(req))


def test_story_unused_must_keep_fact(example: Loader) -> None:
    package = m.ContentPackage.model_validate(example("content_package"))
    raw = copy.deepcopy(example("story_plan"))
    for t in raw["theses"]:  # type: ignore[union-attr]
        t["fact_refs"] = [r for r in t.get("fact_refs", []) if r != "f2"]
        t["statement"] = t["statement"].replace("{fact:f2}", "часть бюджета")
    story = m.StoryPlan.model_validate(raw)
    assert "must_keep_fact_unused" in codes(v.check_story_plan(story, package))


def test_audit_reason_required_for_not_checked(example: Loader) -> None:
    raw = copy.deepcopy(example("audit_report"))
    for r in raw["results"]:  # type: ignore[union-attr]
        if r["outcome"] == "not_checked":
            r.pop("reason")
    report = m.AuditReport.model_validate(raw)
    assert "reason_required" in codes(v.check_audit_report(report))


def test_result_succeeded_requires_complete_audit(example: Loader) -> None:
    raw = copy.deepcopy(example("generation_result"))
    raw["status"] = "succeeded"
    result = m.GenerationResult.model_validate(raw)
    assert "succeeded_with_gaps" in codes(v.check_generation_result(result))


def test_raise_if_wraps_violations() -> None:
    with pytest.raises(v.ContractError) as info:
        v.raise_if([v.Violation("x", "тест")])
    assert "x: тест" in str(info.value)


def test_slide_plan_overrides_rules(docs: dict[str, object]) -> None:
    """Ручные правки (этап 22): пример проходит; адрес, содержимое, пределы, ресурсы и
    повторы отклоняются своими кодами."""
    assert _plan(docs, lambda raw: None) == []

    def mutate(raw: dict) -> None:
        raw["slides"][2]["overrides"] = [
            {"op": "text", "text": "без адреса"},
            {"op": "style", "target": {"object_id": "10", "slot_id": "no_such"}, "style": {}},
            {"op": "geometry", "target": {"object_id": "10"}},
            {
                "op": "geometry",
                "target": {"object_id": "11"},
                "geometry": {"bbox": {"x": 1.9, "y": 0.1, "width": 0.001, "height": 0.2}},
            },
            {
                "op": "picture",
                "target": {"object_id": "5"},
                "picture": {"source": {"kind": "template", "asset_id": "asset_nope"}},
            },
            {
                "op": "picture",
                "target": {"object_id": "6"},
                "picture": {"source": {"kind": "file"}},
            },
            {"op": "background", "background": {"kind": "solid"}},
            {"op": "background", "background": {"kind": "image"}},
            {"op": "text", "target": {"object_id": "10"}, "text": "раз"},
            {"op": "text", "target": {"object_id": "10"}, "text": "два"},
        ]

    found = codes(_plan(docs, mutate))
    assert {
        "override_target",
        "override_content",
        "override_bbox",
        "asset_missing",
        "override_file_ref",
        "override_duplicate",
    } <= found
