"""Миграции схемы: база версии 1 доводится до текущей, снимок читается отдельно."""

from __future__ import annotations

import pathlib
import sqlite3

from presentation_designer.pipeline.state import LATEST_SCHEMA, MIGRATIONS, State


def _v1_database(path: pathlib.Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY);\n"
        + MIGRATIONS[0]
        + "\nINSERT INTO schema_migrations(version) VALUES (1);"
    )
    conn.execute(
        "INSERT INTO projects(id, title, created_at, updated_at, brief, settings)"
        " VALUES ('prj_1', 'Старый', 't', 't', '{}', '{}')"
    )
    # Исправление, записанное до миграции 4: после неё читается как kind=repair.
    conn.execute(
        "INSERT INTO jobs(id, kind, status, stage, created_at)"
        " VALUES ('rep_1', 'repair', 'succeeded', 'done', 't')"
    )
    conn.execute(
        "INSERT INTO repairs(repair_job_id, job_id, variant_id, base_revision, issue_ids, result)"
        " VALUES ('rep_1', 'job_1', 'balanced', 1, '[\"i1\"]', 'applied')"
    )
    conn.commit()
    conn.close()


def test_pending_migrations_and_upgrade(tmp_path: pathlib.Path) -> None:
    db = tmp_path / "state.sqlite3"
    assert State(db, migrate=False).schema_version() == 0
    _v1_database(db)
    state = State(db, migrate=False)
    assert state.schema_version() == 1
    assert state.pending_migrations() == list(range(2, LATEST_SCHEMA + 1))
    assert state.migrate() == (1, LATEST_SCHEMA)
    assert state.pending_migrations() == []
    assert state.get_project("prj_1")["title"] == "Старый"
    assert state.gc_marks() == {}
    old_repair = state.get_repair("rep_1")
    assert old_repair["kind"] == "repair" and old_repair["issue_ids"] == ["i1"]
    assert old_repair["instruction"] is None and old_repair["slide_index"] is None
    assert state.list_repairs("job_1", kind="edit") == []
    assert [r["repair_job_id"] for r in state.list_repairs("job_1", kind="repair")] == ["rep_1"]
    # Миграция 5 (этап 23): у старых исправлений нет документа ручных правок.
    assert old_repair["patch"] is None and state.list_repairs("job_1", kind="patch") == []
    assert [r["repair_job_id"] for r in state.list_repairs("job_1", kind=("repair", "patch"))] == [
        "rep_1"
    ]
    # Повторный запуск ничего не меняет.
    assert State(db).migrate() == (LATEST_SCHEMA, LATEST_SCHEMA)


def test_snapshot_is_consistent_copy(tmp_path: pathlib.Path) -> None:
    state = State(tmp_path / "state.sqlite3")
    state.create_project("Проект", {}, {})
    copy = state.snapshot(tmp_path / "backup" / "state.sqlite3")
    assert copy.is_file() and not (copy.parent / ".state.sqlite3.tmp").exists()
    restored = State(copy, migrate=False)
    assert restored.integrity_ok()
    assert restored.counts()["projects"] == 1
    assert restored.schema_version() == LATEST_SCHEMA
