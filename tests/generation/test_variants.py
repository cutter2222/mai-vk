"""Планы трёх вариантов: структура паттернов и кандидаты кодом, измерение ёмкости и лестница,
ключ кэша, планы на записанных ответах (replay) для пакета из examples/content на двух
собственных шаблонах, детерминированный план без модели, кэш готовых планов, повтор негодного
пакета с подсказкой, точное число слайдов, различимость вариантов."""

from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
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
from tests.parsing.content.conftest import EXAMPLES

EXAMPLE_FILES = ("overview.docx", "metrics.xlsx", "notes.md", "openrate_chart.png", "logo.png")
MINI_TEMPLATE = (
    pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "pptx" / "mini_template.pptx"
)


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
    **kwargs: Any,
) -> dict[str, vr.PlanResult]:
    out: dict[str, vr.PlanResult] = {}
    for variant_id in vr.VARIANTS:
        out[variant_id] = vr.build_variant_plan(
            story,
            profile,
            package,
            variant_id,
            {"language": "ru"},
            slide_count=resolve_slide_count(variant_id, {"language": "ru"}),
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
    assert [p.pattern_id for p in chart] == ["pat_s3"]
    bullets = mt.candidates_for(patterns, mt.Need("bullets", items=3), "compact")
    assert bullets and bullets[0].pattern_id == "pat_s2"
    # Служебные роли содержательными кандидатами не становятся.
    assert all(p.role not in mt.FIXED_ROLES for p in bullets)
    assert mt.fixed_pattern(patterns, "title") is not None
    assert mt.fixed_pattern(patterns, "thanks") is not None
    assert mt.fixed_pattern(patterns, "section_divider") is None
    assert mt.fallback_visual("table", patterns, has_datasets=False) in ("bullets", "cards", "text")
    assert mt.fallback_visual("timeline", patterns, has_datasets=True) == "cards"
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
    на семь карточек под два пункта заменяется ближайшей по объёму; подпись карточки в
    список не склеивается через тире."""
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
        "bullets_1": ["Пункт 1", "Пункт 2", "Пункт 3"],
        "bullets_2": ["Пункт 4", "Пункт 5", "Пункт 6"],
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
        assert plan["schema_version"] == "1.2" and plan["variant"]["value"] == variant_id
        assert plan["coverage"]["missing"] == []
        assert {c["thesis_id"] for c in plan["coverage"]["covered"]} == required
        assert plan["slide_count"]["min"] <= len(plan["slides"]) <= plan["slide_count"]["max"]
        assert plan["slides"][0]["role"] == "title" and plan["slides"][-1]["role"] == "thanks"
        assert plan["comparison"]["pattern_sequence"] == [s["pattern_id"] for s in plan["slides"]]
        meta = plan["generation_meta"]
        assert meta["skills"] == [{"name": "variant_planner", "version": "0.3.0"}]
        assert meta["prompts"] == [{"name": "plan.slides", "version": "0.3.0"}]
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
        assert plan["slides"][-1]["role"] != "thanks"
        assert result.report["structure"]["final_pattern"] is None
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
    # Иная цель по числу слайдов — иной ключ: промах кэша, запрос к модели (в replay — нет записи).
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
    """Короткое содержание даёт короткую колоду, а не слайды из одного пункта: диапазон по
    умолчанию — с предупреждением slide_count_relaxed, явный диапазон — slide_count_short,
    точное число — ошибка с причиной."""
    short = json.loads(json.dumps(example_story))
    short["effective_brief"] = {"language": "ru", "purpose": short["purpose"]}
    short["theses"] = [t for t in short["theses"] if t["order"] <= 4]
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
    with pytest.raises(vr.PlanError) as info:
        vr.build_variant_plan(
            short,
            mini_profile,
            example_package,
            "compact",
            {"language": "ru", "slide_count": {"exact": 10}},
            slide_count=10,
            use_model=False,
        )
    assert info.value.code == "plan_slide_count"


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
) -> None:
    """Ответ с чужим тезисом повторяется с подсказкой; второй пакет не трогается."""
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
        пустых слайдов: два пункта на карточки mini_template."""
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
                    "title": f"{t.statement[:36]} {i + 1}",
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
