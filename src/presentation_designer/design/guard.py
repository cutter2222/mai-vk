"""Застава качества: план не доходит до вёрстки с дефектами, которые видны глазом.

Три проверки, каждая — с починкой, а не только с жалобой. Аудит по готовому
файлу сообщает постфактум; здесь брак не допускается до выдачи.

Поводом стал замер колоды от 18.09.2026 (шаблон VK Tech, 11 слайдов): слово
«Заголовок» ушло в выдачу четыре раза, одно и то же предложение попало в три
слота и наложилось само на себя, текст требовал 72pt в рамке высотой 34pt.
Ни одна из этих бед не была замечена — слой аудита у сервиса заглушка.

Материал для проверок готов и лежит в профиле шаблона: список образцов
`placeholder_markers`, `sample_text` у каждого слота и его геометрия.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from presentation_designer.design.fit import Decision, _fits, content_slots
from presentation_designer.design.measure import overflows_badly

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]

_WS = re.compile(r"\s+")
_SENTENCE = re.compile(r"(?<=[.!?…])\s+")
# Подстановки фактов вида {fact:f1} к моменту проверки ещё не раскрыты: их
# длина отличается от подставленного значения незначительно, но в сравнении
# с образцом шаблона они мешают.
_FACT = re.compile(r"\{fact:[^}]+\}")


def _norm(text: str) -> str:
    return _WS.sub(" ", _FACT.sub("", str(text or ""))).strip().lower()


# -- 1. образцы шаблона ------------------------------------------------------

def drop_placeholders(
    slide: JsonDict, pattern: JsonDict, markers: set[str]
) -> list[str]:
    """Убирает блоки, чей текст — образец шаблона, а не содержание.

    Пустой слот лучше слова «Заголовок» в готовой презентации: первое
    выглядит сдержанно, второе — как незаполненная форма. Приложение 1
    считает дефектом именно второе.
    """
    samples = {
        _norm(s.get("sample_text", "")) for s in pattern.get("slots", []) if s.get("sample_text")
    }
    samples |= {_norm(m) for m in markers}
    samples.discard("")

    kept, dropped = [], []
    for block in slide.get("blocks", []):
        text = _norm(block.get("text", ""))
        if text and text in samples:
            dropped.append(block.get("slot_id"))
            continue
        kept.append(block)
    slide["blocks"] = kept
    return dropped


# -- 2. повторы на слайде ----------------------------------------------------

def drop_duplicates(slide: JsonDict) -> list[str]:
    """Оставляет один блок на каждую формулировку.

    Планировщик записал одно и то же предложение в три слота, и на слайде
    оно наложилось само на себя. Повтор на одном слайде не несёт смысла ни
    в каком макете.
    """
    seen: set[str] = set()
    kept, dropped = [], []
    for block in slide.get("blocks", []):
        text = _norm(block.get("text", ""))
        if text and text in seen:
            dropped.append(block.get("slot_id"))
            continue
        if text:
            seen.add(text)
        kept.append(block)
    slide["blocks"] = kept
    return dropped


# -- 3. вместимость ----------------------------------------------------------

def _shorten(text: str) -> str | None:
    """Первое предложение — если оно короче исходного."""
    parts = [p for p in _SENTENCE.split(str(text).strip()) if p.strip()]
    if len(parts) > 1:
        return parts[0].strip()
    return None


def enforce_capacity(
    slide: JsonDict, pattern: JsonDict, canvas: Any | None = None
) -> list[tuple[str, str]]:
    """Приводит текст к вместимости слота: сокращением, иначе удалением.

    Порядок именно такой: сокращение сохраняет мысль, удаление — нет.
    Уменьшать кегль здесь нельзя: он пришёл из шаблона и уже согласован с
    остальными слотами; правка кегля — дело `generation/capacity.py`.
    """
    slots = {s.get("slot_id"): s for s in content_slots(pattern)}
    kept, changes = [], []
    for block in slide.get("blocks", []):
        slot = slots.get(block.get("slot_id"))
        text = str(block.get("text") or "")
        # Удаляем только при ГРУБОМ выходе за рамку. PowerPoint рамкой текст
        # не обрезает, и небольшой выход — приём шаблона, а не дефект.
        if slot is None or not text or not overflows_badly(slot, text, canvas):
            kept.append(block)
            continue

        short = _shorten(text)
        if short and _fits(slot, short, canvas) is True:
            block["text"] = short
            block["shortened_by"] = "design.guard"
            changes.append((block.get("slot_id"), "сокращён"))
            kept.append(block)
            continue

        # Заголовок не удаляется никогда. Слайд без названия хуже, чем слайд
        # с тесным названием: первое — потеря смысла, второе — вопрос кегля,
        # и его решает `generation/capacity.py`. Оставляем и сообщаем.
        if block.get("kind") == "title":
            changes.append((block.get("slot_id"), "тесно, но заголовок сохранён"))
            kept.append(block)
            continue

        changes.append((block.get("slot_id"), "удалён: не помещается"))
    slide["blocks"] = kept
    return changes


# -- оркестрация -------------------------------------------------------------

def guard(
    slide: JsonDict, pattern: JsonDict, markers: set[str], canvas: Any | None = None
) -> list[Decision]:
    """Все три проверки подряд; каждая отчитывается отдельным решением."""
    slide_id = slide.get("slide_id", "?")
    out: list[Decision] = []

    dropped = drop_placeholders(slide, pattern, markers)
    if dropped:
        out.append(
            Decision(
                slide_id=slide_id,
                action="drop_placeholder",
                reason=f"убраны образцы шаблона в слотах: {', '.join(dropped)}",
                after={"slots": dropped},
            )
        )

    duplicated = drop_duplicates(slide)
    if duplicated:
        out.append(
            Decision(
                slide_id=slide_id,
                action="drop_duplicate",
                reason=f"повтор одной формулировки в слотах: {', '.join(duplicated)}",
                after={"slots": duplicated},
            )
        )

    changes = enforce_capacity(slide, pattern, canvas)
    if changes:
        out.append(
            Decision(
                slide_id=slide_id,
                action="enforce_capacity",
                reason="; ".join(f"{slot}: {what}" for slot, what in changes),
                after={"changes": [list(c) for c in changes]},
            )
        )
    return out
