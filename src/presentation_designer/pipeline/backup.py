"""Резервные копии и восстановление: SQLite снимком, загрузки, артефакты, Valkey.

Копия — один tar без сжатия (PPTX, PDF и PNG уже сжаты): `meta.json`, `state.sqlite3`
(снимок через backup API — живая база в WAL одним файлом не копируется), `valkey.dump`
(логический дамп ключей DUMP/RESTORE с TTL, без ключей воркеров и служебных отметок),
`uploads/<aa>/<sha256>` и `artifacts/{jobs,templates,packages}/…` без временных каталогов.
Порядок записи: сначала база, потом очередь, потом файлы. Файлы хранилища неизменяемы
и адресуются по sha256, ревизии публикуются атомарно, поэтому копия согласована для
всего, на что ссылается снимок базы; появившееся позже — лишние сироты, их снимет
сборка мусора. Материалы организаторов (`data/organizers`) и кэши в копию не входят.

Проверка распаковывает копию в отдельный каталог и сверяет ссылки файлов и манифесты
артефактов; восстановление делает то же на staging-каталогах, откладывает текущие данные
в `.previous-<время>` и переносит восстановленные на место. Восстановление выполняется при
остановленных API и воркерах: их останавливает вызывающий скрипт.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import pathlib
import shutil
import socket
import tarfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from presentation_designer import __version__
from presentation_designer.contracts import CONTRACTS_VERSION
from presentation_designer.pipeline.artifacts import MANIFEST_NAME, ArtifactStore
from presentation_designer.pipeline.files import FileStore
from presentation_designer.pipeline.state import LATEST_SCHEMA, JsonDict, State, now_iso

log = logging.getLogger(__name__)

BACKUP_PREFIX = "backup-"
BACKUP_SUFFIX = ".tar"
META_NAME = "meta.json"
DB_NAME = "state.sqlite3"
VALKEY_NAME = "valkey.dump"
FORMAT = "presentation-designer-backup"
FORMAT_VERSION = 1
# Ключи, которые не переносятся: регистрация воркеров и служебные отметки живого стека.
VALKEY_SKIP_PREFIXES = ("rq:worker", "rq:workers", "pd:renderer:", "pd:gc:")


class BackupError(RuntimeError):
    pass


@dataclass
class BackupResult:
    path: pathlib.Path
    name: str
    size_bytes: int
    meta: JsonDict
    pruned: list[str] = field(default_factory=list)


@dataclass
class VerifyReport:
    name: str
    meta: JsonDict
    problems: list[str] = field(default_factory=list)
    checked: dict[str, int] = field(default_factory=dict)
    work_dir: pathlib.Path | None = None

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "ok": self.ok,
            "problems": self.problems,
            "checked": self.checked,
            "meta": self.meta,
            "work_dir": str(self.work_dir) if self.work_dir else None,
        }


@dataclass
class RestoreReport:
    name: str
    previous_dir: pathlib.Path | None
    verify: VerifyReport
    valkey_keys: int | None
    only_db: bool


def backup_name(now: datetime | None = None, label: str | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    suffix = f"-{_safe_label(label)}" if label else ""
    return f"{BACKUP_PREFIX}{stamp}{suffix}"


def _safe_label(label: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in label.strip())
    return cleaned[:60] or "backup"


def list_backups(out_dir: pathlib.Path) -> list[pathlib.Path]:
    if not out_dir.is_dir():
        return []
    return sorted(
        p
        for p in out_dir.iterdir()
        if p.is_file() and p.name.startswith(BACKUP_PREFIX) and p.name.endswith(BACKUP_SUFFIX)
    )


# ---------- создание ----------


def create_backup(
    state: State,
    files: FileStore,
    artifacts: ArtifactStore,
    out_dir: pathlib.Path,
    *,
    label: str | None = None,
    keep: int = 7,
    valkey_url: str | None = None,
    extra_meta: JsonDict | None = None,
) -> BackupResult:
    """Создаёт копию `out_dir/backup-<время>[-label].tar` и удаляет старые сверх `keep`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    name = backup_name(label=label)
    final = out_dir / f"{name}{BACKUP_SUFFIX}"
    partial = out_dir / f".{name}{BACKUP_SUFFIX}.partial"
    scratch = out_dir / f".{name}.work"
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir()
    meta: JsonDict = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "name": name,
        "created_at": now_iso(),
        "label": label,
        "app_version": __version__,
        "contracts_version": CONTRACTS_VERSION,
        "schema_version": state.schema_version(),
        "hostname": socket.gethostname(),
        "image_tag": os.environ.get("PD_IMAGE_TAG") or None,
        "commit": os.environ.get("PD_BUILD_COMMIT") or None,
        **(extra_meta or {}),
    }
    try:
        with tarfile.open(partial, "w") as tar:
            # 1. База: согласованный снимок.
            if state.db_path.exists():
                db_copy = state.snapshot(scratch / DB_NAME)
                meta["counts"] = State(db_copy, migrate=False).counts()
                tar.add(db_copy, arcname=DB_NAME)
            else:
                meta["counts"] = {}
            # 2. Очередь: логический дамп, если Valkey доступен.
            meta["valkey"] = None
            if valkey_url:
                try:
                    dump_path = scratch / VALKEY_NAME
                    keys = dump_valkey(valkey_url, dump_path)
                    tar.add(dump_path, arcname=VALKEY_NAME)
                    meta["valkey"] = {"keys": keys}
                except Exception as e:  # Valkey необязателен для копии данных
                    log.warning("Valkey не сохранён: %s", e)
                    meta["valkey"] = {"error": str(e)}
            # 3. Хранилище загрузок.
            count = total = 0
            for sha, path in files.iter_blobs():
                tar.add(path, arcname=f"uploads/{sha[:2]}/{sha}")
                count += 1
                total += path.stat().st_size
            meta["uploads"] = {"files": count, "bytes": total}
            # 4. Артефакты без временных каталогов.
            count = total = 0
            for kind in artifacts.KINDS:
                for dir_name in artifacts.list_dirs(kind):
                    base = artifacts.root / kind / dir_name
                    for path in sorted(base.rglob("*")):
                        rel = path.relative_to(artifacts.root)
                        if any(part.startswith(".") for part in rel.parts) or not path.is_file():
                            continue
                        tar.add(path, arcname=f"artifacts/{rel.as_posix()}")
                        count += 1
                        total += path.stat().st_size
            meta["artifacts"] = {"files": count, "bytes": total}
            # 5. Описание — последним: у оборванной копии его нет.
            meta_path = scratch / META_NAME
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            tar.add(meta_path, arcname=META_NAME)
        os.replace(partial, final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    pruned = prune_backups(out_dir, keep)
    log.info(
        "резервная копия %s: %.1f МБ, база %s строк, загрузок %d, артефактов %d",
        final.name,
        final.stat().st_size / (1024 * 1024),
        sum(meta.get("counts", {}).values()),
        meta["uploads"]["files"],
        meta["artifacts"]["files"],
    )
    return BackupResult(final, name, final.stat().st_size, meta, pruned)


def prune_backups(out_dir: pathlib.Path, keep: int) -> list[str]:
    """Оставляет `keep` последних копий по имени (в имени — время создания)."""
    if keep <= 0:
        return []
    removed: list[str] = []
    for path in list_backups(out_dir)[:-keep]:
        path.unlink(missing_ok=True)
        removed.append(path.name)
        log.info("старая резервная копия удалена: %s", path.name)
    return removed


# ---------- Valkey ----------


def _redis(url: str) -> Any:
    from presentation_designer.shared.valkey import connect

    return connect(url)


def dump_valkey(url: str, dest: pathlib.Path) -> int:
    """Логический дамп: по строке на ключ — имя, TTL и сериализованное значение (DUMP)."""
    client = _redis(url)
    client.ping()
    count = 0
    with dest.open("w", encoding="utf-8") as out:
        out.write(json.dumps({"format": "valkey-dump", "version": 1}) + "\n")
        for key in client.scan_iter(count=200):
            name = key.decode("utf-8", "surrogateescape")
            if name.startswith(VALKEY_SKIP_PREFIXES):
                continue
            payload = client.dump(key)
            if payload is None:
                continue
            ttl = client.pttl(key)
            out.write(
                json.dumps(
                    {
                        "k": base64.b64encode(key).decode("ascii"),
                        "t": int(ttl) if ttl and ttl > 0 else 0,
                        "v": base64.b64encode(payload).decode("ascii"),
                    }
                )
                + "\n"
            )
            count += 1
    return count


def restore_valkey(url: str, dump: pathlib.Path, *, flush: bool = True) -> int:
    client = _redis(url)
    client.ping()
    if flush:
        client.flushdb()
    count = 0
    with dump.open(encoding="utf-8") as src:
        header = json.loads(src.readline() or "{}")
        if header.get("format") != "valkey-dump":
            raise BackupError("valkey.dump: неизвестный формат")
        for line in src:
            if not line.strip():
                continue
            entry = json.loads(line)
            client.restore(
                base64.b64decode(entry["k"]),
                int(entry.get("t") or 0),
                base64.b64decode(entry["v"]),
                replace=True,
            )
            count += 1
    return count


# ---------- проверка ----------


def _extract(archive: pathlib.Path, target: pathlib.Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r") as tar:
        tar.extractall(target, filter="data")


def read_meta(archive: pathlib.Path) -> JsonDict:
    with tarfile.open(archive, "r") as tar:
        try:
            member = tar.extractfile(META_NAME)
        except KeyError as e:
            raise BackupError(f"{archive.name}: нет {META_NAME} — копия оборвана") from e
        if member is None:
            raise BackupError(f"{archive.name}: нет {META_NAME}")
        meta = json.loads(member.read().decode("utf-8"))
    if meta.get("format") != FORMAT:
        raise BackupError(f"{archive.name}: это не резервная копия сервиса")
    return meta  # type: ignore[no-any-return]


def verify_backup(
    archive: pathlib.Path, work_dir: pathlib.Path, *, keep: bool = False
) -> VerifyReport:
    """Распаковывает копию в `work_dir/<имя>` и сверяет её содержимое с базой."""
    meta = read_meta(archive)
    name = (
        archive.name[: -len(BACKUP_SUFFIX)]
        if archive.name.endswith(BACKUP_SUFFIX)
        else archive.stem
    )
    target = work_dir / name
    if target.exists():
        shutil.rmtree(target)
    _extract(archive, target)
    report = VerifyReport(name=name, meta=meta, work_dir=target if keep else None)
    try:
        check_layout(target / DB_NAME, target / "uploads", target / "artifacts", report, meta=meta)
    finally:
        if not keep:
            shutil.rmtree(target, ignore_errors=True)
    return report


def check_layout(
    db_path: pathlib.Path,
    uploads_dir: pathlib.Path,
    artifacts_dir: pathlib.Path,
    report: VerifyReport,
    *,
    meta: JsonDict | None = None,
) -> VerifyReport:
    """Сверка распакованной копии: целостность базы, файлы по sha256, манифесты ревизий,
    превью шаблонов. Проблемы накапливаются в отчёте, первая ошибка не прерывает проверку."""
    problems = report.problems
    if not db_path.is_file():
        problems.append("нет state.sqlite3")
        return report
    state = State(db_path, migrate=False)
    if not state.integrity_ok():
        problems.append("PRAGMA integrity_check не прошёл")
    version = state.schema_version()
    report.checked["schema_version"] = version
    if version > LATEST_SCHEMA:
        problems.append(f"схема {version} новее, чем поддерживает этот код ({LATEST_SCHEMA})")
    counts = state.counts()
    report.checked.update({f"rows_{k}": v for k, v in counts.items()})
    if meta and meta.get("counts") and meta["counts"] != counts:
        problems.append("число строк в базе не совпадает с meta.json")

    snapshot = state.gc_snapshot()
    expected: dict[str, int | None] = {}
    for row in snapshot["files"]:
        expected[row["sha256"]] = row.get("size_bytes")
    for row in snapshot["templates"]:
        expected.setdefault(row["sha256"], None)
    checked = 0
    for sha, size in sorted(expected.items()):
        path = uploads_dir / sha[:2] / sha
        if not path.is_file():
            problems.append(f"нет файла хранилища {sha}")
            continue
        actual = path.stat().st_size
        if size is not None and actual != size:
            problems.append(f"размер {sha}: {actual} вместо {size}")
        if _sha256(path) != sha:
            problems.append(f"содержимое {sha} не совпадает с именем")
        checked += 1
    report.checked["blobs"] = checked

    revisions = 0
    artifact_files = 0
    with state.read() as conn:
        rows = conn.execute("SELECT job_id, variant_id, revision, artifacts_prefix FROM revisions")
        for row in rows:
            revisions += 1
            base = (
                artifacts_dir / "jobs" / row["job_id"] / row["variant_id"] / f"r{row['revision']}"
            )
            manifest_path = base / MANIFEST_NAME
            if not manifest_path.is_file():
                problems.append(f"нет манифеста ревизии {row['job_id']}/{row['artifacts_prefix']}")
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except ValueError:
                problems.append(f"манифест не читается: {manifest_path}")
                continue
            for name, entry in manifest.items():
                rel = name[len(row["artifacts_prefix"]) :]
                path = base / pathlib.Path(*pathlib.PurePosixPath(rel).parts)
                if not path.is_file():
                    problems.append(f"нет артефакта {row['job_id']}/{name}")
                    continue
                if (
                    entry.get("size_bytes") is not None
                    and path.stat().st_size != entry["size_bytes"]
                ):
                    problems.append(f"размер артефакта {row['job_id']}/{name} не совпадает")
                elif entry.get("sha256") and _sha256(path) != entry["sha256"]:
                    problems.append(f"sha256 артефакта {row['job_id']}/{name} не совпадает")
                artifact_files += 1
    report.checked["revisions"] = revisions
    report.checked["artifact_files"] = artifact_files

    previews = 0
    for row in snapshot["templates"]:
        names = json.loads(row.get("previews") or "[]")
        for preview in names:
            if not (artifacts_dir / "templates" / row["id"] / preview).is_file():
                problems.append(f"нет превью шаблона {row['id']}/{preview}")
            else:
                previews += 1
    report.checked["previews"] = previews
    return report


def _archive_name(archive: pathlib.Path) -> str:
    if archive.name.endswith(BACKUP_SUFFIX):
        return archive.name[: -len(BACKUP_SUFFIX)]
    return archive.stem


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------- восстановление ----------


def restore_backup(
    archive: pathlib.Path,
    *,
    data_dir: pathlib.Path,
    artifacts_dir: pathlib.Path,
    valkey_url: str | None = None,
    valkey_mode: str = "restore",
    only_db: bool = False,
    force: bool = False,
) -> RestoreReport:
    """Восстанавливает копию на место. Текущие данные откладываются в `.previous-<время>`
    внутри тех же каталогов, чтобы ничего не терять; их удаляет администратор.

    `valkey_mode`: restore — очередь из копии, flush — очистить (откат схемы: задания
    в очереди ссылаются на строки, которых больше нет), keep — не трогать.
    Вызывать только при остановленных API и воркерах.
    """
    meta = read_meta(archive)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    name = (
        archive.name[: -len(BACKUP_SUFFIX)]
        if archive.name.endswith(BACKUP_SUFFIX)
        else archive.stem
    )
    data_stage = data_dir / f".restore-{stamp}"
    art_stage = artifacts_dir / f".restore-{stamp}"
    data_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    data_stage.mkdir()
    art_stage.mkdir()
    try:
        # Распаковка по назначению: артефакты могут лежать на другом монтировании,
        # поэтому staging-каталоги создаются рядом с целевыми и переносятся переименованием.
        with tarfile.open(archive, "r") as tar:
            for member in tar.getmembers():
                if member.name.startswith("artifacts/"):
                    member.name = member.name[len("artifacts/") :]
                    if not only_db:
                        tar.extract(member, art_stage, filter="data")
                elif member.name.startswith("uploads/"):
                    if not only_db:
                        tar.extract(member, data_stage, filter="data")
                elif member.name in (DB_NAME, VALKEY_NAME, META_NAME):
                    tar.extract(member, data_stage, filter="data")
        report = VerifyReport(name=name, meta=meta)
        if only_db:
            check_layout(
                data_stage / DB_NAME, data_dir / "uploads", artifacts_dir, report, meta=meta
            )
        else:
            check_layout(data_stage / DB_NAME, data_stage / "uploads", art_stage, report, meta=meta)
        if report.problems and not force:
            raise BackupError(
                "копия не прошла проверку, восстановление отменено: "
                + "; ".join(report.problems[:5])
            )

        previous = data_dir / f".previous-{stamp}"
        previous.mkdir()
        for suffix in ("", "-wal", "-shm"):
            current = data_dir / f"{DB_NAME}{suffix}"
            if current.exists():
                current.rename(previous / current.name)
        (data_stage / DB_NAME).rename(data_dir / DB_NAME)
        if not only_db:
            uploads = data_dir / "uploads"
            if uploads.exists():
                uploads.rename(previous / "uploads")
            staged_uploads = data_stage / "uploads"
            if staged_uploads.exists():
                staged_uploads.rename(uploads)
            else:
                uploads.mkdir()
            art_previous = artifacts_dir / f".previous-{stamp}"
            art_previous.mkdir()
            for kind in ArtifactStore.KINDS:
                current_kind = artifacts_dir / kind
                if current_kind.exists():
                    current_kind.rename(art_previous / kind)
                staged_kind = art_stage / kind
                if staged_kind.exists():
                    staged_kind.rename(current_kind)
        keys: int | None = None
        dump = data_stage / VALKEY_NAME
        if valkey_url and valkey_mode == "restore" and dump.exists():
            keys = restore_valkey(valkey_url, dump, flush=True)
        elif valkey_url and valkey_mode in ("restore", "flush"):
            _redis(valkey_url).flushdb()
            keys = 0
        log.info(
            "восстановлено из %s: база схемы %s, предыдущие данные в %s",
            archive.name,
            report.checked.get("schema_version"),
            previous,
        )
        return RestoreReport(name, previous, report, keys, only_db)
    finally:
        shutil.rmtree(data_stage, ignore_errors=True)
        shutil.rmtree(art_stage, ignore_errors=True)
