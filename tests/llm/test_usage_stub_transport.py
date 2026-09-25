"""Учёт usage в формате контракта, заглушка по схеме, сборка параметров транспорта."""

from __future__ import annotations

import json
from typing import Any

import pytest

from presentation_designer.contracts.models import LlmCall
from presentation_designer.contracts.models import Metrics1 as ResultMetrics
from presentation_designer.llm.stub import StubTransport, minimal_instance
from presentation_designer.llm.tokens import estimate_request_tokens, estimate_text_tokens
from presentation_designer.llm.transport import build_params, build_reasoning, usage_from
from presentation_designer.llm.types import Image, Message, Request, Response, Usage
from presentation_designer.llm.usage import UsageRecorder


def test_usage_recorder_matches_contract() -> None:
    rec = UsageRecorder()
    req = Request(
        role="llm",
        messages=[Message("user", "x")],
        stage="plan",
        variant_id="compact",
        slide_ids=["s1"],
        prompt=("plan.slides", "0.1.0"),
    )
    rec.record_response(
        req,
        Response(
            text="{}",
            usage=Usage(100, 20, 5, "provider"),
            model="m",
            latency_ms=300,
            quota_wait_ms=40,
            attempts=2,
        ),
    )
    rec.record_response(
        req, Response(text="{}", usage=Usage(0, 0, None, "cache"), model="m", cache_hit=True)
    )
    rec.record_failure(req, model="m", attempts=3, error_code="rate_limited", quota_wait_ms=10)
    metrics = rec.metrics()
    for call in metrics["llm_calls"]:
        LlmCall.model_validate(call)
    ResultMetrics.model_validate(metrics)
    assert metrics["totals"] == {"llm_calls": 3, "prompt_tokens": 100, "completion_tokens": 20}
    assert metrics["retries"] == 3 and metrics["quota_wait_ms"] == 50
    assert metrics["cache"] == {"llm_hits": 1, "llm_misses": 1}
    assert metrics["llm_calls"][0]["prompt"] == {"name": "plan.slides", "version": "0.1.0"}
    assert metrics["llm_calls"][2]["error_code"] == "rate_limited"
    stages = rec.by_stage()
    assert stages["plan"]["calls"] == 3 and stages["plan"]["errors"] == 1


def test_minimal_instance_from_schema() -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "required": ["title", "kind", "items", "count", "nested"],
        "properties": {
            "title": {"type": "string", "maxLength": 5},
            "kind": {"enum": ["a", "b"]},
            "items": {"type": "array", "minItems": 2, "items": {"type": "integer"}},
            "count": {"type": "number", "minimum": 3},
            "nested": {"$ref": "#/$defs/inner"},
            "optional": {"type": "string"},
        },
        "$defs": {
            "inner": {
                "type": "object",
                "required": ["flag", "when"],
                "properties": {
                    "flag": {"type": "boolean"},
                    "when": {"type": "string", "format": "date-time"},
                },
            }
        },
    }
    doc = minimal_instance(schema, schema, "abc")
    assert doc["kind"] == "a" and len(doc["title"]) <= 5 and doc["items"] == [0, 0]
    assert doc["count"] == 3.0 and doc["nested"] == {"flag": False, "when": "2026-01-01T00:00:00Z"}
    assert "optional" not in doc
    any_of = {"anyOf": [{"type": "object", "required": ["x"], "properties": {"x": {"const": 1}}}]}
    assert minimal_instance(any_of, any_of, "s") == {"x": 1}


async def test_stub_is_deterministic_and_counts_calls() -> None:
    stub = StubTransport()
    req = Request(role="llm", messages=[Message("user", "один")], response_format="json_object")
    a = await stub.complete(req, model="m", timeout_s=1)
    b = await stub.complete(req, model="m", timeout_s=1)
    assert a.text == b.text and json.loads(a.text)["stub"]
    other = Request(role="llm", messages=[Message("user", "два")], response_format="json_object")
    assert (await stub.complete(other, model="m", timeout_s=1)).text != a.text
    assert len(stub.calls) == 3 and a.usage is not None and a.usage.source == "provider"


def test_build_params_formats_and_reasoning() -> None:
    req = Request(
        role="llm",
        messages=[Message("system", "s"), Message("user", "u")],
        response_format="json_schema",
        schema={"type": "object"},
        schema_name="plan",
        reasoning="low",
        max_output_tokens=500,
        temperature=0.1,
        seed=7,
    )
    qwen = build_params(req, model="q", reasoning_style="qwen_enable_thinking")
    assert qwen["response_format"]["json_schema"]["name"] == "plan"
    assert qwen["response_format"]["json_schema"]["strict"] is True
    assert qwen["max_completion_tokens"] == 500 and qwen["seed"] == 7
    assert qwen["extra_body"] == {"enable_thinking": True, "thinking_budget": 1024}
    assert "reasoning_effort" not in qwen
    openai_style = build_params(req, model="o", reasoning_style="openai_reasoning_effort")
    assert openai_style["reasoning_effort"] == "low" and "extra_body" not in openai_style
    off = Request(role="llm", messages=[Message("user", "u")], reasoning="off")
    assert build_reasoning(off, "qwen_enable_thinking") == ({}, {"enable_thinking": False})
    assert build_reasoning(off, "openai_reasoning_effort") == ({}, {})
    assert build_reasoning(off, "none") == ({}, {})
    assert build_reasoning(off, "openrouter_reasoning") == ({}, {"reasoning": {"enabled": False}})
    assert build_reasoning(req, "openrouter_reasoning") == ({}, {"reasoning": {"effort": "low"}})
    plain = build_params(off, model="m", reasoning_style="none", stream=True)
    assert plain["stream"] is True and plain["stream_options"] == {"include_usage": True}
    assert "response_format" not in plain
    obj = Request(role="llm", messages=[Message("user", "u")], response_format="json_object")
    assert build_params(obj, model="m", reasoning_style="none")["response_format"] == {
        "type": "json_object"
    }


def test_usage_from_dict_and_missing() -> None:
    usage = usage_from(
        {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "completion_tokens_details": {"reasoning_tokens": 2},
        }
    )
    assert usage is not None
    assert (usage.prompt_tokens, usage.completion_tokens, usage.reasoning_tokens) == (10, 5, 2)
    assert usage_from(None) is None and usage_from({}) is None


def test_token_estimate_is_conservative() -> None:
    text = "Слово " * 100  # 600 символов
    assert estimate_text_tokens(text, 3.0) >= 200
    req = Request(role="vlm", messages=[Message("user", text, (Image(b"x"), Image(b"y")))])
    assert estimate_request_tokens(req, image_tokens=1000) >= 2200


@pytest.mark.parametrize("bad", ["{", "", "]"])
def test_stub_rules_can_raise(bad: str) -> None:
    stub = StubTransport()
    stub.fail(ValueError(bad or "пусто"), times=1)
    assert stub.rules[0].times == 1
