"""Типы запросов и ответов адаптера моделей.

Запрос описывает роль модели, сообщения с текстом и изображениями, формат ответа и параметры
вызова; ответ несёт текст, разобранный документ, usage и служебные метрики. Слои работают
только с этими типами, детали OpenAI-совместимого протокола остаются в `llm/client.py`.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Any, Literal

JsonDict = dict[str, Any]
ResponseFormat = Literal["json_schema", "json_object", "text"]
ReasoningMode = Literal["off", "low", "medium", "high", "provider_default"]
UsageSource = Literal["provider", "estimated", "cache", "replay", "stub"]


class LlmError(Exception):
    """База ошибок адаптера; code попадает в контракт error."""

    code = "llm_error"
    retryable = False

    def __init__(self, message: str, *, code: str | None = None, retryable: bool | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable


class ConfigError(LlmError):
    code = "llm_not_configured"


class ProviderError(LlmError):
    """Ошибка провайдера: HTTP-статус и Retry-After, если сервер его прислал."""

    code = "provider_error"

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retry_after_s: float | None = None,
        retryable: bool = False,
        code: str | None = None,
    ) -> None:
        super().__init__(message, code=code, retryable=retryable)
        self.status = status
        self.retry_after_s = retry_after_s


class ResponseError(LlmError):
    """Ответ получен, но не разбирается или не проходит схему; повторяется с подсказкой."""

    code = "bad_response"
    retryable = True


class DeadlineError(LlmError):
    code = "deadline_exceeded"


class QuotaTimeoutError(DeadlineError):
    code = "quota_wait_timeout"


class ReplayMissError(LlmError):
    """В режиме replay нет записи для ключа: сеть не вызывается, тест должен записать ответ."""

    code = "replay_miss"


@dataclass(frozen=True)
class Image:
    """Изображение запроса: байты и тип; в протокол уходит data URL."""

    data: bytes
    mime: str = "image/png"
    detail: str | None = None

    def data_url(self) -> str:
        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode('ascii')}"


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    text: str
    images: tuple[Image, ...] = ()


@dataclass(frozen=True)
class Deadline:
    """Момент, до которого запрос вместе с ожиданием квоты и повторами должен завершиться."""

    at_monotonic: float

    @classmethod
    def after(cls, seconds: float) -> Deadline:
        return cls(time.monotonic() + seconds)

    def remaining(self) -> float:
        return self.at_monotonic - time.monotonic()

    def expired(self) -> bool:
        return self.remaining() <= 0


@dataclass
class Request:
    """Один вызов модели. `prompt` и `skill` — версии для ключа кэша и метрик."""

    role: str
    messages: list[Message]
    response_format: ResponseFormat = "text"
    schema: JsonDict | None = None
    schema_name: str = "response"
    reasoning: ReasoningMode | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    seed: int | None = None
    prompt: tuple[str, str] | None = None
    skill: tuple[str, str] | None = None
    stage: str = "probe"
    variant_id: str | None = None
    slide_ids: list[str] | None = None
    deadline: Deadline | None = None
    regenerate_nonce: str | None = None
    extra: JsonDict = field(default_factory=dict)

    @property
    def images(self) -> list[Image]:
        return [img for m in self.messages for img in m.images]


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int | None = None
    source: UsageSource = "estimated"
    # Провайдер не отдал reasoning_tokens отдельно: значение оценено по тексту рассуждения.
    reasoning_estimated: bool = False

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class Response:
    text: str
    parsed: Any = None
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    provider: str = ""
    finish_reason: str | None = None
    latency_ms: int = 0
    quota_wait_ms: int = 0
    attempts: int = 1
    cache_hit: bool = False
    cache_key: str | None = None
    prompt: tuple[str, str] | None = None
    raw: JsonDict | None = None
