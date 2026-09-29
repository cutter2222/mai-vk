"""Объёмные диаграммы для python-pptx.

python-pptx не знает c:bar3DChart, c:line3DChart и c:pie3DChart: перебор chart.plots на
такой диаграмме падает «unsupported plot type», и шаблон с объёмной гистограммой ронял
сборку. Здесь они читаются как обычные графики: ряды, категории, подписи значений, а
chart_type — объёмный тип XL_CHART_TYPE (ряды заодно и у area3DChart). Свойства формы
(gap_width и т. п.) не даём: порядок элементов у объёмных другой, запись испортила бы XML.
"""

from __future__ import annotations

from typing import Any

from pptx.chart import chart as _chart
from pptx.chart import plot as _plot
from pptx.chart import series as _series
from pptx.enum.chart import XL_CHART_TYPE as XL
from pptx.oxml import register_element_cls
from pptx.oxml.chart.plot import CT_BarChart, CT_LineChart, CT_PieChart
from pptx.oxml.ns import qn

# Дочерние элементы до подписей (dLbls) у объёмных те же, что у плоских, — для чтения рядов
# и включения подписей классов плоских хватает.
register_element_cls("c:bar3DChart", CT_BarChart)
register_element_cls("c:line3DChart", CT_LineChart)
register_element_cls("c:pie3DChart", CT_PieChart)


class Bar3DPlot(_plot._BasePlot):
    """Объёмная гистограмма (c:bar3DChart)."""


class Line3DPlot(_plot._BasePlot):
    """Объёмный график (c:line3DChart)."""


class Pie3DPlot(_plot._BasePlot):
    """Объёмная круговая (c:pie3DChart)."""


def _val(element: Any, tag: str, default: str) -> str:
    child = element.find(qn(tag))
    return default if child is None else str(child.get("val", default))


def _bar3d_type(plot: Any) -> Any:
    column = _val(plot._element, "c:barDir", "col") == "col"
    grouping = _val(plot._element, "c:grouping", "clustered")
    if column:
        return {
            "standard": XL.THREE_D_COLUMN,
            "stacked": XL.THREE_D_COLUMN_STACKED,
            "percentStacked": XL.THREE_D_COLUMN_STACKED_100,
        }.get(grouping, XL.THREE_D_COLUMN_CLUSTERED)
    return {
        "stacked": XL.THREE_D_BAR_STACKED,
        "percentStacked": XL.THREE_D_BAR_STACKED_100,
    }.get(grouping, XL.THREE_D_BAR_CLUSTERED)


_PLOTS: dict[str, Any] = {
    qn("c:bar3DChart"): Bar3DPlot,
    qn("c:line3DChart"): Line3DPlot,
    qn("c:pie3DChart"): Pie3DPlot,
}
_TYPES: dict[str, Any] = {
    "Bar3DPlot": _bar3d_type,
    "Line3DPlot": lambda plot: XL.THREE_D_LINE,
    "Pie3DPlot": lambda plot: XL.THREE_D_PIE,
}

# Ряды: у area3DChart python-pptx знает график, но не ряды.
_SERIES: dict[str, Any] = {
    qn("c:area3DChart"): _series.AreaSeries,
    qn("c:bar3DChart"): _series.BarSeries,
    qn("c:line3DChart"): _series.LineSeries,
    qn("c:pie3DChart"): _series.PieSeries,
}

# Исходные функции python-pptx (без аннотаций — держим как Any).
_plot_factory: Any = _plot.PlotFactory
_series_factory: Any = _series._SeriesFactory
_chart_type: Any = vars(_plot.PlotTypeInspector)["chart_type"].__func__


def PlotFactory(x_chart: Any, chart: Any) -> Any:  # noqa: N802 — имя как в python-pptx
    cls = _PLOTS.get(x_chart.tag)
    return cls(x_chart, chart) if cls is not None else _plot_factory(x_chart, chart)


def _SeriesFactory(ser: Any) -> Any:  # noqa: N802
    cls = _SERIES.get(ser.getparent().tag)
    return cls(ser) if cls is not None else _series_factory(ser)


def _inspect(cls: Any, plot: Any) -> Any:
    kind = _TYPES.get(plot.__class__.__name__)
    return kind(plot) if kind is not None else _chart_type(cls, plot)


_plot.PlotFactory = PlotFactory
_series._SeriesFactory = _SeriesFactory
setattr(_chart, "PlotFactory", PlotFactory)  # noqa: B010 — pptx.chart.chart импортирует её к себе
setattr(_plot.PlotTypeInspector, "chart_type", classmethod(_inspect))  # noqa: B010
