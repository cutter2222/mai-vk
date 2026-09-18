"""Дозапрос на пустые слоты: модель дописывает, код проверяет."""

from __future__ import annotations

import json

from presentation_designer.design.refill import (
    _capacity_chars,
    apply_answer,
    collect_gaps,
    refill,
)


def _slot(slot_id, kind, w, h, size_pt=12.0):
    return {
        "slot_id": slot_id,
        "kind": kind,
        "bbox": {"x": 0.1, "y": 0.1, "width": w, "height": h},
        "font": {"size_pt": size_pt},
    }


PATTERN = {
    "pattern_id": "cards_3",
    "role": "cards",
    "slots": [
        _slot("title_1", "title", 0.74, 0.14, 24.0),
        _slot("body_1", "body", 0.27, 0.16),
        _slot("body_2", "body", 0.27, 0.16),
        _slot("body_3", "body", 0.27, 0.16),
    ],
}
PROFILE = {"patterns": [PATTERN], "placeholder_markers": ["Заголовок", "Описание"]}


def _plan():
    return {
        "slides": [
            {
                "slide_id": "s7",
                "pattern_id": "cards_3",
                "title": "120 перевозчиков делают 3400 рейсов",
                "blocks": [
                    {"slot_id": "title_1", "kind": "title", "text": "120 перевозчиков"},
                    {"slot_id": "body_1", "kind": "body", "text": "120 подключено"},
                ],
            }
        ]
    }


def test_вместимость_считается_по_рамке_и_кеглю():
    assert _capacity_chars(_slot("b", "body", 0.27, 0.16)) > 20
    assert _capacity_chars({"slot_id": "x", "kind": "body"}) is None


def test_задание_содержит_только_пустые_слоты_с_вместимостью():
    tasks = collect_gaps(_plan(), {"cards_3": PATTERN}, limit=24)
    assert len(tasks) == 1
    slots = {g["slot_id"] for g in tasks[0]["empty_slots"]}
    assert slots == {"body_2", "body_3"}, "заполненные и заголовок не запрашиваются"
    assert all(g["max_chars"] > 0 for g in tasks[0]["empty_slots"])
    assert tasks[0]["filled"], "модель должна видеть, что уже написано"


def test_заголовки_и_числа_не_запрашиваются():
    pattern = {**PATTERN, "slots": PATTERN["slots"] + [_slot("number_1", "number", 0.2, 0.1)]}
    tasks = collect_gaps(_plan(), {"cards_3": pattern}, limit=24)
    kinds = {g["kind"] for g in tasks[0]["empty_slots"]}
    assert "number" not in kinds and "title" not in kinds


def test_ответ_модели_принимается_после_проверки():
    plan = _plan()
    taken = apply_answer(
        plan, {"cards_3": PATTERN}, {"s7": {"body_2": "3400 рейсов ежемесячно"}}, set()
    )
    assert taken == [("s7", "body_2")]
    added = [b for b in plan["slides"][0]["blocks"] if b.get("source") == "design.refill"]
    assert added[0]["text"] == "3400 рейсов ежемесячно"


def test_образец_шаблона_от_модели_отвергается():
    plan = _plan()
    apply_answer(plan, {"cards_3": PATTERN}, {"s7": {"body_2": "Описание"}}, {"Описание"})
    assert not [b for b in plan["slides"][0]["blocks"] if b.get("source") == "design.refill"]


def test_повтор_уже_написанного_отвергается():
    plan = _plan()
    apply_answer(plan, {"cards_3": PATTERN}, {"s7": {"body_2": "120 подключено"}}, set())
    assert not [b for b in plan["slides"][0]["blocks"] if b.get("source") == "design.refill"]


def test_слишком_длинный_ответ_отвергается():
    plan = _plan()
    apply_answer(plan, {"cards_3": PATTERN}, {"s7": {"body_2": "слово " * 200}}, set())
    assert not [b for b in plan["slides"][0]["blocks"] if b.get("source") == "design.refill"]


def test_сбой_модели_не_роняет_прогон():
    def broken(_system, _user):
        raise RuntimeError("провайдер недоступен")

    assert refill(_plan(), PROFILE, broken, prompt="p") == []


def test_ответ_в_ограждении_разбирается():
    def fenced(_system, _user):
        return '```json\n{"s7": {"body_2": "3400 рейсов в месяц"}}\n```'

    plan = _plan()
    taken = refill(plan, PROFILE, fenced, prompt="p")
    assert taken == [("s7", "body_2")]


def test_модель_видит_вместимость_в_задании():
    captured = {}

    def spy(_system, user):
        captured["user"] = json.loads(user)
        return "{}"

    refill(_plan(), PROFILE, spy, prompt="p")
    slot = captured["user"]["slides"][0]["empty_slots"][0]
    assert "max_chars" in slot and slot["max_chars"] > 0


def test_пересказ_заголовка_отвергается():
    """Усечение заголовка формально не повтор, по смыслу — пустая строка.

    Замер 18.09.2026: под «Экономический эффект для партнёров» модель писала
    «Экономический эффект», и слайд оставался таким же пустым по смыслу.
    """
    plan = _plan()
    plan["slides"][0]["title"] = "Экономический эффект для партнёров"
    apply_answer(plan, {"cards_3": PATTERN}, {"s7": {"body_2": "Экономический эффект"}}, set())
    assert not [b for b in plan["slides"][0]["blocks"] if b.get("source") == "design.refill"]


def test_содержательная_подпись_принимается():
    plan = _plan()
    plan["slides"][0]["title"] = "Экономический эффект для партнёров"
    taken = apply_answer(
        plan, {"cards_3": PATTERN}, {"s7": {"body_2": "Выручка выросла на 18%"}}, set()
    )
    assert taken == [("s7", "body_2")]


def test_пересказ_с_пропущенным_словом_тоже_отвергается():
    """«Масштаб и активность» под «Масштаб подключения и активность».

    Подстрокой это не является, но сведений не добавляет — значит пересказ.
    """
    plan = _plan()
    plan["slides"][0]["title"] = "Масштаб подключения и активность"
    apply_answer(plan, {"cards_3": PATTERN}, {"s7": {"body_2": "Масштаб и активность"}}, set())
    assert not [b for b in plan["slides"][0]["blocks"] if b.get("source") == "design.refill"]


def test_служебные_слова_не_делают_текст_пересказом():
    plan = _plan()
    plan["slides"][0]["title"] = "Рост выручки для партнёров и клиентов"
    taken = apply_answer(
        plan, {"cards_3": PATTERN}, {"s7": {"body_2": "Плюс 18% за полгода"}}, set()
    )
    assert taken == [("s7", "body_2")]
