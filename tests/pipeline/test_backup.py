"""Резервная копия: снимок базы, файлы и артефакты; проверка в отдельный каталог;
восстановление на чистый каталог с сохранением прежних данных."""

from __future__ import annotations

import pathlib
import tarfile

import pytest
from fastapi.testclient import TestClient

from presentation_designer.pipeline import backup as bk
from presentation_designer.pipeline.jobs import Orchestrator
from presentation_designer.pipeline.state import LATEST_SCHEMA, State
from tests.pipeline.helpers import run_generation


def _backup(orch: Orchestrator, label: str | None = None, keep: int = 7) -> bk.BackupResult:
    return bk.create_backup(
        orch.state,
        orch.files,
        orch.artifacts,
        orch.settings.backups_dir,
        label=label,
        keep=keep,
        valkey_url=None,
    )


def test_backup_contents_and_verify(
    client: TestClient, orchestrator: Orchestrator, pptx_bytes: bytes, xlsx_bytes: bytes
) -> None:
    run = run_generation(client, pptx_bytes, xlsx_bytes)
    # Незавершённая публикация не попадает в копию.
    staging = orchestrator.artifacts.job_dir(run["job_id"]) / "compact" / ".r2.tmp"
    staging.mkdir()
    (staging / "deck.pptx").write_bytes(b"partial")

    result = _backup(orchestrator, label="тест копии")
    assert result.path.is_file() and result.name.endswith("-тест-копии")
    with tarfile.open(result.path) as tar:
        names = tar.getnames()
    assert "meta.json" in names and "state.sqlite3" in names
    assert f"uploads/{run['data_sha'][:2]}/{run['data_sha']}" in names
    assert any(n.startswith(f"artifacts/jobs/{run['job_id']}/compact/r1/") for n in names)
    assert any(n.startswith(f"artifacts/templates/{run['template_id']}/") for n in names)
    assert not any(".r2.tmp" in n for n in names)
    assert result.meta["schema_version"] == LATEST_SCHEMA
    assert result.meta["counts"]["projects"] == 1
    assert result.meta["valkey"] is None

    work = orchestrator.settings.backups_dir / ".verify"
    report = bk.verify_backup(result.path, work)
    assert report.ok, report.problems
    assert report.checked["blobs"] == 2 and report.checked["revisions"] == 3
    assert report.checked["artifact_files"] > 0 and report.checked["previews"] > 0
    assert not (work / result.name).exists(), "без --keep распакованный каталог удаляется"


def test_verify_detects_missing_and_corrupted_files(
    client: TestClient, orchestrator: Orchestrator, pptx_bytes: bytes, xlsx_bytes: bytes
) -> None:
    run = run_generation(client, pptx_bytes, xlsx_bytes)
    result = _backup(orchestrator)
    work = orchestrator.settings.backups_dir / ".verify"
    report = bk.verify_backup(result.path, work, keep=True)
    assert report.ok and report.work_dir is not None
    unpacked = report.work_dir
    (unpacked / "uploads" / run["data_sha"][:2] / run["data_sha"]).write_bytes(b"broken")
    deck = next((unpacked / "artifacts" / "jobs" / run["job_id"]).rglob("deck.pptx"))
    deck.unlink()
    again = bk.VerifyReport(name=result.name, meta=result.meta)
    bk.check_layout(unpacked / "state.sqlite3", unpacked / "uploads", unpacked / "artifacts", again)
    assert not again.ok
    assert any("размер" in p for p in again.problems)
    assert any("нет артефакта" in p for p in again.problems)


def test_truncated_archive_is_rejected(orchestrator: Orchestrator, tmp_path: pathlib.Path) -> None:
    result = _backup(orchestrator)
    broken = tmp_path / "backup-broken.tar"
    with tarfile.open(broken, "w") as tar:
        with tarfile.open(result.path) as src:
            member = src.getmember("state.sqlite3")
            tar.addfile(member, src.extractfile(member))
    with pytest.raises(bk.BackupError, match="оборвана"):
        bk.read_meta(broken)


