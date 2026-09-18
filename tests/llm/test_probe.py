"""Зонд на заглушке: проверки выполняются без сети, отчёт собирается, секретов в нём нет,
smoke-команда отказывается работать без настроенного провайдера."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from presentation_designer.llm import probe as pr
from presentation_designer.llm.client import LlmClient
from presentation_designer.llm.stub import StubTransport
from presentation_designer.llm.types import Request


def _stub_for_probe() -> StubTransport:
    stub = StubTransport()

    def by_schema(req: Request, _attempt: int) -> object:
        name = req.schema_name
        text = "\n".join(m.text for m in req.messages)
        if req.response_format == "text":
            return "Рим" if "Италии" in text else "Париж"
        if name == "ProbePlan" or "три раздела" in text:
            return {
                "title": "План",
                "sections": [{"title": f"Р{i}", "key_points": ["т"]} for i in range(3)],
            }
        if name == "ProbeImageReading":
            return {"number": 42, "title": "Итоги квартала"}
        if name == "ProbeWhichImage":
            return {"index": 1, "numbers": [42, 17]}
        return {
            "topic": "итоги квартала",
            "audience": "совет директоров",
            "goal": "утвердить бюджет",
            "slide_count_min": 10,
            "slide_count_max": 12,
        }

    stub.on(lambda _r: True, by_schema)
    return stub


async def test_probe_runs_on_stub_and_renders_report(
    make_client: Callable[..., LlmClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PD_QWEN_BASE_URL", "https://api.example.org/v1?token=SECRET")
    monkeypatch.setenv("PD_QWEN_API_KEY", "sk-SECRET")
    client = make_client(_stub_for_probe())
    probe = pr.Probe(
        client, role="llm", vlm_role="vlm", deadline_s=5, documented={"concurrency": 4, "rpm": 60}
    )
    result = await probe.run([1, 2, 8])
    names = {c.name for c in result.checks}
    assert {
        "text",
        "usage",
        "json_object",
        "json_schema",
        "brief_extract",
        "image",
        "multi_image",
        "reasoning_off",
        "reasoning_low",
    } <= names
    assert pr.failed_checks(result) == [], [(c.name, c.detail) for c in result.checks]
    assert result.check("provider_cache").kind == "info"
    assert result.check("streaming") is not None and result.check("streaming").ok is None
    assert [row["level"] for row in result.concurrency] == [1, 2], "8 выше квоты — пропущен"
    assert all(row["ok"] == row["requests"] for row in result.concurrency)
    assert any("8 выше квоты" in n for n in result.notes)

    report = pr.render_report(result, commit="abc123")
    assert "SECRET" not in report and "sk-" not in report
    from presentation_designer.shared.settings import get_models_config

    assert "https://api.example.org" in report
    assert get_models_config().role("llm").model in report
    assert "| json_schema | да |" in report and "| multi_image | да |" in report
    assert "Короткий зонд их не доказывает" in report
    snippet = pr.yaml_snippet(result)
    assert "json_schema: true" in snippet and "rpm: 60" in snippet
    assert "verified: { by: probe" in snippet
    json.dumps(result.as_dict())  # сериализуется для runs/probe


def test_slide_image_is_png() -> None:
    img = pr.render_slide_image("Заголовок", 42, (10, 20, 30))
    assert img.data[:8] == b"\x89PNG\r\n\x1a\n" and img.mime == "image/png"


def test_smoke_refuses_without_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PD_QWEN_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("PD_QWEN_API_KEY", "replace-me")
    monkeypatch.delenv("PD_VALKEY_URL", raising=False)
    from presentation_designer.shared import settings as s

    s.reset_cache()
    with pytest.raises(SystemExit, match="не настроен"):
        pr.main(["smoke"])
    with pytest.raises(SystemExit, match="не настроен"):
        pr.main(["probe", "--report", "/dev/null"])
    s.reset_cache()


def test_parser_defaults() -> None:
    parser = pr.build_parser()
    args = parser.parse_args(["probe", "--concurrency", "1,2"])
    assert args.role == "llm" and args.concurrency == "1,2"
    lim = parser.parse_args(["limiter", "--processes", "3"])
    assert lim.processes == 3 and lim.concurrency == 2
