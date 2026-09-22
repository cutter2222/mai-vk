"""Правка одного слайда по инструкции: ответ модели заменяет только этот слайд, план остаётся
валидным, композиция вне списка переназначается, переполнение повторяется с подсказкой,
отказ модели не меняет план, служебные слайды правятся из пула роли, без модели — ошибка."""

from __future__ import annotations

import json
from typing import Any

import pytest

from presentation_designer.contracts import ContentPackage, SlidePlan, StoryPlan, TemplateProfile
from presentation_designer.contracts.validators import check_slide_plan
from presentation_designer.generation import edit as ed
from presentation_designer.generation import matching as mt
from presentation_designer.generation import variants as vr
from presentation_designer.llm.skills import get_skill
from presentation_designer.pipeline.run import resolve_slide_count
from tests.generation.test_variants import (  # noqa: F401
    example_package,
    example_story,
    mini_profile,
)

pytestmark = pytest.mark.usefixtures("mini_profile")


@pytest.fixture
def base_plan(
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
) -> dict[str, Any]:
    """Детерминированный план balanced без модели: исходная ревизия для правок."""
    return vr.build_variant_plan(
        example_story,
        mini_profile,
        example_package,
        "balanced",
        {"language": "ru"},
        slide_count=resolve_slide_count("balanced", {"language": "ru"}),
        use_model=False,
    ).plan


def _valid(
    plan: dict[str, Any], profile: dict[str, Any], package: dict[str, Any], story: dict[str, Any]
) -> None:
    violations = check_slide_plan(
        SlidePlan.model_validate(plan),
        TemplateProfile.model_validate(profile),
        ContentPackage.model_validate(package),
        StoryPlan.model_validate(story),
    )
    assert not violations, [str(v) for v in violations]


def _content_index(plan: dict[str, Any]) -> int:
    """Содержательный слайд под правку — с одним тезисом.

    Ответ модели в этих проверках несёт одну мысль, и слайд на два тезиса
    после такой правки терял бы второй. Правка это заметит и откажет
    (`coverage_missing`) — верное поведение, но проверяется здесь не оно.
    """
    slides = ed.ordered_slides(plan)
    single = [
        i
        for i, s in enumerate(slides)
        if s["role"] not in mt.FIXED_ROLES
        and len(s.get("thesis_refs") or []) == 1
        # Со слотом заголовка: на слайде таблицы его нет, и проверки про
        # заголовок там ничего не проверяют.
        and any(b.get("kind") == "title" for b in s.get("blocks") or [])
    ]
    if single:
        # The canned answer contains prose and two bullets, not a KPI-only edit.
        # Choose a text slide so keeping its layout does not require losing that prose.
        text_slides = [
            i
            for i in single
            if any(b.get("kind") in ("body", "bullets") for b in slides[i].get("blocks", []))
        ]
        if text_slides:
            return text_slides[0]
        return single[0]
    return next(
        i for i, s in enumerate(slides) if s["role"] not in mt.FIXED_ROLES and s["thesis_refs"]
    )


