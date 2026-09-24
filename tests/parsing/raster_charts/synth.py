"""Синтетические диаграммы-картинки с известными значениями: рисуются PIL шрифтом Montserrat
из репозитория так, как их рисует PowerPoint — плоские цвета на белом, подписи делений слева
(и справа для второй оси), категории под графиком."""

from __future__ import annotations

import io
import math
import pathlib

from PIL import Image, ImageDraw, ImageFont

FONT = (
    pathlib.Path(__file__).resolve().parents[3] / "docker/fonts/montserrat/Montserrat-Regular.ttf"
)

W, H = 640, 440
LEFT, RIGHT, TOP, BOTTOM = 80, 580, 30, 380

BLUE = (0, 119, 255)
PINK = (255, 56, 133)
CYAN = (124, 237, 248)
LIGHT = (200, 214, 229)
GRAY = (160, 160, 160)


def font(size: int = 14) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size)


def png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (W, H), "white")
    return image, ImageDraw.Draw(image)


def _y(value: float, lo: float, hi: float) -> float:
    return BOTTOM - (value - lo) / (hi - lo) * (BOTTOM - TOP)


def _ticks(
    d: ImageDraw.ImageDraw, lo: float, hi: float, step: float, *, right: bool = False
) -> list[str]:
    f = font(14)
    texts = []
    v = lo
    while v <= hi + 1e-9:
        text = f"{v:,.0f}" if step >= 1 else f"{v:g}"
        texts.append(text)
        y = _y(v, lo, hi)
        w = d.textlength(text, font=f)
        x = RIGHT + 12 if right else LEFT - 12 - w
        d.text((x, y - 9), text, fill=(50, 50, 50), font=f)
        v += step
    return texts[::-1]  # как напечатано сверху вниз


def _categories(d: ImageDraw.ImageDraw, names: list[str], centers: list[float]) -> None:
    f = font(14)
    for name, x in zip(names, centers, strict=True):
        w = d.textlength(name, font=f)
        d.text((x - w / 2, BOTTOM + 10), name, fill=(50, 50, 50), font=f)


def doughnut(
    values: list[float], colors: list[tuple[int, int, int]], *, hole: float = 0.7
) -> bytes:
    image, d = _canvas()
    cx, cy, r = W / 2, H / 2 - 10, 170
    total = sum(values)
    start = -90.0
    for v, c in zip(values, colors, strict=True):
        sweep = 360 * v / total
        d.pieslice([cx - r, cy - r, cx + r, cy + r], start, start + sweep, fill=c)
        start += sweep
    if hole:
        ri = r * hole
        d.ellipse([cx - ri, cy - ri, cx + ri, cy + ri], fill="white")
    return png(image)


def columns(
    series: list[list[float]],
    colors: list[tuple[int, int, int]],
    names: list[str],
    *,
    hi: float = 100,
    step: float = 20,
    line: list[float] | None = None,
    line_hi: float | None = None,
    line_step: float | None = None,
) -> tuple[bytes, list[str], list[str]]:
    """Сгруппированные столбцы; `line` — линия по второй оси справа (комбо)."""
    image, d = _canvas()
    ticks = _ticks(d, 0, hi, step)
    right_ticks: list[str] = []
    if line is not None and line_hi and line_step:
        right_ticks = _ticks(d, 0, line_hi, line_step, right=True)
    n = len(names)
    group = (RIGHT - LEFT) / n
    bar = group * 0.7 / len(series)
    centers = []
    for c in range(n):
        x0 = LEFT + group * c + group * 0.15
        centers.append(x0 + bar * len(series) / 2)
        for s, values in enumerate(series):
            y = _y(values[c], 0, hi)
            d.rectangle([x0 + bar * s, y, x0 + bar * (s + 1) - 1, BOTTOM], fill=colors[s])
    if line is not None and line_hi:
        pts = [(x, _y(v, 0, line_hi)) for x, v in zip(centers, line, strict=True)]
        d.line(pts, fill=PINK, width=3)
    _categories(d, names, centers)
    return png(image), ticks, right_ticks


def highlighted(values: list[float], names: list[str], *, per_unit: float = 7.0) -> bytes:
    """Столбцы одного ряда без шкалы, как на слайде VK Education «Оформление диаграмм»: первый
    выделен синим, остальные светлые, подпись «N%» над столбцом, базовая линия серая."""
    image, d = _canvas()
    f = font(14)
    group = (RIGHT - LEFT) / len(values)
    centers = []
    for k, v in enumerate(values):
        x0 = LEFT + group * k + group * 0.2
        x1 = x0 + group * 0.6
        centers.append((x0 + x1) / 2)
        top = BOTTOM - v * per_unit
        d.rectangle([x0, top, x1 - 1, BOTTOM - 1], fill=BLUE if k == 0 else LIGHT)
        text = f"{v:g}%"
        d.text(
            ((x0 + x1) / 2 - d.textlength(text, font=f) / 2, top - 20), text, fill=(0, 0, 0), font=f
        )
    d.line([(LEFT - 10, BOTTOM), (RIGHT + 10, BOTTOM)], fill=GRAY, width=2)
    _categories(d, names, centers)
    return png(image)


