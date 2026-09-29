"""Клиент моделей: единственная точка вызова для слоёв.

Порядок одного вызова: роль → провайдер и модель из models.yaml → ключ кэша → поиск в кэше или
записях → объединение одинаковых одновременных запросов → аренда лимитера (ожидание входит
в deadline) → HTTP-вызов транспорта с тайм-аутом → освобождение аренды по фактическому usage →
разбор и проверка ответа (JSON с починкой, Pydantic) → запись в кэш → учёт usage.
Повторы (429, сеть, 5xx, негодный ответ) — вокруг аренды и вызова, не вокруг кэша.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, TypeVar
from urllib.parse import urlsplit

from pydantic import BaseModel, ValidationError

from presentation_designer.llm.cache import ResponseCache, cache_key
from presentation_designer.llm.limiter import Lease, Limiter, LocalLimiter, Quota, ValkeyLimiter
from presentation_designer.llm.retry import RetryPolicy, RetryTrace, retry_async
from presentation_designer.llm.tokens import estimate_output_tokens, estimate_request_tokens
from presentation_designer.llm.transport import OpenAITransport, RawResult, Transport
from presentation_designer.llm.types import (
    ConfigError,
    Deadline,
    JsonDict,
    LlmError,
    Message,
    Request,
    Response,
    ResponseError,
    Usage,
)
from presentation_designer.llm.usage import UsageRecorder
from presentation_designer.shared.settings import (
    ModelRole,
    ModelsConfig,
    Provider,
    Settings,
    get_models_config,
    get_settings,
)

log = logging.getLogger(__name__)
M = TypeVar("M", bound=BaseModel)

_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$", re.MULTILINE)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


@dataclass(frozen=True)
class Target:
    """Куда идёт запрос роли: провайдер, модель, ревизия и стиль рассуждения."""

    role: str
    provider_name: str
    provider: Provider
    model: str
    model_revision: str | None
    role_config: ModelRole

    @property
    def limiter_key(self) -> str:
        return f"{self.provider_name}:{self.model}"


def resolve_target(models: ModelsConfig, role: str) -> Target:
    if role not in models.roles:
        raise ConfigError(f"роль {role!r} не описана в config/models.yaml")
    role_config = models.roles[role]
    if not role_config.enabled:
        raise ConfigError(f"роль {role!r} выключена в config/models.yaml")
    if not role_config.model:
        raise ConfigError(f"у роли {role!r} не задана модель")
    provider_name = role_config.provider or models.active_provider
    if provider_name not in models.providers:
        raise ConfigError(f"провайдер {provider_name!r} не описан в config/models.yaml")
    provider = models.providers[provider_name]
    return Target(
        role, provider_name, provider, role_config.model, provider.model_revision, role_config
    )


def quota_for_target(target: Target, settings: Settings) -> Quota:
    """Измеренные лимиты провайдера, иначе умолчания app.yaml."""
    limits = target.provider.limits
    return Quota(
        concurrency=limits.concurrency or settings.llm.concurrency,
        rpm=limits.rpm or settings.llm.rpm,
        tpm=limits.tpm or settings.llm.tpm,
    )


def parse_json_text(text: str) -> Any:
    """JSON из текста модели: снимает ограждения ```, берёт первый объект/массив, чинит
    висящие запятые. Ошибка — ResponseError, чтобы сработал повтор с подсказкой."""
    if not text or not text.strip():
        raise ResponseError("пустой ответ модели")
    cleaned = _FENCE.sub("", text.strip()).strip()
    candidates = [cleaned]
    start = min((i for i in (cleaned.find("{"), cleaned.find("[")) if i >= 0), default=-1)
    if start > 0:
        candidates.append(cleaned[start:])
    if start >= 0:
        closer = "}" if cleaned[start] == "{" else "]"
        end = cleaned.rfind(closer)
        if end > start:
            candidates.append(cleaned[start : end + 1])
    last_error: Exception | None = None
    for candidate in candidates:
        for variant in (candidate, _TRAILING_COMMA.sub(r"\1", candidate)):
            try:
                return json.loads(variant)
            except json.JSONDecodeError as e:
                last_error = e
    raise ResponseError(f"ответ модели не разбирается как JSON: {last_error}")


def redact_url(url: str | None) -> str | None:
    """Только схема и хост: в адресах провайдеров бывают токены в пути и параметрах."""
    if not url:
        return None
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}" if parts.hostname else None


class LlmClient:
    def __init__(
        self,
        *,
        settings: Settings,
        models: ModelsConfig,
        transport: Transport | Callable[[Target], Transport],
        limiter: Limiter,
        cache: ResponseCache,
        recorder: UsageRecorder | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.settings = settings
        self.models = models
        self._transport = transport
        self.limiter = limiter
        self.cache = cache
        self.recorder = recorder or UsageRecorder()
        self.retry_policy = retry_policy or RetryPolicy(
            max_retries=settings.llm.max_retries,
            base_s=settings.llm.retry_base_s,
            max_s=settings.llm.retry_max_s,
        )
        self._inflight: dict[str, asyncio.Future[Response]] = {}
        self._transports: dict[str, Transport] = {}

    # ----- служебное -----

    def target(self, role: str) -> Target:
        return resolve_target(self.models, role)

    def transport_for(self, target: Target) -> Transport:
        if hasattr(self._transport, "complete"):
            return self._transport  # type: ignore[return-value]
        if target.provider_name not in self._transports:
            self._transports[target.provider_name] = self._transport(target)
        return self._transports[target.provider_name]

    def _apply_role_defaults(self, req: Request, target: Target) -> Request:
        rc = target.role_config
        req = replace(req)
        if req.reasoning is None and rc.reasoning.mode:
            req.reasoning = rc.reasoning.mode  # type: ignore[assignment]
        if req.max_output_tokens is None:
            req.max_output_tokens = rc.reasoning.max_output_tokens
        if req.temperature is None:
            req.temperature = rc.temperature
        return req

    # ----- основной вызов -----

    async def complete(
        self,
        req: Request,
        *,
        parse: type[BaseModel] | None = None,
        validator: Callable[[Any], Any] | None = None,
    ) -> Response:
        target = self.target(req.role)
        req = self._apply_role_defaults(req, target)
        key = cache_key(
            req,
            provider=target.provider_name,
            model=target.model,
            model_revision=target.model_revision,
        )

        cached = self.cache.lookup(key)
        if cached is not None:
            cached.parsed = self._parse(req, cached.text, parse, validator)
            cached.prompt = req.prompt
            self.recorder.record_response(req, cached)
            return cached

        if key in self._inflight:
            joined = await asyncio.shield(self._inflight[key])
            joined = replace(joined, cache_hit=True, usage=Usage(0, 0, None, "cache"), latency_ms=0)
            self.recorder.record_response(req, joined)
            return joined

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Response] = loop.create_future()
        self._inflight[key] = future
        try:
            resp = await self._call_with_retries(req, target, parse, validator)
            resp.cache_key = key
            resp.prompt = req.prompt
            self.cache.store(key, req, resp)
            self.recorder.record_response(req, resp)
            future.set_result(resp)
            return resp
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
                # Если никто не ждал объединённый результат, исключение не должно
                # всплыть предупреждением цикла событий: оно уже уходит вызывающему.
                future.exception()
            raise
        finally:
            self._inflight.pop(key, None)

    async def _call_with_retries(
        self,
        req: Request,
        target: Target,
        parse: type[BaseModel] | None,
        validator: Callable[[Any], Any] | None,
    ) -> Response:
        transport = self.transport_for(target)
        deadline = req.deadline
        trace = RetryTrace()
        quota_wait_ms = 0
        last_bad: tuple[str, str] | None = None  # (текст ответа, ошибка) для подсказки

        base_req = _downgrade_format(req, target)

        async def attempt(n: int) -> Response:
            nonlocal quota_wait_ms, last_bad
            attempt_req = base_req if last_bad is None else _with_repair_hint(base_req, *last_bad)
            tokens = estimate_request_tokens(
                attempt_req,
                chars_per_token=self.settings.llm.chars_per_token,
                image_tokens=self.settings.llm.image_tokens,
            ) + estimate_output_tokens(attempt_req)
            lease = await self._acquire(target, tokens, deadline)
            quota_wait_ms += lease.wait_ms
            started = time.monotonic()
            try:
                timeout_s = float(self.settings.timeouts.llm_call_s)
                if deadline is not None:
                    remaining = deadline.remaining()
                    if remaining <= 0.5:
                        raise LlmError(
                            "время запроса истекло до вызова модели", code="deadline_exceeded"
                        )
                    timeout_s = min(timeout_s, remaining)
                raw = await transport.complete(attempt_req, model=target.model, timeout_s=timeout_s)
            except BaseException:
                await self.limiter.release(lease)
                raise
            usage = raw.usage or Usage(
                prompt_tokens=tokens - estimate_output_tokens(attempt_req),
                completion_tokens=estimate_output_tokens(attempt_req),
                source="estimated",
            )
            await self.limiter.release(
                lease, actual_tokens=usage.total if raw.usage is not None else None
            )
            try:
                # Формат берём из исходного запроса: после понижения ответ всё равно JSON.
                parsed = self._parse(req, raw.text, parse, validator)
            except ResponseError as e:
                last_bad = (raw.text, str(e))
                raise
            return Response(
                text=raw.text,
                parsed=parsed,
                usage=usage,
                model=raw.model or target.model,
                provider=target.provider_name,
                finish_reason=raw.finish_reason,
                latency_ms=raw.latency_ms or int((time.monotonic() - started) * 1000),
                quota_wait_ms=quota_wait_ms,
                attempts=n,
                raw=raw.raw,
            )

        try:
            resp = await retry_async(
                attempt,
                policy=self.retry_policy,
                deadline=deadline,
                trace=trace,
                on_error=lambda n, exc: log.warning(
                    "вызов %s/%s попытка %d не удалась: %s",
                    target.provider_name,
                    target.model,
                    n,
                    exc,
                ),
            )
        except LlmError as e:
            self.recorder.record_failure(
                req,
                model=target.model,
                attempts=trace.attempts,
                error_code=e.code,
                quota_wait_ms=quota_wait_ms,
            )
            raise
        except Exception as e:
            self.recorder.record_failure(
                req,
                model=target.model,
                attempts=trace.attempts,
                error_code=type(e).__name__,
                quota_wait_ms=quota_wait_ms,
            )
            raise
        resp.attempts = trace.attempts
        return resp

    async def _acquire(self, target: Target, tokens: int, deadline: Deadline | None) -> Lease:
        try:
            return await self.limiter.acquire(target.limiter_key, tokens, deadline=deadline)
        except LlmError:
            raise
        except Exception as e:  # Valkey недоступен и подобное
            raise LlmError(
                f"лимитер недоступен: {e}", code="limiter_unavailable", retryable=True
            ) from e

    def _parse(
        self,
        req: Request,
        text: str,
        parse: type[BaseModel] | None,
        validator: Callable[[Any], Any] | None,
    ) -> Any:
        if req.response_format == "text" and parse is None and validator is None:
            return text
        value: Any = parse_json_text(text) if req.response_format != "text" or parse else text
        if parse is not None:
            try:
                value = parse.model_validate(value)
            except ValidationError as e:
                raise ResponseError(
                    f"ответ не соответствует модели {parse.__name__}: {_short(e)}"
                ) from e
        if validator is not None:
            try:
                value = validator(value)
            except (ValueError, TypeError, KeyError) as e:
                raise ResponseError(f"ответ не прошёл проверку: {e}") from e
        return value

    # ----- удобства -----

    def complete_sync(self, req: Request, **kwargs: Any) -> Response:
        """Для синхронных слоёв и CLI: запускает вызов в новом цикле событий."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.complete(req, **kwargs))
        raise RuntimeError("complete_sync нельзя вызывать из работающего цикла событий")

    async def complete_model(self, req: Request, model: type[M]) -> M:
        resp = await self.complete(req, parse=model)
        return resp.parsed  # type: ignore[no-any-return]

    async def aclose(self) -> None:
        for transport in self._transports.values():
            close = getattr(transport, "aclose", None)
            if close:
                await close()
        close = getattr(self.limiter, "aclose", None)
        if close:
            await close()


def _downgrade_format(req: Request, target: Target) -> Request:
    """Если зонд показал, что провайдер не принимает json_schema (или json_object), формат
    понижается: схема уходит в системное сообщение текстом, проверка остаётся на нашей стороне."""
    supports = target.provider.supports
    fmt = req.response_format
    if fmt == "json_schema" and supports.json_schema is False:
        fmt = "json_object" if supports.json_object is not False else "text"
    elif fmt == "json_object" and supports.json_object is False:
        fmt = "text"
    if fmt == req.response_format:
        return req
    schema_text = json.dumps(req.schema, ensure_ascii=False) if req.schema else "{}"
    instruction = (
        "Ответь только одним JSON-документом без пояснений и без ограждений ```; "
        f"документ должен соответствовать схеме: {schema_text}"
    )
    messages = list(req.messages)
    if messages and messages[0].role == "system":
        messages[0] = replace(messages[0], text=f"{messages[0].text}\n\n{instruction}")
    else:
        messages.insert(0, Message("system", instruction))
    return replace(req, response_format=fmt, messages=messages)


