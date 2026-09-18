"""Слой design: полупустой слайд получает композицию по объёму содержания."""

from __future__ import annotations

import copy

import pytest

from presentation_designer.design import apply, fill_ratio
from presentation_designer.design.rules import thresholds_for


def _slot(slot_id: str, kind: str, w: float, h: float) -> dict:
    return {"slot_id": slot_id, "kind": kind, "bbox": {"x": 0.1, "y": 0.1, "width": w, "height": h}}


@pytest.fixture
def profile() -> dict:
    """Два паттерна одного назначения: тесный и на двадцать шесть чисел.

    Повторяет случай из шаблона VK Tech, на котором слой и понадобился.
    """
    return {
        "patterns": [
            {
                "pattern_id": "numbers_2",
                "role": "numbers",
                "slots": [
                    _slot("title_1", "title", 0.74, 0.14),
                    _slot("number_1", "number", 0.3, 0.2),
                ],
            },
            {
                "pattern_id": "numbers_26",
                "role": "numbers",
                "slots": [_slot("title_1", "title", 0.74, 0.14)]
                + [_slot(f"number_{i}", "number", 0.16, 0.06) for i in range(1, 17)]
                + [_slot(f"label_{i}", "label", 0.16, 0.06) for i in range(1, 11)],
            },
        ]
    }


@pytest.fixture
def story() -> dict:
    return {
        "theses": [
            {
                "thesis_id": "t3",
                "statement": "Простой снижен",
                "explanation": "Показатель за полгода пилота",
            }
        ]
    }


def _plan(pattern_id: str) -> dict:
    return {
        "variant": "balanced",
        "slides": [
            {
                "slide_id": "s4",
                "pattern_id": pattern_id,
                "thesis_refs": ["t3"],
                "blocks": [
                    {"slot_id": "title_1", "kind": "title", "text": "Простой снижен на 34%"},
                    {"slot_id": "number_1", "kind": "number", "text": "34%"},
                ],
            }
        ],
    }


def test_замер_заполненности_по_площади(profile):
    """Доля площади, а не число слотов: заголовок и подпись весят по-разному."""
    big = profile["patterns"][1]
    fill = fill_ratio(_plan("numbers_26")["slides"][0], big)
    assert fill < 0.35, f"два блока в паттерне на 26 чисел дали {fill:.0%}"


def test_паттерн_на_26_чисел_меняется_на_тесный(profile, story):
    """Главный случай: под один показатель выбран паттерн на двадцать шесть."""
    plan = _plan("numbers_26")
    out, report = apply(plan, profile, story, variant="balanced")
    assert out["slides"][0]["pattern_id"] == "numbers_2"
    assert any(d.action == "pattern_swap" for d in report.decisions)
    # Блоки переехали на слоты нового паттерна, ни один не потерян.
    kinds = {b["kind"] for b in out["slides"][0]["blocks"]}
    assert kinds == {"title", "number"}


def test_исходный_план_не_меняется(profile, story):
    """Слой возвращает копию: исходный документ нужен для сравнения и отката."""
    plan = _plan("numbers_26")
    snapshot = copy.deepcopy(plan)
    apply(plan, profile, story, variant="balanced")
    assert plan == snapshot


def test_заполненный_слайд_не_трогается(profile, story):
    plan = _plan("numbers_2")
    out, report = apply(plan, profile, story, variant="balanced")
    assert out["slides"][0]["pattern_id"] == "numbers_2"
    assert all(d.action == "keep" for d in report.decisions)


def test_добор_берёт_пояснение_тезиса_а_не_выдумывает(profile, story):
    """Слой не сочиняет: пустой слот получает текст из StoryPlan или остаётся пуст."""
    plan = _plan("numbers_26")
    plan["slides"][0]["blocks"].append({"slot_id": "label_1", "kind": "label", "text": ""})
    out, _ = apply(plan, profile, story, variant="balanced")
    added = [b for b in out["slides"][0]["blocks"] if b.get("source") == "design.enrich"]
    for block in added:
        assert block["text"] in {"Показатель за полгода пилота"}


def test_числа_и_заголовки_не_добираются(profile, story):
    """Число обязано прийти из фактов (Приложение 1, вопрос 4)."""
    plan = _plan("numbers_26")
    out, _ = apply(plan, profile, story, variant="balanced")
    added = [b for b in out["slides"][0]["blocks"] if b.get("source") == "design.enrich"]
    assert all(b["kind"] not in {"title", "number"} for b in added)


def test_пороги_у_вариантов_разные():
    """ТЗ п.2.5: плотный вариант требует большей заполненности."""
    compact = thresholds_for("compact")
    detailed = thresholds_for("detailed")
    assert compact.min_fill < detailed.min_fill


def test_неизвестный_вариант_не_роняет_генерацию():
    assert thresholds_for("невиданный") == thresholds_for("balanced")


def test_пороги_читаются_из_конфига():
    """ТЗ п.4: запуск конфиг-файлом, пороги не зашиты в код."""
    limits = thresholds_for("balanced", {"design": {"variants": {"balanced": {"min_fill": 0.9}}}})
    assert limits.min_fill == 0.9


def test_не_добираем_текст_который_не_влезет(profile, story):
    """Пустой слот лучше, чем строки, наезжающие друг на друга.

    Замер 18.09.2026: добор без проверки вместимости положил длинное пояснение
    в узкую подпись, и строки наложились — слайд стал хуже, чем был.
    """
    from presentation_designer.design.fit import _fits

    # Рамка на две строки кеглем 12pt: короткое влезает, длинное нет.
    slot = _slot("label_1", "label", 0.16, 0.06)
    slot["font"] = {"size_pt": 12.0}
    assert _fits(slot, "34%") is True
    assert _fits(slot, "Это основной показатель эффективности платформы.") is not True


def test_избыточный_кандидат_отвергается(story):
    """Замена обязана решать задачу, а не уменьшать пустоту наполовину."""
    prof = {
        "patterns": [
            {
                "pattern_id": "numbers_26",
                "role": "numbers",
                "slots": [_slot("title_1", "title", 0.74, 0.14)]
                + [_slot(f"number_{i}", "number", 0.16, 0.06) for i in range(1, 27)],
            },
            {
                "pattern_id": "numbers_11",
                "role": "numbers",
                "slots": [_slot("title_1", "title", 0.74, 0.14)]
                + [_slot(f"number_{i}", "number", 0.18, 0.06) for i in range(1, 12)],
            },
        ]
    }
    out, report = apply(_plan("numbers_26"), prof, story, variant="balanced")
    assert out["slides"][0]["pattern_id"] == "numbers_26", (
        "одиннадцать слотов под два блока — тоже перебор"
    )
    assert any("избыточен" in d.reason for d in report.decisions)
