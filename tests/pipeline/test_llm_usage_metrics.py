"""Вызовы модели копятся в строке задания по этапам и попадают в метрики результата."""

from __future__ import annotations

import pathlib

from presentation_designer.pipeline.results import build_generation_result
from presentation_designer.pipeline.state import State


def _usage(calls: int, prompt: int, completion: int, *, hits: int = 0) -> dict:
    return {
        "llm_calls": [
            {"stage": "plan", "model": "m", "attempt": 1, "cache_hit": i < hits, "ok": True}
            for i in range(calls)
        ],
        "totals": {"llm_calls": calls, "prompt_tokens": prompt, "completion_tokens": completion},
        "quota_wait_ms": 10,
        "retries": 1,
        "cache": {"llm_hits": hits, "llm_misses": calls - hits},
    }


def test_llm_usage_accumulates_across_stages(tmp_path: pathlib.Path) -> None:
    state = State(tmp_path / "state.sqlite3")
    job = state.create_job(kind="generation")
    job_id = job["job_id"]
    state.create_generation(
        job_id=job_id,
        template_id="tpl",
        package_id="pkg",
        request={},
        idempotency_key=None,
        execution_mode={"mode": "stub", "layers": {}},
        versions={},
        variants=[{"variant_id": "compact", "axis": "density", "value": "compact"}],
    )
    assert state.get_job(job_id)["metrics"] == {}

    state.add_llm_usage(job_id, _usage(2, 100, 40))
    state.add_llm_usage(job_id, _usage(3, 50, 10, hits=1))
    state.add_llm_usage(job_id, {"llm_calls": [], "totals": {"llm_calls": 0}})  # пустой этап

    stored = state.get_job(job_id)["metrics"]
    assert len(stored["llm_calls"]) == 5
    assert stored["totals"] == {"llm_calls": 5, "prompt_tokens": 150, "completion_tokens": 50}
    assert stored["quota_wait_ms"] == 20 and stored["retries"] == 2
    assert stored["cache"] == {"llm_hits": 1, "llm_misses": 4}

    metrics = build_generation_result(state, job_id)["metrics"]
    assert metrics["totals"]["llm_calls"] == 5
    assert metrics["totals"]["prompt_tokens"] == 150
    assert metrics["totals"]["completion_tokens"] == 50
    assert len(metrics["llm_calls"]) == 5
    assert metrics["quota_wait_ms"] == 20 and metrics["retries"] == 2
    assert metrics["cache"]["llm_hits"] == 1 and metrics["cache"]["llm_misses"] == 4
    assert "usage_estimated" not in metrics["totals"]


def test_estimated_usage_is_flagged(tmp_path: pathlib.Path) -> None:
    state = State(tmp_path / "state.sqlite3")
    job_id = state.create_job(kind="generation")["job_id"]
    state.create_generation(
        job_id=job_id,
        template_id="tpl",
        package_id="pkg",
        request={},
        idempotency_key=None,
        execution_mode={"mode": "stub", "layers": {}},
        versions={},
        variants=[],
    )
    usage = _usage(1, 10, 5)
    usage["totals"]["usage_estimated"] = True
    state.add_llm_usage(job_id, usage)
    assert build_generation_result(state, job_id)["metrics"]["totals"]["usage_estimated"] is True
