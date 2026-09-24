"""Куски диаграммы, нарисованной на слайде отдельными картинками: столбец (заливка по длине —
цвет, градиент, прозрачность) и кольцо (центр, радиусы, сегменты по углу).

Такие диаграммы приходят из Google Slides и Figma: каждый столбец — своя PNG с градиентом в
прозрачность, кольцо — PNG с прозрачной серединой, иногда из двух наложенных картинок, а числа
и подписи — отдельные надписи слайда. Модель здесь не нужна: форма и цвет читаются по пикселям,
числа — из надписей (это делает `layout/composite_charts.py`).
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Any, Literal, cast

from PIL import Image

from presentation_designer.parsing.raster_charts.pixels import MAX_PIXELS, Rgb, chroma, dist

# Непрозрачным считается пиксель с альфой от 32 из 255: мягкие края и хвосты градиентов
# в прозрачность дальше этого порога не читаются.
OPAQUE = 32


@dataclass(frozen=True)
class Stop:
    pos: float  # 0–1 вдоль направления заливки
    color: Rgb
    alpha: float  # 0–1


@dataclass(frozen=True)
class Paint:
    """Заливка куска: одна точка — сплошной цвет, больше — линейный градиент.

    `angle` — как у a:lin: 0° — слева направо, 90° — сверху вниз (по часовой стрелке)."""

    stops: tuple[Stop, ...]
    angle: float = 90.0

    @property
    def solid(self) -> bool:
        return len(self.stops) == 1

    @property
    def main(self) -> Rgb:
        """Самый плотный цвет заливки: по нему ряд узнают в легенде и отличают от соседей."""
        return max(self.stops, key=lambda s: s.alpha).color


def rgba(data: bytes) -> Image.Image:
    with Image.open(io.BytesIO(data)) as raw:
        if raw.width * raw.height > MAX_PIXELS:
            raise ValueError("слишком большое изображение")
        return raw.convert("RGBA")


Rgba = tuple[int, int, int, int]


def _at(px: Any, x: int, y: int) -> Rgba:
    return cast(Rgba, px[x, y])


def _is_back(p: Rgba) -> bool:
    """Фон картинки: прозрачный пиксель или белый (кольца бывают и на белой подложке)."""
    return p[3] < OPAQUE or (min(p[:3]) >= 247 and p[3] >= 250)


# ---------- столбец ----------


BAR_SAMPLES = 17  # точек по длине столбца: блик посередине полосы не должен теряться


def bar_paint(image: Image.Image, along: Literal["down", "right"]) -> Paint | None:
    """Заливка столбца: средний цвет и прозрачность поперёк средней части в 17 точках по
    длине, упрощённые до нужных точек градиента. Края отступают от скруглений торцов.
    None — картинка не похожа на столбец: мало заливки или цвет поперёк неровный."""
    w, h = image.size
    if min(w, h) < 4:
        return None
    px = image.load()
    assert px is not None
    length, thick = (h, w) if along == "down" else (w, h)
    cross = [round(thick * (0.25 + 0.5 * k / 8)) for k in range(9)]
    inset = min(0.06, 0.4 * thick / length)
    samples: list[tuple[float, Rgb, float]] = []
    covered = 0
    for k in range(BAR_SAMPLES):
        t = inset + (1 - 2 * inset) * k / (BAR_SAMPLES - 1)
        pos = min(length - 1, round(t * (length - 1)))
        cells = [_at(px, c, pos) if along == "down" else _at(px, pos, c) for c in cross]
        solid = [c for c in cells if c[3] >= OPAQUE]
        if len(solid) < len(cells) * 0.7:
            samples.append((t, (255, 255, 255), 0.0))
            continue
        covered += 1
        mean = _mean([(c[0], c[1], c[2]) for c in solid])
        # Поперёк столбец одного цвета: пятна и надписи внутри картинки — не столбец.
        if max(dist((c[0], c[1], c[2]), mean) for c in solid) > 40:
            return None
        samples.append((t, mean, sum(c[3] for c in solid) / len(solid) / 255))
    if covered < BAR_SAMPLES * 0.55:
        return None
    return _paint(samples, 90.0 if along == "down" else 0.0)


def _paint(samples: list[tuple[float, Rgb, float]], angle: float) -> Paint:
    """Точки по длине → сплошной цвет или градиент: внутренняя точка остаётся, только если
    заметно отходит от прямой между соседями (так сохраняется блик посередине полосы)."""
    colors = [s[1] for s in samples if s[2] > 0]
    alphas = [s[2] for s in samples]
    span = max(dist(a, b) for a in colors for b in colors) if colors else 0.0
    if span <= 12 and max(alphas) - min(alphas) <= 0.1:
        return Paint((Stop(0.0, _mean(colors), round(sum(alphas) / len(alphas), 3)),), angle)
    # Прозрачная точка берёт цвет ближайшей непрозрачной: иначе градиент уйдёт в белый,
    # а не в прозрачность.
    filled: list[tuple[float, Rgb, float]] = []
    for i, (t, c, a) in enumerate(samples):
        if a == 0:
            near = min(
                (j for j in range(len(samples)) if samples[j][2] > 0), key=lambda j: abs(j - i)
            )
            c = samples[near][1]
        filled.append((t, c, a))
    keep = [0, len(filled) - 1]
    _split(filled, 0, len(filled) - 1, keep)
    stops = [
        Stop(round(filled[i][0], 4), filled[i][1], round(filled[i][2], 3)) for i in sorted(keep)
    ]
    return Paint(tuple(stops), angle)


def _split(points: list[tuple[float, Rgb, float]], lo: int, hi: int, keep: list[int]) -> None:
    """Дуглас — Пекер по цвету и прозрачности: точка с наибольшим отходом от прямой между
    `lo` и `hi` остаётся, если отход заметен (цвет больше 8, прозрачность больше 0,05)."""
    if hi - lo < 2:
        return
    t0, c0, a0 = points[lo]
    t1, c1, a1 = points[hi]
    worst, at = 0.0, -1
    for i in range(lo + 1, hi):
        t, c, a = points[i]
        k = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        lerp = (
            round(c0[0] + (c1[0] - c0[0]) * k),
            round(c0[1] + (c1[1] - c0[1]) * k),
            round(c0[2] + (c1[2] - c0[2]) * k),
        )
        off = max(dist(c, lerp) / 8, abs(a - (a0 + (a1 - a0) * k)) / 0.05)
        if off > worst:
            worst, at = off, i
    if worst > 1:
        keep.append(at)
        _split(points, lo, at, keep)
        _split(points, at, hi, keep)


# ---------- кольцо ----------


class RingError(ValueError):
    """Картинка не кольцо или кольцо не делится на сегменты."""


@dataclass(frozen=True)
class Segment:
    start: float  # градусы от 12 часов по часовой стрелке
    sweep: float
    paint: Paint
    mean: Rgb

    @property
    def saturation(self) -> int:
        return chroma(self.mean)


@dataclass(frozen=True)
class Ring:
    cx: float  # центр и радиусы — в пикселях картинки
    cy: float
    outer: float
    inner: float
    segments: tuple[Segment, ...]

    @property
    def hole(self) -> float:
        return self.inner / self.outer

    @property
    def box(self) -> tuple[float, float, float, float]:
        """Рамка кольца в пикселях: left, top, width, height."""
        return (self.cx - self.outer, self.cy - self.outer, 2 * self.outer, 2 * self.outer)


STEP = 0.5  # шаг развёртки, градусы


def ring_shape(image: Image.Image) -> tuple[float, float, float, float]:
    """Центр, внешний и внутренний радиусы кольца по маске непрозрачных пикселей: центр и
    внешний радиус — по рамке маски, внутренний — медиана по лучам от центра."""
    w, h = image.size
    if min(w, h) < 60:
        raise RingError("картинка слишком мала для кольца")
    px = image.load()
    assert px is not None
    step = max(1, min(w, h) // 300)
    xs: list[int] = []
    ys: list[int] = []
    for y in range(0, h, step):
        for x in range(0, w, step):
            if not _is_back(_at(px, x, y)):
                xs.append(x)
                ys.append(y)
    if len(xs) < 50:
        raise RingError("на картинке почти нет заливки")
    left, right, top, bottom = min(xs), max(xs) + step, min(ys), max(ys) + step
    bw, bh = right - left, bottom - top
    if abs(bw - bh) > 0.06 * max(bw, bh):
        raise RingError("заливка не круглая")
    cx, cy, outer = (left + right) / 2, (top + bottom) / 2, max(bw, bh) / 2
    inner = _inner_radius(px, cx, cy, outer)
    if not 0.2 * outer <= inner <= 0.92 * outer:
        raise RingError("у кольца нет отверстия")
    # Середина пустая, полоса заполнена почти по всей окружности: круг с дыркой, а не
    # картинка с просветом.
    band = _sweep(px, cx, cy, inner, outer)
    if sum(1 for s in band if s is not None) < 0.6 * len(band):
        raise RingError("кольцо заполнено меньше чем на 60 %")
    return cx, cy, outer, inner


def measure_ring(image: Image.Image, look: Image.Image | None = None) -> Ring:
    """Кольцо: форма по `image` (см. `ring_shape`), сегменты — по скачкам цвета вдоль средней
    окружности. Градиент вдоль сегмента скачком не считается: соседние точки развёртки близки.

    `look` — та же картинка с наложенными сверху кусками (серая дуга поверх градиентного
    кольца, клин в разрыве): форма кольца берётся по `image`, цвета сегментов — по `look`."""
    cx, cy, outer, inner = ring_shape(image)
    px = (look or image).load()
    assert px is not None
    samples = _sweep(px, cx, cy, inner, outer)
    filled = sum(1 for s in samples if s is not None)
    if filled < 0.6 * len(samples):
        raise RingError("кольцо заполнено меньше чем на 60 %")
    return Ring(cx, cy, outer, inner, tuple(_segments(samples, cx, cy)))


def _inner_radius(px: Any, cx: float, cy: float, outer: float) -> float:
    """Медиана по 72 лучам: первый непрозрачный пиксель от центра наружу."""
    found: list[float] = []
    for k in range(72):
        a = math.radians(k * 5)
        dx, dy = math.sin(a), -math.cos(a)
        r = 0.0
        while r < outer:
            if not _is_back(_at(px, round(cx + dx * r), round(cy + dy * r))):
                found.append(r)
                break
            r += 1.0
    if len(found) < 36:
        return 0.0
    found.sort()
    return found[len(found) // 2]


def _sweep(
    px: Any, cx: float, cy: float, inner: float, outer: float
) -> list[tuple[Rgb, float, float, float] | None]:
    """Развёртка по углу: средний цвет на трёх радиусах полосы и точка на средней окружности;
    None — в полосе прозрачно (зазор между сегментами или пустая часть кольца)."""
    band = outer - inner
    radii = [inner + band * f for f in (0.3, 0.5, 0.7)]
    mid = inner + band * 0.5
    out: list[tuple[Rgb, float, float, float] | None] = []
    n = round(360 / STEP)
    for k in range(n):
        a = math.radians(k * STEP)
        s, c = math.sin(a), -math.cos(a)
        cells = [_at(px, round(cx + s * r), round(cy + c * r)) for r in radii]
        solid = [p for p in cells if not _is_back(p)]
        if len(solid) < 2:
            out.append(None)
            continue
        mean = _mean([(p[0], p[1], p[2]) for p in solid])
        alpha = sum(p[3] for p in solid) / len(solid) / 255
        out.append((mean, alpha, cx + s * mid, cy + c * mid))
    return out


def _segments(
    samples: list[tuple[Rgb, float, float, float] | None], cx: float, cy: float
) -> list[Segment]:
    n = len(samples)
    # Границы: переход «заливка — зазор» и скачок цвета между соседними точками развёртки.
    # Обход начинается с зазора, а без зазоров — с самого резкого скачка цвета.
    start = next((k for k in range(n) if samples[k] is None), None)
    if start is None:
        start = max(range(n), key=lambda k: _step(samples, k))
    runs: list[list[int]] = []
    prev: tuple[Rgb, float, float, float] | None = None
    for i in range(n):
        k = (start + i) % n
        s = samples[k]
        if s is None:
            prev = None
            continue
        if prev is None or dist(s[0], prev[0]) > JUMP:
            runs.append([])
        runs[-1].append(k)
        prev = s
    # Обрывки короче полутора градусов — сглаженный край или тонкий разделитель: их угол
    # делят соседи. Соседние куски одного цвета через такой обрывок — один сегмент.
    short = round(1.5 / STEP)
    runs = [r for r in runs if len(r) >= short]
    if not runs:
        raise RingError("сегменты кольца не выделяются")
    merged: list[list[int]] = [runs[0]]
    for r in runs[1:]:
        if _continues(samples, merged[-1], r, n):
            merged[-1].extend(r)
        else:
            merged.append(r)
    if len(merged) > 1 and _continues(samples, merged[-1], merged[0], n):
        merged[-1].extend(merged.pop(0))
    if len(merged) < 2:
        raise RingError("кольцо одного цвета — это не диаграмма")
    if len(merged) > 12:
        raise RingError("слишком много сегментов — похоже на рисунок, а не диаграмму")
    # Сегмент тянется до середины зазоров с соседями: зазор — разделитель, а не доля.
    spans: list[tuple[float, float]] = []
    for i, ks in enumerate(merged):
        gap_before = ((ks[0] - merged[i - 1][-1]) % n - 1) / 2
        gap_after = ((merged[(i + 1) % len(merged)][0] - ks[-1]) % n - 1) / 2
        begin = (ks[0] - gap_before) * STEP
        end = (ks[-1] + 1 + gap_after) * STEP
        spans.append((begin % 360, (end - begin) % 360 or 360.0))
    total = sum(sweep for _, sweep in spans)
    out: list[Segment] = []
    for (begin, sweep), ks in zip(spans, merged, strict=True):
        pts = [p for k in ks if (p := samples[k]) is not None]
        out.append(
            Segment(
                start=round(begin, 2),
                sweep=round(sweep * 360 / total, 3),
                paint=_arc_paint(pts),
                mean=_mean([p[0] for p in pts]),
            )
        )
    return out


JUMP = 45.0  # скачок цвета между соседними точками развёртки — граница сегментов


def _step(samples: list[tuple[Rgb, float, float, float] | None], k: int) -> float:
    a, b = samples[k], samples[k - 1]
    return dist(a[0], b[0]) if a is not None and b is not None else 0.0


def _continues(
    samples: list[tuple[Rgb, float, float, float] | None], a: list[int], b: list[int], n: int
) -> bool:
    """Кусок `b` продолжает `a`: между ними не больше трёх градусов и цвет на стыке близок."""
    tail, head = samples[a[-1]], samples[b[0]]
    if tail is None or head is None:
        return False
    return (b[0] - a[-1]) % n <= round(3 / STEP) and dist(tail[0], head[0]) <= 2 * JUMP


def _mean(colors: list[Rgb]) -> Rgb:
    n = len(colors)
    return (
        round(sum(c[0] for c in colors) / n),
        round(sum(c[1] for c in colors) / n),
        round(sum(c[2] for c in colors) / n),
    )


def _arc_paint(pts: list[tuple[Rgb, float, float, float]]) -> Paint:
    """Заливка сегмента: градиент по картинке линейный (так их рисуют Figma и Google Slides),
    поэтому цвет по каналам приближается плоскостью c = c0 + gx·x + gy·y; направление — по
    сильнейшему изменению, точки градиента — цвета на краях рамки сегмента."""
    colors = [p[0] for p in pts]
    alpha = round(sum(p[1] for p in pts) / len(pts), 3)
    mean = _mean(colors)
    if max(dist(c, mean) for c in colors) <= 14:
        return Paint((Stop(0.0, mean, alpha),))
    xs = [p[2] for p in pts]
    ys = [p[3] for p in pts]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    det = sxx * syy - sxy * sxy
    if abs(det) < 1e-6:
        return Paint((Stop(0.0, mean, alpha),))
    grads: list[tuple[float, float]] = []
    for ch in range(3):
        vs = [c[ch] for c in colors]
        mv = sum(vs) / len(vs)
        sxv = sum((x - mx) * (v - mv) for x, v in zip(xs, vs, strict=True))
        syv = sum((y - my) * (v - mv) for y, v in zip(ys, vs, strict=True))
        grads.append(((sxv * syy - syv * sxy) / det, (syv * sxx - sxv * sxy) / det))
    gx = sum(g[0] for g in grads)
    gy = sum(g[1] for g in grads)
    if math.hypot(gx, gy) < 1e-9:
        return Paint((Stop(0.0, mean, alpha),))
    ux, uy = gx / math.hypot(gx, gy), gy / math.hypot(gx, gy)
    # Края рамки сегмента вдоль направления: там стоят точки 0 и 1 градиента a:lin.
    proj = [(x - mx) * ux + (y - my) * uy for x, y in zip(xs, ys, strict=True)]
    lo, hi = min(proj), max(proj)
    if hi - lo < 1:
        return Paint((Stop(0.0, mean, alpha),))

    def at(t: float) -> Rgb:
        dx, dy = ux * t, uy * t
        ch = [
            max(0, min(255, round(mean[i] + grads[i][0] * dx + grads[i][1] * dy))) for i in range(3)
        ]
        return (ch[0], ch[1], ch[2])

    a, b = at(lo), at(hi)
    if dist(a, b) <= 14:
        return Paint((Stop(0.0, mean, alpha),))
    angle = math.degrees(math.atan2(uy, ux)) % 360
    return Paint((Stop(0.0, a, alpha), Stop(1.0, b, alpha)), round(angle, 1))
