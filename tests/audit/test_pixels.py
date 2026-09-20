"""Проверки по отрендеренной странице: контраст к настоящей подложке и подмена гарнитуры.

Рендерить PDF в тесте незачем: проверкам нужен объект с двумя ответами — какие строки на
странице и какой цвет под рамкой. Здесь он подставлен вручную, поэтому видно, что именно
решает исход. Само чтение страницы проверяется отдельно, на настоящем PDF из фикстур.
"""

from __future__ import annotations

import pathlib
from typing import Any

from presentation_designer.audit.deterministic import (
    Context,
    check_contrast,
    check_font_substituted,
)
from presentation_designer.audit.pixels import (
    DeckPixels,
    TextRun,
    contrast,
    normalized_family,
)
from presentation_designer.audit.report import build_report

JsonDict = dict[str, Any]
FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"


class _Page:
    """Страница-двойник: заранее известные строки и один цвет подложки."""

    def __init__(self, runs: list[TextRun], background: tuple[int, int, int] | None) -> None:
        self.runs = runs
        self._background = background

    def background_under(self, bbox: JsonDict) -> tuple[int, int, int] | None:
        return self._background


class _Deck:
    def __init__(self, page: _Page) -> None:
        self._page = page

    def slide(self, index: int) -> _Page:
        return self._page


def _profile(families: list[str]) -> JsonDict:
    return {
        "design_tokens": {
            "colors": {"palette": [], "theme": {}},
            "typography": {"fonts": [{"family": f} for f in families], "scale": []},
        },
        "layouts": [],
        "fixed_elements": [],
    }


def _text_object(object_id: str, text: str, family: str = "Montserrat") -> JsonDict:
    return {
        "object_id": object_id,
        "kind": "text",
        "role": "content",
        "content_source": "generated",
        "bbox": {"x": 0.1, "y": 0.1, "width": 0.6, "height": 0.2},
        "text": {
            "plain": text,
            "paragraphs": [{"text": text, "style": {"font": {"family": family, "size_pt": 16.0}}}],
        },
    }


def _run(text: str, font: str, color: tuple[int, int, int], size: float = 16.0) -> TextRun:
    return TextRun(
        bbox={"x": 0.12, "y": 0.12, "width": 0.3, "height": 0.04},
        text=text,
        font=font,
        size_pt=size,
        color=color,
    )


def _context(
    runs: list[TextRun], background: tuple[int, int, int] | None, slide: JsonDict
) -> Context:
    return Context(
        deck={"slides": [slide]},
        profile=_profile(["Montserrat"]),
        pixels=_Deck(_Page(runs, background)),
    )


def test_white_on_crimson_is_a_finding() -> None:
    """Белый текст на фирменной плашке: 3,5:1 — ниже порога WCAG для обычного текста."""
    slide = {"slide_id": "s1", "index": 0, "objects": [_text_object("1", "Коротко о решении")]}
    ctx = _context([_run("Коротко о решении", "Montserrat", (255, 255, 255))], (255, 0, 83), slide)
    issues = check_contrast(slide, ctx)
    assert [i.check_id for i in issues] == ["template.contrast"]
    assert issues[0].evidence["measured"] < 4.5
    assert "измерено по отрендеренной странице" in issues[0].evidence["details"]


def test_dark_text_on_white_passes() -> None:
    slide = {"slide_id": "s1", "index": 0, "objects": [_text_object("1", "Итоги квартала")]}
    ctx = _context([_run("Итоги квартала", "Montserrat", (26, 26, 26))], (255, 255, 255), slide)
    assert check_contrast(slide, ctx) == []


def test_one_finding_per_object() -> None:
    """Двадцать буллетов одного списка — одна проблема списка, а не двадцать находок."""
    slide = {"slide_id": "s1", "index": 0, "objects": [_text_object("1", "Пункт списка")]}
    runs = [_run("• Пункт списка", "Montserrat", (255, 0, 83)) for _ in range(20)]
    issues = check_contrast(slide, _context(runs, (240, 235, 245), slide))
    assert len(issues) == 1
    assert issues[0].element_ids == ["1"]