def hbars(
    values: list[float],
    names: list[str],
    *,
    per_unit: float = 5.0,
    colors: list[tuple[int, int, int]] | None = None,
) -> bytes:
    """Горизонтальные полосы без шкалы, значение подписано справа от полосы."""
    image, d = _canvas()
    f = font(14)
    for k, (v, name) in enumerate(zip(values, names, strict=True)):
        y = 20 + k * 34
        fill = colors[k] if colors else BLUE
        d.rectangle([170, y, 170 + v * per_unit, y + 16], fill=fill)
        d.text((175 + v * per_unit, y - 1), f"{v:g}", fill=(30, 30, 30), font=f)
        d.text((10, y - 1), name, fill=(30, 30, 30), font=f)
    return png(image)


def lines(
    series: list[list[float]],
    colors: list[tuple[int, int, int]],
    names: list[str],
    *,
    hi: float = 30,
    step: float = 5,
) -> tuple[bytes, list[str]]:
    image, d = _canvas()
    ticks = _ticks(d, 0, hi, step)
    n = len(names)
    centers = [LEFT + 20 + (RIGHT - LEFT - 40) * k / (n - 1) for k in range(n)]
    for values, c in zip(series, colors, strict=True):
        d.line([(x, _y(v, 0, hi)) for x, v in zip(centers, values, strict=True)], fill=c, width=3)
    _categories(d, names, centers)
    return png(image), ticks


def areas(
    series: list[list[float]],
    colors: list[tuple[int, int, int]],
    names: list[str],
    *,
    hi: float = 120,
    step: float = 20,
) -> tuple[bytes, list[str]]:
    """Области с перекрытием: первая — задняя."""
    image, d = _canvas()
    ticks = _ticks(d, 0, hi, step)
    n = len(names)
    centers = [LEFT + (RIGHT - LEFT) * k / (n - 1) for k in range(n)]
    for values, c in zip(series, colors, strict=True):
        pts = [(x, _y(v, 0, hi)) for x, v in zip(centers, values, strict=True)]
        d.polygon([(centers[0], BOTTOM), *pts, (centers[-1], BOTTOM)], fill=c)
    _categories(d, names, centers)
    return png(image), ticks


def photo_like() -> bytes:
    """Плавные градиенты во всю площадь — как фотография."""
    image = Image.new("RGB", (400, 300))
    px = image.load()
    assert px is not None
    for x in range(400):
        for y in range(300):
            px[x, y] = (
                int(128 + 127 * math.sin(x / 17 + y / 29)),
                int(128 + 127 * math.sin(y / 13)),
                (x * 7 + y * 3) % 256,
            )
    return png(image)


# ---------- куски диаграмм из фигур слайда (прозрачный фон, как в Google Slides) ----------

TRACK = (230, 234, 242)


def bar_piece(
    w: int, h: int, color: tuple[int, int, int], alpha_top: float = 1.0, alpha_bottom: float = 0.0
) -> bytes:
    """Столбец-картинка: цвет ровный поперёк, прозрачность меняется сверху вниз."""
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = image.load()
    assert px is not None
    for y in range(h):
        a = round(255 * (alpha_top + (alpha_bottom - alpha_top) * y / max(1, h - 1)))
        for x in range(w):
            px[x, y] = (*color, a)
    return png(image)


def ring_piece(
    size: int,
    hole: float,
    arcs: list[tuple[float, float, tuple[int, int, int] | tuple[tuple[int, int, int], ...]]],
) -> bytes:
    """Кольцо на прозрачном фоне: дуги (начало и размах в градусах от 12 часов по часовой
    стрелке, цвет или пара цветов — градиент слева направо по картинке)."""
    k = 2  # сглаживание: рисуем крупнее и уменьшаем
    big = size * k
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    px = image.load()
    assert px is not None
    c = big / 2
    outer, inner = big / 2 - 1, (big / 2 - 1) * hole
    for y in range(big):
        for x in range(big):
            r = math.hypot(x + 0.5 - c, y + 0.5 - c)
            if not inner <= r <= outer:
                continue
            ang = math.degrees(math.atan2(x + 0.5 - c, -(y + 0.5 - c))) % 360
            for start, sweep, color in arcs:
                if (ang - start) % 360 <= sweep:
                    if isinstance(color[0], tuple):
                        a, b = color  # type: ignore[misc]
                        t = x / big
                        rgb = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
                    else:
                        rgb = color  # type: ignore[assignment]
                    px[x, y] = (*rgb, 255)  # type: ignore[misc]
                    break
    return png(image.resize((size, size), Image.Resampling.LANCZOS))
