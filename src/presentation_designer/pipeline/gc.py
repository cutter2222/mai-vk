"""Сборка мусора хранилища и артефактов по срокам из config/app.yaml.

Живыми считаются объекты, до которых можно дойти от проектов: задание, шаблон и пакет
проекта, карточки ленты (задания, пакеты, файлы), файлы проекта, а от них — пакеты
и шаблоны генераций, дочерние исправления, файлы пакетов и байты в хранилище.
Шаблоны — библиотека сервиса: пока есть строка шаблона, живы его задание анализа
и байты; после удаления из библиотеки они становятся сиротами. Незавершённые задания
тоже живые.

Всё остальное — сироты: задания и пакеты удалённых проектов с их артефактами, строки
файлов вне проектов, байты без ссылок, каталоги артефактов без строк в базе, обрывки
загрузок. Сирота не удаляется сразу: при первой встрече она получает отметку в таблице
`gc_marks`, и только когда отметка старше срока из `retention`, объект удаляется.
Так грация защищает промежуточные состояния (файл уже сохранён, но ещё не привязан;
задание создано, но проект ещё не обновлён). Если объект снова стал живым, отметка
снимается. Каждое удаление пишется в лог; сводка возвращается вызывающему.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from presentation_designer.pipeline.artifacts import ArtifactStore, dir_size
from presentation_designer.pipeline.files import FileStore
from presentation_designer.pipeline.state import JsonDict, State, now_iso
from presentation_designer.shared.settings import Retention

log = logging.getLogger(__name__)

ACTIVE = frozenset({"queued", "running"})
KIND_JOB = "job"
KIND_PACKAGE = "package"
KIND_FILE = "file"
KIND_BLOB = "blob"
KIND_DIR = "dir"


@dataclass
class GcReport:
    started_at: str
    dry_run: bool
    finished_at: str = ""
    marked: dict[str, int] = field(default_factory=dict)
    kept: dict[str, int] = field(default_factory=dict)
    removed: dict[str, int] = field(default_factory=dict)
    freed_bytes: int = 0
    items: list[JsonDict] = field(default_factory=list)

    def to_dict(self) -> JsonDict:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "dry_run": self.dry_run,
            "marked": self.marked,
            "kept": self.kept,
            "removed": self.removed,
            "freed_bytes": self.freed_bytes,
            "freed_mb": round(self.freed_bytes / (1024 * 1024), 1),
            "items": self.items,
        }

    def summary(self) -> str:
        removed = sum(self.removed.values())
        marked = sum(self.marked.values())
        waiting = sum(self.kept.values())
        mode = " (пробный прогон, ничего не удалено)" if self.dry_run else ""
        return (
            f"сборка мусора: удалено {removed}, освобождено {self.freed_bytes / (1024 * 1024):.1f}"
            f" МБ, новых отметок {marked}, ждут срока {waiting}{mode}"
        )


@dataclass(frozen=True)
class _Orphan:
    kind: str
    key: str
    grace: timedelta
    bytes_hint: int = 0


def live_sets(snapshot: JsonDict) -> dict[str, set[str]]:
    """Замыкание ссылок от проектов и активных заданий: что нельзя трогать."""
    projects = snapshot["projects"]
    refs = snapshot["event_refs"]
    files = snapshot["files"]
    templates = snapshot["templates"]
    packages = {p["id"]: p for p in snapshot["packages"]}
    jobs = {j["id"]: j for j in snapshot["jobs"]}
    generations = {g["job_id"]: g for g in snapshot["generations"]}
    children: dict[str, list[str]] = {}
    for j in snapshot["jobs"]:
        if j.get("parent_job_id"):
            children.setdefault(j["parent_job_id"], []).append(j["id"])
    for r in snapshot["repairs"]:
        children.setdefault(r["job_id"], []).append(r["repair_job_id"])

    live_jobs: set[str] = set()
    live_packages: set[str] = set()
    live_files: set[str] = set()
    live_templates = {t["id"] for t in templates}

    for p in projects:
        if p.get("job_id"):
            live_jobs.add(p["job_id"])
        if p.get("package_id"):
            live_packages.add(p["package_id"])
    live_jobs.update(refs.get("job_id", []))
    live_packages.update(refs.get("package_id", []))
    live_files.update(refs.get("file_id", []))
    live_jobs.update(j["id"] for j in jobs.values() if j["status"] in ACTIVE)
    live_jobs.update(t["job_id"] for t in templates if t.get("job_id"))
    # Файлы проектов живут вместе с проектом (каскад при удалении проекта).
    for f in files:
        if f.get("project_id"):
            live_files.add(f["id"])
            if f.get("package_id"):
                live_packages.add(f["package_id"])

    # Замыкание: задания → пакеты и дети; пакеты → файлы и задания импорта.
    changed = True
    while changed:
        changed = False
        for job_id in list(live_jobs):
            gen = generations.get(job_id)
            if gen and gen.get("package_id") and gen["package_id"] not in live_packages:
                live_packages.add(gen["package_id"])
                changed = True
            for child in children.get(job_id, []):
                if child not in live_jobs:
                    live_jobs.add(child)
                    changed = True
            parent = jobs.get(job_id, {}).get("parent_job_id")
            if parent and parent not in live_jobs:
                live_jobs.add(parent)
                changed = True
        for package_id in list(live_packages):
            pkg = packages.get(package_id)
            if pkg is None:
                continue
            if pkg.get("job_id") and pkg["job_id"] not in live_jobs:
                live_jobs.add(pkg["job_id"])
                changed = True
            for file_id in pkg.get("file_ids", []):
                if file_id not in live_files:
                    live_files.add(file_id)
                    changed = True

    file_rows = {f["id"]: f for f in files}
    live_blobs = {t["sha256"] for t in templates}
    live_blobs.update(file_rows[fid]["sha256"] for fid in live_files if fid in file_rows)
    return {
        "jobs": live_jobs,
        "packages": live_packages,
        "files": live_files,
        "templates": live_templates,
        "blobs": live_blobs,
    }


def collect_garbage(
    state: State,
    files: FileStore,
    artifacts: ArtifactStore,
    retention: Retention,
    *,
    dry_run: bool = False,
    grace_override: timedelta | None = None,
    now: datetime | None = None,
    report_path: pathlib.Path | None = None,
) -> GcReport:
    """Один проход сборки мусора. `grace_override` (например, 0) заменяет сроки из конфига."""
    now = now or datetime.now(UTC)
    report = GcReport(started_at=now_iso(), dry_run=dry_run)
    snapshot = state.gc_snapshot()
    live = live_sets(snapshot)
    marks = state.gc_marks()

    hours = grace_override
    jobs_grace = hours if hours is not None else timedelta(hours=retention.orphan_jobs_hours)
    blobs_grace = (
        hours if hours is not None else timedelta(hours=retention.unreferenced_uploads_hours)
    )

    orphans: list[_Orphan] = []
    jobs = {j["id"]: j for j in snapshot["jobs"]}
    for job in snapshot["jobs"]:
        if job["id"] not in live["jobs"]:
            orphans.append(
                _Orphan(
                    KIND_JOB,
                    job["id"],
                    jobs_grace,
                    dir_size(artifacts.job_dir(job["id"])),
                )
            )
    for pkg in snapshot["packages"]:
        if pkg["id"] not in live["packages"]:
            orphans.append(
                _Orphan(
                    KIND_PACKAGE,
                    pkg["id"],
                    jobs_grace,
                    dir_size(artifacts.package_dir(pkg["id"])),
                )
            )
    for row in snapshot["files"]:
        if row["id"] not in live["files"]:
            orphans.append(_Orphan(KIND_FILE, row["id"], blobs_grace, 0))
    for sha, path in files.iter_blobs():
        if sha not in live["blobs"]:
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            orphans.append(_Orphan(KIND_BLOB, sha, blobs_grace, size))
    known = {
        "jobs": set(jobs),
        "templates": live["templates"],
        "packages": {p["id"] for p in snapshot["packages"]},
    }
    for kind in artifacts.KINDS:
        for name in artifacts.list_dirs(kind):
            if name not in known[kind]:
                orphans.append(
                    _Orphan(
                        KIND_DIR,
                        f"{kind}/{name}",
                        jobs_grace,
                        dir_size(artifacts.root / kind / name),
                    )
                )

    orphan_keys = {(o.kind, o.key) for o in orphans}
    # Отметки объектов, которые снова живы или исчезли, снимаются.
    for kind, key in list(marks):
        if (kind, key) not in orphan_keys:
            if not dry_run:
                state.gc_unmark(kind, key)
            marks.pop((kind, key), None)

    ts = now_iso()
    for orphan in orphans:
        mark = marks.get((orphan.kind, orphan.key))
        if mark is None:
            report.marked[orphan.kind] = report.marked.get(orphan.kind, 0) + 1
            if not dry_run:
                state.gc_mark(orphan.kind, orphan.key, ts)
            continue
        marked_at = datetime.fromisoformat(mark.replace("Z", "+00:00"))
        if marked_at + orphan.grace > now:
            report.kept[orphan.kind] = report.kept.get(orphan.kind, 0) + 1
            continue
        freed = _remove(state, files, artifacts, orphan, dry_run=dry_run)
        report.removed[orphan.kind] = report.removed.get(orphan.kind, 0) + 1
        report.freed_bytes += freed
        report.items.append({"kind": orphan.kind, "key": orphan.key, "bytes": freed})
        log.info(
            "сборка мусора: %s %s %s (%s, без ссылок с %s)",
            "удалил бы" if dry_run else "удалён",
            _label(orphan.kind),
            orphan.key,
            _mb(freed),
            mark,
        )
        if not dry_run:
            state.gc_unmark(orphan.kind, orphan.key)

    # Обрывки загрузок всегда удаляются только по возрасту: свежий временный файл может
    # принадлежать идущей прямо сейчас загрузке.
    tmp_count, tmp_bytes = (0, 0)
    if not dry_run:
        tmp_count, tmp_bytes = files.cleanup_tmp(older_than_s=retention.upload_tmp_hours * 3600)
    if tmp_count:
        report.removed["tmp"] = tmp_count
        report.freed_bytes += tmp_bytes
        log.info("сборка мусора: удалено обрывков загрузок %d (%s)", tmp_count, _mb(tmp_bytes))

    report.finished_at = now_iso()
    log.info(report.summary())
    if report_path is not None and not dry_run:
        _write_report(report_path, report)
    return report


def _remove(
    state: State, files: FileStore, artifacts: ArtifactStore, orphan: _Orphan, *, dry_run: bool
) -> int:
    if dry_run:
        return orphan.bytes_hint
    if orphan.kind == KIND_JOB:
        freed = dir_size(artifacts.job_dir(orphan.key))
        artifacts.delete_job(orphan.key)
        state.delete_jobs([orphan.key])
        return freed
    if orphan.kind == KIND_PACKAGE:
        path = artifacts.package_dir(orphan.key)
        freed = artifacts.delete_dir("packages", orphan.key) if path.exists() else 0
        state.delete_packages([orphan.key])
        return freed
    if orphan.kind == KIND_FILE:
        state.delete_file_rows([orphan.key])
        return 0
    if orphan.kind == KIND_BLOB:
        return files.remove(orphan.key)
    if orphan.kind == KIND_DIR:
        kind, name = orphan.key.split("/", 1)
        return artifacts.delete_dir(kind, name)
    raise ValueError(orphan.kind)


def _label(kind: str) -> str:
    return {
        KIND_JOB: "задание",
        KIND_PACKAGE: "пакет",
        KIND_FILE: "запись файла",
        KIND_BLOB: "файл хранилища",
        KIND_DIR: "каталог артефактов",
    }.get(kind, kind)


def _mb(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} МБ"


def _write_report(path: pathlib.Path, report: GcReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_report(path: pathlib.Path) -> JsonDict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
