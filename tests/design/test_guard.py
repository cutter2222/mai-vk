"""Застава качества: брак не доходит до вёрстки.

Случаи взяты из колоды от 18.09.2026, где всё это ушло в выдачу незамеченным:
слово «Заголовок» четыре раза, одно предложение в трёх слотах с наложением,
текст на 72pt в рамке высотой 34pt.
"""

from __future__ import annotations

from presentation_designer.design.guard import (
    drop_duplicates,
    drop_placeholders,
    enforce_capacity,
    guard,
)


def _slot(slot_id: str, kind: str, w: float, h: float, size_pt: float = 12.0, sample: str = ""):
    return {
        "slot_id": slot_id,
        "kind": kind,
        "bbox": {"x": 0.1, "y": 0.1, "width": w, "height": h},
        "font": {"size_pt": size_pt},
        "sample_text": sample,
    }


def test_образец_шаблона_не_уходит_в_выдачу():
    """Слайд 10: «Заголовок» ×4 — это незаполненная форма, а не содержание."""
    pattern = {
        "slots": [_slot(f"body_{i}", "body", 0.2, 0.2, sample="Заголовок") for i in range(1, 5)]
    }
    slide = {
        "blocks": [
            {"slot_id": f"body_{i}", "kind": "body", "text": "Заголовок"} for i in range(1, 5)
        ]
    }
    dropped = drop_placeholders(slide, pattern, {"Заголовок"})
    assert len(dropped) == 4
    assert slide["blocks"] == []


def test_образец_опознаётся_и_по_списку_профиля():
    """`placeholder_markers` анализатор уже собрал — надо им пользоваться."""
    pattern = {"slots": [_slot("body_1", "body", 0.3, 0.3)]}
    slide = {"blocks": [{"slot_id": "body_1", "kind": "body", "text": "Имя Фамилия"}]}
    assert drop_placeholders(slide, pattern, {"Имя Фамилия"}) == ["body_1"]


def test_настоящее_содержание_не_трогается():
    pattern = {"slots": [_slot("body_1", "body", 0.3, 0.3, sample="Заголовок")]}
    slide = {"blocks": [{"slot_id": "body_1", "kind": "body", "text": "Простой снижен на 34%"}]}
    assert drop_placeholders(slide, pattern, {"Заголовок"}) == []


def test_повтор_формулировки_на_слайде_убирается():
    """Слайд 9: одно предложение в трёх слотах наложилось само на себя."""
    text = "Платформа обеспечивает рост доходов участников логистической цепи."
    slide = {"blocks": [{"slot_id": f"body_{i}", "kind": "body", "text": text} for i in (1, 2, 5)]}
    dropped = drop_duplicates(slide)
    assert dropped == ["body_2", "body_5"]
    assert len(slide["blocks"]) == 1


def test_повтор_считается_без_учёта_регистра_и_подстановок():
    slide = {
        "blocks": [
            {"slot_id": "a", "kind": "body", "text": "Выручка выросла на {fact:f5}"},
            {"slot_id": "b", "kind": "body", "text": "выручка  выросла на"},
        ]
    }
    assert drop_duplicates(slide) == ["b"]


def test_не_влезающий_текст_сокращается_до_первого_предложения():
    pattern = {"slots": [_slot("body_1", "body", 0.2, 0.06, size_pt=12.0)]}
    long = "Короткая мысль. А дальше идёт длинное продолжение, которое в слот не помещается никак."
    slide = {"blocks": [{"slot_id": "body_1", "kind": "body", "text": long}]}
    enforce_capacity(slide, pattern)
    assert slide["blocks"][0]["text"] == "Короткая мысль."
    assert slide["blocks"][0]["shortened_by"] == "design.guard"


def test_заголовок_никогда_не_удаляется():
    """Слайд без названия хуже, чем слайд с тесным названием."""
    pattern = {"slots": [_slot("title_1", "title", 0.1, 0.02, size_pt=48.0)]}
    slide = {
        "blocks": [
            {"slot_id": "title_1", "kind": "title", "text": "Очень длинное название презентации"}
        ]
    }
    changes = enforce_capacity(slide, pattern)
    assert len(slide["blocks"]) == 1, "заголовок обязан остаться"
    assert any("сохранён" in what for _, what in changes)


def test_все_три_проверки_отчитываются_отдельно():
    pattern = {
        "slots": [
            _slot("title_1", "title", 0.7, 0.14, size_pt=24.0),
            _slot("body_1", "body", 0.2, 0.2, sample="Заголовок"),
            _slot("body_2", "body", 0.2, 0.2),
            _slot("body_3", "body", 0.2, 0.2),
        ]
    }
    slide = {
        "slide_id": "s9",
        "blocks": [
            {"slot_id": "title_1", "kind": "title", "text": "Итоги"},
            {"slot_id": "body_1", "kind": "body", "text": "Заголовок"},
            {"slot_id": "body_2", "kind": "body", "text": "Одно и то же"},
            {"slot_id": "body_3", "kind": "body", "text": "Одно и то же"},
        ],
    }
    actions = {d.action for d in guard(slide, pattern, {"Заголовок"})}
    assert "drop_placeholder" in actions and "drop_duplicate" in actions


def test_нет_данных_о_слоте_не_повод_удалять_содержание():
    """Неизвестно ≠ не влезает.

    Первая версия считала отсутствие кегля отказом и вычищала содержание из
    слотов, о которых профиль ничего не сообщил.
    """
    from presentation_designer.design.fit import _fits

    blind = {
        "slot_id": "body_1",
        "kind": "body",
        "bbox": {"x": 0, "y": 0, "width": 0.3, "height": 0.3},
    }
    assert _fits(blind, "любой текст") is None

    pattern = {"slots": [blind]}
    slide = {
        "blocks": [
            {"slot_id": "body_1", "kind": "body", "text": "Содержание, которое нельзя терять"}
        ]
    }
    enforce_capacity(slide, pattern)
    assert len(slide["blocks"]) == 1
