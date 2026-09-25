"""Проверка вёрстки готовых слайдов по картинке (скилл `visual_reviewer`).

Детерминированный аудит судит по файлу: рамки, кегли, координаты. Но рамка с автоподбором
растёт у рендерера, надпись образца бывает частью картинки, а «пустой» слайд с тремя
карточками по файлу выглядит заполненным. Модель со зрением смотрит на то же, что увидит
человек, и отвечает на короткий список вопросов о вёрстке (приём визуальной проверки
DeepPresenter). Найденное не только попадает в отчёт: конвейер перестраивает такие слайды на
других композициях (`pipeline/run.py`).

Замер 25.09.2026 на 16 размеченных слайдах незнакомых шаблонов: qwen3.8-27b верно отличила
слайд с дефектом от чистого в 15 случаях, 2–8 с на слайд; все слайды варианта идут
параллельно.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]

LAYOUT_CODES = ("clipped", "overlap", "tiny", "leftover", "empty")
REVIEW_SCHEMA: JsonDict = {
    "type": "object",
    "required": ["issues"],
    "properties": {
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["code", "where"],
                "properties": {
                    "code": {"type": "string", "enum": list(LAYOUT_CODES)},
                    "where": {"type": "string"},
                },
            },
        }
    },
}


@dataclass
class LayoutReview:
    """Итог проверки: дефекты по номерам слайдов (с нуля)."""

    ran: bool = False
    findings: dict[int, list[JsonDict]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    metrics: JsonDict = field(default_factory=dict)

    @property
    def defects(self) -> int:
        return sum(len(v) for v in self.findings.values())

    def as_dict(self) -> JsonDict:
        return {
            "ran": self.ran,
            "defects": self.defects,
            "slides": {str(k): v for k, v in sorted(self.findings.items())},
            "errors": self.errors,
            "metrics": self.metrics,
        }


def _request(skill: Any, index: int, total: int, image: bytes, deadline: Any) -> Any:
    from presentation_designer.llm.types import Image, Message

    req = skill.request(
        "review.layout", f"Слайд {index + 1} из {total}.", schema=REVIEW_SCHEMA, stage="audit"
    )
    req.messages[1] = Message("user", req.messages[1].text, (Image(image, "image/png"),))
    req.schema_name = "layout_review"
    req.deadline = deadline
    return req


def review_layout(
    images: dict[int, bytes],
    *,
    client: Any,
    skill: Any,
    concurrency: int = 8,
    deadline_s: float = 40.0,
) -> LayoutReview:
    """Спрашивает модель про каждый слайд параллельно. Сбой вызова не рушит вариант: слайд
    остаётся непроверенным, ошибка — в `errors`."""
    from presentation_designer.llm.types import Deadline

    result = LayoutReview()
    if not images or client is None or skill is None:
        return result
    deadline = Deadline.after(deadline_s)
    total = max(images) + 1
    order = sorted(images)
    gate = asyncio.Semaphore(max(1, concurrency))

    async def ask(index: int) -> Any:
        async with gate:
            return await client.complete(_request(skill, index, total, images[index], deadline))

    async def run_all() -> list[Any]:
        return await asyncio.gather(*(ask(i) for i in order), return_exceptions=True)

    outcomes = asyncio.run(run_all())
    result.ran = True
    calls = prompt_tokens = completion_tokens = 0
    for index, outcome in zip(order, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            result.errors.append(f"{index + 1}: {type(outcome).__name__}: {outcome}"[:200])
            continue
        calls += 1
        prompt_tokens += outcome.usage.prompt_tokens
        completion_tokens += outcome.usage.completion_tokens
        parsed = outcome.parsed if isinstance(outcome.parsed, dict) else {}
        issues = [
            {"code": str(i.get("code")), "where": str(i.get("where") or "")[:160]}
            for i in parsed.get("issues") or []
            if isinstance(i, dict) and i.get("code") in LAYOUT_CODES
        ]
        if issues:
            result.findings[index] = issues
    result.metrics = {
        "llm_calls": calls,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    return result
