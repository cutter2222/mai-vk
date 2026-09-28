"""Планы трёх вариантов: структура паттернов и кандидаты кодом, измерение ёмкости и лестница,
ключ кэша, планы на записанных ответах (replay) для пакета из examples/content на двух
собственных шаблонах, детерминированный план без модели, кэш готовых планов, повтор негодного
пакета с подсказкой, точное число слайдов, различимость вариантов."""

from __future__ import annotations

import copy
import hashlib
import json
import pathlib
import tempfile
from types import SimpleNamespace
from typing import Any

import pytest

from presentation_designer.contracts import ContentPackage, SlidePlan, StoryPlan, TemplateProfile
from presentation_designer.contracts.validators import check_slide_plan
from presentation_designer.generation import capacity as cap
from presentation_designer.generation import matching as mt
from presentation_designer.generation import variants as vr
from presentation_designer.generation.story import build_story
from presentation_designer.llm.skills import get_skill
from presentation_designer.parsing.content.importer import import_content
from presentation_designer.parsing.content.parsers import ParseCache
from presentation_designer.parsing.template.analyzer import analyze_template
from presentation_designer.pipeline.run import resolve_slide_count
from presentation_designer.shared.settings import Settings, get_settings
from tests.fixtures.rich_template import build_rich_template
from tests.fixtures.variety_template import build_variety_template
from tests.parsing.content.conftest import EXAMPLES

EXAMPLE_FILES = ("overview.docx", "metrics.xlsx", "notes.md", "openrate_chart.png", "logo.png")
MINI_TEMPLATE = (
    pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "pptx" / "mini_template.pptx"
)


def test_merge_across_sections_preserves_text_and_all_facts(monkeypatch: Any) -> None:
    ctx = SimpleNamespace(
        patterns=[],
        variant_id="balanced",
        has_datasets=False,
        facts={"f1": {"raw": "5 дней"}, "f2": {"raw": "12 дней"}},
    )
    monkeypatch.setattr(vr, "candidates_for", lambda *a, **kw: [object()])
    monkeypatch.setattr(vr, "fit_draft", lambda ctx, draft: draft)
    a = vr.Draft(
        kind="content",
        theses=["t1"],
        pattern=None,
        section="s1",
        title="Сроки",
        text="Подготовка {fact:f1}",
        facts=["f1", "f2"],
    )
    b = vr.Draft(
        kind="content",
        theses=["t2"],
        pattern=None,
        section="s2",
        title="Условия",
        text="Выход только после разрешений",
    )
    assert vr.merge_drafts(ctx, a, b) is None
    merged = vr.merge_drafts(ctx, a, b, across_sections=True)
    assert merged is not None
    assert merged.theses == ["t1", "t2"]
    text = " ".join(item["text"] for item in merged.items)
    assert "Подготовка {fact:f1}" in text and "{fact:f2}" in text
    assert "Выход только после разрешений" in text
    a.items = [{"text": str(i)} for i in range(8)]
    assert vr.merge_drafts(ctx, a, b, across_sections=True) is None


def own_profile(path: pathlib.Path, template_id: str) -> dict[str, Any]:
    data = path.read_bytes()
    return analyze_template(
        path,
        template_id=template_id,
        name=path.name,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        settings=get_settings(),
        llm_client=None,
        render_slots=None,
        render=False,
        use_vlm=False,
        workdir=pathlib.Path(tempfile.mkdtemp()),
    ).profile


@pytest.fixture(scope="module")
def mini_profile() -> dict[str, Any]:
    return own_profile(MINI_TEMPLATE, "tpl_mini")


