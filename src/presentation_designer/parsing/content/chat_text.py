"""Текст из сообщения чата как материал: Markdown для импорта содержания.

Длинное сообщение с содержанием (тезисы, цифры, раскладка «Слайд 1: …, Слайд 2: …») не
помещается в бриф: бриф берёт тему и число слайдов, а факты терялись. Такое сообщение
сохраняется файлом проекта и разбирается тем же импортом, что и документы.

Скопированный из чата или документа текст часто приходит одной строкой: переносы
исчезают, остаются тройные пробелы и склейки «ТитульныйЗаголовок:», «метрики:Завершён».
Строки восстанавливаются по этим следам, маркеры «Слайд N» становятся заголовками второго
уровня (по ним план повторяет раскладку пользователя), строка-подпись с двоеточием на
конце открывает список из следующих строк.
"""

from __future__ import annotations

import re
from itertools import pairwise

VERSION = "0.1.0"

# «Слайд 2: Индустрия…», «Slide 3 — …», «Слайд №4. …» в начале строки или после конца фразы.
SLIDE_MARK = re.compile(
    r"(?i)(?:^|(?<=[\s.!?…:;)»\]]))(?:слайд|slide)\s*№?\s*(\d{1,2})\s*[:.)—–-]\s*"
)
_HEADING = re.compile(r"(?i)^(?:слайд|slide)\s*№?\s*(\d{1,2})\s*[:.)—–-]\s*(.*)$")
_PREAMBLE = re.compile(r"(?i)^(?:презентация|тема|название)\s*[:—–-]\s*[«\"]?(.+?)[»\"]?\s*$")
_BULLET = re.compile(r"^(?:[-*•·–—]|\d{1,2}[.)])\s+")
_LABEL_ONLY = re.compile(r"^[^:]{2,60}:$")
_LABEL_VALUE = re.compile(r"^([^:]{2,50}):\s+\S")
# Подписи частей слайда, а не пунктов перечня: «Интересный факт: …» закрывает список.
_SECTION_LABEL = re.compile(
    r"(?i)тезис|факт\b|эффект|посыл|вывод|итог|спикер|заметк|акцент|заголовок|как это|цитат"
)


def slide_marks(text: str) -> list[int]:
    """Номера слайдов из маркеров «Слайд N:», если они идут по возрастанию с 1 и их не
    меньше двух; иначе пусто — одиночное «см. слайд 3.» раскладкой не считается."""
    numbers = [int(m.group(1)) for m in SLIDE_MARK.finditer(text or "")]
    if len(numbers) < 2 or numbers[0] != 1:
        return []
    if any(b <= a for a, b in pairwise(numbers)):
        return []
    return numbers


def _flat(text: str) -> bool:
    """Текст одной «простынёй»: переносов почти нет при заметной длине."""
    return len(text) >= 400 and text.count("\n") < max(3, len(text) // 600)


def _unflatten(text: str) -> str:
    # Тройные пробелы — следы переносов строк.
    text = re.sub(r"[ \t]{3,}", "\n", text)
    # «ТитульныйЗаголовок:», «IDEКлючевой тезис:» — подпись приклеена к предыдущей строке.
    text = re.sub(r"([A-Za-zа-яё0-9)»])([А-ЯЁ][а-яё]+(?:[ -][а-яёa-z]+){0,3}:)", r"\1\n\2", text)
    # «лидерство.Сокращение», «(Wasm)Ключевой», «SDK.100%» — новая фраза без пробела.
    text = re.sub(r"(?<=[.!?…)»])(?=[А-ЯЁA-Z«])", "\n", text)
    text = re.sub(r"(?<=[A-Za-zА-Яа-яЁё]\.)(?=\d)", "\n", text)
    # «окупается1 кодовая база» — число с новой строки приклеено к слову.
    text = re.sub(r"(?<=[а-яё]{4})(?=\d+\s)", "\n", text)
    # «метрики:Завершён», «2026:Flutter» — строка после подписи без пробела.
    return re.sub(r"(?<=[а-яёa-z0-9)]:)(?=[А-ЯЁA-Z])", "\n", text)


def _heading_text(rest: str) -> tuple[str, str]:
    """Заголовок слайда и остаток строки: хвост длиннее 120 знаков уходит абзацем."""
    rest = rest.strip()
    if len(rest) <= 120:
        return rest, ""
    end = re.search(r"[.!?…](?=\s)", rest[:120])
    at = end.end() if end else rest[:120].rfind(" ")
    at = at if at > 0 else 120
    return rest[:at].strip(), rest[at:].strip()


def to_markdown(text: str) -> str:
    """Markdown из текста сообщения: заголовок презентации, разделы «Слайд N», абзацы и
    списки. Слова пользователя не меняются, меняются только границы строк."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    if _flat(text):
        text = _unflatten(text)
    outline = bool(slide_marks(text))
    if outline:
        # Маркер слайда всегда начинает строку.
        text = SLIDE_MARK.sub(lambda m: "\n" + m.group(0), text)
    out: list[str] = []
    in_list = False
    for raw in text.split("\n"):
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            in_list = False
            continue
        heading = _HEADING.match(line) if outline else None
        if heading:
            title, tail = _heading_text(heading.group(2))
            out += ["", f"## Слайд {int(heading.group(1))}: {title}".rstrip(": "), ""]
            in_list = False
            if not tail:
                continue
            line = tail
        preamble = _PREAMBLE.match(line) if not out else None
        if preamble:
            out += [f"# {preamble.group(1).strip()}", ""]
            continue
        if _BULLET.match(line):
            out.append(line if line[0].isdigit() else "- " + _BULLET.sub("", line))
            in_list = True
            continue
        if _LABEL_ONLY.match(line):
            out += ["", _escape(line), ""]
            in_list = True
            continue
        label = _LABEL_VALUE.match(line)
        if in_list and not (label and _SECTION_LABEL.search(label.group(1))):
            out.append("- " + line)
            continue
        out += ["", _escape(line), ""]
        in_list = False
    md = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


def _escape(line: str) -> str:
    """Строка-абзац не должна стать заголовком, цитатой или списком Markdown."""
    if re.match(r"^(?:#|>|[-*+]\s|\d{1,2}[.)]\s)", line):
        return "\\" + line
    return line


def is_content_text(text: str) -> bool:
    """Сообщение несёт содержание презентации, а не только задачу: раскладка по слайдам
    или объём, который в бриф не помещается."""
    text = text or ""
    return bool(slide_marks(text)) or len(text.strip()) >= 1200


__all__ = ["SLIDE_MARK", "VERSION", "is_content_text", "slide_marks", "to_markdown"]
