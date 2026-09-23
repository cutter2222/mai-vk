"""Topic-only generation must remain a visibly labelled concept."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from presentation_designer.contracts import ContentPackage, StoryPlan
from presentation_designer.contracts.validators import check_story_plan
from presentation_designer.generation import story as st
from presentation_designer.generation import variants as vr
from presentation_designer.generation.grounding import (
    CONCEPT_POLICY,
    CONCEPT_WARNING,
    is_concept,
    topic_only,
)
from presentation_designer.llm.skills import get_skill
from presentation_designer.parsing.content.importer import import_content
from presentation_designer.shared.settings import Settings
from tests.generation.test_content_first import _setup


@pytest.fixture
def topic_package(import_settings: Settings) -> dict[str, Any]:
    return import_content(
        [],
        {
            "title": "iPhone 18",
            "purpose": "product",
            "goal": "Обсудить продукт",
            "audience": "Команда",
            "must_include": ["Характеристики", "Перспективы"],
            "slide_count": {"exact": 10},
        },
        package_id="pkg_topic",
        settings=import_settings,
        use_model=False,
    ).package


def test_metadata_and_requested_sections_are_not_evidence(topic_package: dict[str, Any]) -> None:
    before = copy.deepcopy(topic_package)
    assert topic_only(topic_package)
    digest, _ = st.story_digest(topic_package, st.effective_brief(topic_package, {}))
    assert CONCEPT_POLICY in digest
    # Safety instruction survives truncation of the source blocks.
    assert CONCEPT_POLICY in st.story_digest(topic_package, {}, max_chars=1)[0]
    assert topic_package == before


@pytest.mark.parametrize(
    "block",
    [
        {"kind": "paragraph", "text": "Пилот работает только после согласия."},
        {"kind": "bullets", "items": ["Участие добровольное"]},
        {"kind": "paragraph", "text": "Пилот завершён.", "tags": ["brief", "notes"]},
        {"kind": "figure", "asset_id": "img_1"},
    ],
)
def test_supplied_content_is_not_topic_only(
    topic_package: dict[str, Any], block: dict[str, Any]
) -> None:
    topic_package["blocks"].append(block)
    assert not topic_only(topic_package)


def test_notes_and_dataset_are_material(topic_package: dict[str, Any]) -> None:
    topic_package["brief"]["notes"] = "Пилот работает только после согласия."
    assert not topic_only(topic_package)
    topic_package["brief"].pop("notes")
    topic_package["datasets"] = [{"rows": [["Пилот", 12]]}]
    assert not topic_only(topic_package)


def test_empty_upload_does_not_claim_grounding(topic_package: dict[str, Any]) -> None:
    topic_package["mode"] = "mixed"
    topic_package["sources"].append({"source_id": "src_empty", "extracted": False})
    topic_package["blocks"].append({"kind": "paragraph", "text": "  "})
    assert topic_only(topic_package)


@pytest.mark.parametrize("language", ["ru", "en"])
def test_no_model_story_is_marked_and_count_preserved(
    topic_package: dict[str, Any], language: str
) -> None:
    result = st.build_story(topic_package, {"language": language}, use_model=False)
    story = result.story
    assert is_concept(story)
    # The importer represents an exact request as the equivalent closed range.
    assert story["effective_brief"]["slide_count"] == topic_package["brief"]["slide_count"]
    assert story["effective_brief"]["slide_count"] == {"min": 10, "max": 10}
    assert any(w["code"] == CONCEPT_WARNING for w in result.report["fixes"])
    assert any("источники" in a or "sources" in a for a in story["assumptions"])
    assert not check_story_plan(
        StoryPlan.model_validate(story), ContentPackage.model_validate(topic_package)
    )


def test_model_cannot_omit_concept_warning(
    topic_package: dict[str, Any], make_client: Any, stub: Any
) -> None:
    stub.answer(
        {
            "key_takeaway": "Вопросы для исследования продукта",
            "theses": [
                {
                    "id": "a",
                    "kind": "context",
                    "statement": "Что нужно проверить?",
                    "required": True,
                }
            ],
        }
    )
    story = st.build_story(
        topic_package, {}, client=make_client(stub), skill=get_skill("story_planner")
    ).story
    assert is_concept(story)
    assert CONCEPT_POLICY in "\n".join(m.text for m in stub.calls[0].messages)


@pytest.mark.parametrize("language,label", [("ru", "Концепция:"), ("en", "Concept:")])
def test_concept_reaches_variant_prompt_and_cover(language: str, label: str) -> None:
    ctx, packet, _ = _setup()
    ctx.story = {
        "language": language,
        "effective_brief": {"title": "iPhone 18"},
        "warnings": [{"code": CONCEPT_WARNING, "message": "No sources"}],
    }
    structure = vr.deck_structure(ctx)
    draft = vr._title_draft(ctx, structure)
    assert draft.title == label + " iPhone 18"
    assert draft.title == vr._cover_title(ctx.story)
    assert "источники" in draft.message or "sources" in draft.message
    assert CONCEPT_POLICY in vr.packet_digest(ctx, structure, packet)


def test_evidence_classification_participates_in_story_cache(topic_package: dict[str, Any]) -> None:
    block = {
        "block_id": "b_extra",
        "kind": "paragraph",
        "text": "Проверить риски",
        "tags": ["brief", "goal"],
    }
    topic_package["blocks"].append(block)
    before = st.story_key(topic_package, {})
    block["tags"] = ["brief", "notes"]
    assert st.story_key(topic_package, {}) != before


def test_material_story_does_not_get_concept_label() -> None:
    ctx, packet, _ = _setup()
    ctx.story = {"effective_brief": {"title": "Результаты пилота"}}
    structure = vr.deck_structure(ctx)
    assert vr._title_draft(ctx, structure).title == "Результаты пилота"
    assert CONCEPT_POLICY not in vr.packet_digest(ctx, structure, packet)


def test_concept_warning_and_title_survive_slide_plan_assembly() -> None:
    ctx, _, _ = _setup()
    ctx.profile["template_id"] = "tpl_test"
    ctx.package["package_id"] = "pkg_test"
    ctx.story = {
        "story_id": "story_test",
        "effective_brief": {"title": "iPhone 18"},
        "warnings": [{"code": CONCEPT_WARNING, "message": "Нет источников"}],
    }
    draft = vr._title_draft(ctx, vr.deck_structure(ctx))
    draft.blocks = vr.fill_blocks(ctx, draft)
    plan = vr.build_document(ctx, [draft], plan_id="plan_test", generation_meta={}, rationale="")
    assert plan["warnings"] == ctx.story["warnings"]
    assert any(b.get("text") == "Концепция: iPhone 18" for b in plan["slides"][0]["blocks"])


def test_cover_fit_uses_same_labelled_text_as_export(monkeypatch: Any) -> None:
    ctx, _, _ = _setup()
    ctx.story = {
        "effective_brief": {"title": "iPhone 18", "audience": "Команда"},
        "warnings": [{"code": CONCEPT_WARNING, "message": "Нет источников"}],
    }
    original = vr._first_fitting
    measured: list[tuple[str, str]] = []

    def capture(context: Any, patterns: Any, title: str, subtitle: str) -> Any:
        measured.append((title, subtitle))
        return original(context, patterns, title, subtitle)

    monkeypatch.setattr(vr, "_first_fitting", capture)
    draft = vr._title_draft(ctx, vr.deck_structure(ctx))
    assert (draft.title, draft.message) in measured