def _with_repair_hint(req: Request, bad_text: str, error: str) -> Request:
    """Повтор после негодного ответа: показываем модели её ответ и ошибку, просим только JSON."""
    hint = (
        "Предыдущий ответ не прошёл проверку: "
        f"{error[:500]}. Верни только исправленный документ в требуемом формате, без пояснений."
    )
    messages = [
        *req.messages,
        Message("assistant", bad_text[:4000]),
        Message("user", hint),
    ]
    return replace(req, messages=messages)


def _short(e: ValidationError) -> str:
    parts = []
    for err in e.errors()[:5]:
        loc = ".".join(str(x) for x in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}")
    return "; ".join(parts)


# ---------- сборка из настроек ----------


def limiter_from_env(settings: Settings, quota_for: Callable[[str], Quota]) -> Limiter:
    """Valkey, если он задан и очередь не встроенная; иначе лимитер процесса."""
    url = os.environ.get("PD_VALKEY_URL")
    if url and os.environ.get("PD_QUEUE_MODE", "rq") != "inline":
        return ValkeyLimiter(
            url,
            quota_for,
            lease_ttl_s=settings.llm.lease_ttl_s,
            max_wait_s=settings.llm.quota_wait_max_s,
        )
    return LocalLimiter(
        quota_for, lease_ttl_s=settings.llm.lease_ttl_s, max_wait_s=settings.llm.quota_wait_max_s
    )