@pytest.fixture(scope="module")
def rich_profile(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return own_profile(
        build_rich_template(tmp_path_factory.mktemp("tpl") / "rich_template.pptx"), "tpl_rich"
    )


@pytest.fixture(scope="module")
def variety_profile(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Два титула, два разделителя и два финала разного тона, два карточных образца одной
    группы (tests/fixtures/variety_template.py)."""
    return own_profile(
        build_variety_template(tmp_path_factory.mktemp("tpl") / "variety_template.pptx"),
        "tpl_variety",
    )


@pytest.fixture
def example_package(materials: Any, cache: ParseCache, import_settings: Settings) -> dict[str, Any]:
    files = materials(*EXAMPLE_FILES, root=EXAMPLES)
    brief = json.loads((EXAMPLES / "brief.json").read_text())
    return import_content(
        files,
        brief,
        package_id="pkg_example",
        settings=import_settings,
        cache=cache,
        use_model=False,
    ).package


@pytest.fixture
def example_story(example_package: dict[str, Any], replay_client: Any) -> dict[str, Any]:
    return build_story(
        example_package, {"language": "ru"}, client=replay_client, skill=get_skill("story_planner")
    ).story


def _assert_valid(
    plan: dict[str, Any],
    profile: dict[str, Any],
    package: dict[str, Any],
    story: dict[str, Any],
) -> SlidePlan:
    doc = SlidePlan.model_validate(plan)
    violations = check_slide_plan(
        doc,
        TemplateProfile.model_validate(profile),
        ContentPackage.model_validate(package),
        StoryPlan.model_validate(story),
    )
    assert not violations, [str(v) for v in violations]
    return doc


def _assert_overflow_reported(plan: dict[str, Any], report: dict[str, Any]) -> None:
    """Переполнение после лестницы не скрывается: блок помечен action=overflow, план несёт
    предупреждение capacity_overflow, счётчик отчёта совпадает с числом таких слайдов."""
    overflow_slides = {
        s["slide_id"]
        for s in plan["slides"]
        for b in s["blocks"]
        if (b.get("fit") or {}).get("action") == "overflow"
    }
    assert report["counts"]["overflow"] == len(overflow_slides)
    warned = sum(1 for w in plan["warnings"] if w["code"] == "capacity_overflow")
    assert warned == len(overflow_slides)


def _plans(
    story: dict[str, Any],
    profile: dict[str, Any],
    package: dict[str, Any],
    client: Any,
    settings: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, vr.PlanResult]:
    out: dict[str, vr.PlanResult] = {}
    settings = settings or {"language": "ru"}
    for variant_id in vr.VARIANTS:
        out[variant_id] = vr.build_variant_plan(
            story,
            profile,
            package,
            variant_id,
            settings,
            slide_count=resolve_slide_count(variant_id, settings),
            client=client,
            skill=get_skill("variant_planner") if client is not None else None,
            use_model=client is not None,
            **kwargs,
        )
    return out


# ---------- структура паттернов и кандидаты ----------


def test_pattern_structure_visuals_and_candidates(mini_profile: dict[str, Any]) -> None:
    patterns = mt.profile_patterns(mini_profile)
    by_id = {p.pattern_id: p for p in patterns}
    assert by_id["pat_s1"].role == "title" and by_id["pat_s1"].title is not None
    cards = by_id["pat_s2"]
    assert cards.cards is not None and cards.cards.count == 3 and cards.item_capacity == 3
    assert {"bullets", "cards"} <= cards.visuals()
    table = by_id["pat_s3"]
    assert table.has_table and table.has_chart and table.fillable(has_datasets=True)
    assert not table.fillable(has_datasets=False)
    assert {"table", "chart"} <= table.visuals()
    # Таблица и диаграмма — только при наборе данных; без него кандидатов на chart нет.
    assert not mt.candidates_for(patterns, mt.Need("chart"), "balanced", has_datasets=False)
    chart = mt.candidates_for(
        patterns, mt.Need("chart", has_dataset=True), "balanced", has_datasets=True
    )
    # Первым идёт паттерн шаблона; собственные композиции библиотеки добирают список после
    # него — они годятся под диаграмму, но своего образца в шаблоне у них нет.
    assert chart[0].pattern_id == "pat_s3"
    assert all(p.builtin for p in chart[1:])
    bullets = mt.candidates_for(patterns, mt.Need("bullets", items=3), "compact")
    assert bullets and bullets[0].pattern_id == "pat_s2"
    # Служебные роли содержательными кандидатами не становятся.
    assert all(p.role not in mt.FIXED_ROLES for p in bullets)
    assert mt.fixed_pattern(patterns, "title") is not None
    assert mt.fixed_pattern(patterns, "thanks") is not None
    # Разделителя в этом шаблоне нет, и роль закрывает своя композиция библиотеки:
    # служебный слайд из библиотеки берётся именно тогда, когда в шаблоне роли нет.
    divider = mt.fixed_pattern(patterns, "section_divider")
    assert divider is not None and divider.builtin
    # Без набора данных таблица заменяется доступной подачей: к паттернам шаблона добавлены
    # собственные композиции, поэтому в запасе есть и ряд показателей.
    assert mt.fallback_visual("table", patterns, has_datasets=False) in (
        "bullets",
        "cards",
        "text",
        "number",
    )
    # Собственная композиция «Шаги» даёт подачу для последовательности, которой в этом
    # шаблоне нет: замена на карточки больше не нужна.
    assert mt.fallback_visual("timeline", patterns, has_datasets=True) in ("timeline", "cards")
    assert "заголовок" in by_id["pat_s2"].summary() and "карточки" in by_id["pat_s2"].summary()


def _two_column_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Профиль с композицией «две колонки списков» и семью карточками поверх mini_template:
    две колонки буллитов в одной группе и карточки на 7 — как в фирменных шаблонах."""
    doc = json.loads(json.dumps(profile))
    base = next(p for p in doc["patterns"] if p["pattern_id"] == "pat_s2")
    columns = json.loads(json.dumps(base))
    columns.update(pattern_id="pat_cols", role="two_column", name="Два столбца")
    title = next(s for s in base["slots"] if s["kind"] == "title")
    body = next(s for s in base["slots"] if s["kind"] in ("label", "body", "caption"))
    columns["slots"] = [json.loads(json.dumps(title))]
    for i, x in enumerate((0.05, 0.52)):
        col = json.loads(json.dumps(body))
        col.update(
            slot_id=f"bullets_{i + 1}",
            kind="bullets",
            repeat_group="g1",
            element_ref=f"col{i}",
            bbox={"x": x, "y": 0.3, "width": 0.43, "height": 0.55},
            capacity={"max_chars": 500, "max_lines": 10, "max_items": 6, "confidence": 0.8},
        )
        col.pop("sample_text", None)
        columns["slots"].append(col)
    columns["static_object_ids"], columns["removable_object_ids"] = [], []
    wide = json.loads(json.dumps(base))
    wide.update(pattern_id="pat_seven", role="process", name="Семь блоков")
    wide["slots"] = [json.loads(json.dumps(title))]
    for i in range(7):
        card = json.loads(json.dumps(body))
        card.update(
            slot_id=f"body_{i + 1}",
            kind="body",
            repeat_group="g1",
            element_ref=f"c{i}",
            bbox={"x": 0.05 + 0.13 * i, "y": 0.4, "width": 0.12, "height": 0.2},
            capacity={"max_chars": 40, "max_lines": 3, "confidence": 0.8},
        )
        wide["slots"].append(card)
    wide["constraints"] = {"min_items": 1, "max_items": 7, "supports": []}
    doc["patterns"].extend([columns, wide])
    return doc


def test_list_columns_share_items_and_thin_patterns_are_right_sized(
    mini_profile: dict[str, Any], example_story: dict[str, Any], example_package: dict[str, Any]
) -> None:
    """Две колонки списков делят пункты между собой, а не по одному на колонку; композиция
    на семь карточек под два пункта заменяется ближайшей по объёму; заголовок блока в
    списке сохраняется перед пояснением."""
    profile = _two_column_profile(mini_profile)
    patterns = {p.pattern_id: p for p in mt.profile_patterns(profile)}
    cols, seven = patterns["pat_cols"], patterns["pat_seven"]
    assert cols.list_columns == 2 and cols.item_capacity == 12 and seven.item_capacity == 7
    ctx = vr.build_context(
        example_story, profile, example_package, "balanced", {}, get_settings(), 12
    )
    items = [{"text": f"Пункт {i}", "sub": "Подпись"} for i in range(1, 7)]
    draft = vr.Draft(kind="content", theses=["t2"], pattern=cols, title="Колонки", items=items)
    blocks = vr.fill_blocks(ctx, draft)
    bullets = {
        b["slot_id"]: [it["text"] for it in b["items"]] for b in blocks if b["kind"] == "bullets"
    }
    assert bullets == {
        "bullets_1": ["Подпись: Пункт 1", "Подпись: Пункт 2", "Подпись: Пункт 3"],
        "bullets_2": ["Подпись: Пункт 4", "Подпись: Пункт 5", "Подпись: Пункт 6"],
    }
    assert all("—" not in t for texts in bullets.values() for t in texts)
    # Два пункта на две колонки — по одному в колонке, полупусто: композиция меняется на карточки.
    thin = vr.Draft(kind="content", theses=["t2"], pattern=cols, title="Два", items=items[:2])
    assert vr.thin_ratio(thin) == (2, 12)
    assert vr.right_size_pattern(ctx, thin) and thin.pattern.item_capacity < 12
    assert any(a.startswith("right_size:") for a in thin.actions)
    # Семь блоков под два пункта — тоже пусто; под пять — нет.
    wide = vr.Draft(kind="content", theses=["t2"], pattern=seven, title="Семь", items=items[:2])
    assert vr.thin_ratio(wide) == (2, 7)
    assert vr.right_size_pattern(ctx, wide) and wide.pattern.pattern_id != "pat_seven"
    full = vr.Draft(kind="content", theses=["t2"], pattern=seven, title="Семь", items=items[:5])
    assert vr.thin_ratio(full) is None and not vr.right_size_pattern(ctx, full)
    # Подсказки модели считаются по её выбору, до подгонки композиции кодом.
    fresh = vr.Draft(kind="content", theses=["t2"], pattern=cols, title="Два", items=items[:2])
    assert "пункт" in (vr.density_hint([fresh]) or "") and vr.thin_count([fresh, full]) == 1
    packet = vr.Packet(0, [ctx.theses[1]], target=3, lo=2, hi=4)
    assert "раскрой тезисы" in (vr.count_hint(packet, [fresh]) or "")
    assert vr.count_hint(packet, [fresh, full]) is None


def test_card_heading_split_and_message_dedupe() -> None:
    assert vr._split_heading("Каталонская школа: контроль мяча и позиционная игра") == (
        "Каталонская школа",
        "контроль мяча и позиционная игра",
    )
    assert vr._split_heading("Просто длинный текст без разделителя внутри") is None
    assert vr._split_heading("Рост {fact:f1}: выше плана") is None
    assert vr._text_repeats("Переход к данным", ["переход к данным."])
    assert vr._text_repeats("Переход к данным", ["Ключевое: переход к данным в тактике"])
    assert not vr._text_repeats("Переход к данным", ["Совсем другой текст"])


def test_sequence_limit() -> None:
    p = mt.PatternInfo("pat_x", "kpi", "x", 0.8, None, {}, [], set(), 2, "any")
    assert mt.sequence_ok([], p) and mt.sequence_ok(["pat_x"], p)
    assert not mt.sequence_ok(["pat_x", "pat_x"], p)
    assert mt.sequence_ok(["pat_x", "pat_y"], p)


# ---------- измерение и лестница ----------


def _slot(
    kind: str = "body", size_pt: float = 18.0, width: float = 0.4, height: float = 0.1, **kw: Any
) -> mt.SlotInfo:
    base: dict[str, Any] = {
        "slot_id": f"{kind}_1",
        "kind": kind,
        "required": False,
        "group": None,
        "bbox": (0.1, 0.1, width, height),
        "family": "Arial",
        "size_pt": size_pt,
        "bold": False,
        "italic": False,
        "line_spacing": 1.0,
        "space_before_pt": 0.0,
        "space_after_pt": 0.0,
        "insets": None,
        "max_chars": 100,
        "max_lines": 2,
        "max_items": 4,
        "autofit": None,
        "sample_text": "Текст",
        "indent_emu": 0,
        "bullet": False,
    }
    base.update(kw)
    return mt.SlotInfo(**base)


def test_measure_lines_and_ladder() -> None:
    slot = _slot()
    short = cap.measure("Короткая строка", slot, 12192000, 6858000)
    assert short.fits and short.lines == 1 and short.max_lines >= 1
    long = cap.measure(" ".join(["слово"] * 60), slot, 12192000, 6858000)
    assert not long.fits and long.lines > long.max_lines
    smaller = cap.measure(" ".join(["слово"] * 60), slot, 12192000, 6858000, size_pt=10)
    assert smaller.lines < long.lines
    steps = cap.font_steps(slot, [44.0, 32.0, 18.0, 16.0, 14.0, 12.0], min_ratio=0.75, min_pt=12)
    assert steps == [16.0, 14.0]
    assert cap.font_steps(_slot(size_pt=20.0), [], min_ratio=0.75, min_pt=12) == [18.0, 16.0]
    items = cap.measure(["пункт один", "пункт два", "пункт три"], slot, 12192000, 6858000)
    assert items.lines == 3


def test_facts_substitution_and_shortening() -> None:
    facts = {"f1": {"fact_id": "f1", "raw": "40 %", "value": 40, "unit": "%"}}
    assert (
        cap.substitute_facts("Теряется {fact:f1} писем и {fact:f9}", facts)
        == "Теряется 40 % писем и {fact:f9}"
    )
    assert cap.number_format({"raw": "12,5 млн ₽"}) == "{value} млн ₽"
    assert cap.number_format({"raw": "+13 п. п."}) == "{value} п. п."
    assert cap.fact_text({"value": 3, "unit": "шт"}) == "3 шт"
    text = "Первое предложение с {fact:f1}. Второе без цифр. Третье тоже."
    assert cap.shorten_text(text) == "Первое предложение с {fact:f1}. Второе без цифр."
    assert cap.shorten_text("Одно предложение.") is None
    assert cap.shorten_text("Только {fact:f1}. И {fact:f2}.") is None
    items = [{"text": "а", "fact_refs": ["f1"]}, {"text": "б"}, {"text": "в"}]
    assert cap.shorten_items(items) == items[:2]
    assert cap.shorten_items([items[0]]) is None
    assert cap.shorten_title("Открываемость выросла: с {fact:f1} до {fact:f2}") is None
    assert (
        cap.shorten_title("Пилот окупается за год, а экономия растёт") == "Пилот окупается за год"
    )


# ---------- ключ кэша ----------


def test_plan_key_depends_on_story_profile_settings_and_versions(
    example_story: dict[str, Any], mini_profile: dict[str, Any], rich_profile: dict[str, Any]
) -> None:
    skill = get_skill("variant_planner")
    model = {"role": "llm", "name": "m"}
    base = vr.plan_key(
        example_story,
        mini_profile,
        "balanced",
        {"language": "ru"},
        slide_count=12,
        skill=skill,
        model=model,
    )
    assert base == vr.plan_key(
        example_story,
        mini_profile,
        "balanced",
        {"language": "ru"},
        slide_count=12,
        skill=skill,
        model=model,
    )
    other_ids = {**mini_profile, "template_id": "tpl_other", "created_at": "2000-01-01T00:00:00Z"}
    assert (
        vr.plan_key(
            example_story,
            other_ids,
            "balanced",
            {"language": "ru"},
            slide_count=12,
            skill=skill,
            model=model,
        )
        == base
    )
    assert (
        vr.plan_key(
            example_story,
            mini_profile,
            "compact",
            {"language": "ru"},
            slide_count=12,
            skill=skill,
            model=model,
        )
        != base
    )
    assert (
        vr.plan_key(
            example_story,
            mini_profile,
            "balanced",
            {"language": "ru"},
            slide_count=13,
            skill=skill,
            model=model,
        )
        != base
    )
    assert (
        vr.plan_key(
            example_story,
            rich_profile,
            "balanced",
            {"language": "ru"},
            slide_count=12,
            skill=skill,
            model=model,
        )
        != base
    )
    changed = {**example_story, "content_hash": "sha256:other"}
    assert (
        vr.plan_key(
            changed,
            mini_profile,
            "balanced",
            {"language": "ru"},
            slide_count=12,
            skill=skill,
            model=model,
        )
        != base
    )
    assert (
        vr.plan_key(
            example_story,
            mini_profile,
            "balanced",
            {"language": "ru", "seed": 3},
            slide_count=12,
            skill=skill,
            model=model,
        )
        != base
    )
    assert (
        vr.plan_key(
            example_story,
            mini_profile,
            "balanced",
            {"language": "ru"},
            slide_count=12,
            skill=skill,
            model={"role": "llm", "name": "z"},
        )
        != base
    )
    assert (
        vr.plan_key(
            example_story,
            mini_profile,
            "balanced",
            {"language": "ru"},
            slide_count=12,
            skill=skill,
            model=model,
            nonce="n",
        )
        != base
    )
    capacity_changed = json.loads(json.dumps(mini_profile))
    capacity_changed["patterns"][1]["slots"][1]["capacity"]["max_chars"] = 1
    assert (
        vr.plan_key(
            example_story,
            capacity_changed,
            "balanced",
            {"language": "ru"},
            slide_count=12,
            skill=skill,
            model=model,
        )
        != base
    )


# ---------- планы на записанных ответах ----------


def test_three_plans_on_replay_mini_template(
    example_story: dict[str, Any],
    mini_profile: dict[str, Any],
    example_package: dict[str, Any],
    replay_client: Any,
) -> None:
    results = _plans(example_story, mini_profile, example_package, replay_client)
    required = {t["thesis_id"] for t in example_story["theses"] if t["required"]}
    for variant_id, result in results.items():
        plan = result.plan
        _assert_valid(plan, mini_profile, example_package, example_story)
        assert plan["schema_version"] == "1.3" and plan["variant"]["value"] == variant_id
        assert plan["coverage"]["missing"] == []
        assert {c["thesis_id"] for c in plan["coverage"]["covered"]} == required
        assert plan["slide_count"]["min"] <= len(plan["slides"]) <= plan["slide_count"]["max"]
        assert plan["slides"][0]["role"] == "title" and plan["slides"][-1]["role"] == "thanks"
        assert plan["comparison"]["pattern_sequence"] == [s["pattern_id"] for s in plan["slides"]]
        meta = plan["generation_meta"]
        assert meta["skills"] == [{"name": "variant_planner", "version": "0.5.1"}]
        assert meta["prompts"] == [{"name": "plan.slides", "version": "0.5.1"}]
        assert meta["models"][0]["reasoning_mode"] == "off" and meta["prompt_tokens"] > 1000
        _assert_overflow_reported(plan, result.report)
        # Факты — только ссылками или блоками number; значения не переписаны.
        for s in plan["slides"]:
            for b in s["blocks"]:
                if b["kind"] == "number":
                    assert b["number"]["fact_id"] in {
                        f["fact_id"] for f in example_package["facts"]
                    }
        # Ни один запрос не переписывает смысловой план: у пакетов только стадия plan.
        stages = {r.stage for r in replay_client.recorder.calls}
        assert "plan" in stages
    assert len(results["compact"].plan["slides"]) <= len(results["detailed"].plan["slides"])
    comparison = vr.compare_plans({v: r.plan for v, r in results.items()})
    assert comparison["pairs"] and len(comparison["pairs"]) == 3
    # Различимость: хотя бы одна пара различается композициями или визуализацией; если шаблон
    # с четырьмя паттернами не даёт трёх подач, пары без различий перечислены явно.
    for entry in comparison["indistinct"]:
        assert entry["same_pattern_sequence"] and entry["same_visual_kinds"]
        assert vr.indistinct_warning(entry)["code"] == "variants_indistinct"


def test_three_plans_on_replay_rich_template(
    example_story: dict[str, Any],
    rich_profile: dict[str, Any],
    example_package: dict[str, Any],
    replay_client: Any,
) -> None:
    """Синтетический шаблон без финального слайда и разделителей: призыв к действию —
    содержательный слайд, покрытие полное, число слайдов в диапазоне."""
    results = _plans(example_story, rich_profile, example_package, replay_client)
    for result in results.values():
        plan = result.plan
        _assert_valid(plan, rich_profile, example_package, example_story)
        assert plan["coverage"]["missing"] == []
        assert plan["slide_count"]["min"] <= len(plan["slides"]) <= plan["slide_count"]["max"]
        # Финального образца в шаблоне нет: колоду закрывает обложка шаблона (образец
        # на титульном макете), а если и её нет — своя композиция библиотеки.
        patterns = {p["pattern_id"]: p for p in rich_profile["patterns"]}
        final_pattern = result.report["structure"]["final_pattern"]
        assert final_pattern is None or (
            str(final_pattern).startswith("pat_builtin_")
            or patterns[str(final_pattern)]["role"] == "title"
        )
        _assert_overflow_reported(plan, result.report)


# ---------- без модели, кэш, ошибки ----------


def test_plan_without_model_is_marked_and_valid(
    example_story: dict[str, Any], mini_profile: dict[str, Any], example_package: dict[str, Any]
) -> None:
    results = _plans(example_story, mini_profile, example_package, None)
    for result in results.values():
        _assert_valid(result.plan, mini_profile, example_package, example_story)
        assert any(w["code"] == "plan_without_model" for w in result.plan["warnings"])
        assert result.plan["generation_meta"]["models"] == []
        assert result.plan["coverage"]["missing"] == []
        quality = result.report["page_quality"]
        assert [s["slide_id"] for s in quality] == [s["slide_id"] for s in result.plan["slides"]]
        assert all(0 <= s["body_fill_ratio"] <= 1 for s in quality)
        assert quality[0]["applicable"] is False  # обложка не требует заполнения текстом
        if any(s["underfilled"] for s in quality):
            assert any(w["code"] == "slide_underfilled" for w in result.plan["warnings"])


@pytest.mark.parametrize("mode", ["template_only", "mixed", "all_new"])
def test_fallback_plans_respect_design_mode(
    example_story: dict[str, Any],
    mini_profile: dict[str, Any],
    example_package: dict[str, Any],
    mode: str,
) -> None:
    from presentation_designer.generation.design_mode import validate_plan_mode

    # template_only: содержание раскладывается только по образцам шаблона. Влезет ли оно в
    # мини-шаблон, зависит от записанного плана: без своих композиций слайд, который не
    # помещается, не подменяется композицией библиотеки, а даёт отказ с перечнем слайдов.
    # Абзац, который не встаёт никуда, проверяет test_unplaceable_text_is_overflow_not_loss.
    patterns = {p["pattern_id"]: p for p in mini_profile["patterns"]}
    try:
        result = vr.build_variant_plan(
            example_story,
            mini_profile,
            example_package,
            "balanced",
            {"design_mode": mode, "slide_count": {"min": 3, "max": 30}},
            use_model=False,
        )
    except vr.PlanError as e:
        assert mode == "template_only" and e.code == "template_capacity_exceeded"
        assert e.details["slides"]
        for slide in e.details["slides"]:
            assert patterns[slide["pattern"]]["source"]["kind"] != "builtin"
            assert slide["overflow"]
        return
    validate_plan_mode(result.plan, mini_profile, {"design_mode": mode})
    if mode == "all_new":
        slides = result.plan["slides"]
        assert patterns[slides[0]["pattern_id"]]["source"]["kind"] == "builtin"
        assert patterns[slides[-1]["pattern_id"]]["source"]["kind"] == "builtin"


def test_unplaceable_text_is_overflow_not_loss(
    example_story: dict[str, Any],
    mini_profile: dict[str, Any],
    example_package: dict[str, Any],
) -> None:
    """Абзац, который не встаёт ни в одну композицию шаблона, не пропадает молча и не
    сворачивается до первых слов карточки: он ставится в самый вместительный слот с
    переполнением — его видят подсказка модели, аудит и отказ template_capacity_exceeded."""
    ctx = vr.build_context(
        example_story,
        mini_profile,
        example_package,
        "balanced",
        {"design_mode": "template_only"},
        get_settings(),
        10,
    )
    pattern = next(p for p in ctx.patterns if p.single("body") and not p.builtin)
    # Одно предложение: лестница ёмкости не сокращает его по предложениям.
    text = ", ".join(f"подробность {i} об эффекте пилота в регионе" for i in range(80)) + "."
    draft = vr.Draft(
        kind="content", theses=[], pattern=pattern, title="Итоги", visual="text", text=text
    )
    vr.fit_draft(ctx, draft)
    assert draft.overflow
    assert not draft.unplaced_text
    assert any(text[:60] in str(b.get("text") or "") for b in draft.blocks)


def test_plan_cache_hit_rebinds_ids(
    example_story: dict[str, Any],
    mini_profile: dict[str, Any],
    example_package: dict[str, Any],
    replay_client: Any,
    tmp_path: pathlib.Path,
) -> None:
    cache = vr.PlanCache(tmp_path / "plan-cache")
    first = vr.build_variant_plan(
        example_story,
        mini_profile,
        example_package,
        "balanced",
        {"language": "ru"},
        slide_count=12,
        client=replay_client,
        skill=get_skill("variant_planner"),
        cache=cache,
        plan_id="plan_a",
    )
    assert first.report["cache_hit"] is False
    other_profile = {**mini_profile, "template_id": "tpl_second"}
    other_package = {**example_package, "package_id": "pkg_second"}
    second = vr.build_variant_plan(
        example_story,
        other_profile,
        other_package,
        "balanced",
        {"language": "ru"},
        slide_count=12,
        client=replay_client,
        skill=get_skill("variant_planner"),
        cache=cache,
        plan_id="plan_b",
    )
    assert second.report["cache_hit"] is True and "llm" not in second.report
    assert second.plan["plan_id"] == "plan_b"
    assert second.plan["template_id"] == "tpl_second" and second.plan["package_id"] == "pkg_second"
    assert second.plan["generation_meta"]["cache_hit"] is True
    assert second.plan["slides"] == first.plan["slides"]
    # Иная цель по числу слайдов — иной ключ: промах кэша планов и запрос к модели. Записей
    # для него нет (пустой каталог записей), поэтому replay честно падает, а не берёт чужой ответ.
    replay_client.cache.fixtures.root = tmp_path / "no-fixtures"
    with pytest.raises(vr.PlanError) as info:
        vr.build_variant_plan(
            example_story,
            mini_profile,
            example_package,
            "balanced",
            {"language": "ru"},
            slide_count=13,
            client=replay_client,
            skill=get_skill("variant_planner"),
            cache=cache,
        )
    assert info.value.code == "replay_miss"


def test_missing_model_is_explicit_error(
    example_story: dict[str, Any], mini_profile: dict[str, Any], example_package: dict[str, Any]
) -> None:
    with pytest.raises(vr.PlanError) as info:
        vr.build_variant_plan(
            example_story, mini_profile, example_package, "compact", {}, client=None, skill=None
        )
    assert info.value.code == "plan_llm_not_configured"
    with pytest.raises(vr.PlanError) as info2:
        vr.build_variant_plan(
            example_story, mini_profile, example_package, "wide", {}, use_model=False
        )
    assert info2.value.code == "plan_variant_unknown"


def test_exact_count_too_small_is_structured_error(
    example_story: dict[str, Any], mini_profile: dict[str, Any], example_package: dict[str, Any]
) -> None:
    """Точное число слайдов меньше, чем нужно обязательным тезисам, — структурированная
    причина, а не тихая потеря."""
    with pytest.raises(vr.PlanError) as info:
        vr.build_variant_plan(
            example_story,
            mini_profile,
            example_package,
            "compact",
            {"language": "ru", "slide_count": {"exact": 3}},
            slide_count=3,
            use_model=False,
        )
    assert info.value.code in ("plan_slide_count", "plan_coverage")
    assert info.value.details["required"] == {"exact": 3, "target": 3}
    assert info.value.details["variant_id"] == "compact"


def test_default_range_relaxes_for_short_content(
    example_story: dict[str, Any], mini_profile: dict[str, Any], example_package: dict[str, Any]
) -> None:
    """Короткое содержание даёт короткую колоду, а не слайды из одного пункта и не ошибку:
    диапазон по умолчанию — с предупреждением slide_count_relaxed, явный диапазон и точное
    число — slide_count_short."""
    short = json.loads(json.dumps(example_story))
    short["effective_brief"] = {"language": "ru", "purpose": short["purpose"]}
    short["theses"] = [t for t in short["theses"] if t["order"] <= 4]
    # Без пояснений и ссылок на блоки: тезису нечего раскрывать и нечего делить на два
    # слайда, так что добор до точного числа упирается в содержание, а не в форму записи
    # модели — иначе исход зависел бы от того, сколько пунктов она вынесла в первые тезисы.
    for t in short["theses"]:
        t["explanation"] = ""
        t["source_refs"] = []
    short["allowed_reductions"] = []
    result = vr.build_variant_plan(
        short,
        mini_profile,
        example_package,
        "compact",
        {"language": "ru"},
        slide_count=10,
        use_model=False,
    )
    assert len(result.plan["slides"]) < 10
    assert result.plan["slide_count"]["min"] == len(result.plan["slides"])
    assert any(w["code"] == "slide_count_relaxed" for w in result.plan["warnings"])
    _assert_valid(result.plan, mini_profile, example_package, short)
    explicit = vr.build_variant_plan(
        short,
        mini_profile,
        example_package,
        "compact",
        {"language": "ru", "slide_count": {"min": 10, "max": 15}},
        slide_count=10,
        use_model=False,
    )
    assert len(explicit.plan["slides"]) < 10
    assert any(w["code"] == "slide_count_short" for w in explicit.plan["warnings"])
    # Ни один содержательный слайд не состоит из заголовка и одного пункта.
    for slide in explicit.plan["slides"]:
        items = [b for b in slide["blocks"] if b["kind"] == "bullets"]
        assert not (items and len(items[0]["items"]) == 1 and len(slide["blocks"]) <= 2), slide
    exact = vr.build_variant_plan(
        short,
        mini_profile,
        example_package,
        "compact",
        {"language": "ru", "slide_count": {"exact": 10}},
        slide_count=10,
        use_model=False,
    )
    n = len(exact.plan["slides"])
    assert n < 10
    assert exact.plan["slide_count"] == {"min": n, "max": 10, "target": n}
    short_warning = next(w for w in exact.plan["warnings"] if w["code"] == "slide_count_short")
    assert short_warning["message"] == f"просили 10 слайдов, содержания хватило на {n}"
    _assert_valid(exact.plan, mini_profile, example_package, short)


def test_exact_count_is_respected(
    example_story: dict[str, Any], mini_profile: dict[str, Any], example_package: dict[str, Any]
) -> None:
    result = vr.build_variant_plan(
        example_story,
        mini_profile,
        example_package,
        "detailed",
        {"language": "ru", "slide_count": {"exact": 11}},
        slide_count=11,
        use_model=False,
    )
    assert len(result.plan["slides"]) == 11 and result.plan["slide_count"] == {
        "exact": 11,
        "target": 11,
    }
    _assert_valid(result.plan, mini_profile, example_package, example_story)


def test_bad_packet_is_retried_with_hint_only_for_that_packet(
    example_story: dict[str, Any],
    mini_profile: dict[str, Any],
    example_package: dict[str, Any],
    make_client: Any,
    stub: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ответ с чужим тезисом повторяется с подсказкой; второй пакет не трогается."""
    # Isolate schema retries from the separate capacity retry loop. Inheriting all
    # thesis facts can legitimately require a capacity retry for other packets.
    fit_packet = vr.fit_packet

    async def schema_only(ctx: Any, packet: Any, req: Any, client: Any, **kw: Any) -> Any:
        return await fit_packet(ctx, packet, req, client, capacity_retries=0)

    monkeypatch.setattr(vr, "fit_packet", schema_only)
    settings = get_settings()
    ctx = vr.build_context(
        example_story, mini_profile, example_package, "balanced", {"language": "ru"}, settings, 12
    )
    structure = vr.deck_structure(ctx)
    assert len(structure.packets) >= 2
    for packet in structure.packets:
        packet.candidates = vr.packet_candidates(ctx, packet)

    def good_answer(packet: vr.Packet) -> dict[str, Any]:
        """Ответ в допустимом числе слайдов пакета (иначе повтор из-за недобора) и без
        пустых слайдов: два пункта на карточки mini_template. Заголовок короткий, чтобы
        поместиться и в узкий слот собственных композиций (цитата — 35 символов): иначе
        повтор случился бы из-за переполнения, а не из-за чужого тезиса."""
        slides = []
        theses = list(packet.theses)
        while len(theses) < packet.lo:
            theses.append(theses[len(theses) % len(packet.theses)])
        for i, t in enumerate(theses):
            cands = packet.candidates[t.id]
            slides.append(
                {
                    "theses": [t.id],
                    "pattern": cands[0].pattern_id,
                    "title": f"{t.statement[:20]} {i + 1}",
                    "message": t.statement[:80],
                    "visual": "bullets",
                    "items": [{"text": "Пункт один"}, {"text": "Пункт два"}],
                    "facts": t.fact_refs[:1],
                }
            )
        return {"slides": slides, "rationale": "тест"}

    def packet_of(req: Any) -> vr.Packet:
        text = "\n".join(m.text for m in req.messages)  # повтор добавляет подсказку в конец
        for packet in structure.packets:
            if all(f"{t.id} · " in text for t in packet.theses):
                return packet
        raise AssertionError("запрос не относится ни к одному пакету")

    def is_first(req: Any) -> bool:
        return packet_of(req) is structure.packets[0]

    bad = {"slides": [{"theses": ["t999"], "pattern": "pat_s2", "title": "x", "visual": "text"}]}
    stub.on(is_first, lambda _r, _a: bad, times=1)
    stub.on(lambda _r: True, lambda r, _a: good_answer(packet_of(r)))
    client = make_client(stub, max_retries=2)
    result = vr.build_variant_plan(
        example_story,
        mini_profile,
        example_package,
        "balanced",
        {"language": "ru"},
        slide_count=12,
        client=client,
        skill=get_skill("variant_planner"),
    )
    llm = {r["packet"]: r for r in result.report["llm"]}
    assert llm[0]["attempts"] == 2
    assert all(llm[i]["attempts"] == 1 for i in range(1, len(structure.packets)))
    assert len(stub.calls) == len(structure.packets) + 1
    hint_call = [c for c in stub.calls if is_first(c)][1]
    assert "не прошёл проверку" in hint_call.messages[-1].text
    _assert_valid(result.plan, mini_profile, example_package, example_story)


# ---------- стили служебных слайдов и братья по группе ----------


def _with_style_policy(policy: str) -> Settings:
    app = get_settings()
    return app.model_copy(update={"plan": app.plan.model_copy(update={"style_policy": policy})})


def _with_sections(story: dict[str, Any], before: dict[str, str]) -> dict[str, Any]:
    """Копия смыслового плана с разделами перед указанными тезисами."""
    out = copy.deepcopy(story)
    theses: list[dict[str, Any]] = []
    section = ""
    for t in sorted(out["theses"], key=lambda t: t["order"]):
        if t["thesis_id"] in before:
            section = f"sec_{t['thesis_id']}"
            theses.append(
                {
                    "thesis_id": section,
                    "order": 0,
                    "kind": "section",
                    "statement": before[t["thesis_id"]],
                    "required": True,
                }
            )
        if section and t["kind"] != "section":
            t["parent_id"] = section
        theses.append(t)
    for order, t in enumerate(theses, start=1):
        t["order"] = order
    out["theses"] = theses
    return out


def test_style_policy_per_variant(
    example_story: dict[str, Any], variety_profile: dict[str, Any], example_package: dict[str, Any]
) -> None:
    """Три плана без модели: титул, разделители и финал выбираются из пулов ролей единым стилем
    внутри колоды и разным между вариантами (per_variant); first даёт прежнее поведение —
    первый паттерн пула у всех вариантов."""
    by_id = {p["pattern_id"]: p for p in variety_profile["patterns"]}
    # Запас по числу слайдов и разделы: проверяется стиль разделителей, а записанный
    # смысловой план может обойтись одним разделом на всю колоду.
    story = _with_sections(example_story, {"t4": "Результаты пилота", "t8": "План и риски"})
    results = _plans(
        story,
        variety_profile,
        example_package,
        None,
        settings={"language": "ru", "slide_count": {"min": 10, "max": 16}},
        app_settings=_with_style_policy("per_variant"),
    )
    titles: dict[str, str] = {}
    finals: dict[str, str] = {}
    divider_styles: dict[str, str] = {}
    for variant_id, result in results.items():
        plan = result.plan
        _assert_valid(plan, variety_profile, example_package, story)
        assert plan["coverage"]["missing"] == []
        titles[variant_id] = plan["slides"][0]["pattern_id"]
        finals[variant_id] = plan["slides"][-1]["pattern_id"]
        dividers = {s["pattern_id"] for s in plan["slides"] if s["role"] == "section_divider"}
        assert len(dividers) <= 1, f"{variant_id}: разделители в одной колоде одного стиля"
        if dividers:
            divider_styles[variant_id] = by_id[dividers.pop()]["style_key"]
        styles = result.report["structure"]["service_styles"]
        assert styles["title"]["pattern_id"] == titles[variant_id]
        assert result.report["structure"]["style_policy"] == "per_variant"
        assert result.report["structure"]["pools"]["divider"] == ["pat_s3", "pat_s4"]
    # compact и balanced — первые варианты своего тона: титул и финал в тон разделителя
    # (у compact разделитель светлый pat_s3, у balanced тёмный pat_s4).
    assert (titles["compact"], finals["compact"]) == ("pat_s1", "pat_s9")
    assert (titles["balanced"], finals["balanced"]) == ("pat_s2", "pat_s8")
    assert divider_styles == {
        "balanced": "dark|section header|plain",
        "detailed": "light|section header|plain",
    }
    # detailed — второй светлый вариант: светлый титул и финал в фикстуре по одному, поэтому он
    # берёт следующие по пулу образцы, а не повторяет compact.
    assert (titles["detailed"], finals["detailed"]) == ("pat_s2", "pat_s8")
    assert len(set(titles.values())) >= 2, titles
    # Сравнение видит одинаковое содержание за разными разделителями.
    comparison = vr.compare_plans({v: r.plan for v, r in results.items()})
    for entry in comparison["pairs"]:
        assert "same_content_sequence" in entry

    first = _plans(
        example_story,
        variety_profile,
        example_package,
        None,
        app_settings=_with_style_policy("first"),
    )
    for result in first.values():
        plan = result.plan
        assert plan["slides"][0]["pattern_id"] == "pat_s1"
        assert plan["slides"][-1]["pattern_id"] == "pat_s8"
        assert {s["pattern_id"] for s in plan["slides"] if s["role"] == "section_divider"} <= {
            "pat_s3"
        }
    # Политика входит в ключ кэша планов.
    assert results["balanced"].report["plan_key"] != first["balanced"].report["plan_key"]


def test_single_thesis_sections_do_not_create_empty_dividers(
    example_story: dict[str, Any], variety_profile: dict[str, Any], example_package: dict[str, Any]
) -> None:
    """Раздел с одним тезисом не получает отдельный пустой слайд-заголовок."""
    content_ids = [
        t["thesis_id"]
        for t in example_story["theses"]
        if t["kind"] not in ("section", "call_to_action", "conclusion")
    ]
    story = _with_sections(
        example_story,
        {thesis_id: f"Раздел {index}" for index, thesis_id in enumerate(content_ids)},
    )
    result = vr.build_variant_plan(
        story,
        variety_profile,
        example_package,
        "balanced",
        {"language": "ru"},
        slide_count=12,
        use_model=False,
    )

    assert result.report["structure"]["dividers"] == []
    assert not any(slide["role"] == "section_divider" for slide in result.plan["slides"])


def test_multi_thesis_section_keeps_justified_divider(
    example_story: dict[str, Any], variety_profile: dict[str, Any], example_package: dict[str, Any]
) -> None:
    """Раздел с несколькими тезисами сохраняет фирменный разделитель."""
    story = _with_sections(example_story, {"t4": "Результаты пилота"})
    result = vr.build_variant_plan(
        story,
        variety_profile,
        example_package,
        "balanced",
        {"language": "ru"},
        slide_count=12,
        use_model=False,
    )

    assert result.report["structure"]["dividers"]
    assert any(slide["role"] == "section_divider" for slide in result.plan["slides"])


def test_series_uses_group_siblings(
    example_story: dict[str, Any], variety_profile: dict[str, Any], example_package: dict[str, Any]
) -> None:
    """Серия одинаковых карточек длиннее max_consecutive разбавляется братом по группе (тот же
    состав, другой образец) раньше, чем кандидатами другой композиции."""
    ctx = vr.build_context(
        example_story,
        variety_profile,
        example_package,
        "balanced",
        {"language": "ru"},
        get_settings(),
        12,
    )
    cards = next(p for p in ctx.patterns if p.pattern_id == "pat_s5")
    bullets = next(p for p in ctx.patterns if p.pattern_id == "pat_s7")
    assert mt.siblings_of(ctx.patterns, cards)[0].pattern_id == "pat_s6"
    deck = [
        vr.fit_draft(
            ctx,
            vr.Draft(
                kind="content",
                theses=["t5"],
                pattern=cards,
                title=f"Слайд {i}",
                visual="cards",
                items=[{"text": "Первый"}, {"text": "Второй"}, {"text": "Третий"}],
                candidates=[bullets],
            ),
        )
        for i in range(5)
    ]
    out = vr._limit_series(ctx, deck)
    sequence = [d.pattern.pattern_id for d in out]
    assert sequence[:3] == ["pat_s5"] * 3
    assert sequence[3] == "pat_s6", sequence
    assert all(not d.overflow for d in out)
    assert any("брат по группе" in f["message"] for f in ctx.fixes if f["code"] == "series_limited")


def test_compare_plans_reports_indistinct_pairs() -> None:
    a = {
        "slides": [{}] * 3,
        "comparison": {
            "pattern_sequence": ["p1", "p2", "p3"],
            "visual_kinds": ["title", "bullets", "thanks"],
            "text_chars_total": 100,
        },
    }
    b = {
        "slides": [{}] * 3,
        "comparison": {
            "pattern_sequence": ["p1", "p2", "p3"],
            "visual_kinds": ["title", "bullets", "thanks"],
            "text_chars_total": 140,
        },
    }
    c = {
        "slides": [{}] * 3,
        "comparison": {
            "pattern_sequence": ["p1", "p4", "p3"],
            "visual_kinds": ["title", "number", "thanks"],
            "text_chars_total": 90,
        },
    }
    out = vr.compare_plans({"compact": a, "balanced": b, "detailed": c})
    assert not out["all_distinct"]
    assert [e["variants"] for e in out["indistinct"]] == [["balanced", "compact"]]
    warning = vr.indistinct_warning(out["indistinct"][0])
    assert warning["code"] == "variants_indistinct" and "140" in warning["message"]
    same_patterns = {
        **c,
        "comparison": {**c["comparison"], "visual_kinds": ["title", "bullets", "thanks"]},
    }
    out2 = vr.compare_plans({"a": a, "c": same_patterns})
    assert out2["all_distinct"]  # разные композиции при одинаковой визуализации — различимы


def test_slide_spec_from_settings_and_brief(example_story: dict[str, Any]) -> None:
    spec = vr.slide_spec({}, example_story, "compact", None)
    assert (spec.min, spec.max, spec.target, spec.explicit) == (10, 15, 10, True)
    no_brief = {**example_story, "effective_brief": {"language": "ru"}}
    assert vr.slide_spec({}, no_brief, "balanced", 12).explicit is False
    assert vr.slide_spec({}, example_story, "detailed", None).target == 15
    assert vr.slide_spec(
        {"slide_count": {"exact": 8}}, example_story, "balanced", 12
    ).as_dict() == {"exact": 8, "target": 8}
    assert (
        vr.slide_spec({"slide_count": {"min": 6, "max": 9}}, example_story, "balanced", 12).target
        == 9
    )


def test_title_falls_back_to_speaker_or_any_titled_pattern(mini_profile: dict[str, Any]) -> None:
    """Обычная презентация, отданная как шаблон (popov.pptx, 19.09): титульной композиции нет,
    первый слайд — спикер, остальные — списки. Титулом становится слайд спикера, а без него —
    образец с заголовком и наименьшим числом обязательных слотов; отказа plan_no_title_pattern
    нет."""
    profile = json.loads(json.dumps(mini_profile))
    roles = {"pat_s1": "speaker", "pat_s2": "bullets", "pat_s3": "table", "pat_s4": "bullets"}
    for p in profile["patterns"]:
        p["role"] = roles.get(p["pattern_id"], "bullets")
    patterns = mt.profile_patterns(profile)
    title = mt.fixed_pattern(patterns, "title")
    assert title is not None and title.pattern_id == "pat_s1" and title.role == "speaker"
    for p in profile["patterns"]:
        if p["pattern_id"] == "pat_s1":
            p["role"] = "bullets"
    patterns = mt.profile_patterns(profile)
    title = mt.fixed_pattern(patterns, "title")
    assert title is not None and title.title is not None and title.role == "bullets"
    assert mt.fixed_pattern(patterns, "thanks") is None


def test_clean_cuts_at_sentence_or_word_not_mid_word() -> None:
    """Пояснение раздела резалось ровно на 200 знаках: «Это формирует цикл зависим»."""
    text = (
        "Сладкие продукты вызывают выброс дофамина, создавая быстрый эффект удовольствия. "
        "Часто мы едим сладкое не из-за голода, а из-за эмоционального состояния. "
        "Это формирует цикл зависимости, из которого трудно выйти без плана и поддержки."
    )
    cut = vr._clean(text, 200)
    assert cut.endswith("эмоционального состояния.")
    words = "слово " * 60
    clipped = vr._clean(words, 100)
    assert clipped.endswith("слово…") and len(clipped) <= 100
    assert vr._clean("коротко", 200) == "коротко"


def test_service_font_steps_go_below_template_scale() -> None:
    """Шкала обложки 54 → 43,5 → 36: тема в 60 знаков не входила ни в одну ступень и
    наезжала на подзаголовок. Для служебных слайдов лестница идёт дальше шагами по 2 пт."""
    slot = SimpleNamespace(size_pt=54.0)
    scale = [54.0, 43.5, 36.0, 24.0]
    plain = cap.font_steps(slot, scale, min_ratio=0.5, min_pt=20.0)
    assert plain == [43.5, 36.0]
    service = cap.font_steps(slot, scale, min_ratio=0.5, min_pt=20.0, fill_below=True)
    assert service[:2] == [43.5, 36.0] and service[-1] == 28.0 and 30.0 in service


def test_route_cards_pair_left_to_right_and_follow_author_numbers() -> None:
    """Маршрут-зигзаг: номера и подписи в разных группах шли «по строкам» и расходились;
    змейка из двух рядов нумеруется справа налево во втором ряду."""

    def slot(slot_id: str, kind: str, x: float, y: float, text: str, group: str) -> dict:
        return {
            "slot_id": slot_id,
            "kind": kind,
            "bbox": {"x": x, "y": y, "width": 0.05, "height": 0.04},
            "sample_text": text,
            "repeat_group": group,
        }

    zigzag = [0.72, 0.78, 0.71, 0.79]
    route = {
        "pattern_id": "p",
        "role": "title",
        "slots": [
            slot(f"n{i}", "number", 0.1 + i * 0.2, y, f"0{i + 1}", "g1")
            for i, y in sorted(enumerate(zigzag), key=lambda p: p[1])
        ]
        + [
            slot(f"b{i}", "body", 0.09 + i * 0.2, y + 0.07, f"Шаг {i + 1}", "g2")
            for i, y in sorted(enumerate(zigzag), key=lambda p: -p[1])
        ],
    }
    info = mt.pattern_info(route)
    numbers = next(g for g in info.groups if "number" in g.by_kind).by_kind["number"]
    bodies = next(g for g in info.groups if "body" in g.by_kind).by_kind["body"]
    assert [s.sample_text for s in numbers] == ["01", "02", "03", "04"]
    assert [s.sample_text for s in bodies] == ["Шаг 1", "Шаг 2", "Шаг 3", "Шаг 4"]
    snake = {
        "pattern_id": "s",
        "role": "process",
        "slots": [
            slot(f"n{r}{c}", "number", 0.1 + c * 0.25, 0.3 + r * 0.3, f"0{n}", "g1")
            for r, row in enumerate(([1, 2, 3], [6, 5, 4]))
            for c, n in enumerate(row)
        ],
    }
    order = [s.sample_text for s in mt.pattern_info(snake).groups[0].by_kind["number"]]
    assert order == ["01", "02", "03", "04", "05", "06"]


def test_typed_page_number_is_a_footer_not_a_number_slot() -> None:
    """«09» внизу девятого образца — номер страницы: планировщик клал туда «5 %»."""
    raw = {
        "pattern_id": "pat_s9",
        "role": "process",
        "source": {"kind": "sample_slide", "slide_index": 9},
        "slots": [
            {
                "slot_id": "number_1",
                "kind": "number",
                "sample_text": "01",
                "bbox": {"x": 0.1, "y": 0.4, "width": 0.05, "height": 0.05},
            },
            {
                "slot_id": "number_4",
                "kind": "number",
                "sample_text": "09",
                "bbox": {"x": 0.895, "y": 0.933, "width": 0.05, "height": 0.03},
            },
        ],
    }
    info = mt.pattern_info(raw)
    assert info.slots["number_4"].kind == "footer"
    assert info.slots["number_1"].kind == "number"
