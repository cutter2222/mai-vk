"""Повтор по ёмкости не должен улучшать метрики удалением содержания."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from presentation_designer.generation import variants as vr
from presentation_designer.llm.types import Request, Response
from tests.generation.test_number_content import _context


def _setup() -> tuple[vr.Context, vr.Packet, dict[str, Any]]:
    ctx = _context("balanced")
    ctx.theses = [vr.Thesis("t1", 1, "content", "Пилот", "", True, None, [], [], [], [], None)]
    packet = vr.Packet(0, ctx.theses, 1, 1, 2, {"t1": ctx.patterns})
    answer = {
        "slides": [
            {
                "theses": ["t1"],
                "pattern": "metrics",
                "title": "Пилот",
                "visual": "number",
                "facts": ["f1"],
                "items": [
                    {
                        "text": "Охват {fact:f1}",
                        "facts": ["f1"],
                        "sub": "Только после согласия пользователя",
                    },
                    {"text": "Риск нагрузки поддержки"},
                ],
                "text": "Результат относится только к участникам пилота",
                "message": "Масштабирование после проверки",
                "notes": "Проверить согласие перед запуском",
            }
        ],
        "rationale": "исходный",
    }
    return ctx, packet, answer


class Answers:
    def __init__(self, answers: list[dict[str, Any]]) -> None:
        self.answers = answers
        self.requests: list[Request] = []

    async def complete(self, req: Request, *, validator: Any) -> Response:
        answer = self.answers[len(self.requests)]
        self.requests.append(req)
        return Response(text=json.dumps(answer, ensure_ascii=False), parsed=validator(answer))


def _overflow(monkeypatch: pytest.MonkeyPatch) -> None:
    # Изолируем критерий принятия от метрик шрифтов. Реальную ёмкость проверяют
    # test_variants и test_number_content; здесь исходный ответ всегда переполнен.
    def fit(ctx: vr.Context, draft: vr.Draft) -> vr.Draft:
        draft.overflow = (
            [
                {
                    "kind": "text",
                    "slot_id": "text",
                    "chars": 200,
                    "lines": 8,
                    "max_chars": 50,
                    "max_lines": 2,
                }
            ]
            if draft.text
            else []
        )
        return draft

    monkeypatch.setattr(vr, "fit_draft", fit)


@pytest.mark.parametrize("lost", ["all", "sub", "risk", "facts", "message", "notes"])
async def test_capacity_retry_keeps_original_on_content_loss(
    monkeypatch: pytest.MonkeyPatch,
    lost: str,
) -> None:
    ctx, packet, first = _setup()
    retry = copy.deepcopy(first)
    slide = retry["slides"][0]
    if lost == "all":
        retry["slides"] = [{k: slide[k] for k in ("title", "theses", "pattern", "visual")}]
    elif lost == "sub":
        del slide["items"][0]["sub"]
    elif lost == "risk":
        slide["items"].pop()
    elif lost == "facts":
        slide["facts"] = []
        slide["items"][0] = {"text": "Охват", "sub": slide["items"][0]["sub"]}
    else:
        del slide[lost]
    retry["rationale"] = "удалено"
    client = Answers([first, retry])
    _overflow(monkeypatch)
    drafts, rationale, responses = await vr.fit_packet(ctx, packet, Request("llm", []), client)
    assert drafts[0].items == vr.drafts_from_answer(ctx, packet, first)[0].items
    assert rationale == "исходный"
    assert drafts[0].overflow, "Потеря содержания не должна скрыть исходное переполнение"
    assert len(responses) == len(client.requests) == 2
    assert any(f["code"] == "packet_retry_rejected_content" for f in ctx.fixes)


async def test_capacity_retry_accepts_relayout_without_content_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, packet, first = _setup()
    retry = copy.deepcopy(first)
    slide = retry["slides"][0]
    slide["items"].append({"text": slide.pop("text")})
    retry["rationale"] = "переразмещено"
    _overflow(monkeypatch)
    client = Answers([first, retry])
    drafts, rationale, _ = await vr.fit_packet(ctx, packet, Request("llm", []), client)
    assert not drafts[0].overflow
    assert rationale == "переразмещено"
    assert not any(f["code"] == "packet_retry_rejected_content" for f in ctx.fixes)


def test_retry_change_invalidates_plan_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx, _, _ = _setup()
    key = vr.plan_key(ctx.story, ctx.profile, ctx.variant_id, {})
    monkeypatch.setattr(vr, "PLAN_VERSION", "0.3.2")
    assert vr.plan_key(ctx.story, ctx.profile, ctx.variant_id, {}) != key


@pytest.mark.parametrize("change", ["negation", "notes_only", "thesis", "dataset", "image", "refs"])
def test_preservation_guard_rejects_unproved_changes(change: str) -> None:
    ctx, packet, answer = _setup()
    before = vr.drafts_from_answer(ctx, packet, answer)
    before[0].dataset = "ds1"
    before[0].columns = ["Год", "Доход"]
    before[0].image = "img1"
    after = copy.deepcopy(before)
    if change == "negation":
        after[0].items[1]["text"] = "Не " + after[0].items[1]["text"]
    elif change == "notes_only":
        after[0].notes += after[0].text
        after[0].text = ""
    elif change == "thesis":
        after[0].theses = ["t2"]
    elif change == "dataset":
        after[0].columns.reverse()
    elif change == "image":
        after[0].image = None
    else:
        before[0].facts.append("f2")  # ссылка без маркера в тексте тоже обязательна
    assert vr.retry_content_loss(before, after)


def test_preservation_allows_reordering_splitting_and_duplicate_removal() -> None:
    ctx, packet, answer = _setup()
    before = vr.drafts_from_answer(ctx, packet, answer)
    before[0].items.append(copy.deepcopy(before[0].items[1]))
    after = copy.deepcopy(before)
    second = copy.deepcopy(after[0])
    second.items = [after[0].items.pop(1)]
    after[0].items.pop()
    second.title = "Продолжение"
    after[0].text = "  РЕЗУЛЬТАТ относится   только к участникам пилота. "
    assert vr.retry_content_loss(before, [second, *after]) is None


async def test_rejected_retry_is_not_the_next_prompt_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, packet, first = _setup()
    bad = copy.deepcopy(first)
    bad["slides"][0]["items"] = []
    good = copy.deepcopy(first)
    good["slides"][0]["items"].append({"text": good["slides"][0].pop("text")})
    _overflow(monkeypatch)
    client = Answers([first, bad, good])
    drafts, _, responses = await vr.fit_packet(
        ctx,
        packet,
        Request("llm", []),
        client,
        capacity_retries=2,
    )
    assert not drafts[0].overflow
    assert len(responses) == 3
    assert client.requests[2].messages[-2].text == responses[0].text


async def test_preserved_retry_still_cannot_worsen_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, packet, first = _setup()
    retry = copy.deepcopy(first)
    retry["slides"][0]["items"].append({"text": "Дополнительный пункт"})
    retry["rationale"] = "хуже"
    _overflow(monkeypatch)
    fit = vr.fit_draft

    def worse(ctx: vr.Context, draft: vr.Draft) -> vr.Draft:
        result = fit(ctx, draft)
        if len(draft.items) > 2:
            result.overflow *= 2
        return result

    monkeypatch.setattr(vr, "fit_draft", worse)
    drafts, rationale, _ = await vr.fit_packet(
        ctx,
        packet,
        Request("llm", []),
        Answers([first, retry]),
    )
    assert len(drafts[0].overflow) == 1
    assert rationale == "исходный"


async def test_recorded_title_only_response_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "llm"
        / "37"
        / ("37c88d2779afe144c1997f6d575ca1a174c923660f9d5c9cfcd401b1634618f7.json")
    )
    recorded = json.loads(json.loads(path.read_text())["response"]["text"])
    ctx, packet, first = _setup()
    ctx.theses[0].id = "t2"
    other = copy.deepcopy(ctx.theses[0])
    other.id = "t4"
    ctx.theses.append(other)
    packet.target = packet.lo = packet.hi = 2
    packet.candidates = {tid: ctx.patterns for tid in ("t2", "t4")}
    second = copy.deepcopy(first["slides"][0])
    first["slides"][0]["theses"] = ["t2"]
    second["theses"] = ["t4"]
    first["slides"].append(second)
    _overflow(monkeypatch)
    drafts, rationale, responses = await vr.fit_packet(
        ctx,
        packet,
        Request("llm", []),
        Answers([first, recorded]),
    )
    assert all(d.items and d.text and d.overflow for d in drafts)
    assert rationale == "исходный" and len(responses) == 2
    assert any(f["code"] == "packet_retry_rejected_content" for f in ctx.fixes)
