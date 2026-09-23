"""Картинка → `ChartReading`: предфильтр, устройство от модели, измерение, сведение с подписями.

Сведение: напечатанная подпись берётся точно (`label`), остальное — мера по пикселям
(`measured`), точка, закрытая другими рядами, — продолжение видимого ребра или соседей
(`inferred`), совсем невидимая — оценка модели (`model`). Если мера расходится с подписью
больше допуска, чтение отклоняется: одно из двух неверно, и картинка остаётся картинкой.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from PIL import Image

from presentation_designer.llm.types import Deadline
from presentation_designer.parsing.raster_charts.labels import (
    LabelNumber,
    comma_thousands,
    label_number,
    number_format,
)
from presentation_designer.parsing.raster_charts.measure import (
    Measured,
    MeasureError,
    Scale,
    measure_axes,
    measure_round,
)
from presentation_designer.parsing.raster_charts.model import (
    ChartReading,
    ChartStructure,
    Geometry,
    Point,
    ReadSeries,
)
from presentation_designer.parsing.raster_charts.model import Scale as ScaleOut
from presentation_designer.parsing.raster_charts.pixels import chart_likeness, hex_of, load
from presentation_designer.parsing.raster_charts.reading import read_structure

Status = Literal["read", "rejected", "not_chart", "skipped"]


@dataclass
class Outcome:
    status: Status
    reason: str = ""
    reading: ChartReading | None = None
    structure: ChartStructure | None = None
    measured: Measured | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "reading": self.reading.model_dump() if self.reading else None,
            "structure": self.structure.model_dump() if self.structure else None,
            "issues": self.measured.issues if self.measured else [],
        }


def image_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------- сведение ----------


def _labels(texts: list[str | None], n: int, *, thousands: bool) -> list[LabelNumber | None]:
    padded = (list(texts) + [None] * n)[:n]
    return [label_number(t, comma_thousands=thousands) for t in padded]


def _scale_out(scale: Scale | None, ticks: list[str], thousands: bool) -> ScaleOut | None:
    if scale is None:
        return None
    fmt = number_format([x for t in ticks if (x := label_number(t, comma_thousands=thousands))])
    return ScaleOut(minimum=scale.lo, maximum=scale.hi, major_unit=scale.major, number_format=fmt)


def reconcile(image: Image.Image, st: ChartStructure, m: Measured) -> ChartReading:
    if st.kind in ("pie", "doughnut"):
        return _reconcile_round(image, st, m)
    return _reconcile_axes(image, st, m)


def _reconcile_round(image: Image.Image, st: ChartStructure, m: Measured) -> ChartReading:
    n = len(m.colors)
    names = (
        list(st.categories) if len(st.categories) == n else [f"Сегмент {k + 1}" for k in range(n)]
    )
    thousands = comma_thousands(st.slice_labels)
    labels = _labels(st.slice_labels, n, thousands=thousands)
    shares = m.values[0]
    points: list[Point] = []
    given = [lb for lb in labels if lb is not None]
    percent = bool(given) and all(lb.unit == "%" for lb in given)
    scale_k: float | None = None
    if given and not percent:
        ks = [lb.value / s for lb, s in zip(labels, shares, strict=True) if lb and s]
        if not ks or max(ks) > 1.03 * min(ks):
            raise MeasureError("подписи сегментов не пропорциональны их долям")
        scale_k = sum(ks) / len(ks)
    for k in range(n):
        share, lb = shares[k], labels[k]
        if lb is not None:
            if share is not None:
                expected = share if percent else share * (scale_k or 1)
                tol = 1.6 if percent else 0.03 * abs(lb.value) + 0.5
                if abs(expected - lb.value) > tol:
                    raise MeasureError(
                        f"подпись «{st.slice_labels[k]}» у «{names[k]}» расходится с долей {share}%"
                    )
            points.append(Point(value=lb.value, basis="label", label=st.slice_labels[k]))
        elif share is not None:
            value = share if scale_k is None else share * scale_k
            points.append(Point(value=round(value, 4), basis="measured"))
        else:
            raise MeasureError(f"сегмент «{names[k]}» не найден")
    return ChartReading(
        kind=st.kind or "doughnut",
        categories=names,
        series=[ReadSeries(name=st.title or "Значения", color=hex_of(m.colors[0]), points=points)],
        slice_colors=[hex_of(c) for c in m.colors],
        hole=m.hole if st.kind == "doughnut" else 0.0,
        first_angle=m.first_angle,
        legend=st.legend,
        data_labels=bool(given),
        label_format=number_format(given),
        unit=st.unit or ("%" if percent else None),
        title=st.title,
        width=image.width,
        height=image.height,
        geometry=Geometry(plot=m.plot, font=m.font),
        notes=list(m.issues),
    )


def _reconcile_axes(image: Image.Image, st: ChartStructure, m: Measured) -> ChartReading:
    ncat = len(st.categories)
    all_texts: list[str | None] = [t for s in st.series for t in s.labels]
    for axis in (st.primary_axis, st.secondary_axis):
        if axis:
            all_texts += axis.ticks
    thousands = comma_thousands(all_texts)
    out: list[ReadSeries] = []
    given_all: list[LabelNumber] = []
    value_max: float | None = None
    for i, s in enumerate(st.series):
        scale = m.secondary if s.axis == "secondary" else m.primary
        labels = _labels(s.labels, ncat, thousands=thousands)
        texts = (list(s.labels) + [None] * ncat)[:ncat]
        guesses = (list(s.guess) + [None] * ncat)[:ncat]
        given = [lb for lb in labels if lb is not None]
        given_all += given
        # Без шкалы значения столбцов — по пропорции длины к подписанным.
        k_len: float | None = None
        if scale is None:
            pairs = [
                (pl, ln)
                for pl, ln in zip(labels, m.lengths[i], strict=True)
                if pl is not None and ln
            ]
            if pairs:
                # Масштаб — по сумме: у короткого столбца пиксель и округление подписи («2%» —
                # это от 1,5 до 2,5) дают десятки процентов ошибки, у длинного — доли.
                k_len = sum(pl.value for pl, _ in pairs) / sum(ln for _, ln in pairs)
                for pl, ln in pairs:
                    tol = max(0.04 * abs(pl.value), 0.51 * 10**-pl.decimals, 1.5 * abs(k_len))
                    if abs(k_len * ln - pl.value) > tol:
                        raise MeasureError(
                            f"подписи ряда {i + 1} не пропорциональны длинам столбцов"
                        )
                if value_max is None and m.plot is not None and s.axis == "primary":
                    # Шкалы нет: ось значений кончается там же, где область данных на картинке.
                    left, top, right, bottom = m.plot
                    extent = right - left + 1 if st.kind == "bar" else bottom - top + 1
                    value_max = round(k_len * extent, 4)
        points: list[Point] = []
        for c in range(ncat):
            lb, measured, basis = labels[c], m.values[i][c], m.basis[i][c]
            if measured is None and k_len is not None and m.lengths[i][c]:
                measured, basis = k_len * (m.lengths[i][c] or 0), "measured"
            if lb is not None:
                if measured is not None and basis == "measured":
                    span = scale.span if scale else max(abs(lb.value), 1.0)
                    tol = max(0.025 * span, 0.51 * 10**-lb.decimals)
                    if abs(measured - lb.value) > tol:
                        raise MeasureError(
                            f"подпись «{texts[c]}» ряда {i + 1} расходится с мерой {measured:.4g}"
                        )
                points.append(Point(value=lb.value, basis="label", label=texts[c]))
            elif measured is not None:
                points.append(Point(value=_round(measured, scale), basis=basis or "measured"))
            elif (cover := _cover(st, m, i, c)) is not None:
                # Точка области целиком закрыта рядами спереди: она не выше их, и значение,
                # равное закрывающему, даёт ту же картинку.
                guess = guesses[c]
                value = min(float(guess), cover) if guess is not None else cover
                points.append(Point(value=_round(value, scale), basis="inferred"))
            elif guesses[c] is not None:
                points.append(Point(value=float(guesses[c] or 0), basis="model"))
            elif scale is None and k_len is None:
                side = "вспомогательной" if s.axis == "secondary" else "основной"
                raise MeasureError(f"шкала {side} оси не откалибрована по подписям делений")
            else:
                where = st.categories[c] if c < ncat else str(c + 1)
                raise MeasureError(f"точка ряда {i + 1} в «{where}» не видна и не оценена")
        out.append(
            ReadSeries(
                name=s.name or f"Ряд {i + 1}",
                color=hex_of(m.colors[i]),
                type=s.type,
                axis=s.axis,
                smooth=s.smooth,
                points=points,
                point_colors=[hex_of(c) for c in m.point_colors] if i == 0 else [],
            )
        )
    if value_max is not None:
        # Средний масштаб по подписям чуть занижает конец оси: самая длинная полоса не
        # должна упираться в край и обрезаться.
        value_max = max([value_max, *(p.value for r in out for p in r.points)])
    stroke = list(m.stroke) or [None] * len(out)
    if m.order:
        out = [out[k] for k in m.order]
        stroke = [stroke[k] for k in m.order]
    primary_ticks = st.primary_axis.ticks if st.primary_axis else []
    secondary_ticks = st.secondary_axis.ticks if st.secondary_axis else []
    units = {lb.unit for lb in given_all if lb.unit}
    return ChartReading(
        kind=st.kind or "column",
        stacked=st.stacked,
        categories=list(st.categories),
        series=out,
        primary=_scale_out(m.primary, primary_ticks, thousands),
        secondary=_scale_out(m.secondary, secondary_ticks, thousands),
        legend=st.legend,
        data_labels=bool(given_all),
        label_format=number_format(given_all),
        unit=st.unit or (units.pop() if len(units) == 1 else None),
        title=st.title,
        width=image.width,
        height=image.height,
        geometry=Geometry(
            plot=m.plot,
            stroke=stroke,
            bar=m.bar,
            pitch=m.pitch,
            font=m.font,
            value_max=value_max,
        ),
        notes=list(m.issues),
    )


def _cover(st: ChartStructure, m: Measured, i: int, c: int) -> float | None:
    """Верх областей, нарисованных перед областью `i` в категории `c`, если она за ними."""
    if st.series[i].type != "area" or not m.order or i not in m.order:
        return None
    front = m.order[m.order.index(i) + 1 :]
    tops = [v for k in front if st.series[k].type == "area" and (v := m.values[k][c]) is not None]
    return max(tops) if tops else None


def _round(value: float, scale: Scale | None) -> float:
    """Мера точнее доли деления не бывает: округление до тысячной шага шкалы."""
    if scale and scale.major:
        q = scale.major / 1000
        return round(round(value / q) * q, 10)
    return round(value, 4)


# ---------- картинка целиком ----------


def read_with_structure(image: Image.Image, st: ChartStructure) -> Outcome:
    """Измерение и сведение при известном устройстве (модель не вызывается)."""
    if st.status != "chart":
        status: Status = "not_chart" if st.status == "not_chart" else "rejected"
        return Outcome(status, st.reason or st.status, structure=st)
    if st.kind is None:
        return Outcome("rejected", "вид диаграммы не определён", structure=st)
    try:
        measured = (
            measure_round(image, st) if st.kind in ("pie", "doughnut") else measure_axes(image, st)
        )
    except MeasureError as e:
        return Outcome("rejected", str(e), structure=st)
    try:
        reading = reconcile(image, st, measured)
    except MeasureError as e:
        return Outcome("rejected", str(e), structure=st, measured=measured)
    return Outcome("read", "", reading=reading, structure=st, measured=measured)


def _candidate(data: bytes) -> Image.Image | Outcome:
    """Картинка для чтения или отказ без модели: не открылась либо не похожа на диаграмму."""
    try:
        image = load(data)
    except Exception as e:
        return Outcome("skipped", f"картинка не открылась: {e}")
    likeness = chart_likeness(image)
    if not likeness.ok:
        return Outcome("skipped", likeness.reason)
    return image


async def _read(image: Image.Image, client: Any, deadline: Deadline) -> Outcome:
    try:
        st = await read_structure(client, image, deadline=deadline)
    except Exception as e:
        return Outcome("rejected", f"модель не ответила: {(str(e) or type(e).__name__)[:300]}")
    return read_with_structure(image, st)


async def read_chart_image(data: bytes, client: Any, *, deadline: Deadline) -> Outcome:
    prepared = _candidate(data)
    if isinstance(prepared, Outcome):
        return prepared
    return await _read(prepared, client, deadline)


def read_chart_images(
    images: dict[str, bytes],
    client: Any,
    *,
    budget_s: float,
    max_images: int,
    concurrency: int = 4,
    on_read: Callable[[int, int], None] | None = None,
) -> dict[str, Outcome]:
    """Картинки по sha256, не больше `max_images` запросов к модели и `budget_s` секунд всего.
    `on_read(готово, всего)` вызывается после каждой картинки — для хода в интерфейсе."""
    deadline = Deadline.after(budget_s)
    gate = asyncio.Semaphore(concurrency)
    results: dict[str, Outcome] = {}
    calls = 0

    async def one(sha: str, data: bytes) -> None:
        nonlocal calls
        prepared = _candidate(data)
        if isinstance(prepared, Outcome):
            results[sha] = prepared
            return
        if calls >= max_images:
            results[sha] = Outcome("skipped", "лимит чтения диаграмм")
            return
        calls += 1
        async with gate:
            if deadline.expired():
                results[sha] = Outcome("skipped", "время на чтение диаграмм вышло")
                return
            results[sha] = await _read(prepared, client, deadline)

    async def tracked(sha: str, data: bytes) -> None:
        await one(sha, data)
        if on_read is not None:
            on_read(len(results), len(images))

    async def run() -> None:
        await asyncio.gather(*(tracked(sha, data) for sha, data in images.items()))

    asyncio.run(run())
    return results
