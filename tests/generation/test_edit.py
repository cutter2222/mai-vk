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
    assert {"name": "slide_editor", "version": "0.2.1"} in result.plan["generation_meta"]["skills"]
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
    # Первый отказ возвращается модели: сделать ближайшую выполнимую правку; повторный — принят.
    assert len(stub.calls) == 2
    assert "не отказывай" in stub.calls[1].messages[-1].text


def test_first_refusal_turns_into_best_effort_edit(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    make_client: Any,
    stub: Any,
) -> None:
    """Модель отказала («нет данных»), после подсказки сделала правку без недостающего числа
    и сказала, что прислать: пользователь получает слайд, а не просьбу ввести данные."""
    index = _content_index(base_plan)
    slide = ed.ordered_slides(base_plan)[index]
    stub.answer(
        {"unchanged": True, "reason": "Нет выручки за 2025 год", "change_note": "", "slides": []},
        times=1,
    )
    answer = _answer(slide)
    answer["change_note"] = "Добавил пункт о выручке; пришлите цифру за 2025 год — подставлю"
    stub.answer(answer)
    result = _edit(
        base_plan,
        index,
        "добавь выручку за 2025 год",
        make_client(stub),
        story=example_story,
        profile=mini_profile,
        package=example_package,
    )
    assert result.changed and result.plan is not None
    assert "пришлите цифру" in result.change_note


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
    # Без причины — повтор; первый отказ с причиной — ещё повтор с подсказкой; второй принят.
    assert not result.changed and result.reason == "Нет данных" and len(stub.calls) == 3


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


@pytest.mark.parametrize(
    ("instruction", "count"),
    [
        ("сделай не 4 колонки, а 2", 2),
        ("в две колонки", 2),
        ("оставь три карточки", 3),
        ("вместо 4 столбцов сделай 2", 2),
        ("из четырёх колонок сделай две", 2),
        # Просьбы из чата «Футбол» (VK Tech, слайд 6), которые правка не поняла.
        ("давай в текущий слайдеры поправим колонки сделаем не три а две", 2),
        ("сделаем не три варианда, а два варианта, потому что слишком много элементов", 2),
        ("не три, а две колонки", 2),
        ("колонки две, а не три", 2),
        ("вместо трёх две", 2),
        ("сократи заголовок", None),
        ("сделай 10 слайдов", None),
        ("не 10 слайдов, а 8", None),
        ("сделай 12 колонок", None),
    ],
)
def test_requested_count(instruction: str, count: int | None) -> None:
    assert ed.requested_count(instruction) == count


def test_count_request_brings_grids_with_that_many_columns(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
) -> None:
    ctx = vr.build_context(
        example_story, mini_profile, example_package, "balanced", {}, vr.get_settings(), None
    )
    slides = ed.ordered_slides(base_plan)
    slide = slides[_content_index(base_plan)]
    theses = ed.slide_theses(ctx, slide)
    current = next(p for p in ctx.patterns if p.pattern_id == slide["pattern_id"])
    grids = sorted(
        {ed.columns(p) for p in ctx.patterns if ed.columns(p) >= 2} - {ed.columns(current)}
    )
    assert grids, "в мини-шаблоне есть сетки карточек"
    want = grids[0]
    words = {2: "две", 3: "три", 4: "четыре"}
    instruction = f"сделай не {ed.columns(current) or 5} колонки, а {words.get(want, want)}"
    candidates = ed.edit_candidates(ctx, slide, theses, instruction)
    assert candidates[0].pattern_id == slide["pattern_id"]
    assert ed.columns(candidates[1]) == want
    digest = ed.edit_digest(ctx, base_plan, slide, theses, candidates, instruction)
    assert f"Названо число элементов: {want}" in digest


