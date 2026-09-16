"""Парсеры каждого формата на собственных фикстурах: блоки, таблицы, изображения,
предупреждения о неизвлечённом содержании, кэш разбора."""

from __future__ import annotations

import pathlib

import pytest

from presentation_designer.parsing.content.parsers import (
    PARSER_VERSIONS,
    ParseCache,
    ParserError,
    parse_file,
    parse_key,
)
from presentation_designer.parsing.content.parsers.base import blocks_from_text
from tests.parsing.content.conftest import CONTENT, ROOT

MINI_PPTX = ROOT / "tests" / "fixtures" / "pptx" / "mini_template.pptx"


def test_docx_headings_lists_tables_images_and_captions() -> None:
    doc = parse_file(CONTENT / "product_description.docx", "docx")
    kinds = [b.kind for b in doc.blocks]
    assert kinds == ["heading", "paragraph", "heading", "bullets", "heading", "paragraph"]
    assert doc.blocks[0].level == 1 and doc.blocks[0].text == "Проблема"
    assert doc.blocks[3].items == [
        "Приоритизация по контексту",
        "Единый центр уведомлений",
        "Тихие часы",
    ]
    assert doc.units["chars"] > 100 and doc.extracted

    rich = parse_file(CONTENT / "report_with_table.docx", "docx")
    kinds = [b.kind for b in rich.blocks]
    assert kinds == ["heading", "paragraph", "table", "figure"]
    assert rich.tables[0].rows[0] == ["Квартал", "Выручка, млн ₽", "Клиенты"]
    assert rich.tables[0].rows[1] == ["1 кв.", "80", "1 200"]
    figure = rich.blocks[3]
    assert figure.caption == "Рисунок 1. Динамика открываемости"
    assert rich.images[0].width_px == 640 and rich.images[0].mime == "image/png"
    assert rich.images[0].caption == figure.caption


def test_xlsx_and_csv_tables() -> None:
    xlsx = parse_file(CONTENT / "metrics.xlsx", "xlsx")
    assert xlsx.units == {"sheets": 1, "tables": 1}
    table = xlsx.tables[0]
    assert table.title == "Метрики" and table.location.sheet == "Метрики"
    assert table.location.cell_range == "A1:C4"
    assert table.rows[0] == ["Месяц", "Открываемость, %", "Отписки, %"]
    assert table.rows[1] == ["Май", 31, 4.1]

    csv_doc = parse_file(CONTENT / "metrics.csv", "csv")
    assert csv_doc.tables[0].rows[0] == ["Месяц", "Открываемость", "Отписки"]
    assert csv_doc.tables[0].rows[3] == ["Июль", "44", "2.7"]


def test_markdown_and_text() -> None:
    md = parse_file(CONTENT / "brief.md", "markdown")
    assert [b.kind for b in md.blocks] == [
        "heading",
        "paragraph",
        "paragraph",
        "heading",
        "bullets",
    ]
    assert md.blocks[0].level == 1 and md.blocks[3].level == 2
    assert md.blocks[4].items == ["метрики пилота", "план на квартал"]

    txt = parse_file(CONTENT / "notes.txt", "text")
    assert txt.blocks[0].kind == "heading" and txt.blocks[0].text == "Заметки по пилоту"
    assert txt.blocks[-1].kind == "bullets" and len(txt.blocks[-1].items) == 2