def openai_transport_factory(settings: Settings) -> Callable[[Target], Transport]:
    def make(target: Target) -> Transport:
        p = target.provider
        base_url, api_key = p.base_url(), p.api_key()
        if not p.configured():
            raise ConfigError(
                f"провайдер {target.provider_name}: задайте {p.env_base_url} и {p.env_api_key} "
                "в окружении (.env); значения-образцы не принимаются"
            )
        return OpenAITransport(
            base_url=base_url or "",
            api_key=api_key or "",
            provider=target.provider_name,
            reasoning_style=p.reasoning_style,
            timeout_s=float(settings.timeouts.llm_call_s),
        )

    return make


def build_client(
    settings: Settings | None = None,
    models: ModelsConfig | None = None,
    *,
    transport: Transport | Callable[[Target], Transport] | None = None,
    limiter: Limiter | None = None,
    cache_mode: str | None = None,
    recorder: UsageRecorder | None = None,
) -> LlmClient:
    settings = settings or get_settings()
    models = models or get_models_config()

    def quota_for(key: str) -> Quota:
        provider_name = key.split(":", 1)[0]
        provider = models.providers.get(provider_name)
        limits = provider.limits if provider else None
        return Quota(
            concurrency=(limits.concurrency if limits else None) or settings.llm.concurrency,
            rpm=(limits.rpm if limits else None) or settings.llm.rpm,
            tpm=(limits.tpm if limits else None) or settings.llm.tpm,
        )

    cache = ResponseCache(
        cache_mode or settings.llm.cache_mode, settings.llm_cache_dir, settings.llm_fixtures_dir
    )
    return LlmClient(
        settings=settings,
        models=models,
        transport=transport or openai_transport_factory(settings),
        limiter=limiter or limiter_from_env(settings, quota_for),
        cache=cache,
        recorder=recorder,
    )


