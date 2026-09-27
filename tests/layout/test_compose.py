"""Сборка PPTX по плану на собственных шаблонах: roundtrip и целостность, сверка ComposedDeck
с файлом, нативные таблицы и диаграммы с независимыми книгами данных, сохранение оформления
текста и явный кегль, факты, удаление незаполненных карточек без потери статики, картинки,
иконки и схемы, номера слайдов и заметки, удаление макетов, подключение к pipeline."""

from __future__ import annotations

import copy
import io
import json
import pathlib
import zipfile
from typing import Any

import pytest
from lxml import etree
from pptx import Presentation

from presentation_designer.contracts import ComposedDeck
from presentation_designer.layout import diagrams
from presentation_designer.layout.compose import ComposeError, compose_deck
from presentation_designer.layout.integrity import check_deck
from presentation_designer.layout.ooxml import NS_A
from presentation_designer.layout.shapes import iter_shapes
from presentation_designer.layout.text import FACT_REF
from presentation_designer.pipeline.artifacts import ArtifactStore
from presentation_designer.pipeline.real import RealLayers, is_failure_fixture
from presentation_designer.pipeline.run import ComposeInput, StageError
from presentation_designer.shared.settings import Settings
from tests.layout.conftest import FIXTURES, MINI_TEMPLATE, own_profile

NS = {"a": NS_A, "p": "http://schemas.openxmlformats.org/presentationml/2006/main"}


def _package(example_package: dict[str, Any]) -> tuple[dict[str, Any], pathlib.Path]:
    package = {k: v for k, v in example_package.items() if not k.startswith("_")}
    return package, pathlib.Path(example_package["_assets_dir"])


def _compose(
    plan: dict[str, Any],
    profile: dict[str, Any],
    template: pathlib.Path,
    example_package: dict[str, Any],
    out: pathlib.Path,
    **kwargs: Any,
) -> Any:
    package, assets = _package(example_package)
    result = compose_deck(
        plan,
        profile,
        template,
        package,
        out_pptx=out,
        package_dir=assets,
        job_id="job_test",
        **kwargs,
    )
    ComposedDeck.model_validate(result.deck)
    return result


def _slide_texts(slide: Any) -> list[str]:
    return [s.text_frame.text for s in iter_shapes(slide) if s.has_text_frame]


def _slot(profile: dict[str, Any], pattern_id: str, slot_id: str) -> dict[str, Any]:
    pattern = next(p for p in profile["patterns"] if p["pattern_id"] == pattern_id)
    return next(s for s in pattern["slots"] if s["slot_id"] == slot_id)  # type: ignore[no-any-return]


def _plan_with(
    profile: dict[str, Any],
    slides: list[dict[str, Any]],
    *,
    package_id: str = "pkg_example",
) -> dict[str, Any]:
    """Минимальный план из явных слайдов: композер читает только slides, variant, language."""
    return {
        "schema_version": "1.3",
        "plan_id": "plan_manual",
        "template_id": profile["template_id"],
        "package_id": package_id,
        "story_id": "story_manual",
        "language": "ru",
        "variant": {"variant_id": "balanced", "axis": "density", "value": "balanced"},
        "slide_count": {"exact": len(slides)},
        "slides": [{"slide_id": f"s{i + 1}", "order": i + 1, **s} for i, s in enumerate(slides)],
        "coverage": {"required_thesis_ids": [], "covered": []},
        "generation_meta": {"skills": [], "models": []},
    }


# ---------- roundtrip и сверка с файлом ----------


def test_process_cards_keep_backdrops_under_each_filled_body(
    mini_profile,
    example_package,
    tmp_path,
):
    from presentation_designer.library.register import builtin_patterns

    profile = copy.deepcopy(mini_profile)
    pattern = next(
        p for p in builtin_patterns(profile) if p["pattern_id"] == "pat_builtin_process_count3"
    )
    profile["patterns"] = [pattern]
    blocks = [{"slot_id": "title", "kind": "title", "text": "План Q4"}]
    for i, month in enumerate(["Октябрь", "Ноябрь", "Декабрь"], 1):
        blocks.extend(
            [
                {"slot_id": f"step_{i}_title", "kind": "label", "text": month},
                {"slot_id": f"step_{i}_body", "kind": "caption", "text": f"Описание этапа {i}"},
            ]
        )
    plan = _plan_with(
        profile, [{"pattern_id": pattern["pattern_id"], "title": "План Q4", "blocks": blocks}]
    )
    result = _compose(plan, profile, MINI_TEMPLATE, example_package, tmp_path / "q4.pptx")
    slide = Presentation(result.pptx_path).slides[0]
    descriptions = [
        shape
        for shape in slide.shapes
        if shape.has_text_frame and shape.text.startswith("Описание этапа")
    ]
    assert {shape.text for shape in descriptions} == {f"Описание этапа {i}" for i in range(1, 4)}
    for shape in descriptions:
        backdrops = [
            other
            for other in slide.shapes
            if other != shape
            and other.fill.type is not None
            and other.left <= shape.left
            and other.top <= shape.top
            and other.left + other.width >= shape.left + shape.width
            and other.top + other.height >= shape.top + shape.height
        ]
        assert backdrops, f"{shape.text}: потеряна подложка"
        text_color = shape.text_frame.paragraphs[0].runs[0].font.color.rgb
        assert any(other.fill.fore_color.rgb != text_color for other in backdrops)


@pytest.mark.parametrize("mode", ["mixed", "all_new", "template_only"])
def test_generated_diagrams_roundtrip_in_allowed_compositions(
    mode: str,
    mini_profile: dict[str, Any],
    example_package: dict[str, Any],
    tmp_path: pathlib.Path,
) -> None:
    from presentation_designer.contracts import SlidePlan
    from presentation_designer.generation import matching as mt
    from presentation_designer.generation import variants as vr
    from presentation_designer.generation.design_mode import profile_for_mode, validate_plan_mode
    from tests.generation.test_diagram_content import _answer

    profile = profile_for_mode(mini_profile, {"design_mode": mode})
    slides = []
    for i, kind in enumerate(diagrams.KINDS):
        ctx, packet, answer = _answer(kind)
        ctx.profile = profile
        ctx.patterns = mt.profile_patterns(profile)
        ctx.slide_w = Presentation(str(MINI_TEMPLATE)).slide_width
        ctx.slide_h = Presentation(str(MINI_TEMPLATE)).slide_height
        draft = vr.make_validator(ctx, packet)(answer)["drafts"][0]
        vr.fit_draft(ctx, draft)
        assert not draft.unplaced_text and not draft.overflow
        slides.append(vr.slide_from_draft(ctx, draft, slide_id=f"s{i + 1}", order=i + 1))
    plan = _plan_with(profile, slides)
    SlidePlan.model_validate(plan)
    validate_plan_mode(plan, profile, {"design_mode": mode})
    result = _compose(plan, profile, MINI_TEMPLATE, example_package, tmp_path / f"{mode}.pptx")
    prs = Presentation(str(result.pptx_path))
    assert len(prs.slides) == len(diagrams.KINDS)
    for slide in prs.slides:
        text = "\n".join(_slide_texts(slide))
        assert all(
            word in text for word in ("Старт", "Согласие", "Пилот", "Проверка", "Итог", "Решение")
        )
    if mode != "template_only":
        assert result.deck["stats"]["diagrams"] == len(diagrams.KINDS)
    else:
        assert result.deck["stats"]["diagrams"] == 0


