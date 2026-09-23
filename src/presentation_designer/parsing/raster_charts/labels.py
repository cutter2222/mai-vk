"""Числа из подписей диаграммы: как напечатано, без пересчёта множителей.

«12,5 млн» остаётся 12.5 с единицей «млн», «40%» — 40 с «%»: диаграмма показывает то, что было
на картинке, а формат подписи повторяет десятичные знаки и разделители тысяч.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from presentation_designer.parsing.content.numbers import parse_number_text

_CORE = re.compile(
    r"[+\-−–]?(?:\d{1,3}(?:[,  ]\d{3})+(?:[.,]\d+)?|\d{1,3}(?:\.\d{3}){2,}|\d+(?:[.,]\d+)?)"
)


@dataclass(frozen=True)
class LabelNumber:
    value: float
    unit: str | None
    decimals: int
    thousands: bool


def comma_thousands(texts: Sequence[str | None]) -> bool:
    """Запятая — разделитель тысяч: после неё ровно три цифры («3,000», «1,000,000»), и в наборе
    нет десятичной запятой («1,5», «0,25»). Тогда «500,000» — полмиллиона, а «3,000» — три
    тысячи; «12,5 млн» и шкала «0,5 … 2,5» читаются по-русски."""
    joined = [t for t in texts if t]
    thousands = any(re.search(r"\d,\d{3}(?!\d)", t) for t in joined)
    decimal = any(re.search(r"\d,\d{1,2}(?!\d)", t) for t in joined)
    return thousands and not decimal


def label_numbers(texts: Sequence[str | None]) -> list[LabelNumber | None]:
    thousands = comma_thousands(texts)
    return [label_number(t, comma_thousands=thousands) for t in texts]


def label_number(text: str | None, *, comma_thousands: bool = False) -> LabelNumber | None:
    """Подпись целиком — число с единицей до или после: «7%», «$1.2», «20,000,000»."""
    if not text:
        return None
    s = text.strip().replace(" ", " ").replace(" ", " ")
    m = _CORE.search(s)
    if m is None:
        return None
    before, core, after = s[: m.start()].strip(), m.group(0), s[m.end() :].strip()
    if len(before) > 3 or len(after) > 12:
        return None
    if comma_thousands and re.fullmatch(r"[+\-−–]?\d{1,3}(?:,\d{3})+(?:\.\d+)?", core):
        core = core.replace(",", "")
    value = parse_number_text(core)
    if value is None:
        return None
    digits = m.group(0).lstrip("+-−–")
    thousands = bool(re.search(r"\d[,  ]\d{3}(?!\d)", digits)) and not re.fullmatch(
        r"\d+,\d{1,2}", digits
    )
    frac = re.search(r"[.,](\d+)$", digits)
    decimals = len(frac.group(1)) if frac and not (thousands and len(frac.group(1)) == 3) else 0
    unit = (after or before) or None
    return LabelNumber(value, unit, decimals, thousands)


def number_format(samples: list[LabelNumber]) -> str:
    """Формат Excel по напечатанным подписям: тысячи, десятичные знаки, знак процента."""
    if not samples:
        return "General"
    decimals = max(s.decimals for s in samples)
    base = "#,##0" if any(s.thousands for s in samples) else "0"
    if decimals:
        base += "." + "0" * decimals
    units = {s.unit for s in samples if s.unit}
    if units == {"%"}:
        return base + '"%"'
    return base
