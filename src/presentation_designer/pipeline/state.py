"""Состояние сервиса в SQLite: проекты, лента событий, файлы, шаблоны, пакеты, задания, ревизии.

Один файл базы в режиме WAL, который открывают API и воркеры через общий каталог данных.
Транзакции короткие; каждая операция открывает соединение сама, поэтому объект State
можно передавать между потоками. Состояние задания живёт здесь, а не в RQ: исчезновение
записи очереди не означает исчезновения результата.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

JsonDict = dict[str, Any]

MIGRATIONS: list[str] = [
    """
    CREATE TABLE projects (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        template_id TEXT,
        package_id TEXT,
        job_id TEXT,
        chosen_variant TEXT,
        brief TEXT NOT NULL,
        settings TEXT NOT NULL
    );
    CREATE TABLE project_events (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL,
        at TEXT NOT NULL,
        payload TEXT NOT NULL
    );
    CREATE INDEX project_events_project ON project_events(project_id, seq);
    CREATE TABLE files (
        id TEXT PRIMARY KEY,
        project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
        sha256 TEXT NOT NULL,
        name TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        mime TEXT NOT NULL,
        kind TEXT NOT NULL,
        added_at TEXT NOT NULL,
        check_result TEXT NOT NULL,
        template_id TEXT,
        package_id TEXT
    );
    CREATE INDEX files_project ON files(project_id);
    CREATE INDEX files_sha ON files(sha256);
    CREATE TABLE templates (
        id TEXT PRIMARY KEY,
        sha256 TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        job_id TEXT NOT NULL,
        status TEXT NOT NULL,
        profile TEXT,
        previews TEXT NOT NULL DEFAULT '[]',
        error TEXT
    );
    CREATE TABLE packages (
        id TEXT PRIMARY KEY,
        idem_key TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        job_id TEXT NOT NULL,
        status TEXT NOT NULL,
        mode TEXT NOT NULL,
        file_ids TEXT NOT NULL,
        brief TEXT,
        package TEXT,
        error TEXT
    );
    CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        stage TEXT NOT NULL,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        deadline_at TEXT,
        queue_wait_ms INTEGER,
        parent_job_id TEXT,
        depends_on TEXT NOT NULL DEFAULT '[]',
        progress TEXT,
        error TEXT,
        result TEXT NOT NULL DEFAULT '{}',
        stages TEXT NOT NULL DEFAULT '[]',
        rq_ids TEXT NOT NULL DEFAULT '[]'
    );
    CREATE INDEX jobs_status ON jobs(status);
    CREATE TABLE generations (
        job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
        template_id TEXT NOT NULL,
        package_id TEXT NOT NULL,
        request TEXT NOT NULL,
        idempotency_key TEXT UNIQUE,
        canceled INTEGER NOT NULL DEFAULT 0,
        story TEXT,
        story_hit INTEGER NOT NULL DEFAULT 0,
        execution_mode TEXT NOT NULL,
        versions TEXT NOT NULL,
        metrics TEXT NOT NULL DEFAULT '{}',
        warnings TEXT NOT NULL DEFAULT '[]'
    );
    CREATE TABLE variants (
        job_id TEXT NOT NULL REFERENCES generations(job_id) ON DELETE CASCADE,
        variant_id TEXT NOT NULL,
        status TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1,
        axis TEXT NOT NULL,
        value TEXT NOT NULL,
        rationale TEXT NOT NULL DEFAULT '',
        slide_count INTEGER,
        ready_at TEXT,
        audited_at TEXT,
        audit TEXT,
        error TEXT,
        stages TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY (job_id, variant_id)
    );
    CREATE TABLE revisions (
        job_id TEXT NOT NULL,
        variant_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        repair_job_id TEXT,
        changed_slide_ids TEXT NOT NULL DEFAULT '[]',
        pptx_hash TEXT,
        artifacts_prefix TEXT NOT NULL,
        manifest TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (job_id, variant_id, revision),
        FOREIGN KEY (job_id, variant_id) REFERENCES variants(job_id, variant_id) ON DELETE CASCADE
    );
    CREATE TABLE repairs (
        repair_job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
        job_id TEXT NOT NULL,
        variant_id TEXT NOT NULL,
        base_revision INTEGER NOT NULL,
        issue_ids TEXT NOT NULL,
        result TEXT,
        new_revision INTEGER,
        message TEXT,
        changed_slide_ids TEXT NOT NULL DEFAULT '[]'
    );
    CREATE INDEX repairs_job ON repairs(job_id);
    """,
    # Версия 2 (этап 3): отметки сборки мусора — когда объект впервые найден без ссылок.
    # Совместима с кодом версии 1: старый код таблицу не читает.
    """
    CREATE TABLE gc_marks (
        kind TEXT NOT NULL,
        key TEXT NOT NULL,
        marked_at TEXT NOT NULL,
        PRIMARY KEY (kind, key)
    );
    """,
    # 3: ключ профиля шаблона — версия анализатора, контрактов, скилла, модели и шрифтов;
    # при смене ключа шаблон анализируется заново под тем же template_id.
    """
    ALTER TABLE templates ADD COLUMN profile_key TEXT;
    """,
    # 4 (этап 20): правка слайда по запросу из чата — то же задание ревизии, что и
    # исправление, с видом `edit`, слайдом и инструкцией. Старый код колонки не читает.
    """
    ALTER TABLE repairs ADD COLUMN kind TEXT NOT NULL DEFAULT 'repair';
    ALTER TABLE repairs ADD COLUMN slide_index INTEGER;
    ALTER TABLE repairs ADD COLUMN slide_id TEXT;
    ALTER TABLE repairs ADD COLUMN instruction TEXT;
    ALTER TABLE repairs ADD COLUMN change_note TEXT;
    """,
    # 5 (этап 23): ручные правки из визуального редактора — вид `patch`, документ
    # slide_patch целиком (JSON) для истории и воспроизведения. Старый код колонку не читает.
    """
    ALTER TABLE repairs ADD COLUMN patch TEXT;
    """,
]

LATEST_SCHEMA = len(MIGRATIONS)

TERMINAL = frozenset({"succeeded", "needs_review", "failed", "canceled"})


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


class NotFound(LookupError):  # noqa: N818 - имя исключения читается как условие
    """Запись не найдена."""


class State:
    def __init__(self, db_path: pathlib.Path, *, migrate: bool = True) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        if migrate:
            self.migrate()

    # ---------- соединение и миграции ----------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Короткая транзакция на запись: BEGIN IMMEDIATE, чтобы сразу взять блокировку."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    def migrate(self) -> tuple[int, int]:
        """Применяет недостающие миграции; возвращает версию схемы до и после."""
        conn = self._connect()
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)"
            )
            applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
            before = max(applied, default=0)
            for version, script in enumerate(MIGRATIONS, start=1):
                if version in applied:
                    continue
                # executescript завершает открытую транзакцию: BEGIN/COMMIT внутри скрипта.
                conn.executescript(
                    "BEGIN IMMEDIATE;\n"
                    f"{script}\n"
                    f"INSERT INTO schema_migrations(version) VALUES ({int(version)});\n"
                    "COMMIT;"
                )
            return before, LATEST_SCHEMA
        finally:
            conn.close()

    def schema_version(self) -> int:
        """Версия схемы в файле; 0 — база ещё не создана или без миграций."""
        if not self.db_path.exists():
            return 0
        with self.read() as conn:
            tables = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()
            if tables is None:
                return 0
            row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            return int(row[0] or 0)

    def pending_migrations(self) -> list[int]:
        return list(range(self.schema_version() + 1, LATEST_SCHEMA + 1))

    def snapshot(self, dest: pathlib.Path) -> pathlib.Path:
        """Согласованный снимок базы через backup API: живая база в WAL одним файлом
        не копируется. Пишется во временный файл и переименовывается целиком."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(f".{dest.name}.tmp")
        tmp.unlink(missing_ok=True)
        src = self._connect()
        try:
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        tmp.replace(dest)
        return dest

    def integrity_ok(self) -> bool:
        with self.read() as conn:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return bool(row) and row[0] == "ok"

    def counts(self) -> dict[str, int]:
        """Число строк по таблицам — для сводки резервной копии и проверок."""
        out: dict[str, int] = {}
        with self.read() as conn:
            for table in (
                "projects",
                "project_events",
                "files",
                "templates",
                "packages",
                "jobs",
                "revisions",
            ):
                out[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return out

    # ---------- проекты ----------

    @staticmethod
    def _project_row(row: sqlite3.Row) -> JsonDict:
        return {
            "project_id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "template_id": row["template_id"],
            "package_id": row["package_id"],
            "job_id": row["job_id"],
            "chosen_variant": row["chosen_variant"],
            "brief": _loads(row["brief"]),
            "settings": _loads(row["settings"]),
        }

    def create_project(
        self, title: str, brief: JsonDict, settings: JsonDict, **fields: Any
    ) -> JsonDict:
        pid = new_id("prj")
        ts = now_iso()
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO projects(id, title, created_at, updated_at, template_id, package_id, job_id,"  # noqa: E501
                " chosen_variant, brief, settings) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    pid,
                    title,
                    ts,
                    ts,
                    fields.get("template_id"),
                    fields.get("package_id"),
                    fields.get("job_id"),
                    fields.get("chosen_variant"),
                    _dumps(brief),
                    _dumps(settings),
                ),
            )
        return self.get_project(pid)

    def get_project(self, project_id: str) -> JsonDict:
        with self.read() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if row is None:
                raise NotFound(project_id)
            project = self._project_row(row)
            project["files"] = [
                self._file_row(r)
                for r in conn.execute(
                    "SELECT * FROM files WHERE project_id = ? ORDER BY added_at, rowid",
                    (project_id,),
                )
            ]
            project["events"] = [
                self._event_row(r)
                for r in conn.execute(
                    "SELECT * FROM project_events WHERE project_id = ? ORDER BY seq", (project_id,)
                )
            ]
        return project

    def list_projects(self) -> list[JsonDict]:
        with self.read() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
            return [self._project_row(r) for r in rows]

    def update_project(self, project_id: str, patch: JsonDict) -> JsonDict:
        allowed = {
            "title",
            "template_id",
            "package_id",
            "job_id",
            "chosen_variant",
            "brief",
            "settings",
        }
        sets: list[str] = []
        values: list[Any] = []
        for key, value in patch.items():
            if key not in allowed:
                continue
            sets.append(f"{key} = ?")
            values.append(_dumps(value) if key in {"brief", "settings"} else value)
        with self.tx() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
                raise NotFound(project_id)
            sets.append("updated_at = ?")
            values.append(now_iso())
            values.append(project_id)
            conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", values)
        return self.get_project(project_id)

    def touch_project(self, project_id: str) -> None:
        with self.tx() as conn:
            conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now_iso(), project_id))

    def delete_project(self, project_id: str) -> None:
        with self.tx() as conn:
            cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            if cur.rowcount == 0:
                raise NotFound(project_id)

    def find_project_by_job(self, job_id: str) -> JsonDict | None:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM projects WHERE job_id = ? ORDER BY updated_at DESC", (job_id,)
            ).fetchone()
            return self._project_row(row) if row else None

    # ---------- лента событий ----------

    @staticmethod
    def _event_row(row: sqlite3.Row) -> JsonDict:
        payload = _loads(row["payload"])
        return {"event_id": row["id"], "at": row["at"], **payload}

    def append_event(self, project_id: str, payload: JsonDict) -> JsonDict:
        eid = new_id("evt")
        ts = now_iso()
        with self.tx() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
                raise NotFound(project_id)
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM project_events WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO project_events(id, project_id, seq, at, payload) VALUES (?,?,?,?,?)",
                (eid, project_id, seq, ts, _dumps(payload)),
            )
            conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (ts, project_id))
        return {"event_id": eid, "at": ts, **payload}

    def patch_event(self, project_id: str, event_id: str, patch: JsonDict) -> JsonDict:
        with self.tx() as conn:
            row = conn.execute(
                "SELECT * FROM project_events WHERE id = ? AND project_id = ?",
                (event_id, project_id),
            ).fetchone()
            if row is None:
                raise NotFound(event_id)
            payload = {**_loads(row["payload"]), **patch}
            conn.execute(
                "UPDATE project_events SET payload = ? WHERE id = ?", (_dumps(payload), event_id)
            )
            return {"event_id": row["id"], "at": row["at"], **payload}

    def list_events(self, project_id: str) -> list[JsonDict]:
        with self.read() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
                raise NotFound(project_id)
            return [
                self._event_row(r)
                for r in conn.execute(
                    "SELECT * FROM project_events WHERE project_id = ? ORDER BY seq", (project_id,)
                )
            ]

    # ---------- файлы проекта ----------

    @staticmethod
    def _file_row(row: sqlite3.Row) -> JsonDict:
        out = {
            "schema_version": "1.2",
            "file_id": row["id"],
            "name": row["name"],
            "size_bytes": row["size_bytes"],
            "sha256": row["sha256"],
            "mime": row["mime"],
            "kind": row["kind"],
            "added_at": row["added_at"],
            "check": _loads(row["check_result"]),
        }
        if row["template_id"]:
            out["template_id"] = row["template_id"]
        if row["package_id"]:
            out["package_id"] = row["package_id"]
        return out

    def add_file(
        self,
        project_id: str | None,
        *,
        sha256: str,
        name: str,
        size_bytes: int,
        mime: str,
        kind: str,
        check: JsonDict,
    ) -> JsonDict:
        """Запись файла проекта; те же байты с тем же именем в том же проекте не дублируются.

        project_id может быть пустым: загрузки CLI и внешних клиентов живут вне проектов.
        """
        with self.tx() as conn:
            if project_id is not None:
                if not conn.execute(
                    "SELECT 1 FROM projects WHERE id = ?", (project_id,)
                ).fetchone():
                    raise NotFound(project_id)
                existing = conn.execute(
                    "SELECT * FROM files WHERE project_id = ? AND sha256 = ? AND name = ?",
                    (project_id, sha256, name),
                ).fetchone()
                if existing is not None:
                    return self._file_row(existing)
            fid = new_id("file")
            ts = now_iso()
            conn.execute(
                "INSERT INTO files(id, project_id, sha256, name, size_bytes, mime, kind, added_at, check_result)"  # noqa: E501
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (fid, project_id, sha256, name, size_bytes, mime, kind, ts, _dumps(check)),
            )
            if project_id is not None:
                conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (ts, project_id))
            row = conn.execute("SELECT * FROM files WHERE id = ?", (fid,)).fetchone()
            return self._file_row(row)

    def get_file(self, file_id: str, project_id: str | None = None) -> JsonDict:
        with self.read() as conn:
            row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            if row is None or (project_id is not None and row["project_id"] != project_id):
                raise NotFound(file_id)
            out = self._file_row(row)
            out["project_id"] = row["project_id"]
            return out

    def patch_file(self, project_id: str | None, file_id: str, patch: JsonDict) -> JsonDict:
        allowed = {"kind", "template_id", "package_id", "name"}
        with self.tx() as conn:
            row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            if row is None or (project_id is not None and row["project_id"] != project_id):
                raise NotFound(file_id)
            for key, value in patch.items():
                if key in allowed:
                    conn.execute(f"UPDATE files SET {key} = ? WHERE id = ?", (value, file_id))
            if row["project_id"]:
                conn.execute(
                    "UPDATE projects SET updated_at = ? WHERE id = ?",
                    (now_iso(), row["project_id"]),
                )
            return self._file_row(
                conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
            )

    def delete_file(self, project_id: str, file_id: str) -> None:
        with self.tx() as conn:
            cur = conn.execute(
                "DELETE FROM files WHERE id = ? AND project_id = ?", (file_id, project_id)
            )
            if cur.rowcount == 0:
                raise NotFound(file_id)
            conn.execute("UPDATE projects SET updated_at = ? WHERE id = ?", (now_iso(), project_id))

    def project_files(self, project_id: str) -> list[JsonDict]:
        with self.read() as conn:
            return [
                self._file_row(r)
                for r in conn.execute(
                    "SELECT * FROM files WHERE project_id = ? ORDER BY added_at, rowid",
                    (project_id,),
                )
            ]

    def project_usage(self, project_id: str) -> tuple[int, int]:
        """Число файлов и суммарный объём проекта для квот."""
        with self.read() as conn:
            row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0) FROM files WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            return int(row[0]), int(row[1])

    def blob_references(self, sha256: str) -> int:
        """Сколько записей ссылаются на байты: файлы проектов, шаблоны и пакеты."""
        with self.read() as conn:
            files = conn.execute(
                "SELECT COUNT(*) FROM files WHERE sha256 = ?", (sha256,)
            ).fetchone()[0]
            templates = conn.execute(
                "SELECT COUNT(*) FROM templates WHERE sha256 = ?", (sha256,)
            ).fetchone()[0]
            return int(files) + int(templates)

    # ---------- шаблоны ----------

    @staticmethod
    def _template_row(row: sqlite3.Row) -> JsonDict:
        return {
            "template_id": row["id"],
            "sha256": row["sha256"],
            "name": row["name"],
            "size_bytes": row["size_bytes"],
            "created_at": row["created_at"],
            "job_id": row["job_id"],
            "status": row["status"],
            "profile": _loads(row["profile"]),
            "previews": _loads(row["previews"], []),
            "error": _loads(row["error"]),
            "profile_key": row["profile_key"] if "profile_key" in row.keys() else None,
        }

    def get_or_create_template(
        self, *, sha256: str, name: str, size_bytes: int, job_id: str
    ) -> tuple[JsonDict, bool]:
        """Шаблон идемпотентен по sha256: те же байты дают тот же template_id."""
        with self.tx() as conn:
            row = conn.execute("SELECT * FROM templates WHERE sha256 = ?", (sha256,)).fetchone()
            if row is not None:
                return self._template_row(row), True
            tid = new_id("tpl")
            conn.execute(
                "INSERT INTO templates(id, sha256, name, size_bytes, created_at, job_id, status) VALUES (?,?,?,?,?,?,?)",  # noqa: E501
                (tid, sha256, name, size_bytes, now_iso(), job_id, "queued"),
            )
            return self._template_row(
                conn.execute("SELECT * FROM templates WHERE id = ?", (tid,)).fetchone()
            ), False

    def get_template(self, template_id: str) -> JsonDict:
        with self.read() as conn:
            row = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
            if row is None:
                raise NotFound(template_id)
            return self._template_row(row)

    def list_templates(self) -> list[JsonDict]:
        with self.read() as conn:
            return [
                self._template_row(r)
                for r in conn.execute("SELECT * FROM templates ORDER BY created_at DESC")
            ]

    def update_template(self, template_id: str, **fields: Any) -> None:
        json_fields = {"profile", "previews", "error"}
        allowed = {"status", "profile", "previews", "error", "job_id", "profile_key", "name"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"недопустимые поля шаблона: {sorted(unknown)}")
        with self.tx() as conn:
            for key, value in fields.items():
                conn.execute(
                    f"UPDATE templates SET {key} = ? WHERE id = ?",
                    (_dumps(value) if key in json_fields else value, template_id),
                )

    def delete_template(self, template_id: str) -> JsonDict:
        """Убирает шаблон из библиотеки: проекты и файлы теряют ссылку на него, задание
        анализа и байты файла остаются сиротами для сборки мусора. Возвращает удалённую строку."""
        with self.tx() as conn:
            row = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
            if row is None:
                raise NotFound(template_id)
            conn.execute(
                "UPDATE projects SET template_id = NULL, updated_at = ? WHERE template_id = ?",
                (now_iso(), template_id),
            )
            conn.execute(
                "UPDATE files SET template_id = NULL WHERE template_id = ?", (template_id,)
            )
            conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
            return self._template_row(row)

    # ---------- контент-пакеты ----------

    @staticmethod
    def _package_row(row: sqlite3.Row) -> JsonDict:
        return {
            "package_id": row["id"],
            "idem_key": row["idem_key"],
            "created_at": row["created_at"],
            "job_id": row["job_id"],
            "status": row["status"],
            "mode": row["mode"],
            "file_ids": _loads(row["file_ids"], []),
            "brief": _loads(row["brief"]),
            "package": _loads(row["package"]),
            "error": _loads(row["error"]),
        }

    def get_or_create_package(
        self, *, idem_key: str, mode: str, file_ids: list[str], brief: JsonDict | None, job_id: str
    ) -> tuple[JsonDict, bool]:
        with self.tx() as conn:
            row = conn.execute("SELECT * FROM packages WHERE idem_key = ?", (idem_key,)).fetchone()
            if row is not None and row["status"] != "failed":
                return self._package_row(row), True
            if row is not None:
                conn.execute("DELETE FROM packages WHERE id = ?", (row["id"],))
            pid = new_id("pkg")
            conn.execute(
                "INSERT INTO packages(id, idem_key, created_at, job_id, status, mode, file_ids, brief) VALUES (?,?,?,?,?,?,?,?)",  # noqa: E501
                (
                    pid,
                    idem_key,
                    now_iso(),
                    job_id,
                    "queued",
                    mode,
                    _dumps(file_ids),
                    _dumps(brief) if brief is not None else None,
                ),
            )
            return self._package_row(
                conn.execute("SELECT * FROM packages WHERE id = ?", (pid,)).fetchone()
            ), False

    def get_package(self, package_id: str) -> JsonDict:
        with self.read() as conn:
            row = conn.execute("SELECT * FROM packages WHERE id = ?", (package_id,)).fetchone()
            if row is None:
                raise NotFound(package_id)
            return self._package_row(row)

    def update_package(self, package_id: str, **fields: Any) -> None:
        json_fields = {"package", "error", "brief", "file_ids"}
        with self.tx() as conn:
            for key, value in fields.items():
                conn.execute(
                    f"UPDATE packages SET {key} = ? WHERE id = ?",
                    (_dumps(value) if key in json_fields else value, package_id),
                )

    # ---------- задания ----------

    @staticmethod
    def _job_row(row: sqlite3.Row) -> JsonDict:
        return {
            "job_id": row["id"],
            "kind": row["kind"],
            "status": row["status"],
            "stage": row["stage"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "deadline_at": row["deadline_at"],
            "queue_wait_ms": row["queue_wait_ms"],
            "parent_job_id": row["parent_job_id"],
            "depends_on": _loads(row["depends_on"], []),
            "progress": _loads(row["progress"]),
            "error": _loads(row["error"]),
            "result": _loads(row["result"], {}),
            "stages": _loads(row["stages"], []),
            "rq_ids": _loads(row["rq_ids"], []),
        }

    def create_job(
        self,
        *,
        kind: str,
        job_id: str | None = None,
        stage: str = "queued",
        deadline_at: str | None = None,
        parent_job_id: str | None = None,
        depends_on: list[str] | None = None,
        result: JsonDict | None = None,
        progress: JsonDict | None = None,
    ) -> JsonDict:
        jid = job_id or new_id("job")
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO jobs(id, kind, status, stage, created_at, deadline_at, parent_job_id, depends_on, progress, result)"  # noqa: E501
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    jid,
                    kind,
                    "queued",
                    stage,
                    now_iso(),
                    deadline_at,
                    parent_job_id,
                    _dumps(depends_on or []),
                    _dumps(progress) if progress else None,
                    _dumps(result or {}),
                ),
            )
        return self.get_job(jid)

    def get_job(self, job_id: str) -> JsonDict:
        with self.read() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise NotFound(job_id)
            return self._job_row(row)

    def update_job(self, job_id: str, **fields: Any) -> None:
        json_fields = {"depends_on", "progress", "error", "result", "stages", "rq_ids"}
        with self.tx() as conn:
            for key, value in fields.items():
                conn.execute(
                    f"UPDATE jobs SET {key} = ? WHERE id = ?",
                    (_dumps(value) if key in json_fields else value, job_id),
                )

    def list_active_jobs(self) -> list[JsonDict]:
        with self.read() as conn:
            return [
                self._job_row(r)
                for r in conn.execute(
                    "SELECT * FROM jobs WHERE status IN ('queued', 'running') ORDER BY created_at"
                )
            ]

    def job_started(self, job_id: str) -> None:
        """Отмечает начало работы, считает ожидание очереди; повторный вызов ничего не меняет."""
        with self.tx() as conn:
            row = conn.execute(
                "SELECT created_at, started_at FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None or row["started_at"]:
                return
            ts = now_iso()
            wait_ms = int((_parse(ts) - _parse(row["created_at"])).total_seconds() * 1000)
            conn.execute(
                "UPDATE jobs SET started_at = ?, status = 'running', queue_wait_ms = ? WHERE id = ? AND status = 'queued'",  # noqa: E501
                (ts, wait_ms, job_id),
            )

    def add_stage(self, job_id: str, stage: JsonDict) -> None:
        """Добавляет или обновляет запись этапа (по stage и variant_id)."""
        with self.tx() as conn:
            row = conn.execute("SELECT stages FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise NotFound(job_id)
            stages: list[JsonDict] = _loads(row["stages"], [])
            key = (stage.get("stage"), stage.get("variant_id"))
            stages = [s for s in stages if (s.get("stage"), s.get("variant_id")) != key]
            stages.append(stage)
            conn.execute("UPDATE jobs SET stages = ? WHERE id = ?", (_dumps(stages), job_id))

    # ---------- генерации ----------

    @staticmethod
    def _generation_row(row: sqlite3.Row) -> JsonDict:
        return {
            "job_id": row["job_id"],
            "template_id": row["template_id"],
            "package_id": row["package_id"],
            "request": _loads(row["request"]),
            "idempotency_key": row["idempotency_key"],
            "canceled": bool(row["canceled"]),
            "story": _loads(row["story"]),
            "story_hit": bool(row["story_hit"]),
            "execution_mode": _loads(row["execution_mode"]),
            "versions": _loads(row["versions"]),
            "metrics": _loads(row["metrics"], {}),
            "warnings": _loads(row["warnings"], []),
        }

    def create_generation(
        self,
        *,
        job_id: str,
        template_id: str,
        package_id: str,
        request: JsonDict,
        idempotency_key: str | None,
        execution_mode: JsonDict,
        versions: JsonDict,
        variants: list[JsonDict],
    ) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO generations(job_id, template_id, package_id, request, idempotency_key, execution_mode, versions)"  # noqa: E501
                " VALUES (?,?,?,?,?,?,?)",
                (
                    job_id,
                    template_id,
                    package_id,
                    _dumps(request),
                    idempotency_key,
                    _dumps(execution_mode),
                    _dumps(versions),
                ),
            )
            for v in variants:
                conn.execute(
                    "INSERT INTO variants(job_id, variant_id, status, axis, value, rationale) VALUES (?,?,?,?,?,?)",  # noqa: E501
                    (
                        job_id,
                        v["variant_id"],
                        "pending",
                        v["axis"],
                        v["value"],
                        v.get("rationale", ""),
                    ),
                )

    def get_generation(self, job_id: str) -> JsonDict:
        with self.read() as conn:
            row = conn.execute("SELECT * FROM generations WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise NotFound(job_id)
            return self._generation_row(row)

    def find_generation_by_key(self, idempotency_key: str) -> JsonDict | None:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM generations WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            return self._generation_row(row) if row else None

    def update_generation(self, job_id: str, **fields: Any) -> None:
        json_fields = {"story", "execution_mode", "versions", "metrics", "warnings", "request"}
        with self.tx() as conn:
            for key, value in fields.items():
                if key == "canceled":
                    value = 1 if value else 0
                conn.execute(
                    f"UPDATE generations SET {key} = ? WHERE job_id = ?",
                    (_dumps(value) if key in json_fields else value, job_id),
                )

    def find_story(self, content_hash: str) -> JsonDict | None:
        """Готовый StoryPlan с тем же смысловым хешем из прошлых заданий: содержание,
        эффективный бриф, модель и промпт совпали — план не зависит от шаблона и job_id."""
        with self.read() as conn:
            row = conn.execute(
                "SELECT g.story FROM generations g JOIN jobs j ON j.id = g.job_id"
                " WHERE g.story IS NOT NULL AND json_extract(g.story, '$.content_hash') = ?"
                " AND j.status IN ('succeeded', 'needs_review')"
                " ORDER BY j.created_at DESC LIMIT 1",
                (content_hash,),
            ).fetchone()
            return _loads(row["story"]) if row else None

    # ---------- варианты и ревизии ----------

    @staticmethod
    def _variant_row(row: sqlite3.Row) -> JsonDict:
        return {
            "job_id": row["job_id"],
            "variant_id": row["variant_id"],
            "status": row["status"],
            "revision": row["revision"],
            "axis": row["axis"],
            "value": row["value"],
            "rationale": row["rationale"],
            "slide_count": row["slide_count"],
            "ready_at": row["ready_at"],
            "audited_at": row["audited_at"],
            "audit": _loads(row["audit"]),
            "error": _loads(row["error"]),
            "stages": _loads(row["stages"], []),
        }

    def get_variants(self, job_id: str) -> list[JsonDict]:
        with self.read() as conn:
            return [
                self._variant_row(r)
                for r in conn.execute(
                    "SELECT * FROM variants WHERE job_id = ? ORDER BY rowid", (job_id,)
                )
            ]

    def get_variant(self, job_id: str, variant_id: str) -> JsonDict:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM variants WHERE job_id = ? AND variant_id = ?", (job_id, variant_id)
            ).fetchone()
            if row is None:
                raise NotFound(variant_id)
            return self._variant_row(row)

    def update_variant(self, job_id: str, variant_id: str, **fields: Any) -> None:
        json_fields = {"audit", "error", "stages"}
        with self.tx() as conn:
            for key, value in fields.items():
                conn.execute(
                    f"UPDATE variants SET {key} = ? WHERE job_id = ? AND variant_id = ?",
                    (_dumps(value) if key in json_fields else value, job_id, variant_id),
                )

    def add_variant_stage(self, job_id: str, variant_id: str, stage: JsonDict) -> None:
        with self.tx() as conn:
            row = conn.execute(
                "SELECT stages FROM variants WHERE job_id = ? AND variant_id = ?",
                (job_id, variant_id),
            ).fetchone()
            if row is None:
                raise NotFound(variant_id)
            stages: list[JsonDict] = [
                s for s in _loads(row["stages"], []) if s.get("stage") != stage.get("stage")
            ]
            stages.append(stage)
            conn.execute(
                "UPDATE variants SET stages = ? WHERE job_id = ? AND variant_id = ?",
                (_dumps(stages), job_id, variant_id),
            )

    @staticmethod
    def _revision_row(row: sqlite3.Row) -> JsonDict:
        return {
            "job_id": row["job_id"],
            "variant_id": row["variant_id"],
            "revision": row["revision"],
            "created_at": row["created_at"],
            "repair_job_id": row["repair_job_id"],
            "changed_slide_ids": _loads(row["changed_slide_ids"], []),
            "pptx_hash": row["pptx_hash"],
            "artifacts_prefix": row["artifacts_prefix"],
            "manifest": _loads(row["manifest"], {}),
        }

    def add_revision(
        self,
        *,
        job_id: str,
        variant_id: str,
        revision: int,
        artifacts_prefix: str,
        manifest: JsonDict,
        pptx_hash: str | None = None,
        repair_job_id: str | None = None,
        changed_slide_ids: list[str] | None = None,
    ) -> None:
        """Публикует ревизию: запись появляется только после того, как файлы и манифест записаны."""
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO revisions(job_id, variant_id, revision, created_at, repair_job_id, changed_slide_ids, pptx_hash, artifacts_prefix, manifest)"  # noqa: E501
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    variant_id,
                    revision,
                    now_iso(),
                    repair_job_id,
                    _dumps(changed_slide_ids or []),
                    pptx_hash,
                    artifacts_prefix,
                    _dumps(manifest),
                ),
            )
            conn.execute(
                "UPDATE variants SET revision = ? WHERE job_id = ? AND variant_id = ?",
                (revision, job_id, variant_id),
            )

    def update_revision(self, job_id: str, variant_id: str, revision: int, **fields: Any) -> None:
        json_fields = {"manifest", "changed_slide_ids"}
        with self.tx() as conn:
            for key, value in fields.items():
                conn.execute(
                    f"UPDATE revisions SET {key} = ? WHERE job_id = ? AND variant_id = ? AND revision = ?",  # noqa: E501
                    (_dumps(value) if key in json_fields else value, job_id, variant_id, revision),
                )

    def list_revisions(self, job_id: str, variant_id: str | None = None) -> list[JsonDict]:
        with self.read() as conn:
            if variant_id is None:
                rows = conn.execute(
                    "SELECT * FROM revisions WHERE job_id = ? ORDER BY variant_id, revision",
                    (job_id,),
                )
            else:
                rows = conn.execute(
                    "SELECT * FROM revisions WHERE job_id = ? AND variant_id = ? ORDER BY revision",
                    (job_id, variant_id),
                )
            return [self._revision_row(r) for r in rows]

    def get_revision(self, job_id: str, variant_id: str, revision: int) -> JsonDict:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM revisions WHERE job_id = ? AND variant_id = ? AND revision = ?",
                (job_id, variant_id, revision),
            ).fetchone()
            if row is None:
                raise NotFound(f"{variant_id}/r{revision}")
            return self._revision_row(row)

    # ---------- исправления ----------

    @staticmethod
    def _repair_row(row: sqlite3.Row) -> JsonDict:
        return {
            "repair_job_id": row["repair_job_id"],
            "job_id": row["job_id"],
            "variant_id": row["variant_id"],
            "base_revision": row["base_revision"],
            "issue_ids": _loads(row["issue_ids"], []),
            "result": row["result"],
            "new_revision": row["new_revision"],
            "message": row["message"],
            "changed_slide_ids": _loads(row["changed_slide_ids"], []),
            "kind": row["kind"] or "repair",
            "slide_index": row["slide_index"],
            "slide_id": row["slide_id"],
            "instruction": row["instruction"],
            "change_note": row["change_note"],
            "patch": _loads(row["patch"], None) if "patch" in row.keys() else None,
        }

    def create_repair(
        self,
        *,
        repair_job_id: str,
        job_id: str,
        variant_id: str,
        base_revision: int,
        issue_ids: list[str],
        kind: str = "repair",
        slide_index: int | None = None,
        instruction: str | None = None,
        patch: JsonDict | None = None,
    ) -> None:
        """Задание ревизии: исправление по находкам (`repair`), правка слайда по инструкции
        (`edit`) или ручные правки редактора (`patch`); все создают новую ревизию варианта и
        делят блокировку."""
        with self.tx() as conn:
            conn.execute(
                "INSERT INTO repairs(repair_job_id, job_id, variant_id, base_revision, issue_ids,"
                " kind, slide_index, instruction, patch) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    repair_job_id,
                    job_id,
                    variant_id,
                    base_revision,
                    _dumps(issue_ids),
                    kind,
                    slide_index,
                    instruction,
                    _dumps(patch) if patch is not None else None,
                ),
            )

    def get_repair(self, repair_job_id: str) -> JsonDict:
        with self.read() as conn:
            row = conn.execute(
                "SELECT * FROM repairs WHERE repair_job_id = ?", (repair_job_id,)
            ).fetchone()
            if row is None:
                raise NotFound(repair_job_id)
            return self._repair_row(row)

    def update_repair(self, repair_job_id: str, **fields: Any) -> None:
        json_fields = {"issue_ids", "changed_slide_ids"}
        with self.tx() as conn:
            for key, value in fields.items():
                conn.execute(
                    f"UPDATE repairs SET {key} = ? WHERE repair_job_id = ?",
                    (_dumps(value) if key in json_fields else value, repair_job_id),
                )

    def list_repairs(
        self, job_id: str, kind: str | tuple[str, ...] | None = None
    ) -> list[JsonDict]:
        """Задания ревизий генерации в порядке создания; `kind` (вид или несколько видов)
        сужает до исправлений, правок из чата или ручных правок."""
        kinds = (kind,) if isinstance(kind, str) else kind
        with self.read() as conn:
            rows = conn.execute("SELECT * FROM repairs WHERE job_id = ? ORDER BY rowid", (job_id,))
            return [
                self._repair_row(r)
                for r in rows
                if kinds is None or (r["kind"] or "repair") in kinds
            ]

    def active_repair(self, job_id: str, variant_id: str) -> JsonDict | None:
        """Незавершённое исправление или правка слайда варианта: две несовместимые правки
        одной ревизии не допускаются."""
        with self.read() as conn:
            row = conn.execute(
                "SELECT r.* FROM repairs r JOIN jobs j ON j.id = r.repair_job_id"
                " WHERE r.job_id = ? AND r.variant_id = ? AND j.status IN ('queued', 'running') LIMIT 1",  # noqa: E501
                (job_id, variant_id),
            ).fetchone()
            return self._repair_row(row) if row else None

    # ---------- сборка мусора ----------

    def gc_snapshot(self) -> JsonDict:
        """Всё, что нужно сборке мусора, одним чтением: ссылки проектов и ленты, файлы,
        шаблоны, пакеты, задания."""
        with self.read() as conn:
            projects = [
                dict(r)
                for r in conn.execute("SELECT id, template_id, package_id, job_id FROM projects")
            ]
            event_refs: dict[str, set[str]] = {
                "job_id": set(),
                "template_id": set(),
                "package_id": set(),
                "file_id": set(),
            }
            for row in conn.execute("SELECT payload FROM project_events"):
                payload = _loads(row["payload"], {})
                for key, bucket in event_refs.items():
                    value = payload.get(key)
                    if isinstance(value, str) and value:
                        bucket.add(value)
                for value in payload.get("file_ids") or []:
                    if isinstance(value, str):
                        event_refs["file_id"].add(value)
            files = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, project_id, sha256, size_bytes, template_id, package_id, added_at"
                    " FROM files"
                )
            ]
            templates = [
                dict(r) for r in conn.execute("SELECT id, sha256, job_id, previews FROM templates")
            ]
            packages = [
                {**dict(r), "file_ids": _loads(r["file_ids"], [])}
                for r in conn.execute("SELECT id, job_id, file_ids, status FROM packages")
            ]
            jobs = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, kind, status, created_at, parent_job_id FROM jobs"
                )
            ]
            generations = [
                dict(r)
                for r in conn.execute("SELECT job_id, template_id, package_id FROM generations")
            ]
            repairs = [dict(r) for r in conn.execute("SELECT repair_job_id, job_id FROM repairs")]
        return {
            "projects": projects,
            "event_refs": {k: sorted(v) for k, v in event_refs.items()},
            "files": files,
            "templates": templates,
            "packages": packages,
            "jobs": jobs,
            "generations": generations,
            "repairs": repairs,
        }

    def gc_marks(self) -> dict[tuple[str, str], str]:
        with self.read() as conn:
            return {
                (r["kind"], r["key"]): r["marked_at"]
                for r in conn.execute("SELECT kind, key, marked_at FROM gc_marks")
            }

    def gc_mark(self, kind: str, key: str, marked_at: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO gc_marks(kind, key, marked_at) VALUES (?,?,?)",
                (kind, key, marked_at),
            )

    def gc_unmark(self, kind: str, key: str) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM gc_marks WHERE kind = ? AND key = ?", (kind, key))

    def delete_jobs(self, job_ids: list[str]) -> int:
        """Удаляет задания вместе с генерациями, вариантами, ревизиями и исправлениями."""
        if not job_ids:
            return 0
        with self.tx() as conn:
            marks = ",".join("?" for _ in job_ids)
            conn.execute(f"DELETE FROM repairs WHERE job_id IN ({marks})", job_ids)
            cur = conn.execute(f"DELETE FROM jobs WHERE id IN ({marks})", job_ids)
            return int(cur.rowcount)

    def delete_packages(self, package_ids: list[str]) -> int:
        if not package_ids:
            return 0
        with self.tx() as conn:
            marks = ",".join("?" for _ in package_ids)
            conn.execute(
                f"UPDATE files SET package_id = NULL WHERE package_id IN ({marks})", package_ids
            )
            cur = conn.execute(f"DELETE FROM packages WHERE id IN ({marks})", package_ids)
            return int(cur.rowcount)

    def delete_file_rows(self, file_ids: list[str]) -> int:
        if not file_ids:
            return 0
        with self.tx() as conn:
            marks = ",".join("?" for _ in file_ids)
            cur = conn.execute(f"DELETE FROM files WHERE id IN ({marks})", file_ids)
            return int(cur.rowcount)


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))
