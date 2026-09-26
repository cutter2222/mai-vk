"""Immutable office PPTX revisions, with exclusive leases for in-place AI patches.

The prototype keeps bytes and metadata in one SQLite transaction. Its separate database
must be included in backups; the generation GC must not delete office copies.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import pathlib
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from presentation_designer.api.errors import ApiError


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def sign(payload: dict[str, Any], secret: str) -> str:
    header = _encode(b'{"alg":"HS256","typ":"JWT"}')
    body = _encode(json.dumps(payload, separators=(",", ":")).encode())
    data = f"{header}.{body}"
    return f"{data}.{_encode(hmac.digest(secret.encode(), data.encode(), 'sha256'))}"


def verify(token: str, secret: str) -> dict[str, Any]:
    try:
        if not secret or len(token) > 65536:
            raise ValueError("token")
        header, body, signature = token.split(".")

        def decode(value: str) -> bytes:
            return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

        if json.loads(decode(header)).get("alg") != "HS256":
            raise ValueError("algorithm")
        expected = hmac.digest(secret.encode(), f"{header}.{body}".encode(), "sha256")
        if not hmac.compare_digest(decode(signature), expected):
            raise ValueError("signature")
        payload = json.loads(decode(body))
        if not isinstance(payload, dict):
            raise ValueError("payload")
        for claim in ("exp", "nbf"):
            if claim in payload:
                value = payload[claim]
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError("invalid time claim")
                if claim == "exp" and value <= time.time():
                    raise ValueError("expired")
                if claim == "nbf" and value > time.time():
                    raise ValueError("not yet valid")
        return payload
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise ApiError(403, "office_token_invalid", "Недействительная подпись ONLYOFFICE") from exc


class OfficeStore:
    def __init__(self, data_dir: pathlib.Path) -> None:
        self.path = data_dir / "onlyoffice.sqlite3"
        data_dir.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, source TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL, revision INTEGER NOT NULL,
                    active_key TEXT, seed_revision INTEGER, error TEXT
                );
                CREATE TABLE IF NOT EXISTS revisions (
                    document_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    sha256 TEXT NOT NULL, saved_at REAL NOT NULL, pptx BLOB NOT NULL,
                    PRIMARY KEY(document_id, revision)
                );
                CREATE TABLE IF NOT EXISTS edit_locks (
                    document_id TEXT PRIMARY KEY, token TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS save_normalizations (
                    document_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    raw_sha256 TEXT NOT NULL, parts TEXT NOT NULL, raw_pptx BLOB NOT NULL,
                    PRIMARY KEY(document_id, revision, raw_sha256)
                );
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, source: str, title: str, pptx: bytes) -> dict[str, Any]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM documents WHERE source=?", (source,)).fetchone()
            if row:
                return dict(row)
            document_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO documents(id,source,title,revision) VALUES(?,?,?,0)",
                (document_id, source, title),
            )
            db.execute(
                "INSERT INTO revisions VALUES(?,?,?,?,?)",
                (document_id, 0, hashlib.sha256(pptx).hexdigest(), time.time(), pptx),
            )
        return self.get(document_id)

    def find(self, source: str) -> dict[str, Any] | None:
        """Копия по источнику, если она уже есть."""
        with self.connect() as db:
            row = db.execute("SELECT id FROM documents WHERE source=?", (source,)).fetchone()
        return self.get(row[0]) if row else None

    def get(self, document_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise ApiError(404, "office_not_found", "Офисная копия не найдена")
            result = dict(row)
            result["revisions"] = [
                dict(r)
                for r in db.execute(
                    "SELECT revision,sha256,saved_at FROM revisions WHERE document_id=? "
                    "ORDER BY revision DESC",
                    (document_id,),
                )
            ]
            result["normalizations"] = [
                {**dict(item), "parts": json.loads(item["parts"])}
                for item in db.execute(
                    "SELECT revision,raw_sha256,parts FROM save_normalizations "
                    "WHERE document_id=? ORDER BY revision DESC",
                    (document_id,),
                )
            ]
            return result

    def open(self, document_id: str) -> dict[str, Any]:
        self.get(document_id)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            lock = db.execute(
                "SELECT expires FROM edit_locks WHERE document_id=?", (document_id,)
            ).fetchone()
            if lock and lock[0] > time.time():
                raise ApiError(
                    409, "office_edit_busy", "ИИ применяет правку. Повторите открытие позже"
                )
            db.execute(
                "UPDATE documents SET active_key=?,seed_revision=revision,error=NULL "
                "WHERE id=? AND active_key IS NULL",
                (uuid.uuid4().hex, document_id),
            )
        return self.get(document_id)

    def begin_edit(self, document_id: str, revision: int) -> str:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise ApiError(404, "office_not_found", "Документ не найден")
            if row["active_key"] or row["error"] or row["revision"] != revision:
                raise ApiError(
                    409, "office_edit_conflict", "Дождитесь сохранения и закрытия всех вкладок"
                )
            db.execute("DELETE FROM edit_locks WHERE expires<=?", (time.time(),))
            token = uuid.uuid4().hex
            try:
                db.execute(
                    "INSERT INTO edit_locks VALUES(?,?,?)", (document_id, token, time.time() + 240)
                )
            except sqlite3.IntegrityError as exc:
                raise ApiError(409, "office_edit_busy", "Другая ИИ-правка ещё выполняется") from exc
            return token

    def end_edit(self, document_id: str, token: str) -> None:
        with self.connect() as db:
            db.execute(
                "DELETE FROM edit_locks WHERE document_id=? AND token=?", (document_id, token)
            )

    def commit_edit(self, document_id: str, token: str, revision: int, pptx: bytes) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            lock = db.execute(
                "SELECT * FROM edit_locks WHERE document_id=? AND token=?", (document_id, token)
            ).fetchone()
            row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if (
                not lock
                or lock["expires"] <= time.time()
                or not row
                or row["active_key"]
                or row["revision"] != revision
                or row["error"]
            ):
                raise ApiError(
                    409, "office_edit_conflict", "Документ изменился; правка не применена"
                )
            digest = hashlib.sha256(pptx).hexdigest()
            previous = db.execute(
                "SELECT sha256 FROM revisions WHERE document_id=? AND revision=?",
                (document_id, revision),
            ).fetchone()[0]
            if digest != previous:
                db.execute(
                    "INSERT INTO revisions VALUES(?,?,?,?,?)",
                    (document_id, revision + 1, digest, time.time(), pptx),
                )
                db.execute(
                    "UPDATE documents SET revision=? WHERE id=?", (revision + 1, document_id)
                )

    def read(self, document_id: str, revision: int) -> bytes:
        with self.connect() as db:
            row = db.execute(
                "SELECT pptx FROM revisions WHERE document_id=? AND revision=?",
                (document_id, revision),
            ).fetchone()
            if row is None:
                raise ApiError(404, "office_revision_not_found", "Версия не найдена")
            return bytes(row[0])

    def callback(
        self,
        document_id: str,
        key: str,
        status: int,
        pptx: bytes | None,
        *,
        raw_pptx: bytes | None = None,
        normalized_parts: list[str] | None = None,
    ) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if row is None or row["active_key"] != key:
                return False
            if pptx is not None:
                revision = row["revision"]
                digest = hashlib.sha256(pptx).hexdigest()
                previous = db.execute(
                    "SELECT sha256 FROM revisions WHERE document_id=? AND revision=?",
                    (document_id, row["revision"]),
                ).fetchone()[0]
                if previous != digest:
                    revision = row["revision"] + 1
                    db.execute(
                        "INSERT INTO revisions VALUES(?,?,?,?,?)",
                        (
                            document_id,
                            revision,
                            digest,
                            time.time(),
                            pptx,
                        ),
                    )
                    db.execute(
                        "UPDATE documents SET revision=? WHERE id=?",
                        (
                            revision,
                            document_id,
                        ),
                    )
                if raw_pptx is not None and normalized_parts:
                    db.execute(
                        "INSERT OR IGNORE INTO save_normalizations VALUES(?,?,?,?,?)",
                        (
                            document_id,
                            revision,
                            hashlib.sha256(raw_pptx).hexdigest(),
                            json.dumps(normalized_parts),
                            raw_pptx,
                        ),
                    )
            if status in {2, 4}:
                db.execute("UPDATE documents SET active_key=NULL WHERE id=?", (document_id,))
            # Presence / no-change notifications cannot clear a failed save.
            error = row["error"]
            if status in {3, 7}:
                error = "ONLYOFFICE сообщил об ошибке сохранения"
            elif pptx is not None:
                error = None
            db.execute("UPDATE documents SET error=? WHERE id=?", (error, document_id))
            return True
