"""Правила оформления из текста самого шаблона: инструкции на слайдах и в заметках.

Из слайдов-инструкций и инструктивных фраз на образцах берутся законченные предложения с
глаголами-указаниями и словами про шрифты, цвета, диаграммы, таблицы, иконки и сетку; каждое
получает вид (`kind`) по ключевым словам. Ссылки и пустые фразы отбрасываются.
"""

from __future__ import annotations

import re
from typing import Any

from presentation_designer.parsing.template.classify import Classification, instruction_hits
from presentation_designer.parsing.template.package import TemplatePackage

KIND_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("chart", ("диаграмм", "график", "ряд", "гистограмм", "легенд", "ось", "оси")),
    ("table", ("таблиц", "ячее", "строк", "столбц")),
    ("icons", ("иконк", "пиктограмм", "логотип")),
    ("typography", ("шрифт", "кегл", "заголовк", "текст", "начертан", "регистр", "строк")),
    ("color", ("цвет", "палитр", "заливк", "контраст", "оттен")),
    (
        "layout",
        (
            "отступ",
            "сетк",
            "поля",
            "выравн",
            "макет",
            "композиц",
            "размещ",
            "слайд-разделител",
            "навигац",
            "точки",
        ),
    ),
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")
_URL = re.compile(r"https?://\S+")


def _kind(sentence: str) -> str:
    low = sentence.lower()
    for kind, words in KIND_WORDS:
        if any(w in low for w in words):
            return kind
    return "general"


def extract_guidelines(pkg: TemplatePackage, classes: list[Classification]) -> list[dict[str, Any]]:
    by_kind = {c.slide_index: c.kind for c in classes}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for slide in pkg.slides:
        cls = by_kind.get(slide.index)
        if cls == "hidden":
            continue
        texts = [s.text for s in slide.shapes if s.text]
        if slide.notes_text:
            texts.append(slide.notes_text)
        for text in texts:
            for raw in _SENTENCE_SPLIT.split(text):
                sentence = _URL.sub("", raw).strip(" •-–—\t")
                if len(sentence) < 15 or len(sentence) > 300:
                    continue
                # На образцах берём только явно инструктивные фразы; на слайдах-инструкциях —
                # все содержательные предложения.
                hits = instruction_hits(sentence)
                if cls != "style_guide" and hits == 0:
                    continue
                if cls == "style_guide" and hits == 0 and len(sentence.split()) < 6:
                    continue
                key = sentence.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    {"text": sentence, "source_slide_index": slide.index, "kind": _kind(sentence)}
                )
    return out[:80]
