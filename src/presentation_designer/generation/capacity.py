"""Проверка ёмкости планов: измерение текста после подстановки фактов и лестница размещения.

Текст блока измеряется по файлам шрифтов рендерера через `shared/text_metrics`: слова
переносятся по фактической ширине строки (кернинг FreeType), высота строки берётся из метрик
файла, запас `margin_ratio` записывается в результат. Ёмкость в символах из профиля служит
только ориентиром для модели; здесь решает измерение.

Лестница при переполнении: более вместительный паттерн среди кандидатов → допустимый кегль из
шкалы шаблона (не ниже доли исходного и порога по роли текста) → сокращение без потери
обязательного (пункты без фактов, предложения без фактов) → разделение слайда в пределах
диапазона. Что не удалось — возвращается как структурированное переполнение, а не теряется.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.generation.matching import SlotInfo
from presentation_designer.shared import text_metrics

JsonDict = dict[str, Any]

FACT_REF = re.compile(r"\{fact:([A-Za-z0-9_.:-]+)\}")
_SENTENCE = re.compile(r"(?<=[.!?…])\s+")
_WS = re.compile(r"\s+")

DEFAULT_INSETS_EMU = (91440, 45720, 91440, 45720)


@dataclass
class Measure:
    """Результат измерения текста в слоте при кегле size_pt."""

    size_pt: float
    lines: int
    max_lines: int
    chars: int
    max_chars: int
    fits: bool
    method: str
    substituted: bool = False
    notes: list[str] = field(default_factory=list)

    def as_fit(self, slot_size_pt: float, action: str, note: str | None = None) -> JsonDict:
        out: JsonDict = {
            "size_pt": round(self.size_pt, 1),
            "slot_size_pt": round(slot_size_pt, 1),
            "lines": self.lines,
            "max_lines": self.max_lines,
            "chars": self.chars,
            "action": action,
        }
        if note:
            out["note"] = note
        return out


# ---------- подстановка фактов ----------


def fact_text(fact: JsonDict) -> str:
    raw = str(fact.get("raw") or "").strip()
    if raw:
        return raw
    value = fact.get("value")
    unit = fact.get("unit")
    if value is None:
        return ""
    text = str(value)
    return f"{text} {unit}".strip() if unit else text


def substitute_facts(text: str, facts: dict[str, JsonDict]) -> str:
    """{fact:id} → значение как в источнике; неизвестная ссылка остаётся как есть (её отловит
    валидатор ссылок)."""

    def repl(m: re.Match[str]) -> str:
        fact = facts.get(m.group(1))
        return fact_text(fact) if fact else m.group(0)

    return FACT_REF.sub(repl, text)


def number_format(fact: JsonDict) -> str:
    """Формат числа для блока number: сырой текст с числом, заменённым на {value}."""
    raw = fact_text(fact)
    m = re.search(r"[-+−]?\d[\d.,]*(?:\s\d{3})*", raw)
    if not m:
        return "{value}"
    return (raw[: m.start()] + "{value}" + raw[m.end() :]).strip() or "{value}"


# ---------- измерение ----------


@functools.lru_cache(maxsize=256)
def _pil_font(path: str, size_pt: float) -> Any:
    from PIL import ImageFont

    return ImageFont.truetype(path, max(4, round(size_pt * 4)))


def _width_pt(text: str, font: text_metrics.ResolvedFont, size_pt: float) -> float:
    if font.face is None:
        return len(text) * text_metrics.heuristic_metrics(size_pt).avg_char_width_pt
    return float(_pil_font(str(font.face.path), round(size_pt, 2)).getlength(text)) / 4.0


def wrap_lines(text: str, width_pt: float, font: text_metrics.ResolvedFont, size_pt: float) -> int:
    """Число строк при жадном переносе по словам; слово шире рамки переносится по символам."""
    if width_pt <= 0:
        return max(1, len(text))
    lines = 0
    for paragraph in text.split("\n"):
        words = _WS.split(paragraph.strip())
        words = [w for w in words if w]
        if not words:
            lines += 1
            continue
        current = ""
        lines += 1
        for word in words:
            candidate = f"{current} {word}".strip()
            if _width_pt(candidate, font, size_pt) <= width_pt:
                current = candidate
                continue
            if current:
                lines += 1
            # Слово шире строки: считаем, сколько строк займёт само слово.
            word_w = _width_pt(word, font, size_pt)
            if word_w > width_pt:
                lines += int(word_w // width_pt)
                current = ""
            else:
                current = word
    return max(lines, 1)


def slot_box_emu(
    slot: SlotInfo, slide_w_emu: int, slide_h_emu: int
) -> tuple[int, int, tuple[int, int, int, int]]:
    """Ширина и высота слота в EMU и внутренние поля."""
    _, _, w, h = slot.bbox
    # Место до соседа и до препятствия в полосе (анализ шаблона), если оно меньше рамки.
    if slot.clear_width:
        w = min(w, slot.clear_width)
    if slot.clear_height:
        h = min(h, slot.clear_height)
    width = round(w * slide_w_emu)
    height = round(h * slide_h_emu)
    insets = DEFAULT_INSETS_EMU
    if slot.insets:
        insets = (
            round(float(slot.insets.get("left", 0)) * slide_w_emu),
            round(float(slot.insets.get("top", 0)) * slide_h_emu),
            round(float(slot.insets.get("right", 0)) * slide_w_emu),
            round(float(slot.insets.get("bottom", 0)) * slide_h_emu),
        )
    return width, height, insets


def measure(
    text: str | list[str],
    slot: SlotInfo,
    slide_w_emu: int,
    slide_h_emu: int,
    *,
    size_pt: float | None = None,
    margin_ratio: float = 0.92,
) -> Measure:
    """Сколько строк занимает текст (или пункты списка) в слоте при кегле size_pt.

    Список — каждый пункт с новой строки с учётом отступа маркера; высота строки и интервалы
    как в образце. Для слота без шрифта (картинка) измерение не имеет смысла: возвращается
    «помещается»."""
    paragraphs = text if isinstance(text, list) else [text]
    chars = sum(len(p) for p in paragraphs)
    base_size = float(slot.size_pt or 18.0)
    size = float(size_pt or base_size)
    if slot.family is None and slot.size_pt is None:
        return Measure(size, 0, 0, chars, slot.max_chars, True, "none")
    font = text_metrics.resolve_font(slot.family, bold=slot.bold, italic=slot.italic)
    width_emu, height_emu, insets = slot_box_emu(slot, slide_w_emu, slide_h_emu)
    left, top, right, bottom = insets
    indent = abs(slot.indent_emu) if (slot.kind == "bullets" or slot.bullet) else 0
    avail_w = max(0.0, (width_emu - left - right - indent) / text_metrics.EMU_PER_PT) * margin_ratio
    avail_h = max(0.0, (height_emu - top - bottom) / text_metrics.EMU_PER_PT)
    metrics = text_metrics.line_metrics(font, size)
    line_h = metrics.line_height_pt * max(slot.line_spacing, 0.5)
    para_gap = (slot.space_before_pt + slot.space_after_pt) * max(len(paragraphs) - 1, 0)
    lines = sum(wrap_lines(p, avail_w, font, size) for p in paragraphs)
    max_lines = int((avail_h - para_gap) // line_h) if line_h > 0 else 0
    if slot.autofit == "resize_shape":
        # Рамка растёт по высоте вместе с текстом: ограничивает только число строк образца
        # с запасом, ширину считаем по факту.
        max_lines = max(max_lines, slot.max_lines, 1)
    if slot.sample_text.strip():
        # Текст образца в рамке помещается по построению: хотя бы одна строка есть.
        max_lines = max(max_lines, 1)
    max_lines = max(max_lines, 0)
    fits = lines <= max_lines and (max_lines > 0 or chars == 0)
    notes = []
    if font.substituted:
        notes.append(f"шрифт {font.requested} заменён на {font.family}")
    return Measure(
        size_pt=size,
        lines=lines,
        max_lines=max_lines,
        chars=chars,
        max_chars=slot.max_chars,
        fits=fits,
        method=metrics.method,
        substituted=font.substituted,
        notes=notes,
    )


# ---------- лестница кеглей ----------


def font_steps(
    slot: SlotInfo,
    scale: list[float],
    *,
    min_ratio: float = 0.75,
    min_pt: float = 12.0,
    fill_below: bool = False,
) -> list[float]:
    """Допустимые кегли ниже исходного: из шкалы шаблона, не ниже min_ratio × исходный и
    min_pt; если шкала не даёт ступеней — шаги по 2 пт. `fill_below` — после ступеней шкалы
    ещё шаги по 2 пт до нижней границы: обложке с темой в 60 знаков мало трёх крупных
    ступеней шаблона (54 → 43,5 → 36), и заголовок иначе наезжает на подзаголовок."""
    base = float(slot.size_pt or 18.0)
    floor = max(base * min_ratio, min_pt)
    steps = sorted({s for s in scale if floor <= s < base}, reverse=True)
    if not steps or fill_below:
        s = (steps[-1] if steps else base) - 2
        while s >= floor:
            steps.append(round(s, 1))
            s -= 2
    return steps


# Ширина, для которой заданы пределы кеглей (слайд 16:9 PowerPoint, 13,33 дюйма).
FLOOR_SLIDE_W_EMU = 12192000


def min_pt_for(
    kind: str, *, body_pt: float, title_pt: float, slide_w_emu: int | None = None
) -> float:
    """Нижний кегль по роли текста. Пределы заданы для слайда 13,33 дюйма; на холсте шире
    (Canva и Google Slides экспортируют 20 дюймов) они растут пропорционально: 12 pt на
    слайде в 20 дюймов выглядят как 8 pt на обычном — мелкий текст, который не прочесть.
    Меньше исходного предел не становится: узкие шаблоны организаторов (10 дюймов) держат
    прежние 12 и 20 pt."""
    scale = max(1.0, (slide_w_emu or FLOOR_SLIDE_W_EMU) / FLOOR_SLIDE_W_EMU)
    if kind in ("title",):
        return title_pt * scale
    if kind in ("subtitle", "number"):
        return max(body_pt, 14.0) * scale
    return body_pt * scale


# ---------- сокращение без потери обязательного ----------


def shorten_text(text: str, keep_facts: bool = True) -> str | None:
    """Убирает последнее предложение без ссылки на факт; None — сокращать нечего."""
    sentences = [s for s in _SENTENCE.split(text.strip()) if s]
    if len(sentences) <= 1:
        return None
    for i in range(len(sentences) - 1, -1, -1):
        if keep_facts and FACT_REF.search(sentences[i]):
            continue
        if len(sentences) - 1 < 1:
            return None
        del sentences[i]
        return " ".join(sentences)
    return None


_CLAUSE = re.compile(r"\s*(?:[:;,]|—|–)\s+")


def shorten_title(text: str) -> str | None:
    """Убирает последнюю часть заголовка после двоеточия, тире или запятой, если ссылки на
    факты остаются в начале; None — сокращать нечего."""
    stripped = text.strip()
    matches = list(_CLAUSE.finditer(stripped))
    if not matches:
        return None
    kept = stripped[: matches[-1].start()].rstrip(" :;,—–")
    if len(kept) < 12:
        return None
    if set(FACT_REF.findall(stripped)) - set(FACT_REF.findall(kept)):
        return None
    return kept


def shorten_items(items: list[JsonDict], min_items: int = 1) -> list[JsonDict] | None:
    """Убирает последний пункт без ссылок на факты; None — нечего убирать."""
    if len(items) <= min_items:
        return None
    for i in range(len(items) - 1, -1, -1):
        item = items[i]
        if item.get("fact_refs") or FACT_REF.search(str(item.get("text", ""))):
            continue
        return items[:i] + items[i + 1 :]
    return None


__all__ = [
    "FACT_REF",
    "Measure",
    "fact_text",
    "font_steps",
    "measure",
    "min_pt_for",
    "number_format",
    "shorten_items",
    "shorten_text",
    "shorten_title",
    "substitute_facts",
    "wrap_lines",
]
