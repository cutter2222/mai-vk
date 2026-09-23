"""Наборы данных из таблиц и листов: заголовки, типы колонок и единицы.

Тип колонки выводится из формата ячеек (xlsx), единицы в заголовке («Открываемость, %»)
и содержимого («12,5 млн ₽»): percent, money, date, number, иначе string. Числа приводятся
к числам, текст остаётся текстом, пустые ячейки — null. Строки за пределом
`max_dataset_rows` не попадают в пакет, но их число записывается (total_rows, truncated).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.parsing.content.numbers import (
    NUMBER_RE,
    find_numbers,
    format_value,
    normalize_unit,
    parse_number_text,
)
from presentation_designer.parsing.content.parsers.base import Location, ParsedTable

JsonDict = dict[str, Any]

_HEADER_UNIT = re.compile(
    r"^(.*?)[,\s]*[\(（]?\s*(%|₽|руб\.?|\$|€|тыс\.? ?₽|млн\.? ?₽|млрд\.? ?₽|тыс\.?|млн\.?|млрд\.?"
    r"|п\. ?п\.|чел\.?|шт\.?|дн(?:ей|и)|ч(?:ас(?:ов|а)?)?|мес\.?|лет|дней)\s*[\)）]?$",
    re.I,
)
_DATE_CELL = re.compile(r"^\d{4}-\d{2}(?:-\d{2})?$|^\d{1,2}[./]\d{1,2}[./]\d{4}$")
_MONTHS = (
    "январ",
    "феврал",
    "март",
    "апрел",
    "май",
    "мая",
    "июн",
    "июл",
    "август",
    "сентябр",
    "октябр",
    "ноябр",
    "декабр",
)
_QUARTER = re.compile(r"^(?:[1-4]|I{1,3}|IV)\s*кв", re.I)
_UNIT_ONLY = re.compile(r"^(%|₽|\$|€|п\. ?п\.)$")


@dataclass
class Column:
    name: str
    type: str
    unit: str | None = None


@dataclass
class Dataset:
    dataset_id: str
    title: str | None
    columns: list[Column]
    rows: list[list[Any]]
    source_id: str
    block_id: str | None
    location: Location
    total_rows: int
    truncated: bool
    # Значения по колонкам до усечения — для фактов и производных показателей.
    numeric_columns: dict[int, list[tuple[int, float]]] = field(default_factory=dict)
    source_chart: JsonDict | None = None

    def as_dict(self) -> JsonDict:
        out: JsonDict = {
            "dataset_id": self.dataset_id,
            "columns": [
                {
                    k: v
                    for k, v in {"name": c.name, "type": c.type, "unit": c.unit}.items()
                    if v is not None
                }
                for c in self.columns
            ],
            "rows": self.rows,
            "source_id": self.source_id,
            "total_rows": self.total_rows,
            "truncated": self.truncated,
        }
        if self.title:
            out["title"] = self.title
        if self.source_chart:
            out["source_chart"] = self.source_chart
        if self.block_id:
            out["block_id"] = self.block_id
        loc = {
            k: v
            for k, v in self.location.as_dict().items()
            if k in ("page", "sheet", "slide", "cell_range")
        }
        if loc:
            out["source_location"] = loc
        return out


def build_dataset(
    table: ParsedTable,
    *,
    dataset_id: str,
    source_id: str,
    block_id: str | None,
    max_rows: int = 200,
) -> Dataset | None:
    rows = [list(r) for r in table.rows if any(c not in (None, "") for c in r)]
    if not rows:
        return None
    width = max(len(r) for r in rows)
    rows = [r + [None] * (width - len(r)) for r in rows]
    header, body = _split_header(rows)
    if not body:
        # Таблица из одной строки: заголовка нет, строка — данные.
        header, body = [f"Колонка {i + 1}" for i in range(width)], rows
    formats = list(table.column_formats) + [None] * (width - len(table.column_formats))
    columns: list[Column] = []
    parsed_body: list[list[Any]] = [[None] * width for _ in body]
    numeric_columns: dict[int, list[tuple[int, float]]] = {}
    for col in range(width):
        name, header_unit = _split_header_unit(header[col])
        raw_values = [r[col] for r in body]
        parsed, kind, unit = _parse_column(raw_values, formats[col], header_unit)
        columns.append(Column(name=name, type=kind, unit=unit))
        for row_index, value in enumerate(parsed):
            parsed_body[row_index][col] = value
        if kind in ("number", "percent", "money"):
            numeric_columns[col] = [
                (i, float(v)) for i, v in enumerate(parsed) if isinstance(v, int | float)
            ]
    total = len(parsed_body)
    truncated = total > max_rows
    return Dataset(
        dataset_id=dataset_id,
        title=table.title,
        columns=columns,
        rows=parsed_body[:max_rows],
        source_id=source_id,
        block_id=block_id,
        location=table.location,
        total_rows=total,
        truncated=truncated,
        numeric_columns=numeric_columns,
    )


def _split_header(rows: list[list[Any]]) -> tuple[list[str], list[list[Any]]]:
    """Первая строка — заголовок, если она текстовая, а ниже есть числа или больше одной строки."""
    first = rows[0]
    textual = sum(1 for c in first if isinstance(c, str) and parse_number_text(c) is None)
    if len(rows) > 1 and textual >= max(1, len(first) // 2):
        header = [
            str(c) if c not in (None, "") else f"Колонка {i + 1}" for i, c in enumerate(first)
        ]
        return header, rows[1:]
    return [f"Колонка {i + 1}" for i in range(len(first))], rows


def _split_header_unit(header: str) -> tuple[str, str | None]:
    m = _HEADER_UNIT.match(header.strip())
    if m and m.group(1).strip():
        unit, _ = normalize_unit(m.group(2))
        return m.group(1).strip(" ,;:"), unit
    return header.strip(), None


def _parse_column(
    values: list[Any], cell_format: str | None, header_unit: str | None
) -> tuple[list[Any], str, str | None]:
    parsed: list[Any] = []
    numeric = 0
    dates = 0
    filled = 0
    units: dict[str, int] = {}
    kinds: dict[str, int] = {}
    for value in values:
        if value in (None, ""):
            parsed.append(None)
            continue
        filled += 1
        if isinstance(value, bool):
            parsed.append("да" if value else "нет")
            continue
        if isinstance(value, int | float):
            parsed.append(format_value(float(value)))
            numeric += 1
            continue
        text = str(value).strip()
        if _is_date_text(text):
            parsed.append(text)
            dates += 1
            continue
        number = _cell_number(text)
        if number is not None:
            val, unit, kind = number
            parsed.append(format_value(val))
            numeric += 1
            if unit:
                units[unit] = units.get(unit, 0) + 1
            kinds[kind] = kinds.get(kind, 0) + 1
            continue
        parsed.append(text)
    if filled == 0:
        return parsed, "string", header_unit
    if numeric >= max(1, round(filled * 0.7)):
        unit = header_unit or (max(units, key=units.get) if units else None)  # type: ignore[arg-type]
        kind = "number"
        if cell_format == "percent":
            kind = "percent"
            unit = unit or "%"
            # Excel хранит проценты долями: 0,31 → 31 %.
            parsed = [
                format_value(v * 100) if isinstance(v, int | float) and abs(v) <= 1.0 else v
                for v in parsed
            ]
        elif cell_format == "money":
            kind, unit = "money", unit or "₽"
        elif unit:
            _, kind_by_unit = normalize_unit(unit)
            kind = kind_by_unit
        elif kinds:
            kind = max(kinds, key=kinds.get)  # type: ignore[arg-type]
        # Текстовые ячейки в числовой колонке остаются текстом (например «н/д»).
        return parsed, kind, unit
    if dates >= max(1, round(filled * 0.7)) or cell_format == "date":
        return parsed, "date", None
    return [str(v) if v is not None else None for v in parsed], "string", None


def _is_date_text(text: str) -> bool:
    lowered = text.lower()
    if _DATE_CELL.match(text):
        return True
    if _QUARTER.match(text):
        return True
    return any(lowered.startswith(m) for m in _MONTHS) and len(text) <= 20


def _cell_number(text: str) -> tuple[float, str | None, str] | None:
    """Ячейка целиком число с единицей: «12,5 млн ₽», «31 %», «-4,1»."""
    m = NUMBER_RE.fullmatch(text.strip())
    if m is None:
        matches = find_numbers(text)
        if len(matches) == 1 and matches[0].kind in ("number", "percent", "money"):
            only = matches[0]
            if only.raw.replace(" ", "") == text.replace(" ", ""):
                return float(only.value), only.unit, only.kind
        return None
    matches = find_numbers(text)
    if not matches or matches[0].kind not in ("number", "percent", "money"):
        return None
    only = matches[0]
    return float(only.value), only.unit, only.kind


def dataset_cell_ref(dataset: Dataset, row_index: int, col_index: int) -> str | None:
    """Адрес ячейки листа по индексам строки/колонки набора (учитывает строку заголовка)."""
    rng = dataset.location.cell_range
    if not rng or ":" not in rng:
        return None
    start = rng.split(":")[0]
    m = re.match(r"([A-Z]+)(\d+)", start)
    if not m:
        return None
    col_start = _col_index(m.group(1))
    row_start = int(m.group(2))
    return f"{_col_letter(col_start + col_index)}{row_start + 1 + row_index}"


def _col_index(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    return index


def _col_letter(index: int) -> str:
    letters = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters or "A"


__all__ = ["Column", "Dataset", "build_dataset", "dataset_cell_ref"]
