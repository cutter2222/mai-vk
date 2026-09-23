"""Пиксельные примитивы чтения диаграмм: растр на белом, палитра плоских цветов, строки текста.

Диаграммы из PowerPoint и Excel, сохранённые картинкой, рисуются плоскими цветами на белом
или прозрачном фоне; сглаживание краёв даёт смеси, которых мало. На этом держится и
предфильтр «похоже на диаграмму», и поиск рядов по цвету.
"""

from __future__ import annotations

import io
from collections import Counter
from dataclasses import dataclass

from PIL import Image

Rgb = tuple[int, int, int]

MAX_PIXELS = 16_000_000


def load(data: bytes) -> Image.Image:
    """RGB на белом: прозрачный фон PNG иначе становится чёрным."""
    with Image.open(io.BytesIO(data)) as raw:
        if raw.width * raw.height > MAX_PIXELS:
            raise ValueError("слишком большое изображение")
        src = raw.convert("RGBA")
    out = Image.new("RGB", src.size, (255, 255, 255))
    out.paste(src, mask=src.getchannel("A"))
    return out


def to_png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def hex_of(c: Rgb) -> str:
    return "#{:02X}{:02X}{:02X}".format(*c)


def rgb_of(value: str) -> Rgb | None:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        return None


def dist(a: Rgb, b: Rgb) -> float:
    return float(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)) ** 0.5)


def is_background(c: Rgb) -> bool:
    return min(c) >= 247


def is_ink(c: Rgb) -> bool:
    """Тёмный ненасыщенный пиксель: подписи осей, категорий, легенды."""
    return max(c) < 150 and max(c) - min(c) < 45


def chroma(c: Rgb) -> int:
    return max(c) - min(c)


def colors(image: Image.Image) -> Counter[Rgb]:
    # getdata устарел в Pillow 12; get_flattened_data появился там же.
    flat = getattr(image, "get_flattened_data", None)
    return Counter(flat() if flat else image.getdata())


def palette(
    image: Image.Image, *, min_share: float = 0.0015, merge: float = 12.0
) -> list[tuple[Rgb, float]]:
    """Плоские цвета картинки по убыванию доли, без фона и тёмного текста.

    Смеси сглаживания встречаются редко и отсекаются порогом доли; редкий близкий оттенок
    (JPEG, сжатие) сливается с частым цветом. Близкие цвета сопоставимой доли остаются
    разными: у блёклых сегментов кольца оттенки отличаются на единицы.
    """
    counts = colors(image)
    total = image.width * image.height
    out: list[tuple[Rgb, float]] = []
    for c, n in counts.most_common():
        share = n / total
        if share < min_share:
            break
        if is_background(c) or is_ink(c):
            continue
        for i, (kept, kept_share) in enumerate(out):
            if dist(kept, c) <= merge and share < 0.1 * kept_share:
                out[i] = (kept, kept_share + share)
                break
        else:
            out.append((c, share))
    return out


def nearest(target: Rgb, choices: list[Rgb]) -> tuple[int, float]:
    best, best_d = -1, float("inf")
    for i, c in enumerate(choices):
        d = dist(target, c)
        if d < best_d:
            best, best_d = i, d
    return best, best_d


def tolerance(color: Rgb, others: list[Rgb], cap: float = 40.0) -> float:
    """Допуск цвета ряда: не шире половины расстояния до ближайшего другого цвета и фона."""
    near = [dist(color, o) for o in [*others, (255, 255, 255)] if o != color]
    return max(4.0, min([cap, *(d / 2 for d in near)]))


@dataclass(frozen=True)
class Box:
    left: int
    top: int
    right: int  # не включая
    bottom: int  # не включая

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


def ink_bands(image: Image.Image, box: Box, *, along: str) -> list[tuple[float, int, int]]:
    """Полосы текста в прямоугольнике: `along="rows"` — строки (центр y), `"cols"` — столбцы.

    Возвращает (центр, начало, конец) каждой непрерывной полосы с тёмными пикселями.
    """
    px = image.load()
    assert px is not None
    if along == "rows":
        outer, inner = range(box.top, box.bottom), range(box.left, box.right)
        hit = [any(is_ink(px[x, y]) for x in inner) for y in outer]  # type: ignore[arg-type]
        base = box.top
    else:
        outer, inner = range(box.left, box.right), range(box.top, box.bottom)
        hit = [any(is_ink(px[x, y]) for y in inner) for x in outer]  # type: ignore[arg-type]
        base = box.left
    bands: list[tuple[float, int, int]] = []
    start: int | None = None
    for i, h in enumerate([*hit, False]):
        if h and start is None:
            start = i
        elif not h and start is not None:
            bands.append(((start + i - 1) / 2 + base, start + base, i - 1 + base))
            start = None
    return bands


@dataclass(frozen=True)
class Likeness:
    ok: bool
    reason: str
    background: float
    flat: float
    accent: float


def chart_likeness(image: Image.Image) -> Likeness:
    """Предфильтр без модели: плоские цвета, много фона, есть насыщенный цвет.

    Фотографии проваливают «плоскость», логотипы и иконки — размер, скриншоты интерфейсов
    обычно проходят — их отсекает уже модель.
    """
    w, h = image.size
    if w < 160 or h < 120:
        return Likeness(False, "картинка меньше 160×120", 0, 0, 0)
    counts = colors(image)
    total = w * h
    background = sum(n for c, n in counts.items() if is_background(c)) / total
    flat = sum(n for _, n in counts.most_common(24)) / total
    accent = sum(n for c, n in counts.items() if chroma(c) >= 40 and not is_ink(c)) / total
    if background < 0.25:
        return Likeness(False, "мало фона — похоже на фото или заливку", background, flat, accent)
    if flat < 0.85:
        return Likeness(False, "много оттенков — похоже на фото", background, flat, accent)
    if accent < 0.01:
        return Likeness(False, "нет цветных областей", background, flat, accent)
    return Likeness(True, "", background, flat, accent)