def test_diagram_fact_placeholders_are_substituted_during_compose(
    mini_profile: dict[str, Any],
    example_package: dict[str, Any],
    tmp_path: pathlib.Path,
) -> None:
    pattern = next(
        p for p in mini_profile["patterns"] if any(s["kind"] == "diagram" for s in p["slots"])
    )
    fact = example_package["facts"][0]
    from presentation_designer.layout.text import fact_text

    block = {
        "slot_id": "diagram",
        "kind": "diagram",
        "diagram": {
            "kind": "process",
            "items": [
                {"text": "Значение", "sub": "{fact:" + fact["fact_id"] + "}"},
                {"text": "Проверка"},
            ],
        },
    }
    plan = _plan_with(
        mini_profile, [{"pattern_id": pattern["pattern_id"], "title": "Факт", "blocks": [block]}]
    )
    result = _compose(plan, mini_profile, MINI_TEMPLATE, example_package, tmp_path / "facts.pptx")
    text = "\n".join(_slide_texts(Presentation(str(result.pptx_path)).slides[0]))
    assert "{fact:" not in text
    assert fact_text(fact) in text


@pytest.mark.parametrize("kind", ["subtitle", "body", "bullets"])
@pytest.mark.parametrize("measured", [True, False])
def test_measured_text_wraps_even_when_template_disables_wrapping(
    kind: str,
    measured: bool,
    rich_profile: dict[str, Any],
    rich_template_path: pathlib.Path,
    example_package: dict[str, Any],
    tmp_path: pathlib.Path,
) -> None:
    text = "Высокая частота уведомлений без приоритизации приводит к пропуску важных сообщений."
    block: dict[str, Any] = {"slot_id": "subtitle_1", "kind": kind}
    if kind == "bullets":
        block["items"] = [{"text": text}]
    else:
        block["text"] = text
    if measured:
        block["fit"] = {
            "size_pt": 26.0,
            "slot_size_pt": 32.0,
            "lines": 2,
            "max_lines": 2,
            "action": "font_step",
        }
    source = next(
        s
        for s in Presentation(rich_template_path).slides[2].shapes
        if s.has_text_frame and s.text == "Заголовок"
    )
    assert source.text_frame.word_wrap is False
    box = (source.left, source.top, source.width, source.height)
    plan = _plan_with(
        rich_profile,
        [{"pattern_id": "pat_s3", "title": "Показатели", "blocks": [block]}],
    )
    result = _compose(
        plan, rich_profile, rich_template_path, example_package, tmp_path / "wrapped.pptx"
    )
    shape = next(
        s
        for s in Presentation(result.pptx_path).slides[0].shapes
        if s.has_text_frame and s.text == text
    )
    # Проверяем настоящий PPTX после записи, а не только расчёт fit в плане.
    assert shape.text_frame.word_wrap is measured
    assert (shape.left, shape.top, shape.width, shape.height) == box
    if measured:
        assert all(r.font.size.pt == 26.0 for p in shape.text_frame.paragraphs for r in p.runs)


# Значение с единицей — одна строка: пробелы неразрывные, перенос выключен, кегль при
# нехватке ширины уменьшается (перенос «%» под число ложился поверх цифр).
@pytest.mark.parametrize(
    "text,lines,wrap",
    [("3000 м", 2, False), ("18 млн руб", 2, False), ("27", 1, False), ("27", 2, True)],
)
def test_small_number_slot_respects_measured_wrap(
    text: str,
    lines: int,
    wrap: bool,
    rich_profile: dict[str, Any],
    rich_template_path: pathlib.Path,
    example_package: dict[str, Any],
    tmp_path: pathlib.Path,
) -> None:
    profile = copy.deepcopy(rich_profile)
    slot = _slot(profile, "pat_s3", "subtitle_1")
    slot["kind"] = "number"
    slot["capacity"]["max_chars"] = 3
    plan = _plan_with(
        profile,
        [
            {
                "pattern_id": "pat_s3",
                "title": "Показатели",
                "blocks": [
                    {
                        "slot_id": "subtitle_1",
                        "kind": "number",
                        "text": text,
                        "fit": {"size_pt": 26.0, "lines": lines, "max_lines": 2, "action": "as_is"},
                    }
                ],
            }
        ],
    )
    result = _compose(plan, profile, rich_template_path, example_package, tmp_path / "number.pptx")
    shape = next(
        s
        for s in Presentation(result.pptx_path).slides[0].shapes
        if s.has_text_frame and s.text.replace("\u00a0", " ") == text
    )
    assert shape.text_frame.word_wrap is wrap


def test_compose_mini_template_matches_plan_and_deck(
    mini_profile: dict[str, Any], make_plan: Any, example_package: dict[str, Any], tmp_path: Any
) -> None:
    plan = make_plan(mini_profile, "balanced")
    result = _compose(plan, mini_profile, MINI_TEMPLATE, example_package, tmp_path / "deck.pptx")
    assert result.integrity.ok and result.integrity.unreachable_parts == []
    prs = Presentation(str(result.pptx_path))
    assert len(prs.slides) == len(plan["slides"]) == result.deck["stats"]["slides"]
    # Порядок и заголовки — как в плане; ссылки на факты подставлены.
    for slide, plan_slide, deck_slide in zip(
        prs.slides, plan["slides"], result.deck["slides"], strict=True
    ):
        texts = _slide_texts(slide)
        joined = "\n".join(texts)
        assert FACT_REF.search(joined) is None
        assert deck_slide["slide_id"] == plan_slide["slide_id"]
        assert deck_slide["pptx_slide_part"] == str(slide.part.partname).lstrip("/")
        assert deck_slide["title"] == plan_slide["title"]
        # Каждый объект ComposedDeck существует в файле, тексты совпадают.
        by_id = {str(s.shape_id): s for s in iter_shapes(slide)}
        for obj in deck_slide["objects"]:
            assert obj["object_id"] in by_id, (deck_slide["slide_id"], obj["object_id"])
            shape = by_id[obj["object_id"]]
            if obj.get("text") and shape.has_text_frame:
                assert obj["text"]["plain"].replace("\n", "") == shape.text_frame.text.replace(
                    "\x0b", ""
                ).replace("\n", "")
            if obj.get("slot_id"):
                assert obj["content_source"] in ("plan", "sample", "generated")
        # Удалённые объекты в файле отсутствуют.
        for removed in deck_slide["removed_object_ids"]:
            assert removed not in by_id
    # Хеш файла и статистика совпадают с фактом.
    import hashlib

    assert (
        result.deck["pptx_hash"]
        == "sha256:" + hashlib.sha256(result.pptx_path.read_bytes()).hexdigest()
    )
    assert result.deck["stats"]["file_size_bytes"] == result.pptx_path.stat().st_size
    with zipfile.ZipFile(result.pptx_path) as zf:
        names = set(zf.namelist())
        for deck_slide in result.deck["slides"]:
            for obj in deck_slide["objects"]:
                if obj["kind"] == "chart":
                    assert obj["chart"]["chart_part"] in names
    # Образцы шаблона не остались: в файле только слайды плана, заглушек образца нет.
    markers = set(mini_profile.get("placeholder_markers") or [])
    for deck_slide in result.deck["slides"]:
        for obj in deck_slide["objects"]:
            if obj.get("content_source") == "plan" and obj.get("text"):
                assert obj["text"]["plain"].strip() not in markers


