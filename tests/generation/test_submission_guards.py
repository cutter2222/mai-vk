"""Защиты, найденные на колодах для сдачи (29.09.2026): заглушки модели, оборванные маркеры
фактов, пустое оглавление."""

from __future__ import annotations

import pytest

from presentation_designer.generation import variants as vr
from presentation_designer.generation.capacity import substitute_facts


@pytest.mark.parametrize(
    "raw",
    [
        {"title": "Test", "text": "Test text"},
        {"title": "Итоги", "items": [{"text": "Lorem ipsum dolor sit amet"}]},
        {"title": "Тест"},
        {"title": "Рост", "message": "TBD"},
    ],
)
def test_placeholder_answers_are_rejected(raw: dict) -> None:
    assert vr._placeholder_answer(raw)


@pytest.mark.parametrize(
    "raw",
    [
        {"title": "Тестирование гипотез", "text": "Тест производительности прошёл"},
        {"title": "Открываемость выросла", "items": [{"text": "Текст уведомления короче"}]},
    ],
)
def test_real_content_is_not_a_placeholder(raw: dict) -> None:
    assert not vr._placeholder_answer(raw)


def test_broken_fact_marker_never_reaches_the_slide() -> None:
    facts = {"f3": {"raw": "31 %", "value": 31}}
    assert substitute_facts("рост с {fact", facts) == "рост с"
    assert substitute_facts("рост с {fact:f", facts) == "рост с"
    assert substitute_facts("рост с {fact:f3}", facts) == "рост с 31 %"
    # Неизвестная, но целая ссылка остаётся — её ловит валидатор ссылок.
    assert substitute_facts("до {fact:f9}", facts) == "до {fact:f9}"


def test_agenda_without_items_is_not_filled() -> None:
    title_only = vr.Draft(kind="agenda", theses=[], pattern=None, title="Содержание")  # type: ignore[arg-type]
    title_only.blocks = [{"slot_id": "title_1", "kind": "title", "text": "Содержание"}]
    assert not vr._agenda_filled(title_only)
    title_only.blocks.append({"slot_id": "label_1", "kind": "label", "text": "Решение"})
    assert vr._agenda_filled(title_only)
