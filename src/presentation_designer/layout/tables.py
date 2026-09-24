"""Нативные таблицы по набору данных: оформление строк, заголовка и границ из таблицы образца.

Если в образце есть таблица, её строки служат шаблонами: первая — для строки заголовков,
остальные — для строк данных по кругу (полосы образца сохраняются); ячейки копируются вместе
с `a:tcPr` (границы, заливка, отступы) и оформлением фрагментов, меняется только текст.
Сетка колонок берёт пропорции образца и масштабируется под ширину слота; лишние строки и
колонки образца убираются, недостающие клонируются. Без таблицы в образце строится
`add_table()` со стилем темы и шрифтом профиля. Данные уже разбиты планом: строки с
`row_offset` до `max_rows`, колонки по списку.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from lxml import etree
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Pt

from presentation_designer.layout.ooxml import NS_A
from presentation_designer.layout.shapes import NS, set_element_box
from presentation_designer.layout.text import fill_text

A_TR = f"{{{NS_A}}}tr"
A_TC = f"{{{NS_A}}}tc"
_MERGE_ATTRS = ("gridSpan", "rowSpan", "hMerge", "vMerge")
NUMERIC_TYPES = ("number", "percent", "money", "integer", "float")
MIN_COL_EMU = 1_600_000  # ≈ 1,75 дюйма: заголовок «Сумма, млн ₽» в 14 пт без переноса


@dataclass
class TableSpec:
    header: list[str]
    rows: list[list[str]]
    numeric_columns: list[bool]
    dataset_id: str
    row_offset: int
    truncated: bool
    highlight_row: int | None

    @property
    def n_rows(self) -> int:
        return len(self.rows) + 1

    @property
    def n_cols(self) -> int:
        return len(self.header)


def format_number(value: Any, language: str = "ru") -> str:
    """Число как в презентациях: дробная часть через запятую (ru), разряды через неразрывный
    пробел от 10 000; строки остаются как есть."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "да" if value else "нет"
    if isinstance(value, int) or (isinstance(value, float) and value.is_integer()):
        number = int(value)
        text = f"{number:,}".replace(",", " ") if abs(number) >= 10000 else str(number)
        return text
    if isinstance(value, float):
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        if language.startswith("ru"):
            text = text.replace(".", ",")
        return text
    return str(value)


def table_spec(
    block_table: dict[str, Any], dataset: dict[str, Any], *, language: str = "ru"
) -> TableSpec:
    columns = dataset.get("columns") or []
    names = [str(c.get("name", "")) for c in columns]
    chosen = [c for c in block_table.get("columns") or [] if c in names] or names
    indexes = [names.index(c) for c in chosen]
    header: list[str] = []
    numeric: list[bool] = []
    for i in indexes:
        col = columns[i]
        name = str(col.get("name", ""))
        unit = col.get("unit")
        if unit and str(unit) not in name:
            name = f"{name}, {unit}"
        header.append(name)
        numeric.append(str(col.get("type", "")) in NUMERIC_TYPES)
    all_rows = dataset.get("rows") or []
    offset = max(0, int(block_table.get("row_offset") or 0))
    max_rows = int(block_table.get("max_rows") or len(all_rows) or 1)
    window = all_rows[offset : offset + max_rows]
    rows = [[format_number(r[i], language) if i < len(r) else "" for i in indexes] for r in window]
    highlight = block_table.get("highlight_row")
    return TableSpec(
        header=header,
        rows=rows,
        numeric_columns=numeric,
        dataset_id=str(block_table.get("dataset_id", "")),
        row_offset=offset,
        truncated=offset + len(window) < len(all_rows),
        highlight_row=int(highlight) if highlight is not None else None,
    )