def test_compose_three_variants_differ_and_validate(
    mini_profile: dict[str, Any], make_plan: Any, example_package: dict[str, Any], tmp_path: Any
) -> None:
    decks = {}
    for variant in ("compact", "balanced", "detailed"):
        plan = make_plan(mini_profile, variant)
        result = _compose(
            plan,
            mini_profile,
            MINI_TEMPLATE,
            example_package,
            tmp_path / f"{variant}.pptx",
            variant_id=variant,
        )
        assert result.deck["variant_id"] == variant
        assert len(result.slide_titles) == len(plan["slides"])
        decks[variant] = [
            (s.get("pattern_id"), tuple(s.get("title") or "")) for s in plan["slides"]
        ]
    # Варианты сравниваются по составу слайдов, а не по хэшу файла: порядок
    # частей в ZIP у python-pptx зависит от обхода множеств и от запуска к
    # запуску меняется, так что равенство файлов ничего не доказывает.
    #
    # На mini_template три композиции на всю колоду, и деление на варианты там
    # вырождается: различие проверяется на богатом шаблоне соседним тестом
    # (`test_three_plans_on_replay_rich_template`).
    # Раньше на этой фикстуре варианты выходили одинаковой длины: композиций в шаблоне было
    # три на всю колоду. С композициями библиотеки пул шире, и варианты различаются и по
    # составу, и по числу слайдов — проверяется, что собралась каждая колода.
    assert all(d for d in decks.values())
    assert len({tuple(d) for d in decks.values()}) > 1, "варианты собрались по-разному"


# ---------- таблицы и диаграммы ----------


def test_native_table_and_chart_from_dataset(
    mini_profile: dict[str, Any], example_package: dict[str, Any], tmp_path: Any
) -> None:
    package, _ = _package(example_package)
    ds = package["datasets"][0]
    columns = [c["name"] for c in ds["columns"]]
    plan = _plan_with(
        mini_profile,
        [
            {
                "pattern_id": "pat_s3",
                "title": "Таблица и линии",
                "blocks": [
                    {"slot_id": "body_1", "kind": "body", "text": "Таблица и линии"},
                    {
                        "slot_id": "table_1",
                        "kind": "table",
                        "table": {
                            "dataset_id": ds["dataset_id"],
                            "columns": columns[:3],
                            "max_rows": 2,
                            "row_offset": 1,
                        },
                    },
                    {
                        "slot_id": "chart_1",
                        "kind": "chart",
                        "chart": {
                            "type": "line",
                            "dataset_id": ds["dataset_id"],
                            "category_column": columns[0],
                            "series": columns[1:3],
                            "show_legend": True,
                            "show_data_labels": True,
                            "units": "%",
                            "title": "Динамика",
                        },
                    },
                ],
            },
            {
                "pattern_id": "pat_s3",
                "title": "Круговая",
                "blocks": [
                    {"slot_id": "body_1", "kind": "body", "text": "Круговая"},
                    {
                        "slot_id": "chart_1",
                        "kind": "chart",
                        "chart": {
                            "type": "pie",
                            "dataset_id": ds["dataset_id"],
                            "category_column": columns[0],
                            "series": columns[1:2],
                        },
                    },
                ],
            },
        ],
    )
    result = _compose(plan, mini_profile, MINI_TEMPLATE, example_package, tmp_path / "d.pptx")
    prs = Presentation(str(result.pptx_path))
    first, second = prs.slides
    table = next(s for s in first.shapes if s.has_table).table
    # Заголовок + строки набора со смещением 1 и пределом 2; колонки — выбранные.
    assert len(table.rows) == 3 and len(table.columns) == 3
    assert table.cell(0, 0).text == columns[0]
    assert table.cell(1, 0).text == str(ds["rows"][1][0])
    assert table.cell(2, 0).text == str(ds["rows"][2][0])
    chart1 = next(s for s in first.shapes if s.has_chart).chart
    chart2 = next(s for s in second.shapes if s.has_chart).chart
    # Диаграмма образца того же семейства получила данные; круговая построена заново.
    assert [s.name for s in chart1.plots[0].series] == columns[1:3]
    assert list(chart1.plots[0].categories) == [str(r[0]) for r in ds["rows"]]
    assert chart1.has_legend and chart1.plots[0].has_data_labels
    assert str(chart2.chart_type).startswith("PIE")
    assert chart1.part.partname != chart2.part.partname
    deck_objs = {(s["index"], o["kind"]): o for s in result.deck["slides"] for o in s["objects"]}
    assert deck_objs[(0, "table")]["table"] == {
        "rows": 3,
        "cols": 3,
        "header_row": True,
        "dataset_id": ds["dataset_id"],
        "row_offset": 1,
        "truncated": False,
    }
    assert deck_objs[(0, "chart")]["chart"]["built"] == "replaced"
    assert deck_objs[(1, "chart")]["chart"]["built"] == "rebuilt"
    with zipfile.ZipFile(result.pptx_path) as zf:
        names = zf.namelist()
        charts = [n for n in names if n.startswith("ppt/charts/chart")]
        books = [n for n in names if n.startswith("ppt/embeddings/")]
        assert len(charts) == 2 and len(books) == 2
        # Книги данных независимы: в каждой свои значения.
        assert len({zf.read(b) for b in books}) == 2
    # Проверка пакета не находит нарушений: связи целы, недостижимых частей нет.
    report = check_deck(result.pptx_path, expected_slides=2)
    assert report.ok and report.charts == 2 and report.unreachable_parts == []