def test_count_request_stays_in_template_design(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
) -> None:
    """Смешанный режим: правка берёт композиции шаблона, а из встроенных — только ту, на которой
    слайд уже стоит. «Колонки не три, а две» сокращает сетку шаблона на три карточки до двух
    широких (слайд 6 колоды «Футбол», VK Tech), а не приносит встроенную сетку чужого вида.
    В режиме «Все слайды новые» встроенные сетки остаются."""
    slide = ed.ordered_slides(base_plan)[_content_index(base_plan)]
    instruction = "колонки сделаем не три а две"
    ctx = vr.build_context(
        example_story, mini_profile, example_package, "balanced", {}, vr.get_settings(), None
    )
    theses = ed.slide_theses(ctx, slide)
    three = next(p for p in ctx.patterns if p.pattern_id == "pat_s2")
    ctx.patterns = ed.edit_pool(ctx, slide, 2)
    assert all(not p.builtin or p.pattern_id == slide["pattern_id"] for p in ctx.patterns)
    two = next(p for p in ctx.patterns if p.pattern_id == "pat_s2")
    assert ed.columns(two) == 2 and ed.grid_rank(two, 2) == (0, 0) and "label_3" not in two.slots
    # Ёмкость по раскладке сборки: две карточки шире трёх.
    assert two.slots["label_1"].bbox[2] > 1.4 * three.slots["label_1"].bbox[2]
    assert two.slots["label_1"].max_chars > three.slots["label_1"].max_chars
    candidates = ed.edit_candidates(ctx, slide, theses, instruction)
    digest = ed.edit_digest(ctx, base_plan, slide, theses, candidates, instruction)
    fits = next(line for line in digest.splitlines() if line.startswith("Названо число"))
    assert "pat_s2" in fits and "pat_builtin" not in fits
    assert "заголовок меняй, только если о нём просили" in fits
    everything_new = vr.build_context(
        example_story,
        mini_profile,
        example_package,
        "balanced",
        {"design_mode": "all_new"},
        vr.get_settings(),
        None,
    )
    pool = ed.edit_pool(everything_new, slide, 2)
    assert any(p.builtin and ed.grid_rank(p, 2)[0] == 0 for p in pool)


def test_builtin_slide_returns_to_template_design(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
) -> None:
    """Слайд уже стоит на встроенной композиции (ревизия со встроенными карточками): просьба о
    колонках или о дизайне шаблона выбирает только композиции шаблона, просьба о тексте
    оставляет слайд на его композиции."""
    ctx = vr.build_context(
        example_story, mini_profile, example_package, "balanced", {}, vr.get_settings(), None
    )
    slide = dict(ed.ordered_slides(base_plan)[_content_index(base_plan)])
    slide["pattern_id"] = "pat_builtin_cards_grid_cols2_numberedFalse_rows1"
    for count, instruction in (
        (2, "сделай две колонки"),
        (None, "дизайн не меняй, оставляй как был"),
    ):
        pool = ed.edit_pool(ctx, slide, count, instruction)
        assert not any(p.builtin for p in pool) and any(p.pattern_id == "pat_s2" for p in pool)
    pool = ed.edit_pool(ctx, slide, None, "сократи заголовок")
    assert [p.pattern_id for p in pool if p.builtin] == [slide["pattern_id"]]


def test_builtin_grid_goes_back_to_template_grid(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    make_client: Any,
    stub: Any,
) -> None:
    """Слайд на встроенных «Карточках: 2 карточки» (ревизия после прежней правки колонок):
    «дизайн не меняй, оставляй как был» возвращает его в сетку шаблона на два места, отказ
    «менять нечего» не принимается."""
    plan = json.loads(json.dumps(base_plan))
    index = _content_index(plan)
    slide = ed.ordered_slides(plan)[index]
    slide["pattern_id"] = "pat_builtin_cards_grid_cols2_numberedFalse_rows1"
    slide["blocks"] = [
        {"slot_id": "title", "kind": "title", "text": slide["title"]},
        {"slot_id": "card_1_title", "kind": "label", "text": "Тактика"},
        {"slot_id": "card_1_body", "kind": "caption", "text": "Проверяем схемы давления"},
        {"slot_id": "card_2_title", "kind": "label", "text": "Аналитика"},
        {"slot_id": "card_2_body", "kind": "caption", "text": "Связываем решения с данными"},
    ]
    stub.answer(
        {"unchanged": True, "reason": "Менять нечего", "change_note": "", "slides": []}, times=1
    )
    items = [{"text": "Тактика: проверяем схемы"}, {"text": "Аналитика: решения и данные"}]
    stub.answer(_answer(slide, pattern="pat_s2", visual="cards", items=items))
    result = _edit(
        plan,
        index,
        "дизайн не меняй, оставляй как был",
        make_client(stub, max_retries=2),
        story=example_story,
        profile=mini_profile,
        package=example_package,
    )
    assert result.changed and result.plan is not None and len(stub.calls) == 2
    assert "встроенной композицией" in stub.calls[0].messages[-1].text
    new = ed.ordered_slides(result.plan)[index]
    assert new["pattern_id"] == "pat_s2"
    filled = [b["slot_id"] for b in new["blocks"] if str(b.get("slot_id")).startswith("label_")]
    assert filled == ["label_1", "label_2"]
    _valid(result.plan, mini_profile, example_package, example_story)


