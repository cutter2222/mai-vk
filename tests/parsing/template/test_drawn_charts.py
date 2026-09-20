"""Распознавание диаграммы, нарисованной руками: ряд столбиков вместо нативного графика.

Отличать её приходится от ряда карточек и ряда иконок: и то, и другое — тоже одинаковые
прямоугольники в ряд. Признак диаграммы — разная длина при общей базе и вытянутость столбика.
"""

from __future__ import annotations

from presentation_designer.parsing.template.drawn_charts import find_drawn_chart
from presentation_designer.parsing.template.geometry import ShapeInfo

EMU = 914400


def _shape(
    element_id: str,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    kind: str = "shape",
    text: str = "",
    fill: str | None = "solid",
) -> ShapeInfo:
    return ShapeInfo(
        element_id=element_id,
        name=f"shape {element_id}",
        kind=kind,
        x=x,
        y=y,
        width=w,
        height=h,
        left_emu=int(x * EMU),
        top_emu=int(y * EMU),
        width_emu=int(w * EMU),
        height_emu=int(h * EMU),
        z_order=int(element_id) if element_id.isdigit() else 0,
        text=text,
        has_text_frame=bool(text),
        fill_kind=fill,
    )


def _row(heights: list[float], *, base: float = 0.8, width: float = 0.06) -> list[ShapeInfo]:
    """Ряд столбиков с общей нижней базой: высоты заданы явно."""
    out: list[ShapeInfo] = []
    for i, h in enumerate(heights):
        x = 0.05 + i * 0.12
        out.append(_shape(str(10 + i), x, base - h, width, h))
    return out


def test_column_row_with_different_heights_is_a_chart() -> None:
    shapes = [
        *_row([0.5, 0.35, 0.42, 0.28, 0.46]),
        _shape("100", 0.05, 0.82, 0.6, 0.04, kind="text", text="Май"),
        _shape("101", 0.02, 0.06, 0.7, 0.12, kind="text", text="Заголовок"),
    ]
    found = find_drawn_chart(shapes)
    assert found is not None and found.kind == "column"
    assert len(found.parts) == 5
    # Подпись категорий под рядом входит в область, заголовок слайда — нет.
    assert "100" in found.labels and "101" not in found.element_ids
    assert found.bbox["width"] >= 0.5 and found.bbox["y"] < 0.35


def test_equal_cards_are_not_a_chart() -> None:
    """Четыре одинаковые карточки — ряд карточек: показывать графику нечего."""
    assert find_drawn_chart(_row([0.4, 0.4, 0.4, 0.4])) is None


def test_small_icons_are_not_a_chart() -> None:
    """Иконки с подписями: квадратики ниже порога и не вытянуты вдоль оси."""
    icons = [
        _shape("20", 0.05, 0.4, 0.08, 0.08),
        _shape("21", 0.35, 0.4, 0.08, 0.11),
        _shape("22", 0.65, 0.4, 0.08, 0.09),
        _shape("23", 0.85, 0.4, 0.08, 0.10),
    ]
    assert find_drawn_chart(icons) is None


def test_horizontal_bars_are_found() -> None:
    """Линейчатая диаграмма: общий левый край, разная длина."""
    bars = [
        _shape("30", 0.1, 0.30, 0.55, 0.06),
        _shape("31", 0.1, 0.42, 0.30, 0.06),
        _shape("32", 0.1, 0.54, 0.44, 0.06),
        _shape("33", 0.1, 0.66, 0.20, 0.06),
    ]
    found = find_drawn_chart(bars)
    assert found is not None and found.kind == "bar"
    assert len(found.parts) == 4


def test_legend_marks_inside_area_go_with_the_chart() -> None:
    """Пустые надписи-кружки легенды внутри области уходят вместе с рядом."""
    shapes = [
        *_row([0.5, 0.35, 0.42, 0.28]),
        _shape("90", 0.4, 0.33, 0.02, 0.02, kind="text"),
        _shape("91", 0.4, 0.37, 0.02, 0.02, kind="text"),
    ]
    found = find_drawn_chart(shapes)
    assert found is not None
    assert {"90", "91"} <= set(found.decor)


def test_text_shapes_are_not_parts() -> None:
    """Столбик с собственным текстом — это карточка: в ряд не берётся."""
    shapes = _row([0.5, 0.35, 0.42, 0.28])
    for s in shapes:
        s.text = "Текст"
        s.has_text_frame = True
    assert find_drawn_chart(shapes) is None
