"""Раскладка оставшихся карточек однорядной сетки шаблона на всю ширину ряда.

Сетка на n карточек, в которой заполнены k < n: пустые карточки сборка убирает, а оставшиеся
расходятся по ширине всего ряда с прежним промежутком. Широкие объекты карточки (подложка, текст)
растягиваются с прежними полями, узкие (иконки, точки) сохраняют размер и держатся своего края.
Одна геометрия нужна подбору — ёмкость расширенных слотов при просьбе «не три колонки, а две»
(`generation/edit.py`) — и сборке, которая двигает сами фигуры (`layout/compose.py`).
Координаты — доли слайда, рамка — (x, y, ширина, высота).
"""

from __future__ import annotations

Box = tuple[float, float, float, float]

ROW_TOLERANCE = 0.02
# Зазор между текстом карточки, выросшим вниз, и объектом под ним (доля высоты слайда).
TEXT_GAP = 0.02


def single_row(boxes: list[Box]) -> bool:
    """Карточки стоят в один ряд слева направо и не заходят друг на друга."""
    if len(boxes) < 2:
        return False
    if max(b[1] for b in boxes) - min(b[1] for b in boxes) > ROW_TOLERANCE:
        return False
    return all(boxes[i][0] + boxes[i][2] <= boxes[i + 1][0] + 0.005 for i in range(len(boxes) - 1))


def row_columns(columns: list[Box], kept: list[int]) -> dict[int, tuple[float, float]] | None:
    """Новые (x, ширина) оставшихся колонок `kept` (индексы в `columns`, ряд слева направо):
    ряд прежней ширины, промежуток между карточками прежний. None — раскладывать нечего или
    колонки не образуют ряд."""
    if not single_row(columns) or not 1 <= len(kept) < len(columns):
        return None
    left = columns[0][0]
    right = columns[-1][0] + columns[-1][2]
    gaps = [columns[i + 1][0] - (columns[i][0] + columns[i][2]) for i in range(len(columns) - 1)]
    gap = max(0.0, sum(gaps) / len(gaps))
    width = (right - left - (len(kept) - 1) * gap) / len(kept)
    if width <= 0:
        return None
    return {i: (left + j * (width + gap), width) for j, i in enumerate(sorted(kept))}


def move_box(box: Box, old: tuple[float, float], new: tuple[float, float]) -> Box:
    """Объект карточки из колонки `old` (x, ширина) в колонку `new`: широкий (от половины
    карточки) растягивается с прежними полями, узкий сохраняет размер и держится своего края,
    а стоявший посередине — середины."""
    x, y, w, h = box
    (left, width), (new_left, new_width) = old, new
    if w >= 0.5 * width:
        return (new_left + (x - left), y, w + new_width - width, h)
    rel = (x + w / 2 - left) / width if width else 0.5
    if rel < 0.4:
        nx = new_left + (x - left)
    elif rel > 0.6:
        nx = new_left + new_width - (left + width - x)
    else:
        nx = new_left + rel * new_width - w / 2
    return (nx, y, w, h)


def grow_down(box: Box, below: list[Box]) -> Box:
    """Текст карточки, прижатый к верху, растёт вниз до ближайшего объекта карточки под ним
    (иконки) с зазором: две карточки вместо трёх несут по два прежних пункта, а в три строки
    текстового блока VK Tech они не входят, хотя до иконки место пустует. Нет объекта под
    текстом — высота прежняя."""
    x, y, w, h = box
    tops = [b[1] for b in below if b[1] >= y + h - 0.005 and b[0] < x + w and b[0] + b[2] > x]
    if not tops:
        return box
    return (x, y, w, max(h, min(tops) - TEXT_GAP - y))


__all__ = ["TEXT_GAP", "Box", "grow_down", "move_box", "row_columns", "single_row"]
