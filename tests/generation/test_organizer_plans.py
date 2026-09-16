"""Планы трёх вариантов на профилях четырёх целевых шаблонов (закрытые материалы, маркер
organizer_data): контент-пакет и смысловой план — собственные (examples/content, ответ
модели из записи replay), профили строятся анализатором без рендера и VLM, планы — без
модели (детерминированный черновик), чтобы содержимое шаблонов не попадало в фикстуры.
Проверяются свойства, которые не зависят от формулировок модели: три валидных плана на
каждом профиле, полное покрытие обязательных тезисов, число слайдов в диапазоне, титульный
слайд первым, таблица и диаграмма только там, где в шаблоне есть такие слоты, различимость
вариантов или явное предупреждение о её отсутствии. Отчёт по каждому шаблону пишется в
runs/plan-tests/<имя>.json.
"""

from __future__ import annotations

import json
import pathlib
import time
from typing import Any

import pytest

from presentation_designer.contracts import ContentPackage, SlidePlan, StoryPlan, TemplateProfile
from presentation_designer.contracts.validators import check_slide_plan
from presentation_designer.generation import variants as vr
from presentation_designer.generation.story import build_story
from presentation_designer.llm.skills import get_skill
from presentation_designer.parsing.content.importer import import_content
from presentation_designer.parsing.content.parsers import ParseCache
from presentation_designer.parsing.template.analyzer import analyze_template
from presentation_designer.pipeline.run import resolve_slide_count
from presentation_designer.shared.settings import ROOT, Settings
from tests.parsing.content.conftest import EXAMPLES

pytestmark = pytest.mark.organizer_data

REPORT_DIR = ROOT / "runs" / "plan-tests"
TEMPLATES = (
    "VK Tech шаблон.pptx",
    "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx",
    "Шаблон презентации VK Education.pptx",
    "ЛЦТ2026 Шаблон презентации.pptx",
)
EXAMPLE_FILES = ("overview.docx", "metrics.xlsx", "notes.md", "openrate_chart.png", "logo.png")


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


@pytest.mark.parametrize("name", TEMPLATES, ids=lambda n: n.split(".")[0][:24])
def test_three_plans_on_organizer_profile(
    organizer_dir: pathlib.Path,
    name: str,
    example_package: dict[str, Any],
    example_story: dict[str, Any],
) -> None:
    path = organizer_dir / name
    if not path.exists():
        pytest.fail(f"нет файла {path}: прогон организаторов не должен пропускаться молча")
    profile = analyze_template(
        path,
        template_id="tpl_org",
        name=name,
        size_bytes=path.stat().st_size,
        render=False,
        use_vlm=False,
    ).profile
    has_table_slot = any(s["kind"] == "table" for p in profile["patterns"] for s in p["slots"])
    has_chart_slot = any(
        s["kind"] == "chart" or (p["role"] == "chart" and s["kind"] == "image")
        for p in profile["patterns"]
        for s in p["slots"]
    )
    required = {t["thesis_id"] for t in example_story["theses"] if t["required"]}
    plans: dict[str, dict[str, Any]] = {}
    report: dict[str, Any] = {"template": name, "variants": {}}
    for variant_id in vr.VARIANTS:
        started = time.perf_counter()
        result = vr.build_variant_plan(
            example_story,
            profile,
            example_package,
            variant_id,
            {"language": "ru"},
            slide_count=resolve_slide_count(variant_id, {"language": "ru"}),
            use_model=False,
        )
        plan = result.plan
        doc = SlidePlan.model_validate(plan)
        violations = check_slide_plan(
            doc,
            TemplateProfile.model_validate(profile),
            ContentPackage.model_validate(example_package),
            StoryPlan.model_validate(example_story),
        )
        assert not violations, [str(v) for v in violations]
        assert plan["coverage"]["missing"] == []
        assert {c["thesis_id"] for c in plan["coverage"]["covered"]} == required
        assert plan["slide_count"]["min"] <= len(plan["slides"]) <= plan["slide_count"]["max"]
        assert plan["slides"][0]["role"] in ("title", "section_divider", "text")
        kinds = {b["kind"] for s in plan["slides"] for b in s["blocks"]}
        if "table" in kinds:
            assert has_table_slot, "таблица запланирована без слота таблицы"
        if "chart" in kinds:
            assert has_chart_slot, "диаграмма запланирована без слота диаграммы"
        # Серии одинаковых композиций не длиннее max_consecutive, если была замена.
        limits = {
            p["pattern_id"]: p.get("sequence_hints", {}).get("max_consecutive", 3)
            for p in profile["patterns"]
        }
        run, prev = 0, None
        for s in plan["slides"]:
            run = run + 1 if s["pattern_id"] == prev else 1
            prev = s["pattern_id"]
            assert run <= max(limits.get(prev, 3), 1) + 1
        plans[variant_id] = plan
        counts = result.report["counts"]
        report["variants"][variant_id] = {
            "slides": len(plan["slides"]),
            "pattern_sequence": plan["comparison"]["pattern_sequence"],
            "visual_kinds": plan["comparison"]["visual_kinds"],
            "counts": counts,
            "warnings": sorted({w["code"] for w in plan["warnings"]}),
            "ms": int((time.perf_counter() - started) * 1000),
        }
    comparison = vr.compare_plans(plans)
    report["comparison"] = comparison
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / f"{name.split('.')[0]}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    # Различимость: у шаблонов датасета десятки паттернов — три подачи различимы;
    # иначе пары без различий должны быть перечислены, а не скрыты.
    assert comparison["pairs"]
    if not comparison["all_distinct"]:
        assert comparison["indistinct"]
    assert len(plans["compact"]["slides"]) <= len(plans["detailed"]["slides"])
