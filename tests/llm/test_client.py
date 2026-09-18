"""Клиент на заглушке транспорта: роли и умолчания, форматы ответа, разбор и починка JSON,
повтор с подсказкой после негодного ответа, изображения, usage по оценке, deadline, сводка
провайдера без секретов."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel

from presentation_designer.llm.client import (
    LlmClient,
    describe_provider,
    model_refs,
    parse_json_text,
    redact_url,
    resolve_target,
)
from presentation_designer.llm.limiter import LocalLimiter, Quota
from presentation_designer.llm.stub import StubTransport
from presentation_designer.llm.transport import build_params
from presentation_designer.llm.types import (
    ConfigError,
    Deadline,
    DeadlineError,
    Image,
    Message,
    ProviderError,
    QuotaTimeoutError,
    Request,
    ResponseError,
)
from presentation_designer.shared.settings import ModelsConfig


class Plan(BaseModel):
    title: str
    points: list[str]


def plan_request(**kw: Any) -> Request:
    req = Request(
        role="llm",
        messages=[Message("system", "составь план"), Message("user", "тема: уведомления")],
        response_format="json_schema",
        schema=Plan.model_json_schema(),
        prompt=("plan.test", "0.0.1"),
        stage="plan",
        variant_id="compact",
    )
    for k, v in kw.items():
        setattr(req, k, v)
    return req


def _limiter_key() -> str:
    """Ключ лимитера текущей роли: имя модели живёт в конфиге, не в тесте."""
    from presentation_designer.shared.settings import get_models_config

    return f"qwen-api:{get_models_config().role('llm').model}"


def test_resolve_target_and_defaults(models: ModelsConfig) -> None:
    # Имя модели берётся из конфига: оно там и живёт (ТЗ п.4), а менять его
    # приходится при смене шлюза. Литерал в тесте ломался бы каждый раз.
    expected = models.role("llm").model
    target = resolve_target(models, "llm")
    assert target.provider_name == "qwen-api" and target.model == expected
    assert target.limiter_key == f"qwen-api:{expected}"
    with pytest.raises(ConfigError):
        resolve_target(models, "text_to_image")
    with pytest.raises(ConfigError):
        resolve_target(models, "нет")


async def test_role_defaults_applied(make_client: Callable[..., LlmClient]) -> None:
    """Умолчания роли берутся из конфига и доезжают до транспорта."""
    from presentation_designer.shared.settings import get_models_config

    role = get_models_config().role("llm")
    stub = StubTransport()
    client = make_client(stub)
    await client.complete(plan_request(), parse=Plan)
    sent = stub.calls[0]
    assert sent.reasoning == role.reasoning.mode
    assert sent.max_output_tokens == role.reasoning.max_output_tokens
    assert sent.temperature == 0.2
    await client.complete(plan_request(reasoning="off", temperature=0.0), parse=Plan)
    assert stub.calls[1].reasoning == "off" and stub.calls[1].temperature == 0.0


async def test_json_schema_parsed_into_model(make_client: Callable[..., LlmClient]) -> None:
    stub = StubTransport()
    stub.answer({"title": "План", "points": ["а", "б"]})
    client = make_client(stub)
    resp = await client.complete(plan_request(), parse=Plan)
    assert isinstance(resp.parsed, Plan) and resp.parsed.points == ["а", "б"]
    assert resp.usage.source == "provider" and resp.prompt == ("plan.test", "0.0.1")
    assert resp.attempts == 1 and resp.cache_hit is False


async def test_bad_json_is_repaired_or_retried_with_hint(
    make_client: Callable[..., LlmClient],
) -> None:
    stub = StubTransport()
    stub.answer('Вот план:\n```json\n{"title": "П", "points": ["а",]}\n```', times=1)
    client = make_client(stub)
    resp = await client.complete(plan_request(), parse=Plan)
    assert resp.parsed.title == "П", "ограждения и висящая запятая починены без повтора"
    assert len(stub.calls) == 1

    stub2 = StubTransport()
    stub2.answer("совсем не json", times=1)
    stub2.answer({"title": "ок", "points": []}, times=1)
    client2 = make_client(stub2)
    resp2 = await client2.complete(plan_request(), parse=Plan)
    assert resp2.parsed.title == "ок" and resp2.attempts == 2
    hinted = stub2.calls[1]
    assert hinted.messages[-2].role == "assistant" and "совсем не json" in hinted.messages[-2].text
    assert "не прошёл проверку" in hinted.messages[-1].text


async def test_schema_violation_exhausts_retries(make_client: Callable[..., LlmClient]) -> None:
    stub = StubTransport()
    stub.answer({"title": 5})
    client = make_client(stub, max_retries=1)
    with pytest.raises(ResponseError, match="Plan"):
        await client.complete(plan_request(), parse=Plan)
    assert len(stub.calls) == 2
    failure = client.recorder.calls[-1]
    assert failure.ok is False and failure.error_code == "bad_response" and failure.attempt == 2


async def test_rate_limit_then_success_counted(make_client: Callable[..., LlmClient]) -> None:
    stub = StubTransport()
    stub.fail(ProviderError("429", status=429, retry_after_s=0.01, retryable=True), times=2)
    client = make_client(stub)
    resp = await client.complete(plan_request(), parse=Plan)
    assert resp.attempts == 3 and len(stub.calls) == 3
    metrics = client.recorder.metrics()
    assert metrics["retries"] == 2 and metrics["totals"]["llm_calls"] == 1


async def test_non_retryable_provider_error(make_client: Callable[..., LlmClient]) -> None:
    stub = StubTransport()
    stub.fail(ProviderError("401", status=401, retryable=False, code="provider_auth"), times=5)
    client = make_client(stub)
    with pytest.raises(ProviderError):
        await client.complete(plan_request())
    assert len(stub.calls) == 1


async def test_images_and_text_only_formats(make_client: Callable[..., LlmClient]) -> None:
    stub = StubTransport()
    client = make_client(stub)
    req = Request(
        role="vlm",
        messages=[Message("user", "что на слайде?", (Image(b"\x89PNG", "image/png"),))],
        response_format="text",
    )
    resp = await client.complete(req)
    assert resp.parsed == resp.text and resp.text.startswith("stub:")
    params = build_params(stub.calls[0], model="m", reasoning_style="none")
    content = params["messages"][0]["content"]
    assert content[0]["type"] == "text" and content[1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


async def test_usage_estimated_when_provider_silent(make_client: Callable[..., LlmClient]) -> None:
    stub = StubTransport(report_usage=False)
    client = make_client(stub)
    resp = await client.complete(plan_request(), parse=Plan)
    assert resp.usage.source == "estimated" and resp.usage.prompt_tokens > 0
    metrics = client.recorder.metrics()
    assert metrics["totals"]["usage_estimated"] is True


async def test_quota_wait_counts_into_deadline(make_client: Callable[..., LlmClient]) -> None:
    limiter = LocalLimiter(Quota(1, 1000, 1_000_000), max_wait_s=5)
    stub = StubTransport()
    client = make_client(stub, limiter=limiter)
    lease = await limiter.acquire(_limiter_key(), 10)  # слот занят другим воркером
    with pytest.raises(QuotaTimeoutError):
        await client.complete(plan_request(deadline=Deadline.after(0.3)))
    assert stub.calls == [], "до модели запрос не дошёл"
    failure = client.recorder.calls[-1]
    assert failure.ok is False and failure.error_code == "quota_wait_timeout"
    await limiter.release(lease)
    resp = await client.complete(plan_request(deadline=Deadline.after(5)), parse=Plan)
    assert resp.attempts == 1


async def test_expired_deadline_never_calls(make_client: Callable[..., LlmClient]) -> None:
    stub = StubTransport()
    client = make_client(stub)
    with pytest.raises(DeadlineError):
        await client.complete(plan_request(deadline=Deadline.after(-1)))
    assert stub.calls == []


async def test_lease_released_after_failure(make_client: Callable[..., LlmClient]) -> None:
    limiter = LocalLimiter(Quota(1, 1000, 1_000_000), max_wait_s=1)
    stub = StubTransport()
    stub.fail(ProviderError("500", status=500, retryable=True), times=1)
    client = make_client(stub, limiter=limiter)
    await client.complete(plan_request(), parse=Plan)
    snap = await limiter.snapshot(_limiter_key())
    assert snap["active"] == 0 and snap["requests_in_window"] == 2


async def test_format_downgrade_when_provider_lacks_json_schema(
    make_client: Callable[..., LlmClient], models: ModelsConfig
) -> None:
    models.providers["qwen-api"].supports.json_schema = False
    stub = StubTransport()
    stub.answer({"title": "т", "points": []})
    client = make_client(stub)
    resp = await client.complete(plan_request(), parse=Plan)
    sent = stub.calls[0]
    assert sent.response_format == "json_object"
    assert "соответствовать схеме" in sent.messages[0].text
    assert resp.parsed.title == "т"
    models.providers["qwen-api"].supports.json_schema = None


def test_parse_json_text_variants() -> None:
    assert parse_json_text('{"a": 1}') == {"a": 1}
    assert parse_json_text('```json\n{"a": [1,2,]}\n```') == {"a": [1, 2]}
    assert parse_json_text('Ответ: {"a": {"b": 2}} — готово') == {"a": {"b": 2}}
    assert parse_json_text("[1, 2]") == [1, 2]
    with pytest.raises(ResponseError):
        parse_json_text("")
    with pytest.raises(ResponseError):
        parse_json_text("нет json")


def test_provider_summary_has_no_secrets(
    models: ModelsConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PD_QWEN_BASE_URL", "https://api.example.org/v1?token=SECRET123")
    monkeypatch.setenv("PD_QWEN_API_KEY", "sk-very-secret")
    summary = describe_provider(models)
    text = str(summary)
    assert "SECRET123" not in text and "sk-very-secret" not in text
    assert summary["host"] == "https://api.example.org"
    assert summary["configured"] is True
    # После зонда 16.09.2026 в models.yaml записаны supports и verified.
    assert summary["probed"] is True and summary["roles"]["llm"]["verified"] is True
    assert redact_url("https://h.example/v1/abc?key=1") == "https://h.example"
    assert redact_url(None) is None
    refs = model_refs(models)
    assert {r["role"] for r in refs} == {"llm", "vlm"}
    # У ролей разные модели: llm — открытая 32B, vlm — своя. Каждая ссылка
    # обязана называть модель своей роли, а не одну на всех.
    assert all(r["name"] == models.role(r["role"]).model for r in refs)


def test_complete_sync_outside_loop(make_client: Callable[..., LlmClient]) -> None:
    client = make_client(StubTransport())
    resp = client.complete_sync(plan_request(), parse=Plan)
    assert isinstance(resp.parsed, Plan)


async def test_complete_sync_inside_loop_refused(make_client: Callable[..., LlmClient]) -> None:
    client = make_client(StubTransport())
    with pytest.raises(RuntimeError):
        client.complete_sync(plan_request())
    await asyncio.sleep(0)
