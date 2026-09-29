"""Срок задания истёк, пока варианты досбирались: итог — по вариантам, а не ошибка."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from presentation_designer.pipeline import jobs
from presentation_designer.pipeline.jobs import Orchestrator
from tests.pipeline.helpers import run_generation

DEADLINE = {"code": "deadline_exceeded", "message": "Общий срок задания истёк", "retryable": True}


def test_variants_done_after_deadline_are_kept(
    client: TestClient,
    orchestrator: Orchestrator,
    pptx_bytes: bytes,
    xlsx_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = run_generation(client, pptx_bytes, xlsx_bytes)
    job_id = run["job_id"]
    done = orchestrator.state.get_job(job_id)["status"]
    assert done in ("succeeded", "needs_review")
    # Служебная проверка сроков пометила задание ошибкой, а варианты потом дособрались.
    orchestrator.state.update_job(job_id, status="failed", error=DEADLINE)
    monkeypatch.setattr(jobs, "get_orchestrator", lambda: orchestrator)
    jobs.task_finalize(job_id)
    job = orchestrator.state.get_job(job_id)
    assert job["status"] == done and job["error"] is None


def test_other_failures_stay_final(
    client: TestClient,
    orchestrator: Orchestrator,
    pptx_bytes: bytes,
    xlsx_bytes: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = run_generation(client, pptx_bytes, xlsx_bytes)
    job_id = run["job_id"]
    lost = {"code": "worker_lost", "message": "Исполнитель задания потерян", "retryable": True}
    orchestrator.state.update_job(job_id, status="failed", error=lost)
    monkeypatch.setattr(jobs, "get_orchestrator", lambda: orchestrator)
    jobs.task_finalize(job_id)
    job = orchestrator.state.get_job(job_id)
    assert job["status"] == "failed" and job["error"]["code"] == "worker_lost"
