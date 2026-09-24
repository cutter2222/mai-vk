"""Первый вариант собирается один: остальные ставятся в очередь за ним, а не рядом."""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient
from pptx import Presentation

from presentation_designer.pipeline import jobs
from presentation_designer.pipeline.jobs import Orchestrator
from presentation_designer.pipeline.run import ChartImagesOutput
from presentation_designer.pipeline.stubs import StubLayers
from tests.pipeline import helpers

ROOT = pathlib.Path(__file__).resolve().parents[2]

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass
class Recorder:
    """Исполнитель, который только записывает постановки."""

    name: str = "record"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def enqueue(self, queue: str, func: Any, args: tuple[Any, ...], **kw: Any) -> str:
        rq_id = f"rq{len(self.calls)}"
        self.calls.append({"id": rq_id, "func": func, "args": args, "depends_on": kw["depends_on"]})
        return rq_id

    def status(self, rq_id: str) -> str | None:
        return None


class RevisionLayers(StubLayers):
    """Заглушки, у которых копия получает r2: диаграмма-картинка заменена."""

    def __init__(self) -> None:
        super().__init__(stage_delay_ms=0)

    def chart_images(self, pptx_path: pathlib.Path, progress: Any = None) -> ChartImagesOutput:
        prs = Presentation(str(pptx_path))
        prs.core_properties.comments = "диаграммы заменены"
        prs.save(str(pptx_path))
        return ChartImagesOutput(
            report={"version": 1, "pictures": [], "swaps": []},
            message="Сделал диаграммы редактируемыми: 1 (слайд 2).",
            replaced=1,
        )


def test_primary_variant_goes_first_and_others_wait(
    client: TestClient, orchestrator: Orchestrator, pptx_bytes: bytes, docx_bytes: bytes
) -> None:
    pid = client.post("/api/projects", json={}).json()["project_id"]
    tpl, doc = client.post(
        f"/api/projects/{pid}/files",
        files=[
            ("files", ("t.pptx", pptx_bytes, PPTX_MIME)),
            ("files", ("d.docx", docx_bytes, DOCX_MIME)),
        ],
    ).json()
    template_id = client.post("/api/templates", json={"file_id": tpl["file_id"]}).json()[
        "template_id"
    ]
    package_id = client.post("/api/content", json={"file_ids": [doc["file_id"]]}).json()[
        "package_id"
    ]

    recorder = Recorder()
    orchestrator.executor = recorder  # type: ignore[assignment]
    orchestrator.submit_generation(
        {
            "schema_version": "1.2",
            "template_id": template_id,
            "package_id": package_id,
            "settings": {"variants": ["compact", "balanced", "detailed"]},
        }
    )
    story = next(c for c in recorder.calls if c["func"] is jobs.task_story)
    variants = [c for c in recorder.calls if c["func"] is jobs.task_variant]
    assert [c["args"][1] for c in variants] == ["balanced", "compact", "detailed"]
    assert story["id"] in variants[0]["depends_on"]
    assert variants[1]["depends_on"] == variants[2]["depends_on"] == [variants[0]["id"]]
    finalize = next(c for c in recorder.calls if c["func"] is jobs.task_finalize)
    assert finalize["depends_on"] == [c["id"] for c in variants]


def test_order_keeps_request_without_primary() -> None:
    assert jobs.ordered_variants(["compact", "detailed"]) == ["compact", "detailed"]
    assert jobs.ordered_variants(["original"]) == ["original"]


def _open_deck(client: TestClient, pptx_bytes: bytes) -> dict[str, str]:
    pid = client.post("/api/projects", json={}).json()["project_id"]
    (row,) = client.post(
        f"/api/projects/{pid}/files", files=[("files", ("deck.pptx", pptx_bytes, PPTX_MIME))]
    ).json()
    template_id = client.post("/api/templates", json={"file_id": row["file_id"]}).json()[
        "template_id"
    ]
    package_id = client.post("/api/content", json={"file_ids": [row["file_id"]]}).json()[
        "package_id"
    ]
    return {"template_id": template_id, "package_id": package_id}


