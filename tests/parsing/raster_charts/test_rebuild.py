"""Чтение диаграмм-картинок: измерение по пикселям на синтетике с известными значениями,
сведение с напечатанными подписями, предфильтр, подписи-числа, путь через модель."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from presentation_designer.parsing.raster_charts.labels import (
    comma_thousands,
    label_number,
    label_numbers,
    number_format,
)
from presentation_designer.parsing.raster_charts.model import (
    AxisInfo,
    ChartReading,
    ChartStructure,
    SeriesInfo,
)
from presentation_designer.parsing.raster_charts.pixels import chart_likeness, load
from presentation_designer.parsing.raster_charts.reading import STRUCTURE_SCHEMA
from presentation_designer.parsing.raster_charts.rebuild import (
    Outcome,
    image_sha,
    read_chart_images,
    read_with_structure,
)
from tests.parsing.raster_charts import synth

CATS = ["Ряд 1", "Ряд 2", "Ряд 3"]


def _read(data: bytes, st: ChartStructure) -> ChartReading:
    outcome = read_with_structure(load(data), st)
    assert outcome.status == "read", outcome.reason
    assert outcome.reading is not None
    return outcome.reading


def _values(reading: ChartReading, name: str) -> list[float]:
    series = next(s for s in reading.series if s.name == name)
    return [p.value for p in series.points]


def _close(got: list[float], want: list[float], tol: float) -> None:
    assert len(got) == len(want)
    for g, w in zip(got, want, strict=True):
        assert abs(g - w) <= tol, (got, want)


# ---------- круг и кольцо ----------


def test_doughnut_shares_hole_and_order() -> None:
    values = [7, 14, 21, 21, 29, 8]
    colors = [synth.BLUE, synth.PINK, synth.CYAN, synth.LIGHT, (255, 200, 0), synth.GRAY]
    st = ChartStructure(
        status="chart",
        kind="doughnut",
        categories=["A", "B", "C", "D", "E", "F"],
        category_colors=["#0070F0", "#F03080", "#80E0F0", "#C0D0E0", "#F0C010", "#A0A0A0"],
    )
    r = _read(synth.doughnut(values, colors), st)
    _close(_values(r, "Значения"), values, 0.6)
    assert r.hole is not None and abs(r.hole - 0.7) < 0.02
    assert r.first_angle is not None and r.first_angle < 1
    assert r.slice_colors[0] == "#0077FF"
    assert all(p.basis == "measured" for p in r.series[0].points)
    assert r.approximate


def test_pie_with_percent_labels_takes_labels_exactly() -> None:
    values = [50, 30, 20]
    st = ChartStructure(
        status="chart",
        kind="pie",
        categories=["A", "B", "C"],
        category_colors=["#0077FF", "#FF3885", "#7CEDF8"],
        slice_labels=["50%", "30%", None],
    )
    r = _read(synth.doughnut(values, [synth.BLUE, synth.PINK, synth.CYAN], hole=0), st)
    points = r.series[0].points
    # Одна доля без подписи — остаток до 100 %, а не замер.
    assert [p.basis for p in points] == ["label", "label", "inferred"]
    assert [p.value for p in points] == [50, 30, 20]
    assert r.hole == 0.0
    assert r.data_labels and r.label_format == '0"%"'


def test_slice_label_that_contradicts_the_share_rejects() -> None:
    st = ChartStructure(
        status="chart",
        kind="doughnut",
        categories=["A", "B"],
        category_colors=["#0077FF", "#FF3885"],
        slice_labels=["40%", None],
    )
    outcome = read_with_structure(load(synth.doughnut([22, 78], [synth.BLUE, synth.PINK])), st)
    assert outcome.status == "rejected"
    assert "расходится" in outcome.reason


# ---------- оси ----------


def test_clustered_columns_with_line_on_secondary_axis() -> None:
    cols = [[26, 43, 20], [53, 70, 18]]
    line = [800, 2400, 1500]
    data, ticks, right = synth.columns(
        cols, [synth.BLUE, synth.CYAN], CATS, line=line, line_hi=3000, line_step=500
    )
    st = ChartStructure(
        status="chart",
        kind="combo",
        categories=CATS,
        series=[
            SeriesInfo(name="A", color="#1E88E5", type="column"),
            SeriesInfo(name="B", color="#80E8F0", type="column"),
            SeriesInfo(name="C", color="#E0508A", type="line", axis="secondary"),
        ],
        primary_axis=AxisInfo(ticks=ticks),
        secondary_axis=AxisInfo(ticks=right),
    )
    r = _read(data, st)
    _close(_values(r, "A"), cols[0], 1.0)
    _close(_values(r, "B"), cols[1], 1.0)
    _close(_values(r, "C"), line, 30)
    assert r.primary and (r.primary.minimum, r.primary.maximum, r.primary.major_unit) == (
        0,
        100,
        20,
    )
    assert r.secondary and r.secondary.maximum == 3000 and r.secondary.number_format == "#,##0"


def test_column_label_that_contradicts_the_bar_rejects() -> None:
    data, ticks, _ = synth.columns([[26, 43, 20]], [synth.BLUE], CATS)
    st = ChartStructure(
        status="chart",
        kind="column",
        categories=CATS,
        series=[SeriesInfo(color="#0077FF", type="column", labels=["26", "60", None])],
        primary_axis=AxisInfo(ticks=ticks),
    )
    outcome = read_with_structure(load(data), st)
    assert outcome.status == "rejected" and "«60»" in outcome.reason


def test_bars_without_axis_take_labels_and_scale_the_rest_by_length() -> None:
    values = [79, 63, 43, 38, 10]
    names = ["Первый", "Второй", "Третий", "Четвёртый", "Пятый"]
    st = ChartStructure(
        status="chart",
        kind="bar",
        categories=names,
        series=[SeriesInfo(color="#1E88E5", type="bar", labels=["79", "63", "43", None, "10"])],
        primary_axis=AxisInfo(ticks=[]),
    )
    r = _read(synth.hbars(values, names), st)
    points = r.series[0].points
    assert [p.basis for p in points] == ["label", "label", "label", "measured", "label"]
    assert abs(points[3].value - 38) <= 0.5


COMPETITORS = ["VK", "Конкурент 1", "Конкурент 2", "Конкурент 3", "Конкурент 4"]


def test_highlighted_columns_are_found_by_every_color_of_the_series() -> None:
    # Модель называет один цвет ряда — выделенный; остальные столбцы светлые, их находит замер.
    st = ChartStructure(
        status="chart",
        kind="column",
        categories=COMPETITORS,
        series=[
            SeriesInfo(color="#007BFF", type="column", labels=["43%", "23%", "18%", "8%", "2%"])
        ],
        primary_axis=AxisInfo(ticks=[]),
    )
    r = _read(synth.highlighted([43, 23, 18, 8, 2], COMPETITORS), st)
    points = r.series[0].points
    assert [p.value for p in points] == [43, 23, 18, 8, 2]
    assert all(p.basis == "label" for p in points)
    light = "#{:02X}{:02X}{:02X}".format(*synth.LIGHT)
    assert r.series[0].point_colors == ["#0077FF", light, light, light, light]


def test_highlighted_bars_work_sideways_and_plain_bars_keep_one_color() -> None:
    values = [79, 63, 43, 38, 10]
    names = ["Первый", "Второй", "Третий", "Четвёртый", "Пятый"]
    colors = [synth.LIGHT, synth.LIGHT, synth.BLUE, synth.LIGHT, synth.LIGHT]
    st = ChartStructure(
        status="chart",
        kind="bar",
        categories=names,
        series=[SeriesInfo(color="#1E88E5", type="bar", labels=["79", "63", "43", "38", "10"])],
        primary_axis=AxisInfo(ticks=[]),
    )
    r = _read(synth.hbars(values, names, colors=colors), st)
    assert [p.value for p in r.series[0].points] == values
    assert r.series[0].point_colors[2] == "#0077FF"
    assert len(set(r.series[0].point_colors)) == 2
    plain = _read(synth.hbars(values, names), st)
    assert plain.series[0].point_colors == []


def test_lines_are_measured_at_category_centers() -> None:
    a = [1.5, 13.6, 9.5, 18.8, 27.3]
    b = [0.5, 16.7, 8.5, 24.6, 18.8]
    names = ["1/1/19", "2/1/19", "3/1/19", "4/1/19", "5/1/19"]
    data, ticks = synth.lines([a, b], [synth.BLUE, synth.PINK], names)
    st = ChartStructure(
        status="chart",
        kind="line",
        categories=names,
        series=[
            SeriesInfo(name="DAU", color="#1A73E8", type="line"),
            SeriesInfo(name="MAU", color="#E8457A", type="line"),
        ],
        primary_axis=AxisInfo(ticks=ticks),
    )
    r = _read(data, st)
    _close(_values(r, "DAU"), a, 0.35)
    _close(_values(r, "MAU"), b, 0.35)


def test_overlapping_areas_recover_hidden_points_and_drawing_order() -> None:
    back = [96, 58, 30]  # закрыта спереди в «Ряд 2» и «Ряд 3»
    front = [17, 70, 37]
    data, ticks = synth.areas([back, front], [synth.PINK, synth.BLUE], CATS)
    st = ChartStructure(
        status="chart",
        kind="area",
        categories=CATS,
        series=[
            SeriesInfo(name="Перед", color="#0077FF", type="area"),
            SeriesInfo(name="Зад", color="#FF3885", type="area"),
        ],
        primary_axis=AxisInfo(ticks=ticks),
    )
    r = _read(data, st)
    assert [s.name for s in r.series] == ["Зад", "Перед"]  # задняя первой
    _close(_values(r, "Перед"), front, 1.0)
    hidden = r.series[0].points
    assert hidden[0].basis == "measured" and abs(hidden[0].value - 96) <= 1
    # «Ряд 2»: ребро видно на участке от «Ряд 1» — продолжение даёт закрытое значение.
    assert hidden[1].basis == "inferred" and abs(hidden[1].value - 58) <= 2
    # «Ряд 3»: не видно совсем — не выше закрывающей области.
    assert hidden[2].basis == "inferred" and hidden[2].value <= 37 + 1


def test_not_a_chart_is_reported_without_measuring() -> None:
    st = ChartStructure(status="not_chart", reason="фотография")
    outcome = read_with_structure(load(synth.photo_like()), st)
    assert outcome.status == "not_chart" and outcome.reason == "фотография"


# ---------- предфильтр, подписи, схема ----------


def test_prefilter_accepts_charts_and_rejects_photos_and_tiny_images() -> None:
    assert chart_likeness(load(synth.doughnut([30, 70], [synth.BLUE, synth.LIGHT]))).ok
    assert not chart_likeness(load(synth.photo_like())).ok
    tiny = synth.png(load(synth.doughnut([30, 70], [synth.BLUE, synth.LIGHT])).resize((120, 90)))
    assert not chart_likeness(load(tiny)).ok


@pytest.mark.parametrize(
    ("text", "value", "unit", "decimals"),
    [
        ("7%", 7, "%", 0),
        ("12,5 млн", 12.5, "млн", 1),
        ("1,5", 1.5, None, 1),
        ("0", 0, None, 0),
        ("20 000", 20000, None, 0),
        ("$1.25", 1.25, "$", 2),
    ],
)
def test_label_number(text: str, value: float, unit: str | None, decimals: int) -> None:
    got = label_number(text)
    assert got is not None
    assert (got.value, got.unit, got.decimals) == (value, unit, decimals)


def test_comma_is_a_thousands_separator_when_the_set_says_so() -> None:
    ticks = ["0", "500,000", "1,000,000"]
    assert comma_thousands(ticks)
    assert [n.value for n in label_numbers(ticks) if n] == [0, 500000, 1000000]
    assert comma_thousands(["3,000", "2,500", "500", "0"])
    assert not comma_thousands(["0,5", "1,5"])
    assert not comma_thousands(["12,5 млн", "1,250"])
    assert number_format([n for n in label_numbers(ticks) if n]) == "#,##0"


def test_structure_schema_is_strict() -> None:
    def objects(node: Any) -> list[dict[str, Any]]:
        found = []
        if isinstance(node, dict):
            if "properties" in node:
                found.append(node)
            for v in node.values():
                found += objects(v)
        elif isinstance(node, list):
            for v in node:
                found += objects(v)
        return found

    for obj in objects(STRUCTURE_SCHEMA):
        assert set(obj["required"]) == set(obj["properties"])
        assert obj["additionalProperties"] is False
        assert all("default" not in p for p in obj["properties"].values())


# ---------- путь через модель ----------


class _FakeClient:
    """Отвечает заранее заданным устройством; считает вызовы."""

    def __init__(self, structure: ChartStructure) -> None:
        self.structure = structure
        self.calls = 0

    async def complete(self, req: Any) -> Any:
        self.calls += 1
        assert req.role == "vlm"
        assert req.messages[-1].images, "картинка уходит в запрос"
        return SimpleNamespace(parsed=self.structure.model_dump())


def test_batch_reads_charts_and_skips_photos_without_calling_the_model() -> None:
    chart = synth.doughnut([30, 70], [synth.BLUE, synth.LIGHT])
    photo = synth.photo_like()
    st = ChartStructure(
        status="chart",
        kind="doughnut",
        categories=["Значение", "Остаток"],
        category_colors=["#0077FF", "#C0D0E0"],
    )
    client = _FakeClient(st)
    out = read_chart_images(
        {image_sha(chart): chart, image_sha(photo): photo}, client, budget_s=30, max_images=5
    )
    assert client.calls == 1
    got: Outcome = out[image_sha(chart)]
    assert got.status == "read" and got.reading is not None
    _close([p.value for p in got.reading.series[0].points], [30, 70], 0.6)
    assert out[image_sha(photo)].status == "skipped"
