"""Сборка мусора: живое не трогается, сироты удаляются по сроку после отметки."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from presentation_designer.pipeline.gc import collect_garbage, live_sets
from presentation_designer.pipeline.jobs import Orchestrator
from tests.pipeline.helpers import run_generation


def _gc(orch: Orchestrator, **kw: object) -> dict[str, object]:
    report = collect_garbage(
        orch.state,
        orch.files,
        orch.artifacts,
        orch.settings.retention,
        report_path=orch.gc_report_path,
        **kw,  # type: ignore[arg-type]
    )
    return report.to_dict()


def test_live_project_is_untouched(
    client: TestClient, orchestrator: Orchestrator, pptx_bytes: bytes, xlsx_bytes: bytes
) -> None:
    run = run_generation(client, pptx_bytes, xlsx_bytes)
    job_dir = orchestrator.artifacts.job_dir(run["job_id"])
    assert job_dir.is_dir()
    live = live_sets(orchestrator.state.gc_snapshot())
    assert run["job_id"] in live["jobs"]
    assert run["package_id"] in live["packages"]
    assert {run["template_sha"], run["data_sha"]} <= live["blobs"]

    first = _gc(orchestrator, grace_override=timedelta(0))
    assert first["marked"] == {} and first["removed"] == {}
    assert job_dir.is_dir()
    assert client.get(f"/api/generations/{run['job_id']}").status_code == 200
    assert orchestrator.gc_report_path.is_file()
    health = client.get("/api/health").json()
    assert health["gc"]["removed"] == {} and "items" not in health["gc"]


def test_deleted_project_is_collected_after_grace(
    client: TestClient, orchestrator: Orchestrator, pptx_bytes: bytes, xlsx_bytes: bytes
) -> None:
    run = run_generation(client, pptx_bytes, xlsx_bytes)
    keep = run_generation(client, pptx_bytes, xlsx_bytes, title="Остаётся")
    job_dir = orchestrator.artifacts.job_dir(run["job_id"])
    assert client.delete(f"/api/projects/{run['project_id']}").status_code == 204

    # Первый проход только отмечает сирот: генерация и импорт пакета — два задания, сам пакет.
    marked = _gc(orchestrator)
    assert marked["marked"] == {"job": 2, "package": 1}
    assert marked["removed"] == {}
    assert job_dir.is_dir(), "до истечения срока артефакты остаются"
    # Байты материала общие с живым проектом: не сироты. Шаблон — библиотека.
    assert "blob" not in marked["marked"]

    # Пока срок не вышел — ждут.
    waiting = _gc(orchestrator)
    assert waiting["removed"] == {} and waiting["kept"] == {"job": 2, "package": 1}

    # После срока сироты удаляются: артефакты, строки задания и пакета.
    grace = orchestrator.settings.retention.orphan_jobs_hours
    removed = _gc(orchestrator, now=datetime.now(UTC) + timedelta(hours=grace + 1))
    assert removed["removed"] == {"job": 2, "package": 1}
    assert removed["freed_bytes"] > 0
    assert not job_dir.exists()
    assert client.get(f"/api/generations/{run['job_id']}").status_code == 404
    assert client.get(f"/api/content/{run['package_id']}").status_code == 404
    # Живой проект цел: задание, файлы и артефакты на месте.
    assert client.get(f"/api/generations/{keep['job_id']}").status_code == 200
    assert orchestrator.artifacts.job_dir(keep["job_id"]).is_dir()
    assert orchestrator.files.exists(keep["data_sha"])
    assert orchestrator.state.gc_marks() == {}


def test_reference_returned_clears_mark(
    client: TestClient, orchestrator: Orchestrator, pptx_bytes: bytes, xlsx_bytes: bytes
) -> None:
    run = run_generation(client, pptx_bytes, xlsx_bytes)
    client.delete(f"/api/projects/{run['project_id']}")
    _gc(orchestrator)
    assert ("job", run["job_id"]) in orchestrator.state.gc_marks()
    # Задание снова привязано к проекту (например, открыто по ссылке /workspace?job=…).
    client.post("/api/projects", json={"title": "Возврат", "job_id": run["job_id"]})
    report = _gc(orchestrator, grace_override=timedelta(0))
    # Задание, его пакет и импорт снова живы; байты материала удалённого проекта
    # недостижимы (строки файлов ушли вместе с проектом) и удаляются.
    assert report["removed"] == {"blob": 1}
    assert ("job", run["job_id"]) not in orchestrator.state.gc_marks()
    assert client.get(f"/api/generations/{run['job_id']}").status_code == 200


def test_unreferenced_blob_and_stray_dirs(
    client: TestClient, orchestrator: Orchestrator, pptx_bytes: bytes
) -> None:
    project_id = client.post("/api/projects", json={}).json()["project_id"]
    row = client.post(
        f"/api/projects/{project_id}/files",
        files=[("files", ("brief.md", "# Тезисы\n\nтекст".encode(), "text/markdown"))],
    ).json()[0]
    client.delete(f"/api/projects/{project_id}/files/{row['file_id']}")
    stray = orchestrator.artifacts.root / "jobs" / "job_stray"
    (stray / "compact" / "r1").mkdir(parents=True)
    (stray / "compact" / "r1" / "deck.pptx").write_bytes(b"PK" * 100)
    staging = orchestrator.artifacts.root / "jobs" / ".job_tmp"
    staging.mkdir()
    old_tmp = orchestrator.files.tmp / "upload-old"
    old_tmp.write_bytes(b"x" * 10)
    stale = time.time() - orchestrator.settings.retention.upload_tmp_hours * 3600 - 60
    os.utime(old_tmp, (stale, stale))
    fresh_tmp = orchestrator.files.tmp / "upload-fresh"
    fresh_tmp.write_bytes(b"y")

    first = _gc(orchestrator)
    assert first["marked"] == {"blob": 1, "dir": 1}
    assert orchestrator.files.exists(row["sha256"])
    assert first["removed"] == {"tmp": 1}
    assert not old_tmp.exists() and fresh_tmp.exists()

    second = _gc(orchestrator, grace_override=timedelta(0))
    assert second["removed"] == {"blob": 1, "dir": 1}
    assert not orchestrator.files.exists(row["sha256"])
    assert not stray.exists()
    assert staging.exists(), "временные каталоги публикации сборка мусора не трогает"


def test_dry_run_changes_nothing(
    client: TestClient, orchestrator: Orchestrator, pptx_bytes: bytes, xlsx_bytes: bytes
) -> None:
    run = run_generation(client, pptx_bytes, xlsx_bytes)
    client.delete(f"/api/projects/{run['project_id']}")
    report = _gc(orchestrator, dry_run=True)
    assert report["marked"]["job"] == 2
    assert orchestrator.state.gc_marks() == {}
    _gc(orchestrator)
    dry = _gc(orchestrator, dry_run=True, grace_override=timedelta(0))
    assert dry["removed"]["job"] == 2 and dry["freed_bytes"] > 0
    assert orchestrator.artifacts.job_dir(run["job_id"]).is_dir()


def test_orchestrator_schedules_gc_once_per_interval(orchestrator: Orchestrator) -> None:
    assert orchestrator.ensure_gc_scheduled() is True
    assert orchestrator.ensure_gc_scheduled() is False
    assert orchestrator.gc_report_path.is_file(), "встроенный исполнитель выполнил задачу сразу"