def test_open_deck_background_goes_to_interactive_queue(
    client: TestClient, orchestrator: Orchestrator, pptx_bytes: bytes
) -> None:
    """Копия показана запросом; фон открытой презентации — в очереди interactive, которую
    воркеры генерации берут первой; полная сборка ждёт и смысловой план, и этот фон."""
    ids = _open_deck(client, pptx_bytes)
    recorder = helpers.Recorder()
    orchestrator.executor = recorder  # type: ignore[assignment]
    job = orchestrator.submit_generation(
        {
            "schema_version": "1.2",
            **ids,
            "settings": {"variants": ["original"], "run_contextual_audit": False},
        }
    )
    # r1 опубликована до того, как выполнилась хоть одна задача.
    assert orchestrator.state.get_revision(job["job_id"], "original", 1)["manifest"]
    queues = {c["func"]: c["queue"] for c in recorder.calls}
    q = orchestrator.settings.queue
    assert queues[jobs.task_original_preview] == q.interactive_queue == "interactive"
    # Разбор без модели (план по профилю, сборка «как есть») и итог — тоже фон открытой
    # презентации: они не ждут чужих сборок в очереди generation.
    assert queues[jobs.task_story] == queues[jobs.task_variant] == q.interactive_queue
    assert queues[jobs.task_finalize] == q.interactive_queue
    # С проверкой моделью (минуты работы) сборка идёт в очереди generation.
    recorder.calls.clear()
    orchestrator.submit_generation(
        {
            "schema_version": "1.2",
            **ids,
            "settings": {"variants": ["original"], "run_contextual_audit": True},
        }
    )
    queues = {c["func"]: c["queue"] for c in recorder.calls}
    assert queues[jobs.task_story] == queues[jobs.task_variant] == q.generation_queue
    assert queues[jobs.task_original_preview] == queues[jobs.task_finalize] == q.interactive_queue
    story = next(c for c in recorder.calls if c["func"] is jobs.task_story)
    preview = next(c for c in recorder.calls if c["func"] is jobs.task_original_preview)
    variant = next(c for c in recorder.calls if c["func"] is jobs.task_variant)
    assert preview["depends_on"] == []
    assert story["id"] in variant["depends_on"] and preview["id"] in variant["depends_on"]


def test_worker_queue_order_puts_interactive_first() -> None:
    compose = (ROOT / "docker" / "compose.yaml").read_text(encoding="utf-8")
    assert '"--queues", "interactive", "generation"]' in compose
    assert compose.count('"interactive", "generation"') == 2  # команда и healthcheck


def test_edit_before_the_plan_waits_and_rebases(
    client: TestClient, orchestrator: Orchestrator, pptx_bytes: bytes
) -> None:
    """Правка из чата сразу после открытия: плана ещё нет — правка принимается, встаёт за
    задачами генерации и честно говорит, когда применится; перед запуском база переносится на
    последнюю ревизию (r2 после замены диаграмм)."""
    ids = _open_deck(client, pptx_bytes)
    recorder = helpers.Recorder()
    orchestrator.executor = recorder  # type: ignore[assignment]
    # Разбор шаблона и импорт тоже ещё не выполнены: всё, кроме копии, ждёт очереди.
    orchestrator.state.update_template(ids["template_id"], status="queued")
    job_id = orchestrator.submit_generation(
        {"schema_version": "1.2", **ids, "settings": {"variants": ["original"]}}
    )["job_id"]
    generation_rq = orchestrator.state.get_job(job_id)["rq_ids"]

    r = client.post(
        f"/api/generations/{job_id}/variants/original/edits",
        json={"base_revision": 1, "slide_index": 1, "instruction": "Новый заголовок слайда"},
    )
    assert r.status_code == 202, r.text
    edit_id = r.json()["edit_job_id"]
    edit_call = next(c for c in recorder.calls if c["func"] is jobs.task_edit)
    assert edit_call["queue"] == orchestrator.settings.queue.repair_queue
    # Правка ждёт сборку варианта (там появляется план), но не итог задания.
    finalize = next(c for c in recorder.calls if c["func"] is jobs.task_finalize)
    assert edit_call["depends_on"] == [rid for rid in generation_rq if rid != finalize["id"]]
    status = client.get(f"/api/jobs/{edit_id}").json()
    assert status["status"] == "queued"
    assert status["progress"]["message"].startswith("Разбираю презентацию, правку применю через ~")
    # Вторая правка той же ревизии ждёт первую, как и раньше.
    busy = client.post(
        f"/api/generations/{job_id}/variants/original/edits",
        json={"base_revision": 1, "slide_index": 0, "instruction": "короче"},
    )
    assert busy.status_code == 409 and busy.json()["error"]["code"] == "repair_in_progress"

    # Разбор в фоне: шаблон готов, копия получила r2, сборка записала план в r2.
    orchestrator.state.update_template(ids["template_id"], status="succeeded")
    orchestrator.layers = RevisionLayers()
    for func in (jobs.task_original_preview, jobs.task_story, jobs.task_variant):
        recorder.run(func)
    recorder.run(jobs.task_finalize)
    variant = orchestrator.state.get_variant(job_id, "original")
    assert variant["revision"] == 2
    recorder.run(jobs.task_edit)
    edit = orchestrator.state.get_repair(edit_id)
    assert edit["result"] == "applied" and edit["base_revision"] == 2 and edit["new_revision"] == 3
    assert orchestrator.state.get_variant(job_id, "original")["revision"] == 3


