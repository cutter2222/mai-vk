"""Создаёт небольшие собственные фикстуры для обычных тестов: PPTX и документы.

Закрытые материалы организаторов сюда не попадают.
Запуск: uv run python tests/fixtures/make_fixtures.py
"""

from __future__ import annotations

import csv
import pathlib

from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Emu, Pt

HERE = pathlib.Path(__file__).resolve().parent
PPTX_DIR = HERE / "pptx"
DOCS_DIR = HERE / "content"


def make_template() -> pathlib.Path:
    """Мини-шаблон: титул, карточки, таблица с диаграммой, финальный слайд."""
    prs = Presentation()
    prs.slide_width = Emu(12192000)
    prs.slide_height = Emu(6858000)
    blank = prs.slide_layouts[6]

    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "Название презентации"
    s.placeholders[1].text = "Имя Фамилия, должность"

    s = prs.slides.add_slide(blank)
    tb = s.shapes.add_textbox(Emu(600000), Emu(400000), Emu(11000000), Emu(800000))
    tb.text_frame.text = "Заголовок в две или одну строчку"
    tb.text_frame.paragraphs[0].runs[0].font.size = Pt(32)
    for i in range(3):
        card = s.shapes.add_textbox(
            Emu(600000 + i * 3700000), Emu(1600000), Emu(3400000), Emu(2600000)
        )
        card.text_frame.text = "Заголовок\nТекст"
        card.text_frame.paragraphs[0].runs[0].font.size = Pt(20)
        card.text_frame.paragraphs[1].runs[0].font.size = Pt(14)

    s = prs.slides.add_slide(blank)
    tb = s.shapes.add_textbox(Emu(600000), Emu(400000), Emu(11000000), Emu(800000))
    tb.text_frame.text = "Таблица и диаграмма"
    rows, cols = 4, 3
    table = s.shapes.add_table(
        rows, cols, Emu(600000), Emu(1500000), Emu(5000000), Emu(2000000)
    ).table
    for r in range(rows):
        for c in range(cols):
            table.cell(r, c).text = "Заголовок" if r == 0 else "Текст"
    data = CategoryChartData()
    data.categories = ["Май", "Июнь", "Июль"]
    data.add_series("Открываемость", (31, 38, 44))
    s.shapes.add_chart(
        XL_CHART_TYPE.COLUMN_CLUSTERED, Emu(6200000), Emu(1500000), Emu(5400000), Emu(3500000), data
    )

    s = prs.slides.add_slide(blank)
    tb = s.shapes.add_textbox(Emu(600000), Emu(2500000), Emu(11000000), Emu(1200000))
    tb.text_frame.text = "Спасибо за внимание!"
    tb.text_frame.paragraphs[0].runs[0].font.size = Pt(40)

    PPTX_DIR.mkdir(parents=True, exist_ok=True)
    out = PPTX_DIR / "mini_template.pptx"
    prs.save(out)
    return out


def make_content() -> list[pathlib.Path]:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    outs: list[pathlib.Path] = []

    doc = Document()
    doc.add_heading("Проблема", level=1)
    doc.add_paragraph(
        "Пользователи пропускают до 40 % важных уведомлений, "
        "потому что получают их без приоритизации."
    )
    doc.add_heading("Решение", level=1)
    for item in ("Приоритизация по контексту", "Единый центр уведомлений", "Тихие часы"):
        doc.add_paragraph(item, style="List Bullet")
    doc.add_heading("Результат пилота", level=1)
    doc.add_paragraph(
        "Экономия составила 12,5 млн ₽ за год при росте открываемости с 31 % до 44 %."
    )
    p = DOCS_DIR / "product_description.docx"
    doc.save(p)
    outs.append(p)

    wb = Workbook()
    ws = wb.active
    ws.title = "Метрики"
    ws.append(["Месяц", "Открываемость, %", "Отписки, %"])
    for row in (["Май", 31, 4.1], ["Июнь", 38, 3.2], ["Июль", 44, 2.7]):
        ws.append(row)
    p = DOCS_DIR / "metrics.xlsx"
    wb.save(p)
    outs.append(p)

    p = DOCS_DIR / "metrics.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Месяц", "Открываемость", "Отписки"])
        w.writerows([["Май", 31, 4.1], ["Июнь", 38, 3.2], ["Июль", 44, 2.7]])
    outs.append(p)

    p = DOCS_DIR / "brief.md"
    p.write_text(
        "# Запуск сервиса умных уведомлений\n\n"
        "Назначение: продукт. Аудитория: руководители продуктовых направлений.\n\n"
        "Цель: получить одобрение на пилот.\n\n"
        "## Обязательно включить\n\n- метрики пилота\n- план на квартал\n"
    )
    outs.append(p)
    return outs


if __name__ == "__main__":
    print(make_template())
    for path in make_content():
        print(path)
