"""Транспорт: один HTTP-вызов OpenAI-совместимого endpoint без повторов, лимитов и кэша.

Сюда сведены все различия протокола: сборка сообщений с изображениями (data URL), формат
ответа (json_schema / json_object / text), параметры рассуждения по стилю провайдера,
чтение usage и перевод ошибок SDK в `ProviderError`. Всё остальное — в `llm/client.py`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from presentation_designer.llm.retry import classify_status
from presentation_designer.llm.types import (
    ConfigError,
    JsonDict,
    ProviderError,
    Request,
    Usage,
)

REASONING_EFFORT = {"low": "low", "medium": "medium", "high": "high"}
# Бюджет токенов рассуждения для стиля qwen_enable_thinking; уточняется зондом.
THINKING_BUDGET = {"low": 1024, "medium": 4096, "high": 16384}


@dataclass
class RawResult:
    text: str
    model: str
    usage: Usage | None
    finish_reason: str | None
    latency_ms: int
    raw: JsonDict | None = None
    ttft_ms: int | None = None
    reasoning_text: str | None = None  # reasoning_content, если endpoint его вернул


def reasoning_content_of(message: Any) -> str | None:
    """Текст рассуждения из ответа: поле reasoning_content (vLLM, DashScope, LiteLLM)."""
    if message is None:
        return None
    value = (
        message.get("reasoning_content")
        if isinstance(message, dict)
        else getattr(message, "reasoning_content", None)
    )
    if not value and not isinstance(message, dict):
        extra = getattr(message, "model_extra", None) or {}
        value = extra.get("reasoning_content") or extra.get("reasoning")
    return str(value) if value else None


def with_reasoning_estimate(usage: Usage | None, reasoning_text: str | None) -> Usage | None:
    """Если провайдер не отдал reasoning_tokens, оценивает их по тексту рассуждения:
    иначе в метриках не видно, куда ушёл бюджет ответа."""
    if usage is None or not reasoning_text or usage.reasoning_tokens is not None:
        return usage
    from presentation_designer.llm.tokens import estimate_text_tokens

    usage.reasoning_tokens = min(usage.completion_tokens, estimate_text_tokens(reasoning_text))
    usage.reasoning_estimated = True
    return usage


class Transport(Protocol):
    name: str

    async def complete(
        self, req: Request, *, model: str, timeout_s: float, **hints: Any
    ) -> RawResult: ...


def build_messages(req: Request) -> list[JsonDict]:
    out: list[JsonDict] = []
    for m in req.messages:
        if not m.images:
            out.append({"role": m.role, "content": m.text})
            continue
        parts: list[JsonDict] = []
        if m.text:
            parts.append({"type": "text", "text": m.text})
        for img in m.images:
            url: JsonDict = {"url": img.data_url()}
            if img.detail:
                url["detail"] = img.detail
            parts.append({"type": "image_url", "image_url": url})
        out.append({"role": m.role, "content": parts})
    return out


def build_response_format(req: Request) -> JsonDict | None:
    if req.response_format == "json_schema":
        if req.schema is None:
            raise ConfigError("для response_format=json_schema нужна схема ответа")
        return {
            "type": "json_schema",
            "json_schema": {"name": req.schema_name, "schema": req.schema, "strict": True},
        }
    if req.response_format == "json_object":
        return {"type": "json_object"}
    return None


def build_reasoning(req: Request, style: str) -> tuple[JsonDict, JsonDict]:
    """(именованные параметры SDK, extra_body) по режиму рассуждения и стилю провайдера.

    Стили: openai_reasoning_effort — параметр reasoning_effort; qwen_enable_thinking —
    enable_thinking/thinking_budget в теле (DashScope); vllm_chat_template —
    chat_template_kwargs.enable_thinking (vLLM и прокси над ним, бюджет не задаётся:
    рассуждение и ответ делят max_completion_tokens); openrouter_reasoning — reasoning.enabled
    или reasoning.effort в теле (OpenRouter); none — ничего не передаётся.
    """
    mode = req.reasoning
    if mode is None or mode == "provider_default" or style == "none":
        return {}, {}
    if style == "openai_reasoning_effort":
        if mode == "off":
            return {}, {}
        return {"reasoning_effort": REASONING_EFFORT[mode]}, {}
    if style == "qwen_enable_thinking":
        if mode == "off":
            return {}, {"enable_thinking": False}
        return {}, {"enable_thinking": True, "thinking_budget": THINKING_BUDGET[mode]}
    if style == "vllm_chat_template":
        return {}, {"chat_template_kwargs": {"enable_thinking": mode != "off"}}
    if style == "openrouter_reasoning":
        if mode == "off":
            return {}, {"reasoning": {"enabled": False}}
        return {}, {"reasoning": {"effort": REASONING_EFFORT[mode]}}
    raise ConfigError(f"неизвестный стиль рассуждения провайдера: {style}")


def build_params(
    req: Request, *, model: str, reasoning_style: str, stream: bool = False
) -> JsonDict:
    kwargs: JsonDict = {"model": model, "messages": build_messages(req)}
    if req.max_output_tokens:
        kwargs["max_completion_tokens"] = req.max_output_tokens
    if req.temperature is not None:
        kwargs["temperature"] = req.temperature
    if req.seed is not None:
        kwargs["seed"] = req.seed
    fmt = build_response_format(req)
    if fmt:
        kwargs["response_format"] = fmt
    named, extra = build_reasoning(req, reasoning_style)
    kwargs.update(named)
    extra_body: JsonDict = {**extra, **dict(req.extra.get("extra_body") or {})}
    if extra_body:
        kwargs["extra_body"] = extra_body
    if stream:
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
    return kwargs


def usage_from(raw: Any) -> Usage | None:
    if raw is None:
        return None
    get = raw.get if isinstance(raw, dict) else (lambda k, d=None: getattr(raw, k, d))
    prompt = get("prompt_tokens")
    completion = get("completion_tokens")
    if prompt is None and completion is None:
        return None
    details = get("completion_tokens_details")
    reasoning = None
    if details is not None:
        reasoning = (
            details.get("reasoning_tokens")
            if isinstance(details, dict)
            else getattr(details, "reasoning_tokens", None)
        )
    return Usage(
        prompt_tokens=int(prompt or 0),
        completion_tokens=int(completion or 0),
        reasoning_tokens=int(reasoning) if reasoning is not None else None,
        source="provider",
    )


def _retry_after(headers: Any) -> float | None:
    if headers is None:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


class OpenAITransport:
    """Асинхронный клиент openai SDK с base_url провайдера; повторы SDK выключены."""

    name = "openai"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        provider: str,
        reasoning_style: str = "none",
        timeout_s: float = 90.0,
    ) -> None:
        from openai import AsyncOpenAI

        if not base_url or not api_key:
            raise ConfigError(f"у провайдера {provider} не заданы адрес или ключ в окружении")
        self.provider = provider
        self.reasoning_style = reasoning_style
        self.client = AsyncOpenAI(
            base_url=base_url, api_key=api_key, max_retries=0, timeout=timeout_s
        )

    async def complete(
        self, req: Request, *, model: str, timeout_s: float, **hints: Any
    ) -> RawResult:
        import openai

        params = build_params(req, model=model, reasoning_style=self.reasoning_style)
        started = time.monotonic()
        try:
            completion = await self.client.chat.completions.create(timeout=timeout_s, **params)
        except openai.APITimeoutError as e:
            raise ProviderError(
                f"тайм-аут вызова модели {model}", retryable=True, code="provider_timeout"
            ) from e
        except openai.APIConnectionError as e:
            raise ProviderError(
                f"нет соединения с провайдером: {e}", retryable=True, code="provider_unreachable"
            ) from e
        except openai.APIStatusError as e:
            raise classify_status(
                e.status_code,
                _safe_error_text(e),
                _retry_after(getattr(e.response, "headers", None)),
            ) from e
        latency_ms = int((time.monotonic() - started) * 1000)
        if not completion.choices:
            raise ProviderError("ответ без choices", retryable=True, code="empty_response")
        choice = completion.choices[0]
        text = choice.message.content or ""
        raw = completion.model_dump(mode="json", exclude_none=True)
        reasoning = reasoning_content_of(raw["choices"][0].get("message"))
        # В raw нет изображений; usage и признаки рассуждения нужны отчёту зонда.
        return RawResult(
            text=text,
            model=completion.model or model,
            usage=with_reasoning_estimate(usage_from(completion.usage), reasoning),
            finish_reason=choice.finish_reason,
            latency_ms=latency_ms,
            raw=raw,
            reasoning_text=reasoning,
        )

    async def stream_probe(self, req: Request, *, model: str, timeout_s: float) -> RawResult:
        """Потоковый вызов для зонда: время первого токена и usage из последнего чанка."""
        import openai

        params = build_params(req, model=model, reasoning_style=self.reasoning_style, stream=True)
        started = time.monotonic()
        ttft_ms: int | None = None
        chunks: list[str] = []
        usage: Usage | None = None
        seen_model = model
        finish: str | None = None
        try:
            stream = await self.client.chat.completions.create(timeout=timeout_s, **params)
            async for chunk in stream:  # type: ignore[attr-defined]  # stream=True в **params
                if getattr(chunk, "usage", None) is not None:
                    usage = usage_from(chunk.usage)
                if chunk.model:
                    seen_model = chunk.model
                for choice in chunk.choices or []:
                    delta = getattr(choice.delta, "content", None)
                    if delta:
                        if ttft_ms is None:
                            ttft_ms = int((time.monotonic() - started) * 1000)
                        chunks.append(delta)
                    if choice.finish_reason:
                        finish = choice.finish_reason
        except openai.APIStatusError as e:
            raise classify_status(
                e.status_code,
                _safe_error_text(e),
                _retry_after(getattr(e.response, "headers", None)),
            ) from e
        except (openai.APITimeoutError, openai.APIConnectionError) as e:
            raise ProviderError(
                f"поток прерван: {e}", retryable=True, code="provider_unreachable"
            ) from e
        return RawResult(
            text="".join(chunks),
            model=seen_model,
            usage=usage,
            finish_reason=finish,
            latency_ms=int((time.monotonic() - started) * 1000),
            ttft_ms=ttft_ms,
        )

    async def list_models(self, timeout_s: float = 30.0) -> list[str]:
        import openai

        try:
            page = await self.client.models.list(timeout=timeout_s)
        except openai.APIStatusError as e:
            raise classify_status(e.status_code, _safe_error_text(e), None) from e
        except (openai.APITimeoutError, openai.APIConnectionError) as e:
            raise ProviderError(f"нет соединения: {e}", retryable=True) from e
        return sorted(str(m.id) for m in page.data)

    async def aclose(self) -> None:
        await self.client.close()


def _safe_error_text(exc: Any) -> str:
    """Текст ошибки провайдера без заголовков и ключей: только статус и тело до 300 символов."""
    body = getattr(exc, "body", None)
    message = str(getattr(exc, "message", "") or exc)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            message = str(err["message"])
    return f"HTTP {getattr(exc, 'status_code', '?')}: {message[:300]}"