def describe_provider(models: ModelsConfig | None = None) -> JsonDict:
    """Сводка провайдера для /api/health и /api/capabilities: без ключей и адресов с токенами."""
    models = models or get_models_config()
    name = models.active_provider
    provider = models.providers[name]
    roles: JsonDict = {}
    for role_name, role in models.roles.items():
        # Роли других провайдеров (распознавание речи на своих весах) в сводку шлюза не входят.
        if not role.enabled or (role.provider and role.provider != name):
            continue
        roles[role_name] = {
            "model": role.model,
            "reasoning": role.reasoning.mode,
            "verified": bool(role.verified.date),
            "verified_date": role.verified.date,
        }
    supports = provider.supports.model_dump()
    return {
        "name": name,
        "kind": provider.kind,
        "host": redact_url(provider.base_url()),
        "configured": provider.configured(),
        "probed": any(v is not None for v in supports.values()),
        "supports": supports,
        "limits": provider.limits.model_dump(),
        "roles": roles,
    }


# Роли, которые участвуют в генерации (перечисление `model_ref.role` в контракте). Голосовой
# ввод (`asr`) — отдельный сервис чата: в результат генерации его модель не попадает.
GENERATION_ROLES = frozenset({"llm", "vlm", "text_to_image", "embedding"})