def test_chart_replaces_picture_in_chart_pattern(
    rich_profile: dict[str, Any],
    rich_template_path: pathlib.Path,
    example_package: dict[str, Any],
    tmp_path: Any,
) -> None:
    """Паттерн роли chart с картинкой в слоте image: картинка удаляется, на её месте —
    нативная диаграмма (независимая часть и книга данных)."""
    profile = copy.deepcopy(rich_profile)
    # У rich_template нет образца с картинкой диаграммы: объявляем роль chart у карточек
    # с иконками и слот image на месте первой иконки, как это делает анализатор для образца
    # с картинкой графика.
    cards = next(p for p in profile["patterns"] if p["role"] == "cards")
    cards["role"] = "chart"
    icon = next(s for s in cards["slots"] if s["kind"] == "icon")
    icon["kind"] = "image"
    package, _ = _package(example_package)
    ds = package["datasets"][0]
    columns = [c["name"] for c in ds["columns"]]
    plan = _plan_with(
        profile,
        [
            {
                "pattern_id": cards["pattern_id"],
                "title": "График",
                "blocks": [
                    {"slot_id": "title_1", "kind": "title", "text": "График"},
                    {
                        "slot_id": icon["slot_id"],
                        "kind": "chart",
                        "chart": {
                            "type": "column",
                            "dataset_id": ds["dataset_id"],
                            "series": columns[1:2],
                        },
                    },
                ],
            }
        ],
    )
    result = _compose(plan, profile, rich_template_path, example_package, tmp_path / "c.pptx")
    slide = next(iter(Presentation(str(result.pptx_path)).slides))
    charts = [s for s in iter_shapes(slide) if getattr(s, "has_chart", False) and s.has_chart]
    assert len(charts) == 1
    assert icon["element_ref"] not in {str(s.shape_id) for s in iter_shapes(slide)}
    obj = next(o for s in result.deck["slides"] for o in s["objects"] if o["kind"] == "chart")
    assert obj["slot_kind"] == "image" and obj["block_kind"] == "chart"
    assert obj["chart"]["built"] == "added" and obj["content_source"] == "generated"


# ---------- текст ----------


def test_text_keeps_run_formatting_and_sets_explicit_size(
    mini_profile: dict[str, Any], example_package: dict[str, Any], tmp_path: Any
) -> None:
    package, _ = _package(example_package)
    fact = package["facts"][0]
    plan = _plan_with(
        mini_profile,
        [
            {
                "pattern_id": "pat_s2",
                "title": "Заголовок",
                "blocks": [
                    {
                        "slot_id": "title_1",
                        "kind": "title",
                        "text": "Заголовок с {fact:" + fact["fact_id"] + "}",
                        "fit": {
                            "size_pt": 28,
                            "slot_size_pt": 32,
                            "lines": 1,
                            "max_lines": 2,
                            "action": "font_step",
                        },
                    },
                    {
                        "slot_id": "label_1",
                        "kind": "label",
                        "text": "Первая строка\nВторая строка",
                        "fit": {"size_pt": 20, "lines": 2, "max_lines": 8, "action": "as_is"},
                    },
                    {
                        "slot_id": "label_2",
                        "kind": "label",
                        "text": "Одна строка",
                        "fit": {"size_pt": 16, "lines": 1, "max_lines": 8, "action": "as_is"},
                    },
                ],
            }
        ],
    )
    result = _compose(plan, mini_profile, MINI_TEMPLATE, example_package, tmp_path / "t.pptx")
    slide = next(iter(Presentation(str(result.pptx_path)).slides))
    by_id = {str(s.shape_id): s for s in iter_shapes(slide)}
    title = by_id[_slot(mini_profile, "pat_s2", "title_1")["element_ref"]]
    assert title.text_frame.text == "Заголовок с " + fact["raw"]
    run = title.text_frame.paragraphs[0].runs[0]
    assert run.font.size is not None and run.font.size.pt == 28
    # Две строки легли на два абзаца образца («Заголовок» 20 пт и «Текст» 14 пт): стили
    # абзацев сохранились, а кегль задан явно на каждом фрагменте.
    label = by_id[_slot(mini_profile, "pat_s2", "label_1")["element_ref"]]
    paragraphs = label.text_frame.paragraphs
    assert [p.text for p in paragraphs] == ["Первая строка", "Вторая строка"]
    assert all(p.runs[0].font.size.pt == 20 for p in paragraphs)
    label2 = by_id[_slot(mini_profile, "pat_s2", "label_2")["element_ref"]]
    assert [p.text for p in label2.text_frame.paragraphs] == ["Одна строка"]
    # Третья карточка не заполнена: её объект удалён по removable_object_ids.
    third = _slot(mini_profile, "pat_s2", "label_3")["element_ref"]
    assert third not in by_id
    deck_slide = result.deck["slides"][0]
    assert third in deck_slide["removed_object_ids"]
    title_obj = next(o for o in deck_slide["objects"] if o["slot_id"] == "title_1")
    assert title_obj["text"]["fact_refs"] == [fact["fact_id"]]
    assert title_obj["fit"]["size_pt"] == 28 and title_obj["fit"]["action"] == "font_step"
    assert title_obj["text"]["computed_style"]["font"]["size_pt"] == 28


def test_bullets_become_paragraphs_and_missing_fact_is_reported(
    mini_profile: dict[str, Any], example_package: dict[str, Any], tmp_path: Any
) -> None:
    plan = _plan_with(
        mini_profile,
        [
            {
                "pattern_id": "pat_s2",
                "title": "Список",
                "blocks": [
                    {"slot_id": "title_1", "kind": "title", "text": "Список"},
                    {
                        "slot_id": "label_1",
                        "kind": "bullets",
                        "items": [
                            {"text": "Первый"},
                            {"text": "Второй {fact:f_missing}"},
                            {"text": "Третий"},
                        ],
                    },
                ],
            }
        ],
    )
    result = _compose(plan, mini_profile, MINI_TEMPLATE, example_package, tmp_path / "b.pptx")
    slide = next(iter(Presentation(str(result.pptx_path)).slides))
    by_id = {str(s.shape_id): s for s in iter_shapes(slide)}
    label = by_id[_slot(mini_profile, "pat_s2", "label_1")["element_ref"]]
    assert [p.text for p in label.text_frame.paragraphs] == [
        "Первый",
        "Второй {fact:f_missing}",
        "Третий",
    ]
    assert any(w["code"] == "fact_missing" for w in result.warnings)


# ---------- карточки, статика, картинки, иконки, схемы ----------


