"""Первый вариант собирается один: остальные ставятся в очередь за ним, а не рядом."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient

from presentation_designer.pipeline import jobs
from presentation_designer.pipeline.jobs import Orchestrator

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
