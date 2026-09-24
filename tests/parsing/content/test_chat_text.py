"""Текст из сообщения чата → Markdown-материал: раскладка «Слайд N», восстановленные строки."""

from __future__ import annotations

import pathlib
import re

from presentation_designer.parsing.content import chat_text
from presentation_designer.parsing.content.parsers.markdown import parse_markdown

FLAT = (
    pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "content" / "chat_outline_flat.txt"
)


def test_flat_outline_from_chat_is_restored() -> None:
    """Сообщение Александра 25.09: десять слайдов одной строкой, переносы потеряны при
    копировании, подписи приклеены («ТитульныйЗаголовок:», «IDEКлючевой тезис:»)."""
    text = FLAT.read_text(encoding="utf-8")
    assert "\n" not in text
    assert chat_text.slide_marks(text) == list(range(1, 11))
    assert chat_text.is_content_text(text)
    md = chat_text.to_markdown(text)
    headings = [line for line in md.splitlines() if line.startswith("## ")]
    assert headings[0] == "## Слайд 1: Титульный"
    assert headings[4] == "## Слайд 5: Главный тренд 2026 — GenUI и Agentic Applications"
    assert headings[6] == "## Слайд 7: Инструменты разработки (DX) и ИИ-агенты в IDE"
    assert len(headings) == 10
    assert md.startswith("# Будущее Flutter в 2026 году: от кроссплатформы")
    assert "Заголовок: Flutter 2026: Эволюция стандарта" in md
    # Перечень метрик — список, подпись «Тезисы для спикера» его закрывает.
    assert "- 2.8+ миллиона ежемесячно активных разработчиков" in md
    assert "\nТезисы для спикера: Кроссплатформа" in md
    assert "- 100% Type-safety:" in md
    assert "\n1 кодовая база — 6 платформ" in md
    # Слова пользователя не теряются: без разметки и пробелов текст совпадает знак в знак.
    body = re.sub(r"(?m)^(?:#{1,2} |- )", "", md)
    assert "".join(body.split()) == "".join(text.replace("Презентация: «", "", 1).split()).replace(
        "»", "", 1
    )


def test_markdown_parses_into_slide_sections(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "chat.md"
    path.write_text(chat_text.to_markdown(FLAT.read_text(encoding="utf-8")), encoding="utf-8")
    doc = parse_markdown(path)
    sections = [b.text for b in doc.blocks if b.kind == "heading" and b.level == 2]
    assert len(sections) == 10 and sections[1].startswith("Слайд 2: Индустрия в цифрах")
    metrics = next(b for b in doc.blocks if b.kind == "bullets")
    assert len(metrics.items) == 4 and metrics.items[0].startswith("~42–46% рынка")


def test_text_with_line_breaks_keeps_its_lines() -> None:
    text = (
        "Слайд 1: Проблема\n"
        "Клиенты ждут ответа 3 дня.\n"
        "Слайд 2: Решение\n"
        "Что делаем:\n"
        "• бот отвечает сразу\n"
        "• оператор подключается при сложном вопросе\n"
    )
    md = chat_text.to_markdown(text)
    assert md == (
        "## Слайд 1: Проблема\n\n"
        "Клиенты ждут ответа 3 дня.\n\n"
        "## Слайд 2: Решение\n\n"
        "Что делаем:\n\n"
        "- бот отвечает сразу\n"
        "- оператор подключается при сложном вопросе\n"
    )


def test_single_slide_reference_is_not_an_outline() -> None:
    assert chat_text.slide_marks("Сделай презентацию на 10 слайдов, см. слайд 3. там график") == []
    assert chat_text.slide_marks("Слайд 2: а. Слайд 1: б.") == []
    assert not chat_text.is_content_text("Презентация про футбол для школьников на 8 слайдов")
