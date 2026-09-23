"""Измерение значений по пикселям, когда устройство диаграммы уже известно.

Цвета рядов уточняются по палитре картинки, шкала калибруется по строкам подписей делений
(берётся равномерная последовательность нужной длины — легенда и подписи категорий рядом с осью
в неё не попадают). Круг и кольцо меряются развёрткой по углу, столбцы и полосы — по краям,
линии — в центрах категорий, области — по верхнему ребру; точка области, закрытая рядом
спереди, восстанавливается продолжением видимого участка ребра.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import reduce
from statistics import median

from PIL import Image, ImageChops, ImageFilter

from presentation_designer.parsing.raster_charts.labels import label_numbers
from presentation_designer.parsing.raster_charts.model import Basis, ChartStructure, SeriesInfo
from presentation_designer.parsing.raster_charts.pixels import Box, Rgb, ink_bands, palette, rgb_of

ROUND_STEPS = 1440


class MeasureError(ValueError):
    """Картинку нельзя измерить при таком устройстве: причина уходит в отчёт, картинка остаётся."""


@dataclass
class Scale:
    a: float
    b: float
    lo: float
    hi: float
    major: float | None

    def value(self, pos: float) -> float:
        return self.a * pos + self.b

    def pos(self, value: float) -> float:
        return (value - self.b) / self.a

    @property
    def span(self) -> float:
        return self.hi - self.lo


@dataclass
class Mark:
    kind: str  # tick | point | bar | hidden | ring
    x: float
    y: float
    text: str = ""
    x2: float = 0.0
    y2: float = 0.0


@dataclass
class Measured:
    values: list[list[float | None]]
    basis: list[list[Basis | None]]
    lengths: list[list[float | None]]
    colors: list[Rgb]
    primary: Scale | None = None
    secondary: Scale | None = None
    hole: float | None = None
    first_angle: float | None = None
    order: list[int] | None = None
    marks: list[Mark] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


# ---------- цвета и маски ----------


def cheb(a: Rgb, b: Rgb) -> int:
    return max(abs(x - y) for x, y in zip(a, b, strict=True))


def resolve_colors(image: Image.Image, wanted: list[str]) -> list[Rgb]:
    """Точные цвета рядов из палитры картинки: жадно по возрастанию «цены» сопоставления.

    Цена — расстояние до примерного цвета модели плюс штраф за редкость: смесь сглаживания
    на краях столбцов бывает ближе к неточному цвету модели, чем сам цвет ряда, но её в
    десятки раз меньше.
    """
    pal = palette(image)
    top = max((share for _, share in pal), default=1.0)
    targets: list[Rgb] = []
    for value in wanted:
        rgb = rgb_of(value)
        if rgb is None:
            raise MeasureError(f"цвет {value!r} не разобран")
        targets.append(rgb)
    pairs = []
    for i, t in enumerate(targets):
        for k, (p, share) in enumerate(pal):
            d = sum((x - y) ** 2 for x, y in zip(t, p, strict=True)) ** 0.5
            if d <= 110:
                pairs.append((d + 25 * math.log10(top / share), i, k))
    pairs.sort()
    chosen: dict[int, int] = {}
    used: set[int] = set()
    for _, i, k in pairs:
        if i in chosen or k in used:
            continue
        chosen[i] = k
        used.add(k)
    missing = [wanted[i] for i in range(len(targets)) if i not in chosen]
    if missing:
        raise MeasureError(f"цвет ряда не найден на картинке: {', '.join(missing)}")
    return [pal[chosen[i]][0] for i in range(len(targets))]


def color_tolerance(color: Rgb, others: list[Rgb]) -> int:
    near = [cheb(color, o) for o in [*others, (255, 255, 255)] if o != color]
    return max(5, min([36, *(d // 2 for d in near)]))


def color_mask(image: Image.Image, color: Rgb, tol: int, *, clean: bool = False) -> Image.Image:
    r, g, b = ImageChops.difference(image, Image.new("RGB", image.size, color)).split()
    mask = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v <= tol else 0)
    if clean:
        mask = mask.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    return mask


def masks_for(image: Image.Image, colors: list[Rgb], clean: list[bool]) -> list[Image.Image]:
    return [
        color_mask(image, c, color_tolerance(c, colors), clean=cl)
        for c, cl in zip(colors, clean, strict=True)
    ]


def runs(flags: list[int] | tuple[int, ...], gap: int = 0) -> list[tuple[int, int]]:
    """Отрезки подряд идущих ненулевых флагов (включительно); промежутки до `gap` склеиваются."""
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, f in enumerate([*flags, 0]):
        if f and start is None:
            start = i
        elif not f and start is not None:
            if out and start - out[-1][1] - 1 <= gap:
                out[-1] = (out[-1][0], i - 1)
            else:
                out.append((start, i - 1))
            start = None
    return out


def bbox(mask: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    part = mask.crop(box).getbbox()
    if part is None:
        return None
    return (part[0] + box[0], part[1] + box[1], part[2] + box[0], part[3] + box[1])


# ---------- шкала ----------


def _even_runs(centers: list[float], k: int) -> list[list[float]]:
    found = []
    for i in range(len(centers) - k + 1):
        seq = centers[i : i + k]
        diffs = [seq[j + 1] - seq[j] for j in range(k - 1)]
        step = median(diffs)
        if step >= 4 and all(abs(d - step) <= max(2.0, 0.08 * step) for d in diffs):
            found.append(seq)
    return found


def _merge(bands: list[tuple[float, int, int]], gap: int) -> list[tuple[float, int, int]]:
    out: list[tuple[float, int, int]] = []
    for _, a, b in bands:
        if out and a - out[-1][2] - 1 <= gap:
            a = out[-1][1]
            out[-1] = ((a + b) / 2, a, b)
        else:
            out.append(((a + b) / 2, a, b))
    return out


def calibrate(
    image: Image.Image,
    texts: list[str],
    band: Box,
    *,
    vertical: bool,
    near: str,
    anchor: float,
) -> Scale | None:
    """Шкала по подписям делений в полосе рядом с областью построения.

    `near` — с какой стороны полосы область построения («right» для левой оси, «left» для
    правой, «top» для оси под графиком); `anchor` — координата нуля или нижнего края данных,
    к которой должна прилегать последовательность делений.
    """
    numbers = label_numbers(texts)
    if len(numbers) < 2 or any(n is None for n in numbers) or band.width <= 0 or band.height <= 0:
        return None
    values = sorted({n.value for n in numbers if n is not None})
    if len(values) < 2:
        return None
    if vertical:
        groups = _merge(ink_bands(image, band, along="cols"), 6)
        groups.sort(key=lambda t: -t[2] if near == "right" else t[1])
        subs = [Box(g[1], band.top, g[2] + 1, band.bottom) for g in groups]
    else:
        groups = sorted(_merge(ink_bands(image, band, along="rows"), 3), key=lambda t: t[1])
        subs = [Box(band.left, g[1], band.right, g[2] + 1) for g in groups]
    seq: list[float] | None = None
    for sub in subs:
        if vertical:
            centers = [c for c, _, _ in ink_bands(image, sub, along="rows")]
        else:
            centers = [c for c, _, _ in _merge(ink_bands(image, sub, along="cols"), 5)]
        candidates = _even_runs(centers, len(values))
        if candidates:
            pick = (lambda s: abs(s[-1] - anchor)) if vertical else (lambda s: abs(s[0] - anchor))
            seq = min(candidates, key=pick)
            break
    if seq is None:
        return None
    ordered = values[::-1] if vertical else values  # по вертикали сверху — наибольшее
    n = len(seq)
    mean_p, mean_v = sum(seq) / n, sum(ordered) / n
    cov = sum((p - mean_p) * (v - mean_v) for p, v in zip(seq, ordered, strict=True))
    var = sum((p - mean_p) ** 2 for p in seq)
    if var == 0:
        return None
    a = cov / var
    b = mean_v - a * mean_p
    step = (values[-1] - values[0]) / (len(values) - 1)
    if any(abs(a * p + b - v) > 0.2 * step for p, v in zip(seq, ordered, strict=True)):
        return None
    return Scale(a, b, values[0], values[-1], step)


# ---------- круг и кольцо ----------


def measure_round(image: Image.Image, st: ChartStructure) -> Measured:
    n = len(st.category_colors)
    if n < 2:
        raise MeasureError("у круговой диаграммы меньше двух цветов сегментов")
    colors = resolve_colors(image, st.category_colors)
    masks = masks_for(image, colors, [True] * n)
    union = reduce(ImageChops.lighter, masks)
    w, h = image.size
    _, rows_proj = union.getprojection()
    row_groups = runs(rows_proj, gap=3)
    if not row_groups:
        raise MeasureError("сегменты не найдены")
    top, bottom = max(row_groups, key=lambda r: r[1] - r[0])
    cols_proj, _ = union.crop((0, top, w, bottom + 1)).getprojection()
    left, right = max(runs(cols_proj, gap=3), key=lambda r: r[1] - r[0])
    cx, cy = (left + right) / 2, (top + bottom) / 2
    r_out = max(right - left, bottom - top) / 2
    px = union.load()
    assert px is not None
    inner = []
    for k in range(36):
        a = 2 * math.pi * k / 36
        r = 0.0
        while r < r_out:
            x, y = int(cx + r * math.sin(a)), int(cy - r * math.cos(a))
            if 0 <= x < w and 0 <= y < h and px[x, y]:
                break
            r += 1
        inner.append(r)
    r_in = median(inner)
    hole = r_in / r_out if r_in > 0.12 * r_out else 0.0
    radii = (
        [r_out * f for f in (0.55, 0.65, 0.75)]
        if hole == 0
        else [r_in + (r_out - r_in) * f for f in (0.3, 0.5, 0.7)]
    )
    src = image.load()
    assert src is not None
    tols = [color_tolerance(c, colors) for c in colors]
    labels: list[int] = []
    for i in range(ROUND_STEPS):
        a = 2 * math.pi * i / ROUND_STEPS
        votes: Counter[int] = Counter()
        for r in radii:
            x, y = int(cx + r * math.sin(a)), int(cy - r * math.cos(a))
            if not (0 <= x < w and 0 <= y < h):
                continue
            c = src[x, y]
            best = min(range(n), key=lambda k: cheb(c, colors[k]))  # type: ignore[arg-type]
            if cheb(c, colors[best]) <= tols[best]:  # type: ignore[arg-type]
                votes[best] += 1
        idx, cnt = votes.most_common(1)[0] if votes else (-1, 0)
        labels.append(idx if cnt >= 2 else -1)
    segments = sorted(_segments(labels), key=lambda seg: seg[1])  # по часовой от 12 часов
    if not segments:
        raise MeasureError("сегменты не найдены")
    first = min(range(len(segments)), key=lambda k: segments[k][0])
    segments = segments[first:] + segments[:first]
    issues: list[str] = []
    if [seg[0] for seg in segments] != sorted(seg[0] for seg in segments):
        # Цвета блёклых сегментов модель путает: PowerPoint рисует категории по часовой по
        # порядку, поэтому при совпадении числа сегментов верен порядок, а не оттенок.
        if len(segments) == n:
            segments = [(k, start, length) for k, (_, start, length) in enumerate(segments)]
            colors = [colors[idx] for idx, _, _ in sorted(_segments(labels), key=lambda t: t[1])]
            colors = colors[first:] + colors[:first]
            issues.append("сегменты сопоставлены с категориями по порядку, а не по цвету")
        else:
            issues.append("порядок сегментов по часовой не совпадает с порядком категорий")
    shares = [0.0] * n
    for idx, _, length in segments:
        shares[idx] += length / ROUND_STEPS * 100
    m = Measured(
        values=[[round(s, 1) if s > 0 else None for s in shares]],
        basis=[["measured" if s > 0 else None for s in shares]],
        lengths=[[None] * n],
        colors=colors,
        hole=round(hole, 3),
        first_angle=round(segments[0][1] * 360 / ROUND_STEPS, 1),
        issues=issues,
    )
    missing = [
        st.categories[i] if i < len(st.categories) else str(i + 1)
        for i, s in enumerate(shares)
        if s == 0
    ]
    if missing:
        m.issues.append(f"сегменты не найдены: {', '.join(missing)}")
    for idx, start, length in segments:
        a = 2 * math.pi * (start + length / 2) / ROUND_STEPS
        r = radii[1]
        m.marks.append(
            Mark("ring", cx + r * math.sin(a), cy - r * math.cos(a), f"{shares[idx]:.1f}%")
        )
        b = 2 * math.pi * start / ROUND_STEPS
        m.marks.append(
            Mark(
                "bar",
                cx + r_in * math.sin(b),
                cy - r_in * math.cos(b),
                "",
                cx + r_out * math.sin(b),
                cy - r_out * math.cos(b),
            )
        )
    return m


def _segments(labels: list[int]) -> list[tuple[int, int, int]]:
    """Сегменты (индекс цвета, начало, длина) по кругу: разрывы подписями и границами склеены."""
    total = len(labels)
    if all(v == -1 for v in labels):
        return []
    shift = next(i for i in range(total) if labels[i] != -1 and labels[i - 1] != labels[i])
    seq = labels[shift:] + labels[:shift]
    raw: list[list[int]] = []  # [idx, start, length]
    for i, v in enumerate(seq):
        if raw and raw[-1][0] == v:
            raw[-1][2] += 1
        else:
            raw.append([v, i, 1])
    # Пустые промежутки: между одинаковыми цветами — склеить, между разными — поделить пополам.
    solid: list[list[int]] = []
    pending = 0
    for idx, start, length in raw:
        if idx == -1:
            pending += length
            continue
        if solid and pending:
            if solid[-1][0] == idx:
                solid[-1][2] += pending
            else:
                solid[-1][2] += pending // 2
                start -= pending - pending // 2
                length += pending - pending // 2
        pending = 0
        if solid and solid[-1][0] == idx:
            solid[-1][2] += length
        else:
            solid.append([idx, start, length])
    if pending and solid:
        solid[-1][2] += pending
    if len(solid) > 1 and solid[0][0] == solid[-1][0]:
        solid[0][1] = solid[-1][1]
        solid[0][2] += solid.pop()[2]
    # Подпись внутри сегмента читается как соседние светлые цвета: остров короче трёх градусов
    # между участками одного цвета поглощается ими.
    island = ROUND_STEPS // 120
    changed = True
    while changed and len(solid) > 2:
        changed = False
        for k, seg in enumerate(solid):
            prev, nxt = solid[k - 1], solid[(k + 1) % len(solid)]
            if seg[2] < island and prev[0] == nxt[0] and prev is not nxt:
                prev[2] += seg[2] + nxt[2]
                solid.remove(seg)
                solid.remove(nxt)
                changed = True
                break
    # Короче полуградуса — сглаживание на стыке, отдаётся соседу.
    merged: list[list[int]] = []
    for seg in solid:
        if seg[2] < ROUND_STEPS / 720 and merged:
            merged[-1][2] += seg[2]
        elif merged and merged[-1][0] == seg[0]:
            merged[-1][2] += seg[2]
        else:
            merged.append(seg)
    return [(idx, (start + shift) % total, length) for idx, start, length in merged]


# ---------- диаграммы с осями ----------


@dataclass
class _Plot:
    top: int
    bottom: int
    left: int
    right: int


def _plot_area(masks: list[Image.Image], legend: str, horizontal: bool) -> _Plot:
    """Область данных без образцов легенды.

    Вдоль оси значений данные — один сплошной блок (все столбцы стоят на одной базе, линии
    непрерывны), образцы легенды лежат отдельно; берётся самый «тяжёлый» блок. Вдоль оси
    категорий между группами есть просветы; крайняя узкая группа со стороны легенды,
    отстоящая заметно дальше остальных, — образец легенды.
    """
    union = reduce(ImageChops.lighter, masks)
    w, h = union.size

    def heavy(proj: list[int], cut: tuple[int, int, int, int] | None) -> tuple[int, int]:
        groups = runs(proj, gap=6)
        if not groups:
            raise MeasureError("ряды не найдены на картинке")

        def weight(g: tuple[int, int]) -> int:
            box = (0, g[0], w, g[1] + 1) if not horizontal else (g[0], 0, g[1] + 1, h)
            return union.crop(box).histogram()[255]

        return max(groups, key=weight)

    cols_proj, rows_proj = union.getprojection()
    v0, v1 = heavy(list(cols_proj if horizontal else rows_proj), None)
    band = (0, v0, w, v1 + 1) if not horizontal else (v0, 0, v1 + 1, h)
    cols_in, rows_in = union.crop(band).getprojection()
    cats = runs(list(rows_in if horizontal else cols_in))
    if not cats:
        raise MeasureError("ряды не найдены на картинке")
    far_side = {"right": not horizontal, "bottom": horizontal}.get(legend)
    near_side = {"left": not horizontal, "top": horizontal}.get(legend)
    if len(cats) > 2:
        gaps = [cats[k + 1][0] - cats[k][1] for k in range(len(cats) - 1)]
        typical = median(gaps)
        if far_side and gaps[-1] > 2 * typical + 4 and cats[-1][1] - cats[-1][0] <= 24:
            cats = cats[:-1]
        if near_side and gaps[0] > 2 * typical + 4 and cats[0][1] - cats[0][0] <= 24:
            cats = cats[1:]
    c0, c1 = cats[0][0], cats[-1][1]
    if horizontal:
        return _Plot(c0, c1, v0, v1)
    return _Plot(v0, v1, c0, c1)


def measure_axes(image: Image.Image, st: ChartStructure) -> Measured:
    series = st.series
    if not series:
        raise MeasureError("нет рядов")
    if st.kind == "area" and st.stacked:
        raise MeasureError("накопительные области пока не поддерживаются")
    horizontal = st.kind == "bar"
    colors = resolve_colors(image, [s.color for s in series])
    shaped = [s.type in ("column", "bar", "area") for s in series]
    masks = masks_for(image, colors, shaped)
    plot = _plot_area(masks, st.legend, horizontal)
    w, h = image.size
    masks = [_keep(mk, (plot.left, plot.top, plot.right + 1, plot.bottom + 1)) for mk in masks]
    ncat = len(st.categories)
    if ncat == 0:
        raise MeasureError("категории не прочитаны")
    m = Measured(
        values=[[None] * ncat for _ in series],
        basis=[[None] * ncat for _ in series],
        lengths=[[None] * ncat for _ in series],
        colors=colors,
    )
    # Шкалы.
    base_pos = plot.bottom if not horizontal else plot.left
    if st.primary_axis and st.primary_axis.ticks:
        if horizontal:
            band = Box(0, plot.bottom + 2, w, h)
            m.primary = calibrate(
                image, st.primary_axis.ticks, band, vertical=False, near="top", anchor=base_pos
            )
        else:
            band = Box(0, 0, max(0, plot.left - 2), h)
            m.primary = calibrate(
                image, st.primary_axis.ticks, band, vertical=True, near="right", anchor=base_pos
            )
        if m.primary is None:
            m.issues.append("шкала основной оси не откалибрована по подписям")
    if st.secondary_axis and st.secondary_axis.ticks and not horizontal:
        band = Box(min(w, plot.right + 3), 0, w, h)
        m.secondary = calibrate(
            image, st.secondary_axis.ticks, band, vertical=True, near="left", anchor=base_pos
        )
        if m.secondary is None:
            m.issues.append("шкала вспомогательной оси не откалибрована по подписям")
    for kind, scale in (("tick", m.primary), ("tick2", m.secondary)):
        if scale is None:
            continue
        for v in _tick_values(scale):
            p = scale.pos(v)
            x, y = (0.0, p) if not horizontal else (p, float(h - 1))
            m.marks.append(Mark(kind, x, y, f"{v:,.10g}".replace(",", " ")))
    # Позиции категорий.
    bar_idx = [i for i, s in enumerate(series) if s.type in ("column", "bar")]
    centers: list[float]
    groups: list[tuple[int, int]] = []
    if bar_idx:
        groups = _category_groups(masks, bar_idx, ncat, horizontal)
        centers = [(a + b) / 2 for a, b in groups]
    else:
        lo_hi = [_extent(masks[i], horizontal) for i in range(len(series))]
        present = [e for e in lo_hi if e is not None]
        if not present:
            raise MeasureError("ряды не найдены на картинке")
        first, last = min(e[0] for e in present), max(e[1] for e in present)
        centers = (
            [first + (last - first) * k / (ncat - 1) for k in range(ncat)]
            if ncat > 1
            else [(first + last) / 2]
        )
    # Значения.
    for i, s in enumerate(series):
        scale = m.secondary if s.axis == "secondary" else m.primary
        if s.type in ("column", "bar"):
            _measure_bars(m, i, masks[i], groups, scale, horizontal, st.stacked)
        elif s.type == "line":
            _measure_line(m, i, masks[i], centers, scale)
        else:
            _measure_area(m, i, masks[i], centers, scale, (plot.left, plot.right))
    if any(s.type == "area" for s in series):
        m.order = _area_order(m, series)
    return m


def _keep(mask: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    out = Image.new("L", mask.size, 0)
    out.paste(mask.crop(box), box[:2])
    return out


def _tick_values(scale: Scale) -> list[float]:
    if not scale.major:
        return [scale.lo, scale.hi]
    n = round(scale.span / scale.major)
    return [scale.lo + k * scale.major for k in range(n + 1)]


def _extent(mask: Image.Image, horizontal: bool) -> tuple[int, int] | None:
    box = mask.getbbox()
    if box is None:
        return None
    return (box[1], box[3] - 1) if horizontal else (box[0], box[2] - 1)


def _category_groups(
    masks: list[Image.Image], bar_idx: list[int], ncat: int, horizontal: bool
) -> list[tuple[int, int]]:
    """Группы столбцов по категориям: отрезки рядов делятся по ncat−1 самым широким просветам."""
    spans: list[tuple[int, int]] = []
    for i in bar_idx:
        cols, rows = masks[i].getprojection()
        spans += [r for r in runs(rows if horizontal else cols) if r[1] - r[0] >= 2]
    if not spans:
        raise MeasureError("столбцы не найдены")
    spans.sort()
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    if len(merged) < ncat:
        raise MeasureError(f"столбцов меньше, чем категорий: {len(merged)} из {ncat}")
    gaps = sorted(
        range(len(merged) - 1), key=lambda k: merged[k + 1][0] - merged[k][1], reverse=True
    )[: ncat - 1]
    cuts = sorted(gaps)
    groups: list[tuple[int, int]] = []
    start = 0
    for c in [*cuts, len(merged) - 1]:
        groups.append((merged[start][0], merged[c][1]))
        start = c + 1
    return groups


def _measure_bars(
    m: Measured,
    i: int,
    mask: Image.Image,
    groups: list[tuple[int, int]],
    scale: Scale | None,
    horizontal: bool,
    stacked: bool,
) -> None:
    w, h = mask.size
    for c, (a, b) in enumerate(groups):
        box = (0, a, w, b + 1) if horizontal else (a, 0, b + 1, h)
        cols, rows = mask.crop(box).getprojection()
        spans = [r for r in runs(rows if horizontal else cols) if r[1] - r[0] >= 2]
        if not spans:
            continue
        s0, s1 = max(spans, key=lambda r: r[1] - r[0])
        s0, s1 = s0 + a, s1 + a
        quarter = (s1 - s0) // 4
        edges = []
        for t in range(s0 + quarter, s1 - quarter + 1):
            line = (0, t, w, t + 1) if horizontal else (t, 0, t + 1, h)
            bb = mask.crop(line).getbbox()
            if bb:
                edges.append((bb[0], bb[2] - 1) if horizontal else (bb[1], bb[3] - 1))
        if not edges:
            continue
        near = median(e[0] for e in edges)
        far = median(e[1] for e in edges)
        mid = (s0 + s1) / 2
        if horizontal:
            m.lengths[i][c] = far - near + 1
            m.marks.append(Mark("bar", near, mid, "", far, mid))
        else:
            m.lengths[i][c] = far - near + 1
            m.marks.append(Mark("bar", mid, near, "", mid, far))
        if scale is None:
            continue
        # По вертикали «near» — верх столбца, по горизонтали — левый край.
        start_v = scale.value(far if not horizontal else near)
        end_v = scale.value(near if not horizontal else far + 1)
        if stacked:
            value = end_v - start_v
        else:
            base = 0.0 if scale.lo <= 0 <= scale.hi else scale.lo
            tol = 0.03 * scale.span
            if abs(start_v - base) <= tol:
                value = end_v
            elif abs(end_v - base) <= tol:
                value = start_v
            else:
                value = end_v
                m.issues.append(f"столбец ряда {i + 1} в категории {c + 1} не начинается от нуля")
        m.values[i][c] = value
        m.basis[i][c] = "measured"
        m.marks[-1].text = f"{value:.4g}"


def _measure_line(
    m: Measured, i: int, mask: Image.Image, centers: list[float], scale: Scale | None
) -> None:
    h = mask.size[1]
    found: list[float | None] = []
    for x in centers:
        y = None
        for dx in (0, 1, -1, 2, -2, 3, -3, 4, -4):
            xi = round(x) + dx
            bb = bbox(mask, (xi - 1, 0, xi + 2, h))
            if bb:
                y = (bb[1] + bb[3] - 1) / 2
                break
        found.append(y)
    for c, y in enumerate(found):
        if y is None:
            # Точка закрыта: линия между соседями, если они видны.
            left = next((k for k in range(c - 1, -1, -1) if found[k] is not None), None)
            right = next((k for k in range(c + 1, len(found)) if found[k] is not None), None)
            if left is None or right is None:
                continue
            yl, yr = found[left], found[right]
            assert yl is not None and yr is not None
            y = yl + (yr - yl) * (centers[c] - centers[left]) / (centers[right] - centers[left])
            basis: Basis = "inferred"
        else:
            basis = "measured"
        if scale is None:
            continue
        value = scale.value(y)
        m.values[i][c] = value
        m.basis[i][c] = basis
        m.marks.append(
            Mark("point" if basis == "measured" else "hidden", centers[c], y, f"{value:.4g}")
        )


def _measure_area(
    m: Measured,
    i: int,
    mask: Image.Image,
    centers: list[float],
    scale: Scale | None,
    span: tuple[int, int],
) -> None:
    h = mask.size[1]
    ncat = len(centers)

    def top_at(x: int) -> int | None:
        bb = mask.crop((x, 0, x + 1, h)).getbbox()
        return bb[1] if bb else None

    tops: list[float | None] = []
    for c, x in enumerate(centers):
        xi = round(x)
        if c == 0:
            xi = max(xi, span[0] + 1)
        if c == ncat - 1:
            xi = min(xi, span[1] - 1)
        t = top_at(xi)
        tops.append(float(t) if t is not None else None)
    for c in range(ncat):
        basis: Basis = "measured"
        y = tops[c]
        if y is None:
            y = _extend_edge(top_at, centers, c)
            basis = "inferred"
        if y is None or scale is None:
            continue
        value = scale.value(y)
        m.values[i][c] = value
        m.basis[i][c] = basis
        m.marks.append(
            Mark("point" if basis == "measured" else "hidden", centers[c], y, f"{value:.4g}")
        )


def _extend_edge(top_at: Callable[[int], int | None], centers: list[float], c: int) -> float | None:
    """Закрытая точка области: продолжение видимого прямого участка ребра из соседнего отрезка."""
    estimates: list[tuple[int, float]] = []
    for a, b in ((c - 1, c), (c, c + 1)):
        if a < 0 or b >= len(centers):
            continue
        x0, x1 = int(centers[a]) + 3, int(centers[b]) - 3
        pts = [(x, t) for x in range(x0, x1 + 1) if (t := top_at(x)) is not None]
        if len(pts) < 8 or pts[-1][0] - pts[0][0] < 12:
            continue
        n = len(pts)
        mx = sum(p[0] for p in pts) / n
        my = sum(p[1] for p in pts) / n
        var = sum((p[0] - mx) ** 2 for p in pts)
        if var == 0:
            continue
        k = sum((p[0] - mx) * (p[1] - my) for p in pts) / var
        q = my - k * mx
        if max(abs(k * p[0] + q - p[1]) for p in pts) > 2.5:
            continue
        estimates.append((n, k * centers[c] + q))
    if not estimates:
        return None
    return max(estimates)[1]


def _area_order(m: Measured, series: list[SeriesInfo]) -> list[int]:
    """Порядок отрисовки областей (задняя первой): ряд, видимый ниже другого, стоит перед ним."""
    idx = [i for i, s in enumerate(series) if s.type == "area"]
    front: Counter[tuple[int, int]] = Counter()
    for a in idx:
        for b in idx:
            if a == b:
                continue
            for c in range(len(m.values[a])):
                va, vb = m.values[a][c], m.values[b][c]
                if va is None or vb is None:
                    continue
                if m.basis[a][c] == "measured" and va < vb - 1e-9:
                    front[(a, b)] += 1
    order: list[int] = []
    rest = list(idx)
    while rest:
        # Задняя — та, что ни перед кем из оставшихся не видна ниже их.
        back = next(
            (a for a in rest if not any(front[(a, b)] > front[(b, a)] for b in rest if b != a)),
            rest[0],
        )
        order.append(back)
        rest.remove(back)
    others = [i for i in range(len(series)) if i not in idx]
    return order + others
