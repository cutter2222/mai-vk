"""Прогоны анализатора на четырёх целевых шаблонах (закрытые материалы, маркер organizer_data).

Ожидания записаны заранее по осмотру файлов (WORK_PLAN §2, docs/pptx-capabilities.md): размеры
слайдов, каталоги иконок, слайды-инструкции, нативные таблицы и диаграммы, роли, которые точно
присутствуют, и отсутствие выдуманных структур. Отчёт по каждому шаблону пишется в
runs/analyze-tests/<имя>.json: число паттернов, роли, уверенность, постоянные элементы,
исключённые листы иконок, время анализа. Без рендера и VLM: сеть в тестах не вызывается.
"""

from __future__ import annotations

import json
import pathlib
import time
from typing import Any

import pytest

from presentation_designer.contracts import TemplateProfile
from presentation_designer.parsing.template.analyzer import analyze_template
from presentation_designer.shared.settings import ROOT

pytestmark = pytest.mark.organizer_data

REPORT_DIR = ROOT / "runs" / "analyze-tests"

EXPECTED: dict[str, dict[str, Any]] = {
    "VK Tech шаблон.pptx": {
        "slide_size": (9144000, 5143500),
        "slides": 54,
        "catalogs": {30},
        "style_guides": set(),
        "native_tables": 0,
        "native_charts": 0,
        "roles_present": {
            "title",
            "thanks",
            "agenda",
            "section_divider",
            "code",
            "speaker",
            # Слайд 44 — столбчатая диаграмма, нарисованная картинками: распознаётся целиком
            # и становится ролью chart с одним слотом на всю область ряда.
            "chart",
        },
        "roles_absent": {"table"},
        "drawn_charts": 1,
        "font_used": "Play",
        "embedded_fonts": 2,
        "min_patterns": 45,
    },
    "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx": {
        "slide_size": (12192000, 6858000),
        "slides": 29,
        "catalogs": set(),
        "style_guides": set(),
        "native_tables": 1,
        "native_charts": 0,
        "roles_present": {"title", "thanks", "agenda", "table", "timeline"},
        "roles_absent": {"chart"},
        "font_used": "Play",
        "guides_from_view_props": True,
        "min_patterns": 25,
    },
    "Шаблон презентации VK Education.pptx": {
        "slide_size": (12192000, 6858000),
        "slides": 55,
        "catalogs": {25},
        "style_guides": {7, 8, 51},
        "native_tables": 3,
        "native_charts": 0,
        "roles_present": {
            "title",
            "thanks",
            "section_divider",
            "quote",
            "team",
            "code",
            "table",
            "timeline",
        },
        "roles_absent": set(),
        "font_used": "Arial",
        "guideline_words": ("Перекрытие рядов",),
        "min_patterns": 45,
        # Группы взаимозаменяемых образцов (этап 14): титулы, «титульные» без роли, тёмные и
        # светлые «паттерн + фото» с разделителями, финалы; тон по заливке макета.
        "groups": [{1, 2}, {4, 5}, {10, 12, 35, 36}, {16, 29, 37, 41}, {9, 11}, {53, 54, 55}],
        "tones": {12: "dark", 10: "light", 1: "dark", 16: "light", 53: "dark", 54: "light"},
    },
    "ЛЦТ2026 Шаблон презентации.pptx": {
        "slide_size": (12192000, 6858000),
        "slides": 37,
        "catalogs": {6, 31, 32, 33, 34, 35, 36},
        "style_guides": {2, 3, 5, 30},
        "native_tables": 0,
        "native_charts": 3,
        "roles_present": {"chart", "team"},
        "roles_absent": {"table"},
        "font_used": "Montserrat",
        # Montserrat и Poppins добавлены в docker/fonts на этапе 8: подмены больше нет.
        "font_available": True,
        "layouts": 23,
        "min_patterns": 20,
    },
}


