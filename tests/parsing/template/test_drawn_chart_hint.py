"""Подсказка зрения о нарисованной диаграмме: роль chart принимается только с рядом.

Ветка редкая и дорогая в проверке вживую (нужен VLM), поэтому проверяется на заглушке модели:
одному образцу модель говорит «chart», и ряд столбиков там есть — роль принимается, слоты
столбиков заменяются одним слотом диаграммы. Другому она говорит то же самое, но ряда нет —
роль отвергается и остаётся эвристика, ничего не выдумывается.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from presentation_designer.llm.client import LlmClient
from presentation_designer.llm.skills import get_skill
from presentation_designer.llm.stub import StubTransport
from presentation_designer.llm.types import Request
from presentation_designer.parsing.template.package import SlideInfo
from presentation_designer.parsing.template.patterns import (
    Pattern,
    Slot,
    attach_drawn_chart,
    refine_roles_with_vlm,
)
from presentation_designer.parsing.template.tone import UNKNOWN
from tests.parsing.template.test_drawn_charts import _row, _shape


def _pattern(pattern_id: str, shapes: list[Any], *, role: str = "cards") -> Pattern:
    slide = SlideInfo(
        index=int(pattern_id[-1]),
        part=f"ppt/slides/slide{pattern_id[-1]}.xml",
        layout_id="slideLayout1",
        master_id="slideMaster1",
        hidden=False,
        shapes=shapes,
        notes_text="",
        element=None,
    )
    slots = [Slot(f"body_{i + 1}", "body", s) for i, s in enumerate(shapes)]
    return Pattern(
        pattern_id=pattern_id,
        slide=slide,
        role=role,
        role_source="heuristic",
        confidence=0.6,
        name=pattern_id,
        slots=slots,
        static_ids=[],
        removable_ids=[],
        repeat_counts={},
        group_id=pattern_id,
        tone=UNKNOWN,
    )


def test_attach_drawn_chart_replaces_row_with_one_slot() -> None:
    pattern = _pattern("pat_s1", _row([0.5, 0.35, 0.42]))
    assert attach_drawn_chart(pattern) is True
    kinds = [s.kind for s in pattern.slots]
    assert kinds == ["chart"], "столбики перестали быть слотами, остался один слот диаграммы"
    assert pattern.slots[0].bbox_override is not None
    assert len(pattern.chart_parts) == 2, "остальные столбики уберёт вёрстка"
    # Повторный вызов ничего не портит: ряд уже заменён.
    assert attach_drawn_chart(pattern) is True and len(pattern.slots) == 1


def test_attach_drawn_chart_refuses_cards() -> None:
    """Ряд одинаковых плашек графиком не становится, даже когда его так назвали."""
    pattern = _pattern("pat_s2", _row([0.4, 0.4, 0.4, 0.4]))
    assert attach_drawn_chart(pattern) is False
    assert not pattern.chart_parts and all(s.kind == "body" for s in pattern.slots)


def test_vlm_chart_hint_is_checked_against_shapes(
    make_client: Callable[..., LlmClient],
) -> None:
    """Модель обоим образцам ставит chart: у первого ряд есть, у второго — карточки."""
    with_row = _pattern("pat_s1", _row([0.5, 0.35, 0.42]))
    cards = [*_row([0.4, 0.4, 0.4, 0.4]), _shape("99", 0.1, 0.1, 0.3, 0.1)]
    without_row = _pattern("pat_s2", cards)
    groups = {"pat_s1": [with_row], "pat_s2": [without_row]}
    previews = {1: b"\x89PNG\r\n\x1a\nfake", 2: b"\x89PNG\r\n\x1a\nfake"}

    stub = StubTransport()

    def respond(req: Request, _attempt: int) -> Any:
        n = "\n".join(m.text for m in req.messages).count("макет «")
        return {
            "items": [{"n": i + 1, "role": "chart", "confidence": 0.9} for i in range(n)],
        }

    stub.on(lambda _r: True, respond)
    summary = refine_roles_with_vlm(
        groups, previews, make_client(stub), get_skill("template_analyzer"), batch=6
    )

    assert summary["asked"] == 2
    assert with_row.role == "chart" and with_row.role_source == "vlm"
    assert [s.kind for s in with_row.slots] == ["chart"]
    assert summary["drawn_charts"] == 1
    assert without_row.role == "cards", "без ряда роль chart отвергнута"
    assert summary["rejected"] == 1


def test_ring_row_is_not_one_chart(make_client: Callable[..., LlmClient]) -> None:
    """Три одинаковых кольца с процентами: зрение зовёт их графиком, и формально права —
    но одна нативная диаграмма встанет на место одной картинки, а соседние останутся
    нарисованными. Пока собирать кольца нечем, роль chart не принимается."""
    rings = [
        _shape("50", 0.06, 0.35, 0.22, 0.39, kind="picture"),
        _shape("51", 0.39, 0.35, 0.22, 0.39, kind="picture"),
        _shape("52", 0.72, 0.35, 0.22, 0.39, kind="picture"),
    ]
    pattern = _pattern("pat_s3", rings)
    for slot, shape in zip(pattern.slots, rings, strict=True):
        slot.kind = "image"
        _ = shape
    stub = StubTransport()
    answer = {"items": [{"n": 1, "role": "chart", "confidence": 0.95}]}
    stub.on(lambda _r: True, lambda _req, _a: answer)
    summary = refine_roles_with_vlm(
        {"pat_s3": [pattern]},
        {3: b"\x89PNG\r\n\x1a\nfake"},
        make_client(stub),
        get_skill("template_analyzer"),
        batch=6,
    )
    assert summary["ring_rows"] == 1 and summary["rejected"] == 1
    assert pattern.role == "cards", "роль осталась эвристической"
