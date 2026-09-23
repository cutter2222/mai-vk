"""Оверлей измерения для глазной проверки: шкала, вершины столбцов, точки, границы сегментов."""

from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw, ImageFont

from presentation_designer.parsing.raster_charts.measure import Measured

FONT = (
    pathlib.Path(__file__).resolve().parents[4] / "docker/fonts/montserrat/Montserrat-Regular.ttf"
)
SCALE = 2


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(FONT), size)
    except OSError:
        return ImageFont.load_default()


def draw_overlay(image: Image.Image, m: Measured) -> Image.Image:
    """Шкалы: основная зелёная, правая синяя; точки красные, восстановленные — оранжевые."""
    k = SCALE
    out = image.resize((image.width * k, image.height * k), Image.Resampling.LANCZOS)
    d = ImageDraw.Draw(out)
    font = _font(15)
    points = [mk for mk in m.marks if mk.kind in ("point", "hidden", "bar")]
    crowded = len(points) > 24
    for mark in m.marks:
        x, y = mark.x * k, mark.y * k
        if mark.kind in ("tick", "tick2"):
            color = (0, 150, 0) if mark.kind == "tick" else (0, 90, 220)
            vertical_axis = mark.x == 0
            line = [(0, y), (out.width, y)] if vertical_axis else [(x, 0), (x, out.height)]
            d.line(line, fill=color, width=1)
            if vertical_axis:
                w = d.textlength(mark.text, font=font)
                pos = (2.0, y - 17) if mark.kind == "tick" else (out.width - w - 2, y - 17)
            else:
                pos = (x + 2, out.height - 18)
            d.text(pos, mark.text, fill=color, font=font)
        elif mark.kind == "bar":
            d.line([(x, y), (mark.x2 * k, mark.y2 * k)], fill=(230, 0, 0), width=2)
            if mark.text and not crowded:
                d.text((x + 4, y - 18), mark.text, fill=(200, 0, 0), font=font)
        elif mark.kind in ("point", "hidden", "ring"):
            color = (230, 0, 0) if mark.kind != "hidden" else (255, 120, 0)
            r = 5
            d.ellipse([x - r, y - r, x + r, y + r], outline=color, width=2)
            if not crowded or mark.kind != "point":
                text = mark.text + ("*" if mark.kind == "hidden" else "")
                d.text((x + 7, y - 18), text, fill=color, font=font)
    return out