def test_unfilled_cards_removed_and_logo_kept(
    rich_profile: dict[str, Any],
    rich_template_path: pathlib.Path,
    example_package: dict[str, Any],
    tmp_path: Any,
) -> None:
    cards = next(p for p in rich_profile["patterns"] if p["role"] == "cards")
    text_slots = [s for s in cards["slots"] if s.get("repeat_group") and s["kind"] != "icon"]
    kinds = sorted({s["kind"] for s in text_slots})
    # Первая карточка: заголовок и описание; вторая — только описание; третья пустая.
    by_kind = {k: [s for s in text_slots if s["kind"] == k] for k in kinds}
    heading_kind = next(k for k in ("label", "title", "subtitle") if k in by_kind)
    body_kind = next(k for k in ("body", "caption") if k in by_kind)
    blocks = [
        {"slot_id": "title_1", "kind": "title", "text": "Карточки"},
        {"slot_id": by_kind[heading_kind][0]["slot_id"], "kind": heading_kind, "text": "Раз"},
        {"slot_id": by_kind[body_kind][0]["slot_id"], "kind": body_kind, "text": "Описание раз"},
        {"slot_id": by_kind[body_kind][1]["slot_id"], "kind": body_kind, "text": "Описание два"},
    ]
    plan = _plan_with(
        rich_profile, [{"pattern_id": cards["pattern_id"], "title": "Карточки", "blocks": blocks}]
    )
    result = _compose(plan, rich_profile, rich_template_path, example_package, tmp_path / "r.pptx")
    slide = next(iter(Presentation(str(result.pptx_path)).slides))
    ids = {str(s.shape_id) for s in iter_shapes(slide)}
    third_card = {s["element_ref"] for s in cards["slots"] if s.get("repeat_group")}
    icons = [s for s in cards["slots"] if s["kind"] == "icon"]
    # Третья карточка целиком (иконка, заголовок, текст) удалена; вторая осталась с иконкой.
    assert icons[2]["element_ref"] not in ids and by_kind[body_kind][2]["element_ref"] not in ids
    assert icons[1]["element_ref"] in ids and by_kind[body_kind][1]["element_ref"] in ids
    # Заголовок второй карточки не заполнен — убран, а не оставлен заглушкой «Заголовок».
    assert by_kind[heading_kind][1]["element_ref"] not in ids
    assert "Заголовок" not in _slide_texts(slide)
    # Логотип (статика образца, повторяется на всех слайдах) на месте.
    logo_ids = set(cards["static_object_ids"])
    assert logo_ids and logo_ids <= ids
    deck_slide = result.deck["slides"][0]
    assert set(deck_slide["removed_object_ids"]) & third_card
    logo_obj = next(o for o in deck_slide["objects"] if o["object_id"] in logo_ids)
    assert logo_obj["role"] == "fixed" and logo_obj["content_source"] == "template"
    icon_obj = next(o for o in deck_slide["objects"] if o["object_id"] == icons[1]["element_ref"])
    assert icon_obj["content_source"] == "sample" and icon_obj["picture"]["fit"] == "as_is"


def test_remaining_cards_spread_over_the_row(
    mini_profile: dict[str, Any],
    example_package: dict[str, Any],
    tmp_path: Any,
) -> None:
    """Сетка шаблона на три карточки с двумя заполненными («не три колонки, а две»): третья
    убрана, две оставшиеся расходятся на всю ширину ряда с прежним промежутком; режим «По
    шаблону» (`reflow_cards=False`) блоки не двигает."""
    labels = [_slot(mini_profile, "pat_s2", f"label_{i}")["bbox"] for i in (1, 2, 3)]
    left, right = labels[0]["x"], labels[2]["x"] + labels[2]["width"]
    gap = labels[1]["x"] - (labels[0]["x"] + labels[0]["width"])
    width = (right - left - gap) / 2
    blocks = [
        {"slot_id": "title_1", "kind": "title", "text": "Две колонки"},
        {"slot_id": "label_1", "kind": "label", "text": "Первая колонка"},
        {"slot_id": "label_2", "kind": "label", "text": "Вторая колонка"},
    ]
    plan = _plan_with(
        mini_profile, [{"pattern_id": "pat_s2", "title": "Две колонки", "blocks": blocks}]
    )
    for reflow in (True, False):
        result = _compose(
            plan,
            mini_profile,
            MINI_TEMPLATE,
            example_package,
            tmp_path / f"cards-{reflow}.pptx",
            reflow_cards=reflow,
        )
        objects = {
            o["slot_id"]: o["bbox"] for o in result.deck["slides"][0]["objects"] if o.get("slot_id")
        }
        assert "label_3" not in objects
        first, second = objects["label_1"], objects["label_2"]
        if reflow:
            assert first["x"] == pytest.approx(left, abs=0.002)
            assert first["width"] == pytest.approx(width, abs=0.003)
            assert second["x"] - (first["x"] + first["width"]) == pytest.approx(gap, abs=0.003)
            assert second["x"] + second["width"] == pytest.approx(right, abs=0.003)
            # Под текстом в карточке ничего нет — высота прежняя.
            assert first["height"] == pytest.approx(labels[0]["height"], abs=0.002)
            assert result.report["counts"]["cards_reflowed"] == 1
        else:
            assert first["width"] == pytest.approx(labels[0]["width"], abs=0.002)
            assert second["x"] == pytest.approx(labels[1]["x"], abs=0.002)
            assert "cards_reflowed" not in result.report["counts"]


def _pattern_with_slot(profile: dict[str, Any], slot_id: str, sample: str | None) -> dict[str, Any]:
    return next(
        p
        for p in profile["patterns"]
        if any(s["slot_id"] == slot_id and s.get("sample_text") == sample for s in p["slots"])
    )


def test_title_moves_away_from_layout_decoration(
    scheme_profile: dict[str, Any],
    scheme_template_path: pathlib.Path,
    example_package: dict[str, Any],
    tmp_path: Any,
) -> None:
    """Заголовок титула перекрыт декором макета справа: слот сужается до свободной части,
    кегль подбирается по лестнице, объект получает явные координаты."""
    title = next(p for p in scheme_profile["patterns"] if p["role"] == "title")
    decor = next(
        f
        for f in scheme_profile["fixed_elements"]
        if f["kind"] == "decoration" and f["appears_on"] == f"layout:{title['source']['layout_id']}"
    )
    slot = _slot(scheme_profile, title["pattern_id"], "title_1")
    assert slot["bbox"]["x"] + slot["bbox"]["width"] > decor["bbox"]["x"], (
        "образец: перекрытие есть"
    )
    text = "Очень длинное название презентации, которое не помещается рядом с декором"
    plan = _plan_with(
        scheme_profile,
        [
            {
                "pattern_id": title["pattern_id"],
                "title": text,
                "blocks": [
                    {"slot_id": "title_1", "kind": "title", "text": text},
                    {"slot_id": "subtitle_1", "kind": "subtitle", "text": "Имя Фамилия"},
                ],
            }
        ],
    )
    result = _compose(
        plan, scheme_profile, scheme_template_path, example_package, tmp_path / "t.pptx"
    )
    obj = next(o for o in result.deck["slides"][0]["objects"] if o.get("slot_id") == "title_1")
    assert obj["fit"]["action"] == "narrowed_for_decor"
    assert obj["bbox"]["x"] == pytest.approx(slot["bbox"]["x"], abs=0.002)
    assert obj["bbox"]["x"] + obj["bbox"]["width"] < decor["bbox"]["x"]
    assert obj["bbox"]["width"] >= 0.3
    assert obj["fit"]["size_pt"] <= obj["fit"]["slot_size_pt"]
    # Подзаголовок макета тоже заходит под декор: сужается так же и не выходит за его край.
    sub = next(o for o in result.deck["slides"][0]["objects"] if o.get("slot_id") == "subtitle_1")
    assert sub["fit"]["action"] == "narrowed_for_decor"
    assert sub["bbox"]["x"] + sub["bbox"]["width"] < decor["bbox"]["x"]
    assert result.report["counts"]["narrowed_slots"] == 2
    slide = next(iter(Presentation(str(result.pptx_path)).slides))
    shape = next(sh for sh in iter_shapes(slide) if str(sh.shape_id) == obj["object_id"])
    assert shape.width / result.deck["slide_size"]["width_emu"] == pytest.approx(
        obj["bbox"]["width"], abs=0.002
    )