def test_text_outside_the_deck_is_not_checked() -> None:
    """Подсказку пустого плейсхолдера рисует рендерер: это не наше содержание."""
    slide = {"slide_id": "s1", "index": 0, "objects": []}
    ctx = _context([_run("Фото команды", "Montserrat", (0, 0, 0))], (72, 8, 104), slide)
    assert check_contrast(slide, ctx) == []
    assert check_font_substituted(slide, ctx) == []


def test_substituted_font_is_reported() -> None:
    slide = {"slide_id": "s1", "index": 0, "objects": [_text_object("1", "Запуск сервиса")]}
    ctx = _context([_run("Запуск сервиса", "DejaVuSans", (0, 0, 0))], (255, 255, 255), slide)
    issues = check_font_substituted(slide, ctx)
    assert [i.check_id for i in issues] == ["template.font_substituted"]
    assert "DejaVuSans" in issues[0].message


def test_same_font_other_weight_is_not_substitution() -> None:
    """«Montserrat-Bold» на странице и «Montserrat» в файле — одна гарнитура."""
    slide = {"slide_id": "s1", "index": 0, "objects": [_text_object("1", "Запуск сервиса")]}
    run = _run("Запуск сервиса", "ABCDEE+Montserrat-Bold", (0, 0, 0))
    ctx = _context([run], (255, 255, 255), slide)
    assert check_font_substituted(slide, ctx) == []


def test_bullet_glyph_is_not_substitution() -> None:
    """Маркер списка рисуется своей гарнитурой по замыслу файла, а не по нехватке шрифта."""
    slide = {"slide_id": "s1", "index": 0, "objects": [_text_object("1", "Пункт")]}
    ctx = _context([_run("•", "ArialMT", (0, 0, 0))], (255, 255, 255), slide)
    assert check_font_substituted(slide, ctx) == []


def test_report_without_pdf_marks_the_check_unchecked() -> None:
    """Без рендера проверка не выдаётся за пройденную: причина записана в покрытие."""
    slide = {"slide_id": "s1", "index": 0, "objects": [_text_object("1", "Запуск сервиса")]}
    report = build_report(
        job_id="job_1",
        variant_id="original",
        revision=1,
        deck={"slides": [slide], "slide_size": {"width_emu": 12192000, "height_emu": 6858000}},
        profile=_profile(["Montserrat"]),
        staging_prefix="original/r1/",
        contextual=False,
    )
    outcomes = {
        r["check_id"]: r["outcome"]
        for r in report["results"]
        if r["check_id"].startswith("template")
    }
    assert outcomes["template.font_substituted"] == "not_checked"
    assert report["coverage"]["complete"] is False
    assert "pdf_file" in report["coverage"]["missing_inputs"]


def test_family_normalization() -> None:
    assert normalized_family("ABCDEE+Montserrat-Bold") == "montserrat"
    assert normalized_family("Poppins-Light") == normalized_family("Poppins Light") == "poppins"
    assert normalized_family("ArialMT") == "arial"


def test_contrast_matches_wcag() -> None:
    assert contrast((255, 255, 255), (0, 0, 0)) == 21.0
    assert contrast((255, 255, 255), (255, 0, 83)) < 4.5


def test_reads_runs_from_a_real_pdf() -> None:
    """Чтение страницы: строки, гарнитура, кегль, цвет букв и подложка под ними."""
    with DeckPixels(FIXTURES / "content" / "report.pdf") as deck:
        assert len(deck) == 1
        page = deck.slide(0)
        assert page is not None
        title = page.runs[0]
        assert title.text.startswith("Pilot report")
        assert title.font and title.size_pt > 0
        assert title.color == (0, 0, 0)
        # Подложка под заголовком — белая страница, значит контраст максимальный.
        assert page.background_under(title.bbox) == (255, 255, 255)
        assert contrast(title.color, (255, 255, 255)) == 21.0
