"""Куски диаграмм из фигур слайда: заливка столбца-картинки и кольцо по углам — доли, отверстие,
градиент сегмента, наложенные сверху куски; не кольца и не столбцы отсекаются."""

from __future__ import annotations

import io
import random

import pytest
from PIL import Image, ImageDraw

from presentation_designer.parsing.raster_charts.pieces import (
    RingError,
    bar_paint,
    measure_ring,
    rgba,
)
from tests.parsing.raster_charts import synth


def test_bar_paint_keeps_the_fade_into_transparency() -> None:
    paint = bar_paint(rgba(synth.bar_piece(60, 300, synth.BLUE, 1.0, 0.0)), "down")
    assert paint is not None and not paint.solid
    assert paint.angle == 90.0
    first, last = paint.stops[0], paint.stops[-1]
    assert first.color == synth.BLUE and first.alpha > 0.9
    # Прозрачный конец берёт цвет соседа: градиент уходит в прозрачность, а не в белый.
    assert last.color == synth.BLUE and last.alpha < 0.1


def test_bar_paint_of_a_flat_bar_is_one_color() -> None:
    paint = bar_paint(rgba(synth.bar_piece(40, 200, synth.PINK, 1.0, 1.0)), "down")
    assert paint is not None and paint.solid
    assert paint.stops[0].color == synth.PINK


def test_two_colors_across_the_bar_are_not_a_bar() -> None:
    image = Image.new("RGBA", (60, 200), (*synth.BLUE, 255))
    image.paste((*synth.PINK, 255), (30, 0, 60, 200))
    assert bar_paint(image, "down") is None


def test_ring_shares_hole_and_start() -> None:
    arcs = [(0.0, 42 * 3.6, synth.BLUE), (42 * 3.6, 58 * 3.6, synth.TRACK)]
    data = synth.ring_piece(400, 0.66, arcs)
    ring = measure_ring(rgba(data))
    assert ring.hole == pytest.approx(0.66, abs=0.02)
    value, rest = sorted(ring.segments, key=lambda s: -s.saturation)
    assert value.sweep / 3.6 == pytest.approx(42, abs=0.5)
    assert rest.sweep / 3.6 == pytest.approx(58, abs=0.5)
    assert min(value.start, 360 - value.start) == pytest.approx(0, abs=1)


def test_gradient_arc_is_one_segment_with_a_gradient() -> None:
    arcs = [(100.0, 256.0, (synth.CYAN, synth.BLUE)), (356.0, 104.0, synth.TRACK)]
    ring = measure_ring(rgba(synth.ring_piece(400, 0.66, arcs)))
    assert len(ring.segments) == 2
    arc = max(ring.segments, key=lambda s: s.saturation)
    assert arc.sweep == pytest.approx(256, abs=2)
    assert not arc.paint.solid


def test_overlay_piece_splits_a_one_color_ring() -> None:
    base = rgba(synth.ring_piece(400, 0.66, [(0, 360, synth.BLUE)]))
    with pytest.raises(RingError):
        measure_ring(base)  # одно кольцо одного цвета — не диаграмма
    look = base.copy()
    overlay = rgba(synth.ring_piece(400, 0.66, [(0, 90, synth.TRACK)]))
    look.alpha_composite(overlay)
    ring = measure_ring(base, look)
    shares = sorted(round(s.sweep / 3.6) for s in ring.segments)
    assert shares == [25, 75]


def test_noise_is_not_a_ring() -> None:
    rnd = random.Random(7)
    image = Image.new("RGBA", (300, 300))
    image.putdata(
        [(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256), 255) for _ in range(90000)]
    )
    with pytest.raises(RingError):
        measure_ring(image)


def test_full_disc_has_no_hole() -> None:
    image = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    disc = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse((0, 0, 299, 299), fill=(*synth.BLUE, 255))
    image.alpha_composite(disc)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    with pytest.raises(RingError):
        measure_ring(rgba(buf.getvalue()))