@dataclass
class TableStyle:
    font_family: str | None = None
    font_size_pt: float = 12.0
    header_fill: str = "#0077FF"
    header_text: str = "#FFFFFF"
    text_color: str = "#000000"

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> TableStyle:
        tokens = profile.get("design_tokens") or {}
        typography = tokens.get("typography") or {}
        fonts = typography.get("fonts") or []
        family = next(
            (f.get("family") for f in fonts if "body" in (f.get("roles") or [])), None
        ) or (fonts[0].get("family") if fonts else None)
        scale = typography.get("scale") or []
        sizes = sorted({float(s["size_pt"]) for s in scale if s.get("role") in ("body", "caption")})
        size = next((s for s in sizes if s >= 12), sizes[-1] if sizes else 12.0)
        colors = tokens.get("colors") or {}
        theme = colors.get("theme") or {}
        palette = colors.get("palette") or []
        primary = next((e["hex"] for e in palette if e.get("role") == "primary"), None)
        text = next((e["hex"] for e in palette if e.get("role") == "text"), None)
        from presentation_designer.library.tokens import DesignCode

        code = DesignCode.from_profile(profile)
        if code.accents_from_slides:
            # Тема файла с оформлением не связана: шапка и текст — цветами слайдов шаблона.
            primary, text = code.primary or code.accent, code.text_color
        return cls(
            font_family=family,
            font_size_pt=min(size, 14.0),
            header_fill=primary or theme.get("accent1") or "#0077FF",
            text_color=text or theme.get("dk1") or "#000000",
        )


def _rgb(hex_color: str) -> Any:
    return RGBColor.from_string(hex_color.lstrip("#"))  # type: ignore[no-untyped-call]


# ---------- таблица по образцу ----------


def _cells(tr: Any) -> list[Any]:
    return list(tr.findall(A_TC))


def _fit_cells(tr: Any, n_cols: int) -> None:
    cells = _cells(tr)
    for extra in cells[n_cols:]:
        tr.remove(extra)
    while len(_cells(tr)) < n_cols:
        tr.append(copy.deepcopy(_cells(tr)[-1]))
    for tc in _cells(tr):
        for attr in _MERGE_ATTRS:
            if attr in tc.attrib:
                del tc.attrib[attr]


def _set_cell_text(tc: Any, text: str, *, align_right: bool, bold: bool = False) -> None:
    fill_text(tc, text)
    for p in tc.findall(f"{{{NS_A}}}txBody/{{{NS_A}}}p"):
        if align_right:
            ppr = p.find(f"{{{NS_A}}}pPr")
            if ppr is None:
                ppr = etree.Element(f"{{{NS_A}}}pPr")
                p.insert(0, ppr)
            ppr.set("algn", "r")
        if bold:
            for rpr in p.iter(f"{{{NS_A}}}rPr"):
                rpr.set("b", "1")


