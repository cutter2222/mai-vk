"""Диаграммы-картинки шаблона VK Education (слайды 45–50): все проходят предфильтр и меряются
при устройстве, какое описывает модель; значения сверяются с числами на самих слайдах."""

from __future__ import annotations

import pathlib

import pytest

from presentation_designer.cli.charts import collect
from presentation_designer.parsing.raster_charts.model import (
    AxisInfo,
    ChartReading,
    ChartStructure,
    SeriesInfo,
)
from presentation_designer.parsing.raster_charts.pixels import chart_likeness, load
from presentation_designer.parsing.raster_charts.rebuild import read_with_structure

pytestmark = pytest.mark.organizer_data

VKEDU = "Шаблон презентации VK Education.pptx"


@pytest.fixture(scope="module")
def pictures(organizer_dir: pathlib.Path) -> dict[tuple[int, int], bytes]:
    items = collect([str(organizer_dir / VKEDU)], set(range(45, 51)))
    out: dict[tuple[int, int], bytes] = {}
    for it in items:
        k = sum(1 for key in out if key[0] == it["slide"])
        out[(it["slide"], k)] = it["data"]
    return out


def _read(data: bytes, st: ChartStructure) -> ChartReading:
    outcome = read_with_structure(load(data), st)
    assert outcome.status == "read", outcome.reason
    assert outcome.reading is not None
    return outcome.reading


def test_all_twelve_chart_pictures_pass_the_prefilter(
    pictures: dict[tuple[int, int], bytes],
) -> None:
    assert len(pictures) == 12
    assert all(chart_likeness(load(data)).ok for data in pictures.values())


def test_progress_rings_match_the_numbers_on_the_slide(
    pictures: dict[tuple[int, int], bytes],
) -> None:
    st = ChartStructure(
        status="chart",
        kind="doughnut",
        categories=["Значение", "Остаток"],
        category_colors=["#0077FF", "#B4D2E8"],
    )
    shares = sorted(_read(pictures[(46, k)], st).series[0].points[0].value for k in range(5))
    # На слайде: 22 % крупно и 42, 32, 22, 12 % в малых кольцах.
    for got, want in zip(shares, [12, 22, 22, 32, 42], strict=True):
        assert abs(got - want) <= 0.5


def test_city_doughnut_labels_agree_with_the_measured_shares(
    pictures: dict[tuple[int, int], bytes],
) -> None:
    st = ChartStructure(
        status="chart",
        kind="doughnut",
        categories=["Москва", "Спб", "Екб", "Тула", "Тверь", "Новгород"],
        category_colors=["#0070F0", "#F03080", "#70E0F0", "#E0ECF0", "#D8F0F8", "#F8E0EC"],
        slice_labels=["7%", "14%", "21%", "21%", None, None],
        legend="bottom",
    )
    r = _read(pictures[(45, 0)], st)
    points = r.series[0].points
    assert [p.value for p in points[:4]] == [7, 14, 21, 21]
    assert abs(points[4].value - 29) <= 1 and abs(points[5].value - 7) <= 0.5


def test_columns_with_smooth_line(pictures: dict[tuple[int, int], bytes]) -> None:
    ticks = ["120", "100", "80", "60", "40", "20", "0"]
    st = ChartStructure(
        status="chart",
        kind="combo",
        categories=["Ряд 1", "Ряд 2", "Ряд 3"],
        series=[
            SeriesInfo(color="#0077FF", type="column"),
            SeriesInfo(color="#FF3885", type="column"),
            SeriesInfo(color="#EBF3F9", type="column"),
            SeriesInfo(color="#7CEDF8", type="line", smooth=True),
        ],
        primary_axis=AxisInfo(ticks=ticks),
    )
    r = _read(pictures[(47, 0)], st)
    got = [[round(p.value) for p in s.points] for s in r.series]
    assert got == [[26, 43, 20], [53, 70, 18], [96, 58, 19], [17, 55, 30]]


def test_labelled_horizontal_bars(pictures: dict[tuple[int, int], bytes]) -> None:
    labels = ["79", "79", "63", "43", "38", "36", "36", "29"]
    labels += ["25", "24", "23", "16", "11", "11", "11", "10"]
    st = ChartStructure(
        status="chart",
        kind="bar",
        categories=[f"Категория {k + 1}" for k in range(16)],
        series=[SeriesInfo(color="#1E88E5", type="bar", labels=labels)],
    )
    r = _read(pictures[(50, 0)], st)
    assert [p.value for p in r.series[0].points] == [float(x) for x in labels]
    assert all(p.basis == "label" for p in r.series[0].points)