def test_step_numbers_follow_filled_cards(
    scheme_profile: dict[str, Any],
    scheme_template_path: pathlib.Path,
    example_package: dict[str, Any],
    tmp_path: Any,
) -> None:
    """Нумерованные этапы: план заполняет только пояснения, номера кружков ставит композер
    подряд по заполненным карточкам; пустые карточки уходят, общая линия остаётся."""
    steps = _pattern_with_slot(scheme_profile, "number_1", "1")
    refs = {s["slot_id"]: s["element_ref"] for s in steps["slots"]}
    plan = _plan_with(
        scheme_profile,
        [
            {
                "pattern_id": steps["pattern_id"],
                "title": "Этапы",
                "blocks": [
                    {"slot_id": "title_1", "kind": "title", "text": "Этапы"},
                    {"slot_id": "body_1", "kind": "body", "text": "Первый шаг"},
                    {"slot_id": "body_3", "kind": "body", "text": "Второй шаг"},
                ],
            }
        ],
    )
    result = _compose(
        plan, scheme_profile, scheme_template_path, example_package, tmp_path / "n.pptx"
    )
    slide = next(iter(Presentation(str(result.pptx_path)).slides))
    texts = {str(sh.shape_id): sh.text_frame.text for sh in iter_shapes(slide) if sh.has_text_frame}
    assert texts[refs["number_1"]] == "1" and texts[refs["number_3"]] == "2"
    assert refs["number_2"] not in texts and refs["number_4"] not in texts
    assert refs["body_2"] not in texts and refs["body_4"] not in texts
    ids = {str(sh.shape_id) for sh in iter_shapes(slide)}
    baseline = next(i for i in steps["static_object_ids"] if i != refs.get("logo", "-"))
    assert set(steps["static_object_ids"]) <= ids, "общая линия и логотип на месте"
    deck_slide = result.deck["slides"][0]
    numbers = {
        o["slot_id"]: o for o in deck_slide["objects"] if o.get("slot_id", "").startswith("number")
    }
    assert numbers["number_1"]["content_source"] == "generated"
    assert numbers["number_3"]["text"]["plain"] == "2"
    assert result.report["counts"]["step_numbers"] == 2
    assert baseline in ids


def test_dangling_arrows_removed_with_empty_boxes(
    scheme_profile: dict[str, Any],
    scheme_template_path: pathlib.Path,
    example_package: dict[str, Any],
    tmp_path: Any,
) -> None:
    """Схема из блоков: заполнен один блок, остальные удалены вместе со стрелками к ним;
    свободная декоративная линия и логотип остаются."""
    scheme = _pattern_with_slot(scheme_profile, "body_1", "Текстовый блок")
    refs = {s["slot_id"]: s["element_ref"] for s in scheme["slots"]}
    plan = _plan_with(
        scheme_profile,
        [
            {
                "pattern_id": scheme["pattern_id"],
                "title": "Схема",
                "blocks": [
                    {"slot_id": "title_1", "kind": "title", "text": "Схема"},
                    {"slot_id": "body_1", "kind": "body", "text": "Старт"},
                ],
            }
        ],
    )
    result = _compose(
        plan, scheme_profile, scheme_template_path, example_package, tmp_path / "a.pptx"
    )
    slide = next(iter(Presentation(str(result.pptx_path)).slides))
    ids = {str(sh.shape_id) for sh in iter_shapes(slide)}
    connectors = {
        str(sh.shape_id): sh for sh in iter_shapes(slide) if sh._element.tag.endswith("}cxnSp")
    }
    assert refs["body_1"] in ids and refs["body_2"] not in ids and refs["body_3"] not in ids
    source = Presentation(str(scheme_template_path)).slides[scheme["source"]["slide_index"] - 1]
    arrows = [
        str(sh.shape_id)
        for sh in iter_shapes(source)
        if sh._element.tag.endswith("}cxnSp") and sh._element.find(".//{*}stCxn") is not None
    ]
    free_lines = [
        str(sh.shape_id)
        for sh in iter_shapes(source)
        if sh._element.tag.endswith("}cxnSp") and sh._element.find(".//{*}stCxn") is None
    ]
    assert len(arrows) == 2 and len(free_lines) == 1
    assert not set(arrows) & ids, "стрелки к удалённым блокам сняты"
    assert set(free_lines) <= set(connectors), "свободная линия декора осталась"
    removed = set(result.deck["slides"][0]["removed_object_ids"])
    assert set(arrows) <= removed and refs["body_2"] in removed


def test_icons_follow_nearest_filled_text(
    scheme_profile: dict[str, Any],
    scheme_template_path: pathlib.Path,
    example_package: dict[str, Any],
    tmp_path: Any,
) -> None:
    """Иконки и тексты карточек попали в группы разного размера (третий текст — одиночный
    слот): иконка остаётся у заполненного текста рядом, уходит с пустой карточкой."""
    cards = _pattern_with_slot(scheme_profile, "icon_3", None)
    refs = {s["slot_id"]: s["element_ref"] for s in cards["slots"]}
    assert not any(s.get("repeat_group") for s in cards["slots"] if s["slot_id"] == "body_3")
    plan = _plan_with(
        scheme_profile,
        [
            {
                "pattern_id": cards["pattern_id"],
                "title": "Три тезиса",
                "blocks": [
                    {"slot_id": "title_1", "kind": "title", "text": "Три тезиса"},
                    {"slot_id": "body_1", "kind": "body", "text": "Первый тезис"},
                    {"slot_id": "body_3", "kind": "body", "text": "Третий тезис"},
                ],
            }
        ],
    )
    result = _compose(
        plan, scheme_profile, scheme_template_path, example_package, tmp_path / "i.pptx"
    )
    slide = next(iter(Presentation(str(result.pptx_path)).slides))
    ids = {str(sh.shape_id) for sh in iter_shapes(slide)}
    assert refs["icon_1"] in ids and refs["icon_3"] in ids, "иконки заполненных карточек на месте"
    assert refs["icon_2"] not in ids and refs["body_2"] not in ids, "пустая карточка ушла с иконкой"
    icon3 = next(o for o in result.deck["slides"][0]["objects"] if o["object_id"] == refs["icon_3"])
    assert icon3["content_source"] == "sample"