def reshape_sample_table(
    frame_element: Any,
    spec: TableSpec,
    box: tuple[int, int, int, int],
    *,
    style: TableStyle,
) -> None:
    """Перестраивает таблицу образца под данные: строки и колонки по шаблонам образца,
    сетка под ширину слота, высота кадра по строкам."""
    tbl = frame_element.find(".//a:tbl", NS)
    if tbl is None:
        raise ValueError("в образце нет a:tbl")
    grid = tbl.find("a:tblGrid", NS)
    rows = tbl.findall(A_TR)
    if grid is None or not rows:
        raise ValueError("таблица образца без сетки или строк")
    header_tpl = rows[0]
    body_tpls = rows[1:] or [rows[0]]
    if len(body_tpls) >= 3:
        body_tpls = body_tpls[:-1]  # последняя строка образца часто итог: в цикл не берём
    x, y, cx, cy = box
    # Сетка колонок: пропорции образца. PowerPoint рисует таблицу по сумме gridCol, а не по
    # ext кадра, поэтому доступная ширина — большее из них; колонки не уже MIN_COL_EMU,
    # при нехватке места сетка масштабируется вниз.
    widths = [int(g.get("w", 0)) for g in grid.findall("a:gridCol", NS)]
    if not widths:
        widths = [cx // max(spec.n_cols, 1)]
    available = max(cx, sum(widths))
    if spec.n_cols <= len(widths):
        chosen = [max(w, MIN_COL_EMU) for w in widths[: spec.n_cols]]
    else:
        chosen = [max(w, MIN_COL_EMU) for w in widths] + [max(widths[-1], MIN_COL_EMU)] * (
            spec.n_cols - len(widths)
        )
    total = sum(chosen) or 1
    if total > available:
        scaled = [max(1, round(w * available / total)) for w in chosen]
        scaled[-1] += available - sum(scaled)
    else:
        scaled = list(chosen)
    cx = sum(scaled)
    for g in list(grid):
        grid.remove(g)
    for w in scaled:
        etree.SubElement(grid, f"{{{NS_A}}}gridCol", w=str(w))
    # Строки: заголовок и данные по шаблонам.
    new_rows: list[Any] = []
    header = copy.deepcopy(header_tpl)
    _fit_cells(header, spec.n_cols)
    for tc, text in zip(_cells(header), spec.header, strict=True):
        _set_cell_text(tc, text, align_right=False)
    new_rows.append(header)
    for r_index, values in enumerate(spec.rows):
        tr = copy.deepcopy(body_tpls[r_index % len(body_tpls)])
        _fit_cells(tr, spec.n_cols)
        highlight = spec.highlight_row is not None and r_index == spec.highlight_row
        for tc, text, numeric in zip(_cells(tr), values, spec.numeric_columns, strict=True):
            _set_cell_text(tc, text, align_right=numeric, bold=highlight)
        new_rows.append(tr)
    for tr in rows:
        tbl.remove(tr)
    for tr in new_rows:
        tbl.append(tr)
    # Высота: строки образца, при переполнении слота — пропорционально меньше.
    heights = [int(tr.get("h", 0)) or Emu(Pt(style.font_size_pt * 2)) for tr in new_rows]
    total_h = sum(heights)
    if total_h > cy and total_h > 0:
        min_h = int(Pt(style.font_size_pt * 1.6))
        factor = cy / total_h
        heights = [max(min_h, round(h * factor)) for h in heights]
    for tr, h in zip(new_rows, heights, strict=True):
        tr.set("h", str(h))
    set_element_box(frame_element, (x, y, cx, sum(heights)))


# ---------- новая таблица ----------


def add_table(
    slide: Any, box: tuple[int, int, int, int], spec: TableSpec, style: TableStyle
) -> Any:
    """Таблица python-pptx со стилем темы; шрифт и заголовок по профилю."""
    x, y, cx, cy = box
    frame = slide.shapes.add_table(spec.n_rows, spec.n_cols, x, y, cx, cy)
    table = frame.table
    row_h = max(int(Pt(style.font_size_pt * 2)), cy // max(spec.n_rows, 1))
    for row in table.rows:
        row.height = Emu(row_h)
    for c, text in enumerate(spec.header):
        cell = table.cell(0, c)
        cell.text = text
        cell.fill.solid()
        cell.fill.fore_color.rgb = _rgb(style.header_fill)
        for p in cell.text_frame.paragraphs:
            for run in p.runs:
                run.font.bold = True
                run.font.size = Pt(style.font_size_pt)
                run.font.color.rgb = _rgb(style.header_text)
                if style.font_family:
                    run.font.name = style.font_family
    for r, values in enumerate(spec.rows, start=1):
        for c, text in enumerate(values):
            cell = table.cell(r, c)
            cell.text = text
            for p in cell.text_frame.paragraphs:
                if spec.numeric_columns[c]:
                    p.alignment = PP_ALIGN.RIGHT
                for run in p.runs:
                    run.font.size = Pt(style.font_size_pt)
                    run.font.bold = spec.highlight_row is not None and r - 1 == spec.highlight_row
                    run.font.color.rgb = _rgb(style.text_color)
                    if style.font_family:
                        run.font.name = style.font_family
    set_element_box(frame._element, (x, y, cx, row_h * spec.n_rows))
    return frame


def describe_table(frame: Any) -> dict[str, Any]:
    table = frame.table
    return {"rows": len(table.rows), "cols": len(table.columns)}


__all__ = [
    "TableSpec",
    "TableStyle",
    "add_table",
    "describe_table",
    "format_number",
    "reshape_sample_table",
    "table_spec",
]
