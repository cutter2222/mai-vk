"""Контекстные проверки: что спрашивают у модели и как её ответ становится находкой.

Модель в тестах — заглушка транспорта: проверяется не качество ответов, а договор вокруг них.
Вопрос задаётся только там, где его есть к чему применить; «нет» становится находкой нужной
проверки; «не уверен», молчание и сбой вызова дают `not_checked` с причиной, а не «пройдено».
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from presentation_designer.audit.contextual import run_contextual_checks
from presentation_designer.audit.report import build_report
from presentation_designer.contracts import models as m
from presentation_designer.contracts import validators as v
from presentation_designer.llm.client import LlmClient
from presentation_designer.llm.skills import get_skill
from presentation_designer.llm.stub import StubTransport
from presentation_designer.llm.types import LlmError, Request

JsonDict = dict[str, Any]

PNG = b"\x89PNG\r\n\x1a\nfake"


def _text(object_id: str, text: str, **extra: Any) -> JsonDict:
    return {
        "object_id": object_id,
        "kind": "text",
        "role": "content",
        "bbox": {"x": 0.05, "y": 0.1, "width": 0.9, "height": 0.2},
        "text": {"plain": text},
        "content_source": "plan",
        **extra,
    }


def _deck(slides: list[JsonDict] | None = None) -> JsonDict:
    if slides is None:
        slides = [
            {
                "slide_id": "s1",
                "index": 0,
                "title": "Выручка выросла вдвое за год",
                "objects": [
                    _text("10", "Выручка выросла вдвое за год", slot_kind="title"),
                    _text("11", "12,5 млн ₽ экономии за год"),
                ],
            },
            {
                "slide_id": "s2",
                "index": 1,
                "title": "Как это устроено",
                "objects": [
                    _text("20", "Как это устроено", slot_kind="title"),
                    {
                        "object_id": "21",
                        "kind": "picture",
                        "role": "content",
                        "bbox": {"x": 0.1, "y": 0.4, "width": 0.3, "height": 0.3},
                        "content_source": "plan",
                    },
                ],
            },
        ]
    return {"deck_id": "deck_1", "variant_id": "balanced", "revision": 1, "slides": slides}


PACKAGE = {"facts": [{"raw": "12,5 млн ₽", "label": "экономия за год", "context": {}}]}
STORY = {"language": "ru", "key_takeaway": "Сервис окупается за год"}


def _asked(request: Request) -> list[int]:
    """Номера вопросов из текста запроса: по ним видно, о чём модель вообще спросили."""
    body = "\n".join(msg.text for msg in request.messages)
    block = body.split("Вопросы:", 1)[-1]
    return [int(n) for n in re.findall(r"^(\d+)\.", block, flags=re.MULTILINE)]


def _answers(request: Request, answer: str = "yes", **per_question: dict[str, Any]) -> JsonDict:
    items = []
    for n in _asked(request):
        item: JsonDict = {"q": n, "answer": answer, "confidence": 0.9, "why": "так на слайде"}
        item.update(per_question.get(str(n), {}))
        items.append(item)
    return {"items": items}


def _client(make_client: Callable[..., LlmClient], responder: Any) -> LlmClient:
    stub = StubTransport()
    stub.on(lambda _r: True, responder)
    return make_client(stub)


def _run(client: LlmClient, deck: JsonDict | None = None, **kwargs: Any) -> Any:
    return run_contextual_checks(
        deck if deck is not None else _deck(),
        PACKAGE,
        STORY,
        images=kwargs.pop("images", {0: PNG, 1: PNG}),
        client=client,
        skill=get_skill("auditor"),
        **kwargs,
    )


def test_question_is_not_asked_where_there_is_nothing_to_check(
    make_client: Callable[..., LlmClient],
) -> None:
    """Вопрос про таблицу слайду без таблицы — не «пройден», а неприменим.

    Иначе покрытие росло бы за счёт вопросов, которые никто не проверял, а модель тратила бы
    время задания на пустое.
    """
    asked: list[list[int]] = []

    def respond(req: Request, _attempt: int) -> JsonDict:
        asked.append(_asked(req))
        return _answers(req)

    result = _run(_client(make_client, respond))

    assert result.outcomes[("content.table_works", 0)] == (
        "not_applicable",
        "на слайде нет таблицы и диаграммы",
    )
    assert result.outcomes[("content.visuals_relevant", 0)][0] == "not_applicable"
    # На втором слайде картинка есть — про неё спрашивают.
    assert ("content.visuals_relevant", 1) not in result.outcomes
    assert 10 not in asked[0] and 6 not in asked[0]
    assert 6 in asked[1]
    assert result.issues == []


def test_no_becomes_issue_of_its_check(make_client: Callable[..., LlmClient]) -> None:
    """«Нет» на вопрос о фактах — находка проверки content.facts_grounded с объяснением."""

    def respond(req: Request, _attempt: int) -> JsonDict:
        return _answers(
            req,
            **{
                "4": {
                    "answer": "no",
                    "why": "числа 12,5 млн ₽ нет в материалах",
                    "confidence": 0.8,
                }
            },
        )

    result = _run(_client(make_client, respond))

    facts = [i for i in result.issues if i.check_id == "content.facts_grounded"]
    assert len(facts) == 2, "вопрос задан каждому слайду"
    assert facts[0].slide_id == "s1" and facts[0].slide_index == 0
    assert "12,5 млн ₽" in facts[0].message
    assert facts[0].evidence["answer"] == "no"


def test_unsure_is_not_a_pass(make_client: Callable[..., LlmClient]) -> None:
    """Модель не разобрала слайд: проверка остаётся непроверенной, а не пройденной."""

    def respond(req: Request, _attempt: int) -> JsonDict:
        return _answers(req, **{"8": {"answer": "unsure", "why": "текст мелкий"}})

    result = _run(_client(make_client, respond))

    assert result.outcomes[("content.no_typos", 0)] == ("not_checked", "модель не уверена в ответе")
    assert not [i for i in result.issues if i.check_id == "content.no_typos"]


def test_missing_answer_is_not_a_pass(make_client: Callable[..., LlmClient]) -> None:
    """Модель промолчала про вопрос: он тоже не становится пройденным."""

    def respond(req: Request, _attempt: int) -> JsonDict:
        answers = _answers(req)
        answers["items"] = [item for item in answers["items"] if item["q"] != 3]
        return answers

    result = _run(_client(make_client, respond))
    assert result.outcomes[("content.one_sentence", 0)] == (
        "not_checked",
        "модель не ответила на вопрос",
    )


def test_deck_questions_are_asked_once_for_the_whole_deck(
    make_client: Callable[..., LlmClient],
) -> None:
    """Язык и связность соседей по одному слайду не проверить: для них отдельный вызов."""
    seen: list[list[int]] = []

    def respond(req: Request, _attempt: int) -> JsonDict:
        seen.append(_asked(req))
        return _answers(
            req,
            **{"11": {"answer": "no", "why": "слайд 2 не продолжает первый", "slide": 2}},
        )

    result = _run(_client(make_client, respond))

    deck_calls = [ids for ids in seen if 9 in ids]
    assert len(deck_calls) == 1 and sorted(deck_calls[0]) == [9, 11]
    assert all(9 not in ids for ids in seen if ids is not deck_calls[0])
    connected = [i for i in result.issues if i.check_id == "content.slides_connected"]
    assert len(connected) == 1
    assert connected[0].slide_id == "s2", "находка показана на том слайде, который выпал"


def test_single_slide_deck_has_no_neighbours(make_client: Callable[..., LlmClient]) -> None:
    one = _deck()
    one["slides"] = one["slides"][:1]

    result = _run(_client(make_client, lambda req, _a: _answers(req)), deck=one, images={0: PNG})

    assert result.outcomes[("content.slides_connected", None)] == (
        "not_applicable",
        "в колоде один слайд: сравнивать не с чем",
    )


def test_failed_call_leaves_reason_not_silence(make_client: Callable[..., LlmClient]) -> None:
    """Провайдер не ответил: проверки этой области непроверены с причиной, аудит не падает."""

    def respond(_req: Request, _attempt: int) -> JsonDict:
        raise LlmError("провайдер недоступен")

    result = _run(_client(make_client, respond))

    assert result.issues == [] and result.answers == []
    outcome, reason = result.outcomes[("content.title_is_takeaway", 0)]
    assert outcome == "not_checked" and "вызов модели не выполнен" in reason
    assert "vlm_error" in result.missing_inputs
    assert result.ran is True, "слой отработал, и это не то же самое, что выключенная проверка"


def test_no_render_keeps_visual_question_unchecked(
    make_client: Callable[..., LlmClient],
) -> None:
    """Миниатюр нет (рендерер недоступен): о картинках модель не спрашивают, текст проверяют."""
    result = _run(_client(make_client, lambda req, _a: _answers(req)), images={})

    assert result.outcomes[("content.visuals_relevant", 1)] == (
        "not_checked",
        "нет картинки слайда: смотреть не на что",
    )
    assert "slide_render" in result.missing_inputs
    assert result.answers, "остальные вопросы заданы по тексту"


def test_report_carries_answers_and_honest_coverage(
    make_client: Callable[..., LlmClient],
) -> None:
    """Отчёт с контекстной частью: ответы внутри, покрытие различает неприменимое и
    непроверенное, документ проходит схему и правила контрактов."""

    def respond(req: Request, _attempt: int) -> JsonDict:
        return _answers(req, **{"7": {"answer": "no", "why": "на слайде реплика докладчика"}})

    deck = _deck()
    result = _run(_client(make_client, respond), deck=deck)
    report = build_report(
        job_id="job_1",
        variant_id="balanced",
        revision=1,
        deck=deck,
        profile={},
        staging_prefix="jobs/job_1/balanced/r1/",
        contextual=result.ran,
        contextual_answers=result.answers,
        contextual_issues=result.issues,
        contextual_outcomes=result.outcomes,
        missing_inputs=result.missing_inputs,
        llm_metrics=result.metrics,
    )

    model = m.AuditReport.model_validate(report)
    assert v.check_audit_report(model) == []
    assert report["summary"]["by_kind"]["contextual"] == 2, "по находке на каждом слайде"
    assert report["metrics"]["llm_calls"] == 3, "два слайда и колода целиком"
    garbage = [r for r in report["results"] if r["check_id"] == "content.no_garbage"]
    assert [r["outcome"] for r in garbage] == ["failed", "failed"]
    table = [r for r in report["results"] if r["check_id"] == "content.table_works"]
    assert all(r["outcome"] == "not_applicable" and r["reason"] for r in table)
    assert report["coverage"]["not_applicable"] >= 2
    answers = report["contextual_answers"]
    assert any(a["question_id"] == 7 and a["outcome"] == "failed" for a in answers)


def test_contextual_off_marks_checks_with_reason(make_client: Callable[..., LlmClient]) -> None:
    """Без контекстной части отчёт не притворяется полным: у каждой такой проверки причина."""
    deck = _deck()
    report = build_report(
        job_id="job_1",
        variant_id="balanced",
        revision=1,
        deck=deck,
        profile={},
        staging_prefix="jobs/job_1/balanced/r1/",
        contextual=False,
        missing_inputs=["vlm_unavailable"],
    )
    model = m.AuditReport.model_validate(report)
    assert v.check_audit_report(model) == []
    content = [r for r in report["results"] if r["check_id"].startswith("content.")]
    assert content and all(r["outcome"] == "not_checked" and r["reason"] for r in content)
    assert report["coverage"]["complete"] is False
