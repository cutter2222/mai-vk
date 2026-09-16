"""Числа, проценты, деньги и даты в русском и английском написании.

Общий разбор для реестра фактов и наборов данных: «12,5 млн ₽» → 12 500 000 с единицей ₽,
«40 %» → 40 с единицей %, «+13 п. п.» → 13 п. п., «16.09.2026» и «май 2026» → даты,
«10–12» → диапазон. Разбор детерминированный; неоднозначный смысл (что именно измерено)
решается контекстом в facts.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

NBSP = " "
NNBSP = " "

MULTIPLIERS: dict[str, float] = {
    "тыс": 1e3,
    "тыс.": 1e3,
    "к": 1e3,
    "k": 1e3,
    "млн": 1e6,
    "млн.": 1e6,
    "mln": 1e6,
    "m": 1e6,
    "млрд": 1e9,
    "млрд.": 1e9,
    "bn": 1e9,
    "b": 1e9,
    "трлн": 1e12,
    "трлн.": 1e12,
    "K": 1e3,
    "M": 1e6,
    "B": 1e9,
}

CURRENCY_UNITS: dict[str, str] = {
    "₽": "₽",
    "руб": "₽",
    "руб.": "₽",
    "р.": "₽",
    "rub": "₽",
    "$": "$",
    "usd": "$",
    "долл": "$",
    "долл.": "$",
    "€": "€",
    "eur": "€",
    "£": "£",
}

PERCENT_UNITS: dict[str, str] = {
    "%": "%",
    "процент": "%",
    "процента": "%",
    "процентов": "%",
    "п. п.": "п. п.",
    "п.п.": "п. п.",
    "пп": "п. п.",
    "п. п": "п. п.",
}

OTHER_UNITS = (
    "чел.",
    "чел",
    "человек",
    "шт.",
    "шт",
    "ед.",
    "дней",
    "дня",
    "день",
    "часов",
    "часа",
    "час",
    "мин",
    "минут",
    "мес.",
    "месяцев",
    "месяца",
    "лет",
    "года",
    "год",
    "недель",
    "недели",
    "неделя",
    "неделю",
    "раз",
    "раза",
    "x",
    "×",
    "штук",
    "клиентов",
    "пользователей",
    "сотрудников",
    "заказов",
    "сделок",
    "слайдов",
    "стран",
    "городов",
    "точек",
    "балл",
    "балла",
    "баллов",
    "км",
    "м",
    "кг",
    "т",
    "гб",
    "мб",
    "тб",
)

MONTHS = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "ма": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}
MONTH_RE = (
    r"(?:январ[ьяе]|феврал[ьяе]|март[ае]?|апрел[ьяе]|ма[йяе]|июн[ьяе]|июл[ьяе]|"
    r"август[ае]?|сентябр[ьяе]|октябр[ьяе]|ноябр[ьяе]|декабр[ьяе])"
)
NUMBER_CORE = r"[+\-−–]?\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|[+\-−–]?\d+(?:[.,]\d+)?"
MULT_RE = r"(?:тыс\.?|млн\.?|млрд\.?|трлн\.?|mln|bn|(?<=\d)[KMB](?![A-Za-z]))"
UNIT_RE = (
    r"(?:%|процент(?:а|ов)?|п\.\s?п\.?|пп|₽|руб\.?|р\.|\$|€|£|usd|eur|rub|долл\.?|"
    + "|".join(re.escape(u) for u in sorted(OTHER_UNITS, key=len, reverse=True))
    + r")"
)

# Порядок важен: сначала даты и диапазоны, потом одиночные числа.
DATE_DMY = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")
DATE_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DATE_MONTH_YEAR = re.compile(rf"\b({MONTH_RE})\s+(\d{{4}})\b", re.I)
DATE_QUARTER = re.compile(
    r"\b([1-4IV]{1,3})\s*(?:-?[йгм]?\s*)?кв(?:\.|артал[ае]?)\s*(\d{4})?", re.I
)
DATE_YEAR = re.compile(r"\b(?:в\s+|за\s+|к\s+|на\s+)?((?:19|20)\d{2})\s*(?:г\.|год[аеу]?|году)?\b")
RANGE_RE = re.compile(
    rf"(?<![\d,.])({NUMBER_CORE})\s*(?:{MULT_RE})?\s*(?:{UNIT_RE})?\s*(?:[-–—]|до)\s*({NUMBER_CORE})\s*({MULT_RE})?\s*({UNIT_RE})?",
    re.I,
)
RATIO_RE = re.compile(
    rf"(?:(?<![\w])[x×]\s?({NUMBER_CORE})|(?<![\d,.])({NUMBER_CORE})\s?(?:[x×](?![\w])|раза?\b))",
    re.I,
)
YEAR_WORDS = ("год", "года", "году", "г.", "гг.", "годы", "лет")
NUMBER_RE = re.compile(
    rf"(?<![\d,.\w])(?:(₽|\$|€|£)\s*)?({NUMBER_CORE})(?![\d])\s*({MULT_RE})?\s*({UNIT_RE})?",
    re.I,
)

_DECIMAL_COMMA = re.compile(r"^[+\-−–]?\d+,\d+$")


@dataclass(frozen=True)
class NumberMatch:
    raw: str
    kind: str  # number | percent | money | date | range | ratio
    value: float | str
    unit: str | None
    start: int
    end: int
    # Для диапазона: обе границы
    value_to: float | str | None = None
    sign: str | None = None  # "+" или "-" из исходного текста


def parse_number_text(text: str) -> float | None:
    """«12 500,5» → 12500.5; «1.234» с одной точкой и тремя цифрами — тысячи."""
    s = text.strip().replace(NBSP, " ").replace(NNBSP, " ")
    negative = s.startswith(("-", "−", "–"))
    s = s.lstrip("+-−– ")
    if not s:
        return None
    if _DECIMAL_COMMA.match(s):
        s = s.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:[ ]\d{3})+(?:[.,]\d+)?", s):
        s = s.replace(" ", "")
        if s.count(",") == 1 and s.count(".") == 0:
            s = s.replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+", s):
        s = s.replace(",", "")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
        s = s.replace(".", "")
    else:
        s = s.replace(" ", "")
        if s.count(",") == 1 and s.count(".") == 0:
            s = s.replace(",", ".")
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


def normalize_unit(unit: str | None) -> tuple[str | None, str]:
    """Единица в каноническом виде и вид факта: percent | money | number."""
    if not unit:
        return None, "number"
    u = unit.strip().lower().replace(NBSP, " ")
    u_compact = re.sub(r"\s+", " ", u)
    if u_compact in PERCENT_UNITS:
        return PERCENT_UNITS[u_compact], "percent"
    if u_compact in CURRENCY_UNITS:
        return CURRENCY_UNITS[u_compact], "money"
    # «млн ₽», «тыс. руб.»: множитель остаётся в единице, вид — деньги.
    parts = u_compact.split()
    if len(parts) == 2 and parts[1] in CURRENCY_UNITS and parts[0].rstrip(".") in MULTIPLIERS:
        return f"{parts[0]} {CURRENCY_UNITS[parts[1]]}", "money"
    return unit.strip(), "number"


def apply_multiplier(value: float, mult: str | None) -> float:
    if not mult:
        return value
    if mult in MULTIPLIERS:
        return value * MULTIPLIERS[mult]
    key = mult.lower().rstrip(".")
    return value * MULTIPLIERS.get(key, 1.0)


def find_numbers(text: str) -> list[NumberMatch]:
    """Все кандидаты в тексте без пересечений: даты, диапазоны, числа с единицами."""
    found: list[NumberMatch] = []
    taken: list[tuple[int, int]] = []

    def free(start: int, end: int) -> bool:
        return all(end <= s or start >= e for s, e in taken)

    def take(m: NumberMatch) -> None:
        found.append(m)
        taken.append((m.start, m.end))

    for m in DATE_DMY.finditer(text):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= d <= 31 and 1 <= mo <= 12:
            take(
                NumberMatch(
                    m.group(0), "date", f"{y:04d}-{mo:02d}-{d:02d}", None, m.start(), m.end()
                )
            )
    for m in DATE_ISO.finditer(text):
        if free(m.start(), m.end()):
            take(NumberMatch(m.group(0), "date", m.group(0), None, m.start(), m.end()))
    for m in DATE_MONTH_YEAR.finditer(text):
        if free(m.start(), m.end()):
            month = _month_number(m.group(1))
            take(
                NumberMatch(
                    m.group(0),
                    "date",
                    f"{int(m.group(2)):04d}-{month:02d}",
                    None,
                    m.start(),
                    m.end(),
                )
            )
    for m in DATE_QUARTER.finditer(text):
        if free(m.start(), m.end()):
            q = _roman_or_int(m.group(1))
            if q is None:
                continue
            year = m.group(2)
            quarter = f"{year}-Q{q}" if year else f"Q{q}"
            take(NumberMatch(m.group(0).strip(), "date", quarter, None, m.start(), m.end()))
    for m in RANGE_RE.finditer(text):
        if not free(m.start(), m.end()):
            continue
        a, b = parse_number_text(m.group(1)), parse_number_text(m.group(2))
        if a is None or b is None:
            continue
        unit, kind = normalize_unit(m.group(4))
        mult = m.group(3)
        a, b = apply_multiplier(a, mult), apply_multiplier(b, mult)
        # «с 31 % до 44 %» — два значения, не диапазон; RANGE ловит только «10–12» и «10 до 12».
        if " до " in m.group(0):
            continue
        if kind == "number" and unit is None and _looks_like_year(a) and _looks_like_year(b):
            continue
        take(NumberMatch(m.group(0).strip(), "range", a, unit, m.start(), m.end(), value_to=b))
    for m in RATIO_RE.finditer(text):
        if not free(m.start(), m.end()):
            continue
        ratio = parse_number_text(m.group(1) or m.group(2) or "")
        if ratio is None:
            continue
        take(NumberMatch(m.group(0).strip(), "ratio", ratio, "×", m.start(), m.end()))
    for m in NUMBER_RE.finditer(text):
        if not free(m.start(), m.end()):
            continue
        core = m.group(2)
        parsed_value = parse_number_text(core)
        if parsed_value is None:
            continue
        currency = m.group(1)
        mult = m.group(3)
        unit_text = m.group(4) or currency
        unit, kind = normalize_unit(unit_text)
        if currency and not m.group(4):
            unit, kind = CURRENCY_UNITS.get(currency, currency), "money"
        value = apply_multiplier(parsed_value, mult)
        raw = m.group(0).strip()
        if (
            kind == "number"
            and mult is None
            and (unit is None or unit.lower() in YEAR_WORDS)
            and _looks_like_year(value)
            and unit is not None
        ):
            take(NumberMatch(raw, "date", str(int(value)), None, m.start(), m.end()))
            continue
        if kind == "number" and unit is None and mult is None and _looks_like_year(value):
            # Голый год без единицы: «в 2025 году» — дата, а не число.
            m_year = DATE_YEAR.search(text, max(0, m.start() - 3), min(len(text), m.end() + 6))
            if m_year and free(m_year.start(), m_year.end()):
                take(
                    NumberMatch(
                        m_year.group(0).strip(),
                        "date",
                        str(int(value)),
                        None,
                        m_year.start(),
                        m_year.end(),
                    )
                )
                continue
        if kind == "number" and unit is None and mult is None and re.fullmatch(r"\d", core):
            # Одиночная цифра без единицы («3 шага») почти всегда не показатель.
            continue
        sign = core[0] if core[0] in "+-−–" else None
        take(NumberMatch(raw, kind, value, unit, m.start(), m.end(), sign=sign))
    found.sort(key=lambda x: x.start)
    return found


def _looks_like_year(value: float) -> bool:
    return float(value).is_integer() and 1990 <= value <= 2100


def _month_number(name: str) -> int:
    lowered = name.lower()
    for prefix, number in sorted(MONTHS.items(), key=lambda kv: -len(kv[0])):
        if lowered.startswith(prefix):
            return number
    return 1


def _roman_or_int(token: str) -> int | None:
    t = token.upper()
    romans = {"I": 1, "II": 2, "III": 3, "IV": 4}
    if t in romans:
        return romans[t]
    try:
        q = int(t)
    except ValueError:
        return None
    return q if 1 <= q <= 4 else None


def format_value(value: Any) -> Any:
    """Целые без дробной части остаются int в JSON: 12500000.0 → 12500000."""
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return int(value)
    return value
