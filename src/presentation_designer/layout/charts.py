"""Нативные диаграммы PowerPoint по набору данных плана и правилам шаблона.

Диаграмма строится через `shapes.add_chart()` python-pptx: отдельная часть `chart*.xml` и
собственная книга данных, поэтому копии редактируются независимо. Ряды берут цвета из
акцентов темы шаблона, подписи — шрифт и кегль подписи из профиля; легенда, подписи данных
и оси включаются по блоку плана. Если в образце уже есть нативная диаграмма того же семейства
(категорийная, круговая, точечная), заменяются только данные — оформление образца остаётся.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from pptx.chart.data import CategoryChartData, XyChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.util import Pt

from presentation_designer.layout.shapes import remove_shape

CHART_TYPES: dict[str, Any] = {
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "stacked_column": XL_CHART_TYPE.COLUMN_STACKED,
    "line": XL_CHART_TYPE.LINE_MARKERS,
    "area": XL_CHART_TYPE.AREA,
    "pie": XL_CHART_TYPE.PIE,
    "doughnut": XL_CHART_TYPE.DOUGHNUT,
    "scatter": XL_CHART_TYPE.XY_SCATTER,
}
FAMILY = {
    "column": "category",
    "bar": "category",
    "stacked_column": "category",
    "line": "category",
    "area": "category",
    "pie": "pie",
    "doughnut": "pie",
    "scatter": "xy",
}
_XL_FAMILY: dict[str, set[Any]] = {
    "pie": {
        XL_CHART_TYPE.PIE,
        XL_CHART_TYPE.PIE_EXPLODED,
        XL_CHART_TYPE.DOUGHNUT,
        XL_CHART_TYPE.DOUGHNUT_EXPLODED,
    },
    "xy": {
        XL_CHART_TYPE.XY_SCATTER,
        XL_CHART_TYPE.XY_SCATTER_LINES,
        XL_CHART_TYPE.XY_SCATTER_LINES_NO_MARKERS,
        XL_CHART_TYPE.XY_SCATTER_SMOOTH,
        XL_CHART_TYPE.XY_SCATTER_SMOOTH_NO_MARKERS,
    },
}
DEFAULT_ACCENTS = ("#0077FF", "#FF3985", "#520A77", "#00B2A9", "#FFAA00", "#7B61FF")
# Логические элементы диаграммы (CT_Boolean): val по схеме по умолчанию «true», и python-pptx
# при True атрибут опускает. ONLYOFFICE такой пустой элемент читает как «false»: скрытая ось
# остаётся видна, сглаженная линия ломается. Поэтому val пишется всегда.
BOOLEAN_TAGS = frozenset(
    {
        "autoTitleDeleted",
        "auto",
        "bubble3D",
        "date1904",
        "delete",
        "invertIfNegative",
        "marker",
        "noMultiLvlLbl",
        "overlay",
        "plotVisOnly",
        "roundedCorners",
        "showBubbleSize",
        "showCatName",
        "showDLblsOverMax",
        "showLeaderLines",
        "showLegendKey",
        "showPercent",
        "showSerName",
        "showVal",
        "smooth",
        "varyColors",
    }
)
_C_NS = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"


@dataclass
class ChartStyle:
    """Шрифт и цвета для новых диаграмм: из профиля шаблона."""

    font_family: str | None = None
    font_size_pt: float = 12.0
    title_size_pt: float = 14.0
    text_color: str = "#000000"
    accents: list[str] = field(default_factory=lambda: list(DEFAULT_ACCENTS))
    gridlines: bool = False

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> ChartStyle:
        tokens = profile.get("design_tokens") or {}
        typography = tokens.get("typography") or {}
        fonts = typography.get("fonts") or []
        family = None
        for f in fonts:
            if "body" in (f.get("roles") or []):
                family = f.get("family")
                break
        if family is None and fonts:
            family = fonts[0].get("family")
        if family is None:
            family = (typography.get("theme_fonts") or {}).get("minor")
        scale = typography.get("scale") or []
        body_sizes = sorted(
            {float(s["size_pt"]) for s in scale if s.get("role") in ("body", "caption")}
        )
        size = next((s for s in body_sizes if s >= 12), body_sizes[-1] if body_sizes else 12.0)
        size = min(size, 14.0)
        colors = tokens.get("colors") or {}
        theme = colors.get("theme") or {}
        accents = [theme[k] for k in sorted(theme) if k.startswith("accent") and theme.get(k)]
        palette = colors.get("palette") or []
        for entry in palette:
            if entry.get("role") in ("primary", "accent") and entry["hex"] not in accents:
                accents.insert(0 if entry.get("role") == "primary" else len(accents), entry["hex"])
        # Слишком светлые акценты (белый на светлом фоне) для рядов не годятся.
        accents = [a for a in accents if luminance(a) < 0.85]
        text = next((e["hex"] for e in palette if e.get("role") == "text"), None)
        text = text or theme.get("dk1") or "#000000"
        from presentation_designer.library.tokens import DesignCode

        code = DesignCode.from_profile(profile)
        if code.accents_from_slides:
            # Тема файла с оформлением не связана: ряды — цветами слайдов шаблона.
            accents, text = list(code.accents), code.text_color
        gridlines = not any(
            any(w in str(g.get("text", "")).lower() for w in ("сетк", "grid"))
            for g in profile.get("guidelines") or []
        )
        return cls(
            font_family=family,
            font_size_pt=size,
            title_size_pt=min(size + 2, 16.0),
            text_color=text,
            accents=accents or list(DEFAULT_ACCENTS),
            gridlines=gridlines,
        )


@dataclass
class ChartSpec:
    """Разобранный блок chart: тип, категории, ряды и признаки оформления."""

    chart_type: str
    categories: list[str]
    series: list[tuple[str, list[float | None]]]
    title: str | None
    units: str | None
    show_legend: bool
    show_axis_labels: bool
    show_data_labels: bool
    number_format: str
    dataset_id: str
    axis_minimum: float | None = None
    axis_maximum: float | None = None
    source_transcription: bool = False


def _to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if math.isfinite(value) else None
    text = str(value).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    text = text.rstrip("%")
    try:
        number = float(text)
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def chart_spec(block_chart: dict[str, Any], dataset: dict[str, Any]) -> ChartSpec:
    source_chart = dataset.get("source_chart") or {}
    if source_chart:
        block_chart = {
            **block_chart,
            "type": source_chart["type"],
            "category_column": dataset["columns"][0]["name"],
            "series": [c["name"] for c in dataset["columns"][1:]],
            "units": dataset["columns"][1].get("unit"),
            "title": dataset.get("title"),
            "show_data_labels": True,
        }
    columns = [str(c.get("name", "")) for c in dataset.get("columns", [])]
    types = {str(c.get("name", "")): str(c.get("type", "")) for c in dataset.get("columns", [])}
    rows = dataset.get("rows") or []
    category = block_chart.get("category_column")
    if category not in columns:
        category = next(
            (c for c in columns if types.get(c) in ("string", "date")),
            columns[0] if columns else "",
        )
    cat_index = columns.index(category) if category in columns else 0
    numeric = [
        c
        for c in columns
        if c != category
        and any(
            columns.index(c) < len(r) and _to_number(r[columns.index(c)]) is not None for r in rows
        )
    ]
    wanted = [s for s in block_chart.get("series") or [] if s in columns and s != category]
    series_names = wanted or numeric[:5]
    categories = [str(r[cat_index]) if cat_index < len(r) else "" for r in rows]
    series: list[tuple[str, list[float | None]]] = []
    for name in series_names:
        idx = columns.index(name)
        series.append((name, [_to_number(r[idx]) if idx < len(r) else None for r in rows]))
    chart_type = str(block_chart.get("type") or "column")
    if chart_type not in CHART_TYPES:
        chart_type = "column"
    if FAMILY[chart_type] == "pie" and series:
        series = series[:1]
    fractional = any(
        v is not None and abs(v - round(v)) > 1e-9 for _, values in series for v in values
    )
    return ChartSpec(
        chart_type=chart_type,
        categories=categories,
        series=series,
        title=str(block_chart["title"]) if block_chart.get("title") else None,
        units=str(block_chart["units"]) if block_chart.get("units") else None,
        show_legend=bool(block_chart.get("show_legend", len(series) > 1)),
        show_axis_labels=bool(block_chart.get("show_axis_labels", True)),
        show_data_labels=bool(block_chart.get("show_data_labels", len(rows) <= 8)),
        number_format="#,##0.0" if fractional else "#,##0",
        dataset_id=str(block_chart.get("dataset_id", "")),
        axis_minimum=source_chart.get("axis_minimum"),
        axis_maximum=source_chart.get("axis_maximum"),
        source_transcription=bool(source_chart),
    )


def chart_data(spec: ChartSpec) -> Any:
    if FAMILY[spec.chart_type] == "xy":
        xy = XyChartData()  # type: ignore[no-untyped-call]
        xs = [_to_number(c) for c in spec.categories]
        for name, values in spec.series:
            s = xy.add_series(name)  # type: ignore[no-untyped-call]
            for x, y in zip(xs, values, strict=False):
                if x is not None and y is not None:
                    s.add_data_point(x, y)
        return xy
    data = CategoryChartData()  # type: ignore[no-untyped-call]
    data.categories = spec.categories
    for name, values in spec.series:
        fractional = any(v is not None and abs(v - round(v)) > 1e-9 for v in values)
        data.add_series(  # type: ignore[no-untyped-call]
            name, values, number_format="#,##0.0" if fractional else "#,##0"
        )
    return data


def luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _rgb(hex_color: str) -> Any:
    return RGBColor.from_string(hex_color.lstrip("#"))  # type: ignore[no-untyped-call]


def _apply_font(font: Any, style: ChartStyle, size_pt: float | None = None) -> None:
    if style.font_family:
        font.name = style.font_family
    font.size = Pt(size_pt or style.font_size_pt)
    font.color.rgb = _rgb(style.text_color)


def style_chart(chart: Any, spec: ChartSpec, style: ChartStyle, *, restyle_series: bool) -> None:
    """Оформление по плану и профилю: легенда, подписи данных, оси, шрифт, цвета рядов."""
    _apply_font(chart.font, style)
    chart.has_title = bool(spec.title)
    if spec.title:
        chart.chart_title.text_frame.text = spec.title
        for p in chart.chart_title.text_frame.paragraphs:
            _apply_font(p.font, style, style.title_size_pt)
    chart.has_legend = spec.show_legend
    if spec.show_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        _apply_font(chart.legend.font, style)
    family = FAMILY[spec.chart_type]
    for plot in chart.plots:
        plot.has_data_labels = spec.show_data_labels
        if spec.show_data_labels:
            labels = plot.data_labels
            labels.number_format_is_linked = True
            _apply_font(labels.font, style)
            if family == "pie":
                labels.show_percentage = False
                labels.show_value = True
            elif spec.chart_type in ("column", "bar"):
                labels.position = XL_LABEL_POSITION.OUTSIDE_END
            elif spec.chart_type == "line":
                labels.position = XL_LABEL_POSITION.ABOVE
        if spec.chart_type in ("column", "bar", "stacked_column") and hasattr(plot, "gap_width"):
            plot.gap_width = 80
            if spec.chart_type == "stacked_column":
                plot.overlap = 100
        if restyle_series:
            _color_series(plot, spec, style)
    if family == "category":
        _style_axes(chart, spec, style)
    elif family == "xy":
        _style_axes(chart, spec, style)


def _color_series(plot: Any, spec: ChartSpec, style: ChartStyle) -> None:
    accents = style.accents or list(DEFAULT_ACCENTS)
    if FAMILY[spec.chart_type] == "pie":
        for series in plot.series:
            for i, point in enumerate(series.points):
                point.format.fill.solid()
                point.format.fill.fore_color.rgb = _rgb(accents[i % len(accents)])
        return
    for i, series in enumerate(plot.series):
        color = _rgb(accents[i % len(accents)])
        if spec.chart_type in ("line", "scatter"):
            series.format.line.color.rgb = color
            series.format.line.width = Pt(2.25)
            series.smooth = False
            try:
                series.marker.format.fill.solid()
                series.marker.format.fill.fore_color.rgb = color
                series.marker.format.line.color.rgb = color
            except (AttributeError, ValueError):
                pass
        else:
            series.format.fill.solid()
            series.format.fill.fore_color.rgb = color


def _style_axes(chart: Any, spec: ChartSpec, style: ChartStyle) -> None:
    try:
        category_axis = chart.category_axis
        value_axis = chart.value_axis
    except (ValueError, AttributeError):
        return
    for axis in (category_axis, value_axis):
        axis.visible = spec.show_axis_labels
        _apply_font(axis.tick_labels.font, style)
        axis.format.line.color.rgb = _rgb("#BFBFBF")
    category_axis.has_major_gridlines = False
    value_axis.has_major_gridlines = style.gridlines and spec.show_axis_labels
    if value_axis.has_major_gridlines:
        value_axis.major_gridlines.format.line.color.rgb = _rgb("#E0E0E0")
    value_axis.tick_labels.number_format = "General"
    value_axis.tick_labels.number_format_is_linked = False
    if spec.axis_minimum is not None:
        value_axis.minimum_scale = spec.axis_minimum
    if spec.axis_maximum is not None:
        value_axis.maximum_scale = spec.axis_maximum
    if spec.units and spec.show_axis_labels:
        value_axis.has_title = True
        value_axis.axis_title.text_frame.text = spec.units
        for p in value_axis.axis_title.text_frame.paragraphs:
            _apply_font(p.font, style)
    else:
        value_axis.has_title = False


def add_chart(
    slide: Any, box: tuple[int, int, int, int], spec: ChartSpec, style: ChartStyle
) -> Any:
    """Новая диаграмма в прямоугольнике box (EMU); возвращает graphicFrame."""
    x, y, cx, cy = box
    frame = slide.shapes.add_chart(CHART_TYPES[spec.chart_type], x, y, cx, cy, chart_data(spec))
    style_chart(frame.chart, spec, style, restyle_series=True)
    explicit_booleans(frame.chart)
    return frame


def explicit_booleans(chart: Any) -> None:
    """Пустые логические элементы диаграммы получают явный val="1" (см. BOOLEAN_TAGS)."""
    for el in chart._chartSpace.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        if (
            tag.startswith(_C_NS)
            and tag[len(_C_NS) :] in BOOLEAN_TAGS
            and el.get("val") is None
            and len(el) == 0
        ):
            el.set("val", "1")


def chart_family_of(chart: Any) -> str:
    try:
        kind = chart.chart_type
    except (ValueError, AttributeError):
        return "unknown"
    for family, members in _XL_FAMILY.items():
        if kind in members:
            return family
    return "category"


def replace_or_add_chart(
    slide: Any,
    frame: Any,
    box: tuple[int, int, int, int],
    spec: ChartSpec,
    style: ChartStyle,
) -> tuple[Any, str]:
    """Диаграмма образца того же семейства получает новые данные (оформление образца
    остаётся); иначе строится новая на том же месте, образец удаляется.
    Возвращает graphicFrame и способ: replaced | rebuilt."""
    if (
        frame is not None
        and not spec.source_transcription
        and chart_family_of(frame.chart) == FAMILY[spec.chart_type]
    ):
        chart = frame.chart
        chart.replace_data(chart_data(spec))
        style_chart(chart, spec, style, restyle_series=False)
        explicit_booleans(chart)
        return frame, "replaced"
    if frame is not None:
        remove_shape(slide, frame._element)
    return add_chart(slide, box, spec, style), "rebuilt"


def describe_chart(chart: Any) -> dict[str, Any]:
    """Сведения о диаграмме для ComposedDeck."""
    try:
        chart_type = str(chart.chart_type).split(".")[-1].split(" ")[0]
    except (ValueError, AttributeError):
        chart_type = "unknown"
    series_count = sum(len(list(p.series)) for p in chart.plots)
    has_axis_titles = False
    try:
        has_axis_titles = bool(chart.value_axis.has_title or chart.category_axis.has_title)
    except (ValueError, AttributeError):
        pass
    return {
        "type": chart_type.lower(),
        "series_count": series_count,
        "has_legend": bool(chart.has_legend),
        "has_axis_titles": has_axis_titles,
        "has_data_labels": any(p.has_data_labels for p in chart.plots),
    }


__all__ = [
    "CHART_TYPES",
    "FAMILY",
    "ChartSpec",
    "ChartStyle",
    "add_chart",
    "chart_data",
    "chart_family_of",
    "chart_spec",
    "describe_chart",
    "explicit_booleans",
    "luminance",
    "replace_or_add_chart",
    "style_chart",
]
