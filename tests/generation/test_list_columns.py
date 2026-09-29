"""Пункты списка по колонкам: порядок сохраняется, длина текста в колонках близка."""

from __future__ import annotations

from itertools import pairwise

import pytest

from presentation_designer.generation.variants import balanced_chunks


@pytest.mark.parametrize(
    ("lengths", "columns", "expected"),
    [
        # Два абзаца и две короткие строки: не «два слева, два справа», а 1 и 3.
        ([250, 230, 30, 40], 2, [(0, 1), (1, 4)]),
        ([50, 50, 50, 50], 2, [(0, 2), (2, 4)]),
        ([5, 5, 5, 5, 5, 5], 3, [(0, 2), (2, 4), (4, 6)]),
        ([100], 2, [(0, 1)]),
        ([10, 400], 2, [(0, 1), (1, 2)]),
    ],
)
def test_balanced_chunks(lengths: list[int], columns: int, expected: list[tuple[int, int]]) -> None:
    chunks = balanced_chunks(lengths, columns)
    assert chunks == expected
    # Все пункты разложены по порядку, ни один не потерян и не повторён.
    assert chunks[0][0] == 0 and chunks[-1][1] == len(lengths)
    assert all(a[1] == b[0] for a, b in pairwise(chunks))
