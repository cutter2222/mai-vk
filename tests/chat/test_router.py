"""Роутер чата (этап 37): нормализатор чисел, адреса слайдов, правила и модель на записанных
ответах против золотого набора фраз `phrases.yaml`."""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
import yaml

from presentation_designer.generation import router
from presentation_designer.llm.skills import get_skill
from tests.llm.conftest import models, settings  # noqa: F401
from tests.parsing.content.conftest import replay_client  # noqa: F401  # фикстура модели

HERE = pathlib.Path(__file__).parent
DECK = json.loads((HERE / "deck.json").read_text(encoding="utf-8"))
PHRASES = yaml.safe_load((HERE / "phrases.yaml").read_text(encoding="utf-8"))


def context(case: dict[str, Any]) -> router.Context:
    chip_object = None
    if case.get("chip_object"):
        obj = next(
            o
            for o in DECK["slides"][case["chip_slide"] - 1]["objects"]
            if o["name"] == case["chip_object"]
        )
        chip_object = router.target_of(DECK, case["chip_slide"], obj["address"]["object_id"])
    return router.Context(
        text=case["text"],
        slide_count=len(DECK["slides"]),
        chip_slide=case.get("chip_slide"),
        chip_object=chip_object,
        pictures=[f"file_{n}" for n in range(case.get("pictures", 0))],
        sheets=[f"sheet_{n}" for n in range(case.get("sheets", 0))],
        can_rebuild=case.get("can_rebuild", True),
        snapshot=DECK,
    )


def matches(decision: router.Decision, expect: dict[str, Any]) -> bool:
    if decision.kind != expect["kind"]:
        return False
    if decision.kind != "run":
        return True
    step = decision.steps[0]
    if step.action not in expect["actions"]:
        return False
    slides = sorted({n for s in decision.steps for n in s.slides})
    if "slides" in expect and slides != expect["slides"]:
        return False
    if "object" in expect and step.action == "object_edit":
        return step.target is not None and step.target.name == expect["object"]
    return True


def test_numbers_in_words_become_digits() -> None:
    assert router.normalize("на слайде три") == "на слайде 3"
    assert router.normalize("на третьем слайде") == "на 3-й слайде"
    assert router.normalize("двадцать пятый слайд") == "25-й слайд"
    assert router.normalize("не три а две колонки") == "не 3 а 2 колонки"
    assert router.slides_in(router.normalize("сделай три слайда"), 8) == []


@pytest.mark.parametrize(
    ("text", "slides"),
    [
        ("на слайде 3", [3]),
        ("слайды 3, 5 и 7", [3, 5, 7]),
        ("на слайдах 2–4", [2, 3, 4]),
        ("на 3-м слайде", [3]),
        ("3-й и 5-й слайды", [3, 5]),
        ("на последнем слайде", [8]),
        ("на последнем", [8]),
        ("на титульном", [1]),
        ("10 слайдов", []),
    ],
)
def test_slide_addresses(text: str, slides: list[int]) -> None:
    assert router.slides_in(router.normalize(text), 8) == slides


RULES = [c for c in PHRASES if c["source"] == "rules"]
MODEL = [c for c in PHRASES if c["source"] == "model"]


@pytest.mark.parametrize("case", RULES, ids=[c["text"] for c in RULES])
def test_rules_decide_without_the_model(case: dict[str, Any]) -> None:
    decision = router.by_rules(context(case))
    assert decision is not None, "правило должно решить без модели"
    assert matches(decision, case["expect"]), decision.as_dict()


@pytest.mark.parametrize("case", MODEL, ids=[c["text"] for c in MODEL])
def test_rules_leave_the_rest_to_the_model(case: dict[str, Any]) -> None:
    assert router.by_rules(context(case)) is None


def test_golden_phrases_with_the_model(replay_client: Any) -> None:  # noqa: F811
    """Модель на записанных ответах; однозначные поддержанные фразы — без вопроса ≥ 90 %."""
    skill = get_skill("chat_router")
    clear_ok = clear_total = 0
    failures = []
    for case in MODEL:
        decision = router.decide(context(case), client=replay_client, skill=skill)
        assert decision.source == "model", f"нет записи ответа модели: {case['text']}"
        ok = matches(decision, case["expect"])
        if case.get("clear"):
            clear_total += 1
            clear_ok += ok
        if not ok:
            failures.append((case["text"], decision.as_dict()))
    clear_rules = sum(1 for c in RULES if c.get("clear"))
    share = (clear_ok + clear_rules) / (clear_total + clear_rules)
    assert share >= 0.9, (share, failures)
    assert len(failures) <= 3, failures


def test_model_answer_is_checked_against_the_snapshot() -> None:
    ctx = context({"text": "сократи заголовок на слайде 3"})
    decision = router.check(
        {"kind": "run", "steps": [{"action": "object_edit", "slides": [3], "object_id": "2",
                                   "instruction": "сократи заголовок"}], "text": "", "options": []},
        ctx,
    )  # fmt: skip
    assert decision.steps[0].target is not None
    assert decision.steps[0].target.name == "Title 1"
    assert decision.steps[0].target.box is not None
    with pytest.raises(ValueError, match="объекта 99 нет"):
        router.check(
            {"kind": "run", "steps": [{"action": "object_edit", "slides": [3], "object_id": "99",
                                       "instruction": "x"}], "text": "", "options": []},
            ctx,
        )  # fmt: skip
    with pytest.raises(ValueError, match="слайдов"):
        router.check(
            {"kind": "run", "steps": [{"action": "slide_rebuild", "slides": [12],
                                       "object_id": None, "instruction": "x"}], "text": "",
             "options": []},
            ctx,
        )  # fmt: skip


def test_without_a_model_a_named_slide_is_rebuilt_and_the_rest_goes_to_the_assistant() -> None:
    ctx = context({"text": "сократи заголовок на слайде 3"})
    assert router.decide(ctx).steps[0].action == "slide_rebuild"
    assert router.decide(context({"text": "поправь это"})).kind == "answer"