def test_prune_keeps_latest(orchestrator: Orchestrator) -> None:
    names = [_backup(orchestrator, label=f"n{i}", keep=100).name for i in range(4)]
    removed = bk.prune_backups(orchestrator.settings.backups_dir, keep=2)
    assert removed == [f"{names[0]}.tar", f"{names[1]}.tar"]
    assert [p.name for p in bk.list_backups(orchestrator.settings.backups_dir)] == [
        f"{names[2]}.tar",
        f"{names[3]}.tar",
    ]


def test_restore_into_clean_dirs_and_previous_kept(
    client: TestClient,
    orchestrator: Orchestrator,
    pptx_bytes: bytes,
    xlsx_bytes: bytes,
    tmp_path: pathlib.Path,
) -> None:
    run = run_generation(client, pptx_bytes, xlsx_bytes)
    result = _backup(orchestrator)
    pptx_name = next(
        v["artifacts"]["pptx"] for v in run["result"]["variants"] if v["variant_id"] == "balanced"
    )

    # Чистые каталоги, как на новом сервере.
    data_dir = tmp_path / "restore" / "data"
    artifacts_dir = tmp_path / "restore" / "artifacts"
    report = bk.restore_backup(
        result.path, data_dir=data_dir, artifacts_dir=artifacts_dir, valkey_url=None
    )
    assert report.verify.ok and report.previous_dir is not None
    state = State(data_dir / "state.sqlite3")
    project = state.get_project(run["project_id"])
    assert project["job_id"] == run["job_id"] and len(project["files"]) == 2
    assert (data_dir / "uploads" / run["data_sha"][:2] / run["data_sha"]).is_file()
    assert (artifacts_dir / "jobs" / run["job_id"] / pptx_name).is_file()
    assert not any(p.name.startswith(".restore-") for p in data_dir.iterdir())

    # Восстановление поверх живых данных: прежние откладываются, а не стираются.
    second = run_generation(client, pptx_bytes, xlsx_bytes, title="После копии")
    report2 = bk.restore_backup(
        result.path,
        data_dir=orchestrator.settings.data_dir,
        artifacts_dir=orchestrator.settings.artifacts_dir,
        valkey_url=None,
    )
    assert report2.previous_dir is not None and (report2.previous_dir / "state.sqlite3").is_file()
    assert (report2.previous_dir / "uploads").is_dir()
    previous_artifacts = next(orchestrator.settings.artifacts_dir.glob(".previous-*"))
    assert (previous_artifacts / "jobs" / second["job_id"]).is_dir()
    assert client.get(f"/api/projects/{second['project_id']}").status_code == 404
    assert client.get(f"/api/projects/{run['project_id']}").status_code == 200
    assert client.get(f"/api/generations/{run['job_id']}/artifacts/{pptx_name}").status_code == 200


def test_restore_only_db_keeps_files(
    client: TestClient, orchestrator: Orchestrator, pptx_bytes: bytes, xlsx_bytes: bytes
) -> None:
    run = run_generation(client, pptx_bytes, xlsx_bytes)
    result = _backup(orchestrator)
    later = run_generation(client, pptx_bytes, xlsx_bytes, title="Позже")
    report = bk.restore_backup(
        result.path,
        data_dir=orchestrator.settings.data_dir,
        artifacts_dir=orchestrator.settings.artifacts_dir,
        valkey_url=None,
        only_db=True,
    )
    assert report.only_db and report.verify.ok
    assert client.get(f"/api/projects/{later['project_id']}").status_code == 404
    assert client.get(f"/api/projects/{run['project_id']}").status_code == 200
    # Файлы позднего задания остались на диске: их снимет сборка мусора как сирот.
    assert orchestrator.artifacts.job_dir(later["job_id"]).is_dir()
