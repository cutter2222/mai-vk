"""CLI обслуживания: копия, проверка, сборка мусора и миграция на временных каталогах."""

from __future__ import annotations

import pathlib
from collections.abc import Iterator

import pytest

from presentation_designer.cli import maintenance
from presentation_designer.pipeline.state import LATEST_SCHEMA, State
from presentation_designer.shared import settings as s


@pytest.fixture
def env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[pathlib.Path]:
    monkeypatch.setenv("PD_PATHS__DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PD_PATHS__ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("PD_PATHS__RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("PD_PATHS__BACKUPS_DIR", str(tmp_path / "backups"))
    s.reset_cache()
    yield tmp_path
    s.reset_cache()


def test_cli_backup_verify_gc(env: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    state = State(env / "data" / "state.sqlite3")
    state.create_project("Проект", {}, {})
    assert maintenance.main(["backup", "--no-valkey", "--label", "cli"]) == 0
    out = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    archive = pathlib.Path(out["archive"])
    assert archive.is_file() and out["schema"] == str(LATEST_SCHEMA)

    assert maintenance.main(["verify", str(archive)]) == 0
    out = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert out["ok"] == "yes"

    assert maintenance.main(["list-backups"]) == 0
    assert archive.name in capsys.readouterr().out

    assert maintenance.main(["gc", "--dry-run"]) == 0
    out = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert out["dry_run"] == "yes" and out["removed"] == "0"


def test_cli_migrate_backs_up_before_schema_change(
    env: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.pipeline.test_state_schema import _v1_database

    db = env / "data" / "state.sqlite3"
    db.parent.mkdir(parents=True)
    _v1_database(db)
    assert maintenance.main(["migrate", "--no-valkey"]) == 0
    out = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert out["schema_before"] == "1" and out["schema_after"] == str(LATEST_SCHEMA)
    assert out["backup"] and "pre-migrate" in out["backup"]
    assert pathlib.Path(out["backup"]).is_file()
    # Без изменений схемы копия не создаётся.
    assert maintenance.main(["migrate", "--no-valkey"]) == 0
    out = dict(line.split("=", 1) for line in capsys.readouterr().out.splitlines())
    assert out["backup"] == "" and out["schema_before"] == out["schema_after"]