def _answer(slide: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    raw = {
        "theses": list(slide["thesis_refs"]),
        "pattern": slide["pattern_id"],
        "title": "Короткий вывод слайда",
        "message": "Один тезис, короче прежнего",
        "visual": "bullets",
        "text": "",
        "items": [{"text": "Первый пункт"}, {"text": "Второй пункт"}],
        "facts": list(slide.get("fact_refs") or [])[:1],
    }
    raw.update(overrides)
    return {"unchanged": False, "reason": "", "change_note": "Заголовок сокращён", "slides": [raw]}


def _edit(
    plan: dict[str, Any], index: int, instruction: str, client: Any, **kw: Any
) -> ed.EditResult:
    return ed.edit_slide(
        plan,
        kw.pop("story"),
        kw.pop("profile"),
        kw.pop("package"),
        slide_index=index,
        instruction=instruction,
        client=client,
        skill=get_skill("slide_editor"),
        settings={"language": "ru"},
        **kw,
    )


def test_edit_replaces_only_the_slide(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    make_client: Any,
    stub: Any,
) -> None:
    index = _content_index(base_plan)
    slide = ed.ordered_slides(base_plan)[index]
    stub.answer(_answer(slide))
    client = make_client(stub)
    result = _edit(
        base_plan,
        index,
        "сделай заголовок короче",
        client,
        story=example_story,
        profile=mini_profile,
        package=example_package,
    )
    assert result.changed and result.plan is not None and result.change_note == "Заголовок сокращён"
    assert result.slide_id == slide["slide_id"]
    _valid(result.plan, mini_profile, example_package, example_story)
    new = ed.ordered_slides(result.plan)[index]
    assert new["slide_id"] == slide["slide_id"] and new["order"] == slide["order"]
    assert new["title"] == "Короткий вывод слайда" and new["revision_note"] == "Заголовок сокращён"
    assert new["thesis_refs"] == slide["thesis_refs"] and new["pattern_id"] == slide["pattern_id"]
    # Остальные слайды не тронуты, порядок и число прежние.
    before = [s for s in ed.ordered_slides(base_plan) if s["slide_id"] != slide["slide_id"]]
    after = [s for s in ed.ordered_slides(result.plan) if s["slide_id"] != slide["slide_id"]]
    assert json.dumps(before, sort_keys=True) == json.dumps(after, sort_keys=True)
    assert len(result.plan["slides"]) == len(base_plan["slides"])
    assert result.plan["comparison"]["pattern_sequence"] == [
        s["pattern_id"] for s in ed.ordered_slides(result.plan)
    ]
    assert result.plan["coverage"]["missing"] == []
    assert {"name": "slide_editor", "version": "0.1.0"} in result.plan["generation_meta"]["skills"]
    assert (
        result.report["llm"]["calls"] == 1
        and result.report["pattern_before"] == slide["pattern_id"]
    )
    # Выдержка для модели несёт просьбу, текущий слайд, соседей и композиции с ёмкостью.
    text = stub.calls[0].messages[-1].text
    assert "Просьба пользователя: «сделай заголовок короче»" in text
    assert f"Слайд {index + 1} из {len(base_plan['slides'])}" in text
    assert "Соседи:" in text and "Композиции шаблона" in text and slide["pattern_id"] in text
    assert stub.calls[0].slide_ids == [slide["slide_id"]]


def test_unknown_pattern_is_remapped_to_current(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    make_client: Any,
    stub: Any,
) -> None:
    index = _content_index(base_plan)
    slide = ed.ordered_slides(base_plan)[index]
    stub.answer(_answer(slide, pattern="pat_nope"))
    result = _edit(
        base_plan,
        index,
        "переделай",
        make_client(stub),
        story=example_story,
        profile=mini_profile,
        package=example_package,
    )
    assert result.changed and result.plan is not None
    assert ed.ordered_slides(result.plan)[index]["pattern_id"] == slide["pattern_id"]
    assert any(f["code"] == "pattern_remapped" for f in result.report["fixes"])
    _valid(result.plan, mini_profile, example_package, example_story)


def test_overflow_is_retried_with_hint(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    make_client: Any,
    stub: Any,
) -> None:
    index = _content_index(base_plan)
    slide = ed.ordered_slides(base_plan)[index]
    # Без запятых и тире лестница не сокращает заголовок: остаётся переполнение.
    long_title = "Очень длинный заголовок который никак не поместится в слот заголовка " * 4
    stub.answer(_answer(slide, title=long_title.strip()), times=1)
    stub.answer(_answer(slide, title="Короткий заголовок"))
    result = _edit(
        base_plan,
        index,
        "сделай длиннее",
        make_client(stub),
        story=example_story,
        profile=mini_profile,
        package=example_package,
    )
    assert result.changed and result.plan is not None
    assert len(stub.calls) == 2
    assert "не помещается" in stub.calls[1].messages[-1].text
    assert ed.ordered_slides(result.plan)[index]["title"] == "Короткий заголовок"
    assert any(f["code"] == "edit_retried" for f in result.report["fixes"])


def test_unchanged_keeps_plan(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    make_client: Any,
    stub: Any,
) -> None:
    index = _content_index(base_plan)
    stub.answer(
        {
            "unchanged": True,
            "reason": "В материалах нет выручки за 2025 год",
            "change_note": "",
            "slides": [],
        }
    )
    result = _edit(
        base_plan,
        index,
        "добавь выручку за 2025 год",
        make_client(stub),
        story=example_story,
        profile=mini_profile,
        package=example_package,
    )
    assert not result.changed and result.plan is None
    assert result.reason == "В материалах нет выручки за 2025 год"
    assert result.slide_id == ed.ordered_slides(base_plan)[index]["slide_id"]


def test_unchanged_without_reason_is_rejected_then_accepted(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    make_client: Any,
    stub: Any,
) -> None:
    """Отказ без причины не проходит проверку ответа: клиент повторяет запрос с подсказкой."""
    index = _content_index(base_plan)
    stub.answer({"unchanged": True, "reason": "", "change_note": "", "slides": []}, times=1)
    stub.answer({"unchanged": True, "reason": "Нет данных", "change_note": "", "slides": []})
    result = _edit(
        base_plan,
        index,
        "добавь",
        make_client(stub, max_retries=2),
        story=example_story,
        profile=mini_profile,
        package=example_package,
    )
    assert not result.changed and result.reason == "Нет данных" and len(stub.calls) == 2


def test_service_slide_uses_role_pool(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    make_client: Any,
    stub: Any,
) -> None:
    title = ed.ordered_slides(base_plan)[0]
    assert title["role"] == "title"
    stub.answer(
        {
            "unchanged": False,
            "reason": "",
            "change_note": "Тема короче",
            "slides": [
                {
                    "theses": [],
                    "pattern": "pat_s2",
                    "title": "Пилот умных уведомлений",
                    "visual": "text",
                    "message": "Для руководителей",
                }
            ],
        }
    )
    result = _edit(
        base_plan,
        0,
        "сделай тему короче",
        make_client(stub),
        story=example_story,
        profile=mini_profile,
        package=example_package,
    )
    assert result.changed and result.plan is not None
    new = ed.ordered_slides(result.plan)[0]
    # Композиция карточек не из пула титульной роли: остаётся титульная.
    assert new["role"] == "title" and new["pattern_id"] == title["pattern_id"]
    assert new["title"] == "Пилот умных уведомлений" and new["thesis_refs"] == title["thesis_refs"]
    _valid(result.plan, mini_profile, example_package, example_story)


def test_requested_visual_extends_candidates(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
) -> None:
    ctx = vr.build_context(
        example_story, mini_profile, example_package, "balanced", {}, vr.get_settings(), None
    )
    slides = ed.ordered_slides(base_plan)
    with_data = next(
        (s for s in slides if any(t.dataset_refs for t in ed.slide_theses(ctx, s))), None
    )
    assert with_data is not None, "в примере есть тезис с набором данных"
    theses = ed.slide_theses(ctx, with_data)
    plain = ed.edit_candidates(ctx, with_data, theses, "сделай короче")
    chart = ed.edit_candidates(ctx, with_data, theses, "покажи это диаграммой")
    assert plain[0].pattern_id == with_data["pattern_id"]
    assert any(c.has_chart for c in chart)
    assert ed.requested_visuals("вынеси цифры в показатели и добавь таблицу") == ["table", "number"]


def test_errors(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    make_client: Any,
    stub: Any,
) -> None:
    with pytest.raises(ed.EditError) as no_model:
        _edit(
            base_plan,
            0,
            "короче",
            None,
            story=example_story,
            profile=mini_profile,
            package=example_package,
        )
    assert no_model.value.code == "edit_llm_not_configured"
    with pytest.raises(ed.EditError) as empty:
        _edit(
            base_plan,
            0,
            "   ",
            make_client(stub),
            story=example_story,
            profile=mini_profile,
            package=example_package,
        )
    assert empty.value.code == "instruction_required"
    with pytest.raises(ed.EditError) as far:
        _edit(
            base_plan,
            99,
            "короче",
            make_client(stub),
            story=example_story,
            profile=mini_profile,
            package=example_package,
        )
    assert far.value.code == "slide_index_out_of_range"
