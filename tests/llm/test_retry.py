"""Повторы: 429 с Retry-After, сеть, 5xx, негодный ответ; 4xx и deadline не повторяются."""

from __future__ import annotations

import pytest

from presentation_designer.llm.retry import (
    RetryPolicy,
    RetryTrace,
    classify_status,
    is_retryable,
    retry_async,
)
from presentation_designer.llm.types import (
    ConfigError,
    Deadline,
    DeadlineError,
    ProviderError,
    ResponseError,
)

POLICY = RetryPolicy(max_retries=3, base_s=0.01, max_s=0.05, jitter=0.0)


async def test_retries_then_succeeds() -> None:
    seen: list[int] = []

    async def fn(attempt: int) -> str:
        seen.append(attempt)
        if attempt < 3:
            raise ProviderError("429", status=429, retryable=True)
        return "ok"

    trace = RetryTrace()
    assert await retry_async(fn, policy=POLICY, trace=trace) == "ok"
    assert seen == [1, 2, 3] and trace.attempts == 3 and len(trace.errors) == 2


async def test_retry_after_has_priority() -> None:
    async def fn(attempt: int) -> str:
        if attempt == 1:
            raise ProviderError("429", status=429, retry_after_s=0.03, retryable=True)
        return "ok"

    trace = RetryTrace()
    await retry_async(fn, policy=POLICY, trace=trace)
    assert trace.slept_ms == 30


async def test_gives_up_after_max_retries() -> None:
    calls = 0

    async def fn(attempt: int) -> str:
        nonlocal calls
        calls += 1
        raise ProviderError("500", status=500, retryable=True)

    with pytest.raises(ProviderError):
        await retry_async(fn, policy=POLICY)
    assert calls == POLICY.max_retries + 1


async def test_non_retryable_raises_immediately() -> None:
    calls = 0

    async def fn(attempt: int) -> str:
        nonlocal calls
        calls += 1
        raise ConfigError("нет ключа")

    with pytest.raises(ConfigError):
        await retry_async(fn, policy=POLICY)
    assert calls == 1


async def test_deadline_stops_retries() -> None:
    calls = 0

    async def fn(attempt: int) -> str:
        nonlocal calls
        calls += 1
        raise ProviderError("429", status=429, retry_after_s=5.0, retryable=True)

    slow = RetryPolicy(max_retries=3, base_s=0.01, max_s=10.0, jitter=0.0)
    with pytest.raises(DeadlineError):
        await retry_async(fn, policy=slow, deadline=Deadline.after(0.2))
    assert calls == 1, "пауза длиннее остатка deadline — повтор не начинается"


async def test_bad_response_is_retried_with_attempt_number() -> None:
    async def fn(attempt: int) -> str:
        if attempt == 1:
            raise ResponseError("не JSON")
        return f"ok{attempt}"

    assert await retry_async(fn, policy=POLICY) == "ok2"


def test_classification() -> None:
    limited = classify_status(429, "x", 2.0)
    assert limited.retryable and limited.retry_after_s == 2.0
    assert classify_status(503, "x", None).retryable
    assert not classify_status(400, "x", None).retryable
    assert classify_status(401, "x", None).code == "provider_auth"
    assert classify_status(404, "x", None).code == "model_not_found"
    assert is_retryable(ConnectionError()) and not is_retryable(DeadlineError("x"))
    assert not is_retryable(ValueError("x"))


def test_backoff_grows_and_caps() -> None:
    policy = RetryPolicy(max_retries=5, base_s=1.0, max_s=4.0, jitter=0.0)
    assert [policy.delay(n) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 4.0]
    assert policy.delay(1, retry_after_s=10.0) == 4.0, "Retry-After тоже ограничен потолком"
