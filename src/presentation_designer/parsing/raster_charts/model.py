"""Модели чтения диаграммы: устройство от модели (`ChartStructure`) и итог (`ChartReading`).

Модель описывает, что нарисовано и что напечатано; числа по геометрии она не оценивает, кроме
точек, которые не видны вовсе (`guess`, последний резерв). Итог хранит для каждой точки
происхождение значения, чтобы диаграмма честно помечалась «восстановлено по картинке».
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal["column", "bar", "line", "area", "combo", "pie", "doughnut"]
SeriesType = Literal["column", "bar", "line", "area"]
Basis = Literal["label", "measured", "inferred", "model"]
Legend = Literal["bottom", "top", "right", "left", "none"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SeriesInfo(_Strict):
    name: str | None = Field(default=None, max_length=120)
    color: str = Field(description="примерный цвет ряда #RRGGBB")
    type: SeriesType
    axis: Literal["primary", "secondary"] = "primary"
    smooth: bool = False
    labels: list[str | None] = Field(default_factory=list, max_length=60)
    guess: list[float | None] = Field(default_factory=list, max_length=60)


class AxisInfo(_Strict):
    ticks: list[str] = Field(default_factory=list, max_length=30)


class ChartStructure(_Strict):
    status: Literal["chart", "not_chart", "several_charts", "unsupported"]
    reason: str = Field(default="", max_length=600)
    kind: Kind | None = None
    stacked: bool = False
    categories: list[str] = Field(default_factory=list, max_length=60)
    category_colors: list[str] = Field(default_factory=list, max_length=60)
    slice_labels: list[str | None] = Field(default_factory=list, max_length=60)
    series: list[SeriesInfo] = Field(default_factory=list, max_length=8)
    primary_axis: AxisInfo | None = None
    secondary_axis: AxisInfo | None = None
    legend: Legend = "none"
    unit: str | None = Field(default=None, max_length=40)
    title: str | None = Field(default=None, max_length=200)


class Point(_Strict):
    value: float
    basis: Basis
    label: str | None = None


class ReadSeries(_Strict):
    name: str
    color: str
    type: SeriesType | None = Field(default=None, description="нет у круга и кольца")
    axis: Literal["primary", "secondary"] = "primary"
    smooth: bool = False
    points: list[Point]
    point_colors: list[str] = Field(
        default_factory=list, description="цвет каждой точки, когда столбцы ряда выделены цветом"
    )


class Scale(_Strict):
    minimum: float
    maximum: float
    major_unit: float | None = None
    number_format: str = "General"


class Geometry(_Strict):
    """Размеры на картинке в пикселях: по ним нативная диаграмма повторяет пропорции."""

    plot: tuple[int, int, int, int] | None = Field(
        default=None, description="область данных: left, top, right, bottom (кольцо — его рамка)"
    )
    stroke: list[float | None] = Field(default_factory=list, description="толщина линии ряда")
    bar: float | None = Field(default=None, description="толщина столбца или полосы")
    pitch: float | None = Field(default=None, description="шаг категорий")
    font: float | None = Field(default=None, description="кегль подписей (шкала, легенда), px")
    value_max: float | None = Field(
        default=None, description="максимум оси значений, когда шкалы на картинке нет"
    )


class ChartReading(_Strict):
    """Диаграмма, прочитанная с картинки; порядок рядов — порядок отрисовки (задний первым)."""

    kind: Kind
    stacked: bool = False
    categories: list[str]
    series: list[ReadSeries]
    slice_colors: list[str] = Field(default_factory=list)
    primary: Scale | None = None
    secondary: Scale | None = None
    hole: float | None = Field(default=None, description="доля внутреннего радиуса кольца")
    first_angle: float | None = Field(default=None, description="угол первого сегмента, градусы")
    legend: Legend = "none"
    data_labels: bool = False
    label_format: str = "General"
    unit: str | None = None
    title: str | None = None
    width: int
    height: int
    geometry: Geometry = Field(default_factory=Geometry)
    notes: list[str] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {"label": 0, "measured": 0, "inferred": 0, "model": 0}
        for s in self.series:
            for p in s.points:
                out[p.basis] += 1
        return out

    @property
    def approximate(self) -> bool:
        c = self.counts()
        return bool(c["measured"] or c["inferred"] or c["model"])