def test_named_count_puts_the_slide_on_the_template_grid(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    make_client: Any,
    stub: Any,
) -> None:
    index = _content_index(base_plan)
    slide = ed.ordered_slides(base_plan)[index]
    assert slide["pattern_id"] != "pat_s2"
    # Модель оставила прежнюю композицию и дала два пункта — код ставит слайд на сетку шаблона,
    # сокращённую до двух мест.
    items = [{"text": "Первая колонка"}, {"text": "Вторая колонка"}]
    stub.answer(_answer(slide, pattern=slide["pattern_id"], visual="cards", items=items))
    result = _edit(
        base_plan,
        index,
        "сделай в две колонки",
        make_client(stub),
        story=example_story,
        profile=mini_profile,
        package=example_package,
    )
    assert result.changed and result.plan is not None
    new = ed.ordered_slides(result.plan)[index]
    assert new["pattern_id"] == "pat_s2"
    filled = {b["slot_id"] for b in new["blocks"] if b.get("text")}
    assert {"label_1", "label_2"} <= filled and "label_3" not in filled
    _valid(result.plan, mini_profile, example_package, example_story)


@pytest.mark.parametrize(
    ("instruction", "kept"),
    [
        ("колонки сделаем не три а две", True),
        ("сделай две колонки и сократи текст", False),
    ],
)
def test_count_request_keeps_the_slide_text(
    instruction: str,
    kept: bool,
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    make_client: Any,
    stub: Any,
) -> None:
    """Три карточки в две (колода «Футбол», слайд 6): модель потеряла третий пункт и сменила
    заголовок, о котором не просили. Просили только колонки — код возвращает прежний заголовок
    и прежние пункты дословно, склеив соседние; просили сократить текст — пункты модели."""
    plan = json.loads(json.dumps(base_plan))
    index = _content_index(plan)
    slide = ed.ordered_slides(plan)[index]
    texts = ["Тактика как гипотеза", "Аналитика как инструмент", "Статус концепции"]
    slide["pattern_id"] = "pat_s2"
    slide["blocks"] = [{"slot_id": "title_1", "kind": "title", "text": slide["title"]}] + [
        {"slot_id": f"label_{i + 1}", "kind": "label", "text": t} for i, t in enumerate(texts)
    ]
    short = [{"text": "Тактика"}, {"text": "Аналитика как инструмент"}]
    stub.answer(
        _answer(slide, pattern="pat_s2", visual="cards", title="Другой заголовок", items=short)
    )
    result = _edit(
        plan,
        index,
        instruction,
        make_client(stub),
        story=example_story,
        profile=mini_profile,
        package=example_package,
    )
    assert result.changed and result.plan is not None
    new = ed.ordered_slides(result.plan)[index]
    assert new["pattern_id"] == "pat_s2" and new["title"] == slide["title"]
    labels = [b["text"] for b in new["blocks"] if str(b.get("slot_id")).startswith("label_")]
    fixes = {f["code"] for f in result.report["fixes"]}
    if kept:
        assert labels == ["Тактика как гипотеза", "Аналитика как инструмент. Статус концепции"]
        assert {"edit_items_kept", "edit_title_kept"} <= fixes
    else:
        assert labels == ["Тактика", "Аналитика как инструмент"]
        assert "edit_items_kept" not in fixes


def test_regroup_keeps_model_grouping_and_old_wording() -> None:
    old = ["Тактика как гипотеза", "Аналитика как инструмент", "Статус концепции: основа"]
    answer = [
        {"sub": "Тактика и аналитика", "text": "Тактика как гипотеза. Аналитика как инструмент"},
        {"sub": "Статус концепции", "text": "Статус концепции: основа"},
    ]
    assert ed.regroup_items(old, answer, 2) == [
        "Тактика как гипотеза. Аналитика как инструмент",
        "Статус концепции: основа",
    ]
    # Потерян пункт или пункты переставлены — группировке модели не доверяем.
    assert ed.regroup_items(old, answer[:1], 2) is None
    assert ed.regroup_items(old, list(reversed(answer)), 2) is None
    # Без группировки модели склеивается самая короткая соседняя пара.
    assert ed.merge_items(old, 2) == [
        "Тактика как гипотеза. Аналитика как инструмент",
        "Статус концепции: основа",
    ]


def test_violations_of_other_slides_do_not_block_the_edit(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    make_client: Any,
    stub: Any,
) -> None:
    index = _content_index(base_plan)
    slides = ed.ordered_slides(base_plan)
    slide = slides[index]
    # Сборка оставила на другом слайде нарушение (ссылку на факт вне пакета): правка тут ни при чём.
    broken = json.loads(json.dumps(base_plan))
    other = next(
        s
        for s in ed.ordered_slides(broken)
        if s["slide_id"] != slide["slide_id"] and s.get("blocks")
    )
    other["blocks"][0]["fact_refs"] = ["f_missing"]
    assert ed.plan_violations(broken, mini_profile, example_package, example_story)
    stub.answer(_answer(slide))
    result = _edit(
        broken,
        index,
        "сделай заголовок короче",
        make_client(stub),
        story=example_story,
        profile=mini_profile,
        package=example_package,
    )
    assert result.changed and result.plan is not None