def test_markdown_table_and_code(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "t.md"
    path.write_text(
        "# Итоги\n\n| Месяц | Выручка |\n| --- | --- |\n| Май | 10 |\n| Июнь | 12 |\n\n"
        "```python\nprint(1)\n```\n\n> Цитата\n\n![график](chart.png)\n"
    )
    doc = parse_file(path, "markdown")
    kinds = [b.kind for b in doc.blocks]
    assert kinds == ["heading", "table", "code", "quote", "paragraph"]
    assert doc.tables[0].rows == [["Месяц", "Выручка"], ["Май", "10"], ["Июнь", "12"]]
    assert doc.tables[0].title == "Итоги"
    assert any(w["code"] == "image_reference_unresolved" for w in doc.warnings)


def test_pdf_text_layer_and_scan() -> None:
    pdf = parse_file(CONTENT / "report.pdf", "pdf")
    assert pdf.units["pages"] == 1 and pdf.units["chars"] > 50
    assert pdf.blocks[0].kind == "heading" and pdf.blocks[0].text == "Pilot report 2026"
    assert any("25 %" in b.text for b in pdf.blocks if b.kind == "paragraph")
    assert pdf.blocks[-1].kind == "bullets" and pdf.blocks[-1].items == [
        "Priority inbox",
        "Quiet hours",
    ]
    assert not pdf.warnings

    scan = parse_file(CONTENT / "scan.pdf", "pdf")
    assert any(w["code"] == "no_text_layer" for w in scan.warnings)
    assert not any(b.kind in ("paragraph", "heading") for b in scan.blocks)
    # Картинка страницы всё же извлечена: сканы остаются доступными как изображение.
    assert scan.images and scan.images[0].width_px == 800


def test_pptx_as_material_reads_text_tables_charts() -> None:
    doc = parse_file(MINI_PPTX, "pptx")
    assert doc.units["slides"] == 4 and doc.units["tables"] == 2
    assert doc.blocks[0].kind == "heading" and doc.blocks[0].location.slide == 1
    chart_blocks = [b for b in doc.blocks if "chart" in b.tags]
    assert len(chart_blocks) == 1
    chart = doc.tables[chart_blocks[0].table_ref or 0]
    assert chart.rows[0] == ["Категория", "Открываемость"]
    assert chart.rows[1:] == [["Май", 31.0], ["Июнь", 38.0], ["Июль", 44.0]]


def test_images() -> None:
    chart = parse_file(CONTENT / "chart.png", "image")
    assert chart.images[0].width_px == 640 and chart.images[0].kind in ("image", "screenshot")
    logo = parse_file(CONTENT / "logo.png", "image")
    assert logo.images[0].kind == "logo"
    with pytest.raises(ParserError) as info:
        parse_file(CONTENT / "notes.txt", "image")
    assert info.value.code == "image_unreadable"


def test_unsupported_and_missing() -> None:
    with pytest.raises(ParserError) as info:
        parse_file(CONTENT / "notes.txt", "video")
    assert info.value.code == "unsupported_format"
    with pytest.raises(ParserError) as info:
        parse_file(CONTENT / "нет.docx", "docx")
    assert info.value.code == "file_missing"


def test_blocks_from_text_headings_and_bullets() -> None:
    blocks = blocks_from_text("Заголовок\n\nАбзац первый. Абзац второй.\n\n- один\n- два\n")
    assert [b.kind for b in blocks] == ["heading", "paragraph", "bullets"]
    assert blocks_from_text("Короткая строка", allow_headings=False)[0].kind == "paragraph"


def test_parse_cache_roundtrip(tmp_path: pathlib.Path) -> None:
    cache = ParseCache(tmp_path / "cache")
    doc = parse_file(CONTENT / "report_with_table.docx", "docx")
    key = parse_key("abc", "docx", {"max_block_chars": 2000})
    assert cache.get(key) is None
    cache.put(key, doc)
    loaded = cache.get(key)
    assert loaded is not None
    assert [b.as_dict() for b in loaded.blocks] == [b.as_dict() for b in doc.blocks]
    assert loaded.images[0].data == doc.images[0].data
    assert loaded.tables[0].rows == doc.tables[0].rows
    # Ключ учитывает версию парсера и параметры.
    assert parse_key("abc", "docx", {"max_block_chars": 1000}) != key
    assert PARSER_VERSIONS["docx"] in {"0.1.0"}
    # Повреждённая запись считается отсутствующей.
    (cache.path(key) / "doc.json").write_text("{не json")
    assert cache.get(key) is None