def model_refs(models: ModelsConfig | None = None) -> list[JsonDict]:
    """Ссылки на модели для GenerationResult.versions.models."""
    models = models or get_models_config()
    out: list[JsonDict] = []
    for role_name, role in models.roles.items():
        if not role.enabled or not role.model or role_name not in GENERATION_ROLES:
            continue
        ref: JsonDict = {"role": role_name, "name": role.model}
        if role.provider:
            ref["provider"] = role.provider
        provider = models.providers.get(role.provider or "")
        if provider is not None and not provider.open_weights:
            # Роль переключена на закрытую модель разработки: карточка открытой модели роли
            # (Qwen, Apache-2.0, 27B) её не описывает и в результат не пишется.
            ref["license"] = "proprietary"
        else:
            if role.hf_url:
                ref["hf_url"] = role.hf_url
            if role.params_b is not None:
                ref["params_b"] = role.params_b
            if role.license:
                ref["license"] = role.license
        if role.reasoning.mode:
            ref["reasoning_mode"] = role.reasoning.mode
        out.append(ref)
    return out


def skill_model_ref(client: LlmClient, skill: Any, role: str = "llm") -> JsonDict | None:
    """Ссылка на модель роли с фактическим режимом рассуждения скилла (а не умолчанием
    роли) — для generation_meta и import_meta."""
    try:
        ref = next((m for m in model_refs(client.models) if m.get("role") == role), None)
    except Exception:
        return None
    reasoning = getattr(getattr(skill, "manifest", None), "reasoning", None)
    mode = getattr(reasoning, "mode", None)
    if ref is not None and mode:
        ref = {**ref, "reasoning_mode": mode}
    return ref


__all__ = [
    "LlmClient",
    "RawResult",
    "Target",
    "build_client",
    "describe_provider",
    "limiter_from_env",
    "model_refs",
    "parse_json_text",
    "quota_for_target",
    "redact_url",
    "resolve_target",
    "skill_model_ref",
]