def test_section_thesis_stays_on_the_edited_slide(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    make_client: Any,
    stub: Any,
) -> None:
    """Обязательный тезис-раздел, который держится только на этом слайде (колода «Футбол»,
    слайд 6), модели не показывается: ссылку на него правка переносит сама, иначе проверка
    покрытия отклоняла бы любую правку слайда."""
    index = _content_index(base_plan)
    story = json.loads(json.dumps(example_story))
    section = next(t for t in story["theses"] if t["kind"] == "section")
    section["required"] = True
    sid = section["thesis_id"]
    plan = json.loads(json.dumps(base_plan))
    for s in plan["slides"]:
        s["thesis_refs"] = [t for t in s.get("thesis_refs") or [] if t != sid]
    slide = ed.ordered_slides(plan)[index]
    theses = list(slide["thesis_refs"])
    slide["thesis_refs"] = [*theses, sid]
    stub.answer(_answer(slide, theses=theses))
    result = _edit(
        plan,
        index,
        "сделай заголовок короче",
        make_client(stub),
        story=story,
        profile=mini_profile,
        package=example_package,
    )
    assert result.changed and result.plan is not None
    assert ed.ordered_slides(result.plan)[index]["thesis_refs"] == [*theses, sid]
    assert sid not in result.plan["coverage"]["missing"]


def test_edit_without_visible_change_makes_no_revision(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    make_client: Any,
    stub: Any,
) -> None:
    """Модель вернула тот же слайд («цифры нет — пришлите»): ревизии без изменений нет, а её
    пояснение уходит пользователю ответом."""
    index = _content_index(base_plan)
    slide = ed.ordered_slides(base_plan)[index]
    stub.answer(_answer(slide), times=1)
    kw = {"story": example_story, "profile": mini_profile, "package": example_package}
    first = _edit(base_plan, index, "сократи", make_client(stub), **kw)
    assert first.changed and first.plan is not None
    same = _answer(slide)
    same["change_note"] = "Цифры за 2025 год нет — пришлите, подставлю"
    stub.answer(same)
    second = _edit(first.plan, index, "добавь выручку за 2025 год", make_client(stub), **kw)
    assert not second.changed and second.plan is None
    assert second.reason == "Цифры за 2025 год нет — пришлите, подставлю"


FOUND = [
    {
        "text": "По данным InfoWatch, в 2025 году зарегистрировано 739 утечек данных.",
        "url": "https://www.infowatch.ru/news/2025",
        "title": "InfoWatch",
    }
]


def test_data_request_searches_the_web_first(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    make_client: Any,
    stub: Any,
    monkeypatch: Any,
) -> None:
    """Просят статистику, которой нет в материалах: поиск до запроса, найденное с адресом."""
    monkeypatch.setattr(ed, "find_evidence", lambda *a, **k: FOUND)
    index = _content_index(base_plan)
    stub.answer(_answer(ed.ordered_slides(base_plan)[index]))
    result = _edit(
        base_plan,
        index,
        "добавь статистику утечек паролей за 2025 год",
        make_client(stub),
        story=example_story,
        profile=mini_profile,
        package=example_package,
        web_search=True,
    )
    assert result.changed
    text = stub.calls[0].messages[-1].text
    assert "Найдено в интернете" in text and "https://www.infowatch.ru/news/2025" in text
    assert result.report["web_evidence"] == ["https://www.infowatch.ru/news/2025"]


def test_refusal_for_missing_data_searches_and_retries(
    base_plan: dict[str, Any],
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    make_client: Any,
    stub: Any,
    monkeypatch: Any,
) -> None:
    """Модель упёрлась в нехватку данных — поиск и повтор с найденным, а не «пришлите»."""
    monkeypatch.setattr(ed, "find_evidence", lambda *a, **k: FOUND)
    index = _content_index(base_plan)
    refusal = {
        "unchanged": True,
        "reason": "Нет данных — пришлите",
        "change_note": "",
        "slides": [],
    }
    stub.answer(refusal, times=2)
    stub.answer(_answer(ed.ordered_slides(base_plan)[index]))
    result = _edit(
        base_plan,
        index,
        "дополни слайд",
        make_client(stub),
        story=example_story,
        profile=mini_profile,
        package=example_package,
        web_search=True,
    )
    assert result.changed and len(stub.calls) == 3
    assert "Найдено в интернете" in stub.calls[2].messages[-1].text
    assert "Найдено в интернете" not in stub.calls[0].messages[-1].text


def test_evidence_query_keeps_the_subject() -> None:
    from presentation_designer.parsing.content.research import evidence_query

    assert evidence_query("добавь на слайд статистику утечек паролей за 2025 год") == (
        "статистику утечек паролей за 2025 год"
    )
    assert ed.wants_data("добавь статистику утечек паролей")
    assert not ed.wants_data("по данным Verizon 81% утечек связаны с паролями — добавь")
