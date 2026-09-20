"""Вариант original: загруженная презентация как готовый результат. Смысловой план и план
слайдов строятся без модели по профилю и пакету, композер сохраняет объекты образца: тексты
и число слайдов первой ревизии совпадают с файлом, правка одного блока меняет только его."""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
from pptx import Presentation

from presentation_designer.contracts import ContentPackage, SlidePlan, StoryPlan, TemplateProfile
from presentation_designer.contracts.validators import check_slide_plan, check_story_plan
from presentation_designer.generation.original import original_plan, original_story
from presentation_designer.layout.compose import compose_deck
from presentation_designer.parsing.content.importer import import_content
from presentation_designer.parsing.content.parsers import ParseCache
from presentation_designer.shared.settings import Settings
from tests.layout.conftest import MINI_TEMPLATE, own_profile
from tests.parsing.content.conftest import material


@pytest.fixture(scope="module")
def mini_profile() -> dict[str, Any]:
    return own_profile(MINI_TEMPLATE, "tpl_mini")


def _texts(path: pathlib.Path) -> list[list[str]]:
    prs = Presentation(str(path))
    out: list[list[str]] = []
    for slide in prs.slides:
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                texts.append(" ".join(shape.text_frame.text.split()))
        out.append(sorted(texts))
    return out


def _package(tmp_path: pathlib.Path, import_settings: Settings) -> dict[str, Any]:
    result = import_content(
        [material(MINI_TEMPLATE)],
        {"purpose": "other", "title": "Мини-шаблон"},
        package_id="pkg_orig",
        settings=import_settings,
        cache=ParseCache(tmp_path / "import-cache"),
        use_model=False,
    )
    return dict(result.package)


def test_original_story_and_plan_are_valid_and_cover_every_slide(
    mini_profile: dict[str, Any], tmp_path: pathlib.Path, import_settings: Settings
) -> None:
    package = _package(tmp_path, import_settings)
    story = original_story(package, mini_profile, {"language": "ru"})
    assert not check_story_plan(
        StoryPlan.model_validate(story), ContentPackage.model_validate(package)
    )
    plan = original_plan(story, mini_profile, package, plan_id="plan_orig")
    violations = check_slide_plan(
        SlidePlan.model_validate(plan),
        TemplateProfile.model_validate(mini_profile),
        ContentPackage.model_validate(package),
        StoryPlan.model_validate(story),
    )
    # Обязательные слоты таблицы и диаграммы заполнены самим образцом — композер их сохраняет.
    assert {v.code for v in violations} <= {"slot_required"}, violations
    slides = plan["slides"]
    # Слайд на каждый образец шаблона; собственные композиции библиотеки образцами не считаются.
    samples = [p for p in mini_profile["patterns"] if p["source"]["kind"] == "sample_slide"]
    assert len(slides) == len(story["theses"]) == len(samples)
    assert plan["variant"]["variant_id"] == "original" and plan["slide_count"]["exact"] == len(
        slides
    )
    assert all(b["fit"]["action"] == "as_is" for s in slides for b in s["blocks"])
    assert plan["coverage"]["missing"] == []
    assert not plan["generation_meta"]["models"], "модель не участвует"


def test_original_compose_keeps_texts_and_edit_changes_only_its_block(
    mini_profile: dict[str, Any], tmp_path: pathlib.Path, import_settings: Settings
) -> None:
    package = _package(tmp_path, import_settings)
    story = original_story(package, mini_profile, {"language": "ru"})
    plan = original_plan(story, mini_profile, package, plan_id="plan_orig")
    out = tmp_path / "r1.pptx"
    result = compose_deck(plan, mini_profile, MINI_TEMPLATE, package, out_pptx=out, job_id="job_o")
    assert result.integrity.ok
    assert _texts(out) == _texts(MINI_TEMPLATE)
    assert all(not s.get("removed_object_ids") for s in result.deck["slides"])
    assert all(
        o.get("content_source") in ("sample", "template", "static")
        for s in result.deck["slides"]
        for o in s.get("objects", [])
        if o.get("content_source")
    )

    # Правка из чата: один блок с новым текстом, остальное остаётся объектами образца.
    edited = {**plan, "slides": [dict(s) for s in plan["slides"]]}
    target = next(s for s in edited["slides"] if s["blocks"])
    block = dict(target["blocks"][0])
    block["text"] = "Новый заголовок после правки"
    target["blocks"] = [block, *target["blocks"][1:]]
    out2 = tmp_path / "r2.pptx"
    result2 = compose_deck(
        edited, mini_profile, MINI_TEMPLATE, package, out_pptx=out2, job_id="job_o"
    )
    assert result2.integrity.ok
    before, after = _texts(MINI_TEMPLATE), _texts(out2)
    changed = [i for i, (a, b) in enumerate(zip(before, after, strict=True)) if a != b]
    assert changed == [target["order"] - 1]
    assert any("Новый заголовок после правки" in t for t in after[target["order"] - 1])


def test_real_layers_plan_and_compose_original_without_model(
    mini_profile: dict[str, Any], tmp_path: pathlib.Path, import_settings: Settings
) -> None:
    """Слой плана строит original без провайдера моделей, слой вёрстки пропускает правку по
    фактам и кладёт ревизию в артефакты; тексты совпадают с файлом."""
    from presentation_designer.pipeline.artifacts import ArtifactStore
    from presentation_designer.pipeline.real import RealLayers
    from presentation_designer.pipeline.run import ComposeInput, PlanInput

    package = _package(tmp_path, import_settings)
    story = original_story(package, mini_profile, {"language": "ru"})
    settings = Settings()
    settings.paths.artifacts_dir = tmp_path / "artifacts"
    layers = RealLayers(settings)
    layers._llm_failed = True  # провайдер не настроен: original обязан обходиться без него
    plan = layers.plan(
        PlanInput("job_o", "original", mini_profile, package, story, {"language": "ru"}, None)
    )
    assert plan["variant"]["variant_id"] == "original" and len(plan["slides"]) == 4
    store = ArtifactStore(settings.artifacts_dir)
    with store.stage_revision("job_o", "original", 1) as staging:
        out = layers.compose(
            ComposeInput(
                "job_o", "original", 1, plan, mini_profile, MINI_TEMPLATE, package, story, staging
            )
        )
    assert layers.last_feedback_report is None or not layers.last_feedback_report.get("applied")
    deck_path = store.revision_dir("job_o", "original", 1) / "deck.pptx"
    assert out.slide_count == 4 and _texts(deck_path) == _texts(MINI_TEMPLATE)


def test_edit_validation_tolerates_sample_filled_required_slots(
    mini_profile: dict[str, Any], tmp_path: pathlib.Path, import_settings: Settings
) -> None:
    """Проверка плана после правки: у original обязательный слот без блока (таблица,
    диаграмма, пустой в файле заголовок) — не нарушение, у вариантов вёрстки — нарушение."""
    from presentation_designer.generation.edit import EditError, validate_plan

    package = _package(tmp_path, import_settings)
    story = original_story(package, mini_profile, {"language": "ru"})
    plan = original_plan(story, mini_profile, package, plan_id="plan_orig")
    plan["slides"][0]["blocks"] = [b for b in plan["slides"][0]["blocks"] if b["kind"] != "title"]
    validate_plan(plan, mini_profile, package, story)
    as_balanced = {**plan, "variant": {**plan["variant"], "variant_id": "balanced"}}
    with pytest.raises(EditError):
        validate_plan(as_balanced, mini_profile, package, story)
