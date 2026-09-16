"""Повторы вызова модели: 429, сетевые ошибки, 5xx и негодный ответ.

Повторы SDK выключены (`max_retries=0` у клиента openai), чтобы попытки не перемножались:
единственный цикл повторов — здесь. Пауза растёт вдвое с разбросом, `Retry-After` провайдера
имеет приоритет, ни одна пауза не выходит за deadline запроса. Ошибки конфигурации, 4xx кроме
429 и исчерпанный deadline не повторяются.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from presentation_designer.llm.types import (
    Deadline,
    DeadlineError,
    LlmError,
    ProviderError,
    ResponseError,
)


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    base_s: float = 1.0
    max_s: float = 30.0
    jitter: float = 0.25

    def delay(self, attempt: int, retry_after_s: float | None = None) -> float:
        """Пауза перед попыткой attempt+1; attempt считается с 1."""
        if retry_after_s is not None and retry_after_s >= 0:
            return min(retry_after_s, self.max_s)
        raw = min(self.base_s * (2 ** (attempt - 1)), self.max_s)
        return float(raw * random.uniform(1 - self.jitter, 1 + self.jitter))


@dataclass
class RetryTrace:
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    slept_ms: int = 0


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, DeadlineError):
        return False
    if isinstance(exc, LlmError):
        return bool(exc.retryable)
    return isinstance(exc, asyncio.TimeoutError | ConnectionError | OSError)


def retry_after_of(exc: BaseException) -> float | None:
    return exc.retry_after_s if isinstance(exc, ProviderError) else None


async def retry_async[T](
    fn: Callable[[int], Awaitable[T]],
    *,
    policy: RetryPolicy,
    deadline: Deadline | None = None,
    trace: RetryTrace | None = None,
    on_error: Callable[[int, BaseException], None] | None = None,
) -> T:
    """Вызывает fn(attempt) до успеха. fn получает номер попытки с 1: клиент по нему добавляет
    подсказку о прошлой ошибке разбора (ResponseError) в следующий запрос."""
    trace = trace if trace is not None else RetryTrace()
    attempt = 0
    while True:
        attempt += 1
        trace.attempts = attempt
        if deadline is not None and deadline.expired():
            raise DeadlineError("время запроса истекло до попытки вызова модели")
        try:
            return await fn(attempt)
        except Exception as exc:
            trace.errors.append(f"{type(exc).__name__}: {exc}"[:300])
            if on_error:
                on_error(attempt, exc)
            if not is_retryable(exc) or attempt > policy.max_retries:
                raise
            delay = policy.delay(attempt, retry_after_of(exc))
            if deadline is not None:
                remaining = deadline.remaining()
                # Ждать дольше, чем осталось, бессмысленно: следующая попытка не успеет.
                if remaining <= delay + 0.05:
                    raise DeadlineError(
                        f"deadline не оставляет времени на повтор после ошибки: {exc}"
                    ) from exc
            trace.slept_ms += int(delay * 1000)
            await asyncio.sleep(delay)


def classify_status(status: int | None, message: str, retry_after_s: float | None) -> ProviderError:
    """HTTP-статус провайдера → ошибка с признаком повторяемости."""
    if status == 429:
        return ProviderError(
            message, status=status, retry_after_s=retry_after_s, retryable=True, code="rate_limited"
        )
    if status is not None and status >= 500:
        return ProviderError(
            message, status=status, retry_after_s=retry_after_s, retryable=True, code="provider_5xx"
        )
    if status in (408,):
        return ProviderError(message, status=status, retryable=True, code="provider_timeout")
    if status in (401, 403):
        return ProviderError(message, status=status, retryable=False, code="provider_auth")
    if status == 404:
        return ProviderError(message, status=status, retryable=False, code="model_not_found")
    return ProviderError(message, status=status, retryable=False, code="provider_rejected")


def bad_response(message: str) -> ResponseError:
    return ResponseError(message)