def test_edit_after_failed_parse_says_so(
    client: TestClient, orchestrator: Orchestrator, pptx_bytes: bytes
) -> None:
    ids = _open_deck(client, pptx_bytes)
    recorder = helpers.Recorder()
    orchestrator.executor = recorder  # type: ignore[assignment]
    job_id = orchestrator.submit_generation(
        {"schema_version": "1.2", **ids, "settings": {"variants": ["original"]}}
    )["job_id"]
    url = f"/api/generations/{job_id}/variants/original/edits"
    r = client.post(url, json={"base_revision": 1, "slide_index": 0, "instruction": "короче"})
    assert r.status_code == 202
    edit_id = r.json()["edit_job_id"]
    # Разбор шаблона не удался: вариант падает, отложенная правка честно это говорит.
    orchestrator.state.update_template(
        ids["template_id"], status="failed", error={"code": "analyze_failed", "message": "x"}
    )
    recorder.run(jobs.task_story)
    recorder.run(jobs.task_edit)
    status = client.get(f"/api/jobs/{edit_id}").json()
    assert status["status"] == "failed" and status["error"]["code"] == "plan_unavailable"
    assert "правки из чата недоступны" in status["error"]["message"]
    again = client.post(url, json={"base_revision": 1, "slide_index": 0, "instruction": "короче"})
    assert again.status_code == 409 or again.status_code == 422
    assert "Разбор презентации не удался" in again.json()["error"]["message"]


def test_deferred_edit_says_when_the_queue_is_busy(
    client: TestClient, orchestrator: Orchestrator, pptx_bytes: bytes
) -> None:
    """Всё, чего ждал разбор, готово, а смысловой план не начинается дольше нескольких секунд:
    воркеры генерации заняты чужими сборками. Срока тогда нет, и фраза говорит об очереди."""
    from datetime import UTC, datetime, timedelta

    from presentation_designer.pipeline.results import deferred_edit_message

    ids = _open_deck(client, pptx_bytes)
    orchestrator.executor = helpers.Recorder()  # type: ignore[assignment]
    job_id = orchestrator.submit_generation(
        {"schema_version": "1.2", **ids, "settings": {"variants": ["original"]}}
    )["job_id"]
    fresh = deferred_edit_message(orchestrator.state, job_id)
    assert fresh.startswith("Разбираю презентацию, правку применю через ~")
    long_ago = (datetime.now(UTC) - timedelta(seconds=40)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
    # Шаблон и содержание разобраны давно (из кэша), задание создано 40 с назад.
    gen = orchestrator.state.get_generation(job_id)
    for owner in (
        orchestrator.state.get_template(gen["template_id"])["job_id"],
        orchestrator.state.get_package(gen["package_id"])["job_id"],
    ):
        orchestrator.state.update_job(owner, finished_at=long_ago + "Z")
    orchestrator.state.update_job(job_id, created_at=long_ago + "Z")
    queued = deferred_edit_message(orchestrator.state, job_id)
    assert "очередь занята другими сборками" in queued
    progress = client.get(f"/api/generations/{job_id}").json()["progress"]
    assert "очередь занята другими сборками" in progress["message"]