def test_image_icon_recolor_and_diagrams(
    rich_profile: dict[str, Any],
    rich_template_path: pathlib.Path,
    example_package: dict[str, Any],
    tmp_path: Any,
) -> None:
    package, _ = _package(example_package)
    cards = next(p for p in rich_profile["patterns"] if p["role"] == "cards")
    icons = [s for s in cards["slots"] if s["kind"] == "icon"]
    body_slots = [s for s in cards["slots"] if s["kind"] in ("body", "caption")]
    template_icon = next(a for a in rich_profile["assets"] if a["kind"] == "icon")
    content_image = next(a for a in package["assets"] if a["width_px"] != a["height_px"])
    slides = [
        {
            "pattern_id": cards["pattern_id"],
            "title": "Картинки",
            "blocks": [
                {"slot_id": "title_1", "kind": "title", "text": "Картинки"},
                {
                    "slot_id": icons[0]["slot_id"],
                    "kind": "image",
                    "image": {"asset_id": content_image["asset_id"], "fit": "cover"},
                },
                {
                    "slot_id": icons[1]["slot_id"],
                    "kind": "icon",
                    "icon": {"asset_id": template_icon["asset_id"], "color": "#FF3985"},
                },
                {"slot_id": icons[2]["slot_id"], "kind": "icon", "icon": {"query": "rocket"}},
                {"slot_id": body_slots[0]["slot_id"], "kind": "body", "text": "а"},
                {"slot_id": body_slots[1]["slot_id"], "kind": "body", "text": "б"},
                {"slot_id": body_slots[2]["slot_id"], "kind": "body", "text": "в"},
            ],
        }
    ]
    for kind in diagrams.KINDS:
        slides.append(
            {
                "pattern_id": cards["pattern_id"],
                "title": f"Схема {kind}",
                "blocks": [
                    {"slot_id": "title_1", "kind": "title", "text": f"Схема {kind}"},
                    {
                        "slot_id": body_slots[0]["slot_id"],
                        "kind": "diagram",
                        "diagram": {
                            "kind": kind,
                            "items": [
                                {"text": "Раз", "sub": "детали"},
                                {"text": "Два"},
                                {"text": "Три"},
                            ],
                        },
                    },
                ],
            }
        )
    plan = _plan_with(rich_profile, slides)
    result = _compose(plan, rich_profile, rich_template_path, example_package, tmp_path / "i.pptx")
    prs = Presentation(str(result.pptx_path))
    first = next(iter(prs.slides))
    by_id = {str(s.shape_id): s for s in iter_shapes(first)}
    deck_first = result.deck["slides"][0]
    objs = {o["object_id"]: o for o in deck_first["objects"]}
    image_obj = objs[icons[0]["element_ref"]]
    assert image_obj["picture"]["asset_id"] == content_image["asset_id"]
    assert image_obj["picture"]["origin"] == "content" and image_obj["content_source"] == "plan"
    assert image_obj["picture"].get("crop") is not None  # cover: картинка 16:9 в квадрат
    assert image_obj["picture"]["natural_width_px"] == content_image["width_px"]
    icon_obj = objs[icons[1]["element_ref"]]
    assert icon_obj["picture"]["recolored"] is True
    assert icon_obj["picture"]["asset_id"] == template_icon["asset_id"] + ":recolored"
    # Иконка по запросу — векторная фигура из набора library/iconset на месте слота.
    query_obj = next(o for o in deck_first["objects"] if o.get("slot_id") == icons[2]["slot_id"])
    assert query_obj["content_source"] == "plan"
    assert by_id[query_obj["object_id"]].name == "Icon rocket"
    assert icons[2]["element_ref"] not in by_id
    assert not any(w["code"] == "icon_query_unsupported" for w in result.warnings)
    # Перекрашенная иконка — отдельная медиа-часть нужного цвета.
    pic = by_id[icons[1]["element_ref"]]
    from PIL import Image

    with Image.open(io.BytesIO(pic.image.blob)) as img:
        rgba = img.convert("RGBA")
        center = rgba.getpixel((rgba.width // 2, rgba.height // 2))
        assert center[:3] == (0xFF, 0x39, 0x85) and center[3] > 0
    content_asset = next(
        a for a in result.deck["assets"] if a["asset_id"] == content_image["asset_id"]
    )
    assert content_asset["origin"] == "content" and content_asset["shared_with_template"] is False
    # Схемы: группа фигур с узлами на каждом слайде, SmartArt нет, HTML поддержан полностью.
    for kind, slide, deck_slide in zip(
        diagrams.KINDS, list(prs.slides)[1:], result.deck["slides"][1:], strict=True
    ):
        group = next(o for o in deck_slide["objects"] if o.get("diagram"))
        assert group["kind"] == "group" and group["diagram"]["kind"] == kind
        ids = {str(s.shape_id) for s in iter_shapes(slide)}
        assert set(group["diagram"]["node_ids"]) <= ids
        assert body_slots[0]["element_ref"] not in ids
        nodes = [o for o in deck_slide["objects"] if o["object_id"] in group["diagram"]["node_ids"]]
        assert nodes and all(o["content_source"] == "generated" for o in nodes)
        assert any("Раз" in (o.get("text") or {}).get("plain", "") for o in nodes)
    assert result.deck["html_support"]["full_native"] is True
    assert result.deck["stats"]["diagrams"] == len(diagrams.KINDS)


# ---------- номера слайдов, заметки, макеты, ошибки ----------


def test_slide_numbers_notes_and_layout_pruning(
    example_package: dict[str, Any], tmp_path: Any
) -> None:
    # Копия mini_template с полем номера слайда на образце карточек.
    prs = Presentation(str(MINI_TEMPLATE))
    slide = list(prs.slides)[1]
    box = slide.shapes.add_textbox(11000000, 6300000, 900000, 400000)
    p = box.text_frame.paragraphs[0]._p
    fld = etree.SubElement(
        p, f"{{{NS_A}}}fld", id="{B6F7A3C2-0000-4000-8000-000000000001}", type="slidenum"
    )
    etree.SubElement(fld, f"{{{NS_A}}}t").text = "2"
    template = tmp_path / "numbered.pptx"
    prs.save(str(template))
    profile = own_profile(template, "tpl_numbered")
    plan = _plan_with(
        profile,
        [
            {
                "pattern_id": "pat_s1",
                "title": "Титул",
                "blocks": [{"slot_id": "title_1", "kind": "title", "text": "Титул"}],
            },
            {
                "pattern_id": "pat_s2",
                "title": "Второй",
                "notes": "Заметка докладчика",
                "blocks": [{"slot_id": "title_1", "kind": "title", "text": "Второй"}],
            },
            {
                "pattern_id": "pat_s2",
                "title": "Третий",
                "blocks": [{"slot_id": "title_1", "kind": "title", "text": "Третий"}],
            },
        ],
    )
    result = _compose(
        plan, profile, template, example_package, tmp_path / "n.pptx", prune_layouts=True
    )
    out = Presentation(str(result.pptx_path))
    slides = list(out.slides)
    numbers = []
    for s in slides:
        for f in s.shapes._spTree.iter(f"{{{NS_A}}}fld"):
            if f.get("type") == "slidenum":
                numbers.append(f.find(f"{{{NS_A}}}t").text)
    assert numbers == ["2", "3"]
    assert (
        slides[1].has_notes_slide
        and slides[1].notes_slide.notes_text_frame.text == "Заметка докладчика"
    )
    assert result.deck["slides"][1]["notes"] == "Заметка докладчика"
    # Неиспользуемые макеты удалены, файл цел, использованные макеты на месте.
    assert result.deck["stats"]["layouts_removed"] > 0
    assert result.deck["stats"]["layouts_kept"] == len(
        {str(s.slide_layout.part.partname) for s in slides}
    )
    assert check_deck(result.pptx_path, expected_slides=3).ok


def test_template_hash_mismatch_and_unknown_pattern(
    mini_profile: dict[str, Any],
    rich_template_path: pathlib.Path,
    make_plan: Any,
    example_package: dict[str, Any],
    tmp_path: Any,
) -> None:
    plan = make_plan(mini_profile)
    package, _assets = _package(example_package)
    with pytest.raises(ComposeError) as e:
        compose_deck(plan, mini_profile, rich_template_path, package, out_pptx=tmp_path / "x.pptx")
    assert e.value.code == "compose_template_mismatch"
    bad = copy.deepcopy(plan)
    bad["slides"][0]["pattern_id"] = "pat_none"
    with pytest.raises(ComposeError) as e2:
        compose_deck(bad, mini_profile, MINI_TEMPLATE, package, out_pptx=tmp_path / "y.pptx")
    assert e2.value.code == "compose_pattern_unknown"


# ---------- подключение к pipeline ----------


def test_real_layers_compose_writes_revision_artifacts(
    mini_profile: dict[str, Any], make_plan: Any, example_package: dict[str, Any], tmp_path: Any
) -> None:
    settings = Settings()
    settings.paths.artifacts_dir = tmp_path / "artifacts"
    layers = RealLayers(settings)
    assert layers.modes["layout"] == "real"
    store = ArtifactStore(settings.artifacts_dir)
    plan = make_plan(mini_profile, "compact")
    package, assets = _package(example_package)
    with store.stage_revision("job_x", "compact", 1) as staging:
        out = layers.compose(
            ComposeInput(
                "job_x",
                "compact",
                1,
                plan,
                mini_profile,
                MINI_TEMPLATE,
                package,
                {},
                staging,
                package_dir=assets,
            )
        )
    manifest = store.read_manifest("job_x", "compact", 1)
    assert {"compact/r1/deck.pptx", "compact/r1/composed.json", "compact/r1/plan.json"} <= set(
        manifest
    )
    assert out.slide_count == len(plan["slides"]) == len(out.slide_titles)
    deck = json.loads((store.revision_dir("job_x", "compact", 1) / "composed.json").read_text())
    ComposedDeck.model_validate(deck)
    assert deck["pptx_artifact"] == "compact/r1/deck.pptx" and deck["job_id"] == "job_x"
    assert out.composed_deck["deck_id"] == "deck_job_x_compact_r1"


def test_failure_fixture_fails_detailed_only(
    mini_profile: dict[str, Any], make_plan: Any, example_package: dict[str, Any], tmp_path: Any
) -> None:
    fail = FIXTURES / "pptx" / "mini_template_fail.pptx"
    assert is_failure_fixture(fail) and not is_failure_fixture(MINI_TEMPLATE)
    settings = Settings()
    settings.paths.artifacts_dir = tmp_path / "artifacts"
    layers = RealLayers(settings)
    store = ArtifactStore(settings.artifacts_dir)
    package, assets = _package(example_package)
    profile = own_profile(fail, "tpl_fail")
    with (
        pytest.raises(StageError) as e,
        store.stage_revision("job_f", "detailed", 1) as staging,
    ):
        layers.compose(
            ComposeInput(
                "job_f",
                "detailed",
                1,
                make_plan(profile, "detailed"),
                profile,
                fail,
                package,
                {},
                staging,
                package_dir=assets,
            )
        )
    assert e.value.code == "compose_failed"
    with store.stage_revision("job_f", "compact", 1) as staging:
        out = layers.compose(
            ComposeInput(
                "job_f",
                "compact",
                1,
                make_plan(profile, "compact"),
                profile,
                fail,
                package,
                {},
                staging,
                package_dir=assets,
            )
        )
    assert out.slide_count > 0


def test_card_frame_contains_wider_caption() -> None:
    """Подпись в карточке шире самой карточки (VK Tech, финал с QR): плашка всё равно
    считается рамкой убранного слота, а соседняя подпись, лишь задевающая её, — нет."""
    from presentation_designer.layout.compose import _contains

    frame = (0.055, 0.557, 0.055, 0.098)
    assert _contains(frame, (0.048, 0.587, 0.071, 0.042))
    assert not _contains(frame, (0.100, 0.587, 0.300, 0.042))
    assert not _contains(frame, (0.055, 0.557, 0.0, 0.0))


def test_overflowing_text_keeps_whole_sentences_and_spills_rest_to_notes(
    rich_profile: dict[str, Any],
    rich_template_path: pathlib.Path,
    example_package: dict[str, Any],
    tmp_path: pathlib.Path,
) -> None:
    """План отметил текст как не помещающийся: на слайде остаются целые предложения, которые
    входят в рамку, остальное — в заметках докладчика. Раньше абзац лез на заголовок."""
    first = "Короткая мысль помещается."
    rest = " ".join(
        f"Длинное пояснение номер {i} с подробностями, которые в рамку уже не входят."
        for i in range(12)
    )
    block = {
        "slot_id": "subtitle_1",
        "kind": "body",
        "text": f"{first} {rest}",
        "fit": {
            "size_pt": 32.0,
            "slot_size_pt": 32.0,
            "lines": 20,
            "max_lines": 2,
            "action": "overflow",
        },
    }
    plan = _plan_with(
        rich_profile, [{"pattern_id": "pat_s3", "title": "Показатели", "blocks": [block]}]
    )
    result = _compose(
        plan, rich_profile, rich_template_path, example_package, tmp_path / "spill.pptx"
    )
    slide = Presentation(result.pptx_path).slides[0]
    texts = _slide_texts(slide)
    assert any(t.startswith(first) for t in texts), "начало текста на слайде"
    assert not any("номер 11" in t for t in texts), "хвост не на слайде"
    notes = slide.notes_slide.notes_text_frame.text
    assert "номер 11" in notes, "остаток сохранён в заметках"
    fit = next(
        o["fit"]
        for s in result.deck["slides"]
        for o in s["objects"]
        if o.get("slot_id") == "subtitle_1"
    )
    assert fit["action"] == "shortened"
