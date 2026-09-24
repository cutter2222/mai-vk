"""Раскладка оставшихся карточек ряда: колонки на всю ширину с прежним промежутком, широкие
объекты растягиваются с прежними полями, узкие держатся своего края."""

from __future__ import annotations

import pytest

from presentation_designer.generation.reflow import (
    TEXT_GAP,
    grow_down,
    move_box,
    row_columns,
    single_row,
)

# Подложки трёх карточек VK Tech («Три текстовых блока», слайд-образец 24).
FRAMES = [(0.031, 0.235, 0.306, 0.586), (0.347, 0.235, 0.306, 0.586), (0.663, 0.235, 0.306, 0.586)]


def test_two_of_three_cards_take_the_whole_row() -> None:
    placed = row_columns(FRAMES, [0, 1])
    assert placed is not None and set(placed) == {0, 1}
    (x0, w0), (x1, w1) = placed[0], placed[1]
    assert x0 == pytest.approx(0.031) and w0 == pytest.approx(w1)
    assert x1 - (x0 + w0) == pytest.approx(0.010)
    assert x1 + w1 == pytest.approx(0.969)


def test_nothing_to_spread() -> None:
    assert row_columns(FRAMES, [0, 1, 2]) is None
    assert row_columns(FRAMES, []) is None
    two_rows = [(0.1, 0.2, 0.3, 0.2), (0.5, 0.2, 0.3, 0.2), (0.1, 0.5, 0.3, 0.2)]
    assert not single_row(two_rows) and row_columns(two_rows, [0]) is None


def test_card_objects_follow_their_edges() -> None:
    old, new = (0.031, 0.306), (0.031, 0.464)
    grow = 0.464 - 0.306
    # Подложка и текст шире с прежними полями.
    frame = move_box(FRAMES[0], old, new)
    assert frame[0] == pytest.approx(0.031) and frame[2] == pytest.approx(0.464)
    body = move_box((0.052, 0.317, 0.269, 0.162), old, new)
    assert body[0] == pytest.approx(0.052) and body[2] == pytest.approx(0.269 + grow)
    # Иконка в правом нижнем углу держится правого края, точка слева — левого, размер прежний.
    icon = move_box((0.265, 0.693, 0.051, 0.091), old, new)
    assert icon[2] == pytest.approx(0.051) and icon[0] == pytest.approx(0.265 + grow)
    dot = move_box((0.052, 0.272, 0.008, 0.015), old, new)
    assert dot[:3] == pytest.approx((0.052, 0.272, 0.008))
    # Объект посередине остаётся посередине.
    mid = move_box((0.164, 0.5, 0.04, 0.04), old, new)
    assert mid[0] + mid[2] / 2 == pytest.approx(0.031 + 0.464 / 2, abs=0.002)


def test_card_text_grows_down_to_the_icon() -> None:
    body = (0.052, 0.317, 0.427, 0.162)
    icon = (0.423, 0.693, 0.051, 0.091)
    dot = (0.052, 0.272, 0.008, 0.015)  # над текстом: не мешает
    grown = grow_down(body, [icon, dot])
    assert grown[:3] == body[:3] and grown[1] + grown[3] == pytest.approx(0.693 - TEXT_GAP)
    # Под текстом ничего нет или объект в стороне — высота прежняя.
    assert grow_down(body, [dot]) == body
    assert grow_down(body, [(0.6, 0.693, 0.05, 0.09)]) == body