@pytest.mark.parametrize("name", sorted(EXPECTED), ids=lambda n: n.split(".")[0][:24])
def test_organizer_template_profile(organizer_dir: pathlib.Path, name: str) -> None:
    path = organizer_dir / name
    if not path.exists():
        pytest.fail(f"нет файла {path}: прогон организаторов не должен пропускаться молча")
    exp = EXPECTED[name]
    started = time.perf_counter()
    result = analyze_template(
        path,
        template_id="tpl_org",
        name=name,
        size_bytes=path.stat().st_size,
        render=False,
        use_vlm=False,
    )
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    profile = result.profile
    TemplateProfile.model_validate(profile)

    assert (profile["slide_size"]["width_emu"], profile["slide_size"]["height_emu"]) == exp[
        "slide_size"
    ]
    assert profile["stats"]["slides"] == exp["slides"]
    assert profile["stats"]["native_tables"] == exp["native_tables"]
    assert profile["stats"]["native_charts"] == exp["native_charts"]
    if "layouts" in exp:
        assert profile["stats"]["layouts"] == exp["layouts"]
    if "embedded_fonts" in exp:
        assert profile["stats"]["embedded_fonts"] == exp["embedded_fonts"]

    # Проверяется разбор шаблона, поэтому собственные композиции библиотеки отфильтрованы:
    # у них нет ни образца-слайда, ни element_ref, ни тона фона.
    template_patterns = [p for p in profile["patterns"] if p["source"]["kind"] != "builtin"]

    classes = {s["slide_index"]: s["classification"] for s in profile["sample_slides"]}
    catalogs = {i for i, c in classes.items() if c == "asset_catalog"}
    guides = {i for i, c in classes.items() if c == "style_guide"}
    assert exp["catalogs"] <= catalogs, f"листы иконок не исключены: {exp['catalogs'] - catalogs}"
    assert exp["style_guides"] <= guides, f"инструкции не исключены: {exp['style_guides'] - guides}"
    # Собственные композиции библиотеки не приходят со слайда шаблона: у них нет slide_index.
    pattern_slides = {
        p["source"]["slide_index"] for p in template_patterns
    }
    assert not (pattern_slides & catalogs) and not (pattern_slides & guides)
    for idx in catalogs:
        assert any(a.get("source_slide_index") == idx for a in profile["assets"]), (
            f"изображения каталога {idx} должны остаться в ресурсах"
        )

    # Нарисованная диаграмма: ряд столбиков из картинок стал одним слотом chart, а части ряда
    # перечислены в chart_parts — вёрстка уберёт их, построив нативную диаграмму.
    drawn = [p for p in template_patterns if p.get("chart_parts")]
    assert len(drawn) == exp.get("drawn_charts", 0), (
        f"нарисованных диаграмм {len(drawn)}, ожидали {exp.get('drawn_charts', 0)}: "
        f"{[p['pattern_id'] for p in drawn]}"
    )
    for pattern in drawn:
        chart_slots = [sl for sl in pattern["slots"] if sl["kind"] == "chart"]
        assert len(chart_slots) == 1, "у нарисованной диаграммы один слот на весь ряд"
        assert chart_slots[0]["bbox"]["width"] >= 0.3, "слот диаграммы шире одного столбика"
        assert pattern["role"] == "chart"

    roles = {p["role"] for p in template_patterns}
    missing = exp["roles_present"] - roles
    assert not missing, f"не найдены роли {missing}; есть {sorted(roles)}"
    invented = exp["roles_absent"] & roles
    assert not invented, f"выдуманы роли без соответствующих объектов: {invented}"
    assert len(template_patterns) >= exp["min_patterns"]
    for p in template_patterns:
        assert p["slots"], p["pattern_id"]
        assert all(s["element_ref"] for s in p["slots"])
        if p["role"] == "table":
            assert any(s["kind"] == "table" for s in p["slots"])
        if p["role"] == "chart":
            assert any(s["kind"] in ("chart", "image") for s in p["slots"])

    fonts = {f["family"]: f for f in profile["design_tokens"]["typography"]["fonts"]}
    assert exp["font_used"] in fonts and fonts[exp["font_used"]]["usage_count"] > 0
    if exp.get("font_substituted"):
        assert any(w["code"] == "font_substituted" for w in profile["warnings"])
    if exp.get("font_available"):
        assert fonts[exp["font_used"]]["available_in_renderer"] is True
    if exp.get("guides_from_view_props"):
        assert any(g["source"] == "view_props" for g in profile["guides"])
    for word in exp.get("guideline_words", ()):
        assert any(word.lower() in g["text"].lower() for g in profile["guidelines"]), word
    assert (
        profile["design_tokens"]["colors"]["palette"]
        and profile["design_tokens"]["typography"]["scale"]
    )
    assert profile["fixed_elements"], "логотипы/номера страниц не найдены"
    by_slide = {
        p["source"]["slide_index"]: p for p in template_patterns
    }
    for members in exp.get("groups", ()):
        ids = {by_slide[i]["group_id"] for i in members}
        assert len(ids) == 1, f"образцы {sorted(members)} должны быть одной группой: {ids}"
        others = {i for i, p in by_slide.items() if p["group_id"] in ids} - members
        assert not others, f"в группу {ids} попали лишние образцы {sorted(others)}"
    for index, tone in exp.get("tones", {}).items():
        assert by_slide[index]["tone"]["background"] == tone, (index, by_slide[index]["tone"])
    assert all(p["style_key"] for p in template_patterns)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "template": name,
        "analysis_ms_without_vlm_and_render": elapsed_ms,
        "timings_ms": result.report.timings_ms,
        "patterns": len(template_patterns),
        "pattern_groups": result.report.counts.get("pattern_groups"),
        "roles": result.report.roles,
        "mean_confidence": round(
            sum(p["confidence"] for p in template_patterns) / max(len(template_patterns), 1), 3
        ),
        "fixed_elements": {
            kind: sum(1 for f in profile["fixed_elements"] if f["kind"] == kind)
            for kind in sorted({f["kind"] for f in profile["fixed_elements"]})
        },
        "excluded_slides": result.report.excluded_slides,
        "assets": len(profile["assets"]),
        "guidelines": len(profile["guidelines"]),
        "warnings": [w["code"] for w in profile["warnings"]],
    }
    (REPORT_DIR / f"{path.stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
