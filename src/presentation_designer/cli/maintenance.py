"""Обслуживание стека: миграции, резервные копии, проверка, восстановление, сборка мусора.

Запускается внутри образа API (deploy/*.sh) или локально:
    python -m presentation_designer.cli.maintenance migrate [--backup-dir DIR] [--label L]
    python -m presentation_designer.cli.maintenance backup [--out DIR] [--label L] [--no-valkey]
    python -m presentation_designer.cli.maintenance verify ARCHIVE [--work-dir DIR] [--keep]
    python -m presentation_designer.cli.maintenance restore ARCHIVE [--only-db] [--valkey MODE]
    python -m presentation_designer.cli.maintenance gc [--dry-run] [--grace-hours H]
    python -m presentation_designer.cli.maintenance list-backups [--dir DIR]

Человекочитаемые сообщения идут в stderr (логи), результат — строками `ключ=значение`
в stdout, чтобы скрипты deploy/ разбирали его без jq.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys
from datetime import timedelta

from presentation_designer.pipeline import backup as bk
from presentation_designer.pipeline.artifacts import ArtifactStore
from presentation_designer.pipeline.files import FileStore
from presentation_designer.pipeline.gc import collect_garbage
from presentation_designer.pipeline.state import LATEST_SCHEMA, State
from presentation_designer.shared.settings import Settings, get_settings

log = logging.getLogger("maintenance")


def _stores(settings: Settings, *, migrate: bool) -> tuple[State, FileStore, ArtifactStore]:
    state = State(settings.db_path, migrate=migrate)
    files = FileStore(
        settings.uploads_dir,
        max_upload_mb=settings.limits.max_upload_mb,
        max_unzipped_mb=settings.limits.max_unzipped_mb,
    )
    return state, files, ArtifactStore(settings.artifacts_dir)


def _valkey_url(args: argparse.Namespace) -> str | None:
    if getattr(args, "no_valkey", False):
        return None
    return os.environ.get("PD_VALKEY_URL", "redis://localhost:6379/0")


def _emit(**values: object) -> None:
    for key, value in values.items():
        print(f"{key}={value}")


def cmd_migrate(args: argparse.Namespace, settings: Settings) -> int:
    """Миграция до up -d: если есть недостающие версии, сначала согласованная копия."""
    state, files, artifacts = _stores(settings, migrate=False)
    before = state.schema_version()
    pending = state.pending_migrations()
    archive: pathlib.Path | None = None
    if pending and state.db_path.exists():
        out_dir = pathlib.Path(args.backup_dir) if args.backup_dir else settings.backups_dir
        log.info(
            "схема %d → %d, перед миграцией создаётся резервная копия в %s",
            before,
            LATEST_SCHEMA,
            out_dir,
        )
        result = bk.create_backup(
            state,
            files,
            artifacts,
            out_dir,
            label=args.label or f"pre-migrate-{LATEST_SCHEMA}",
            keep=settings.backup.keep,
            valkey_url=_valkey_url(args),
        )
        archive = result.path
    before, after = state.migrate()
    log.info("схема базы: %d → %d", before, after)
    _emit(schema_before=before, schema_after=after, backup=archive or "")
    return 0


def cmd_backup(args: argparse.Namespace, settings: Settings) -> int:
    state, files, artifacts = _stores(settings, migrate=False)
    out_dir = pathlib.Path(args.out) if args.out else settings.backups_dir
    result = bk.create_backup(
        state,
        files,
        artifacts,
        out_dir,
        label=args.label,
        keep=settings.backup.keep,
        valkey_url=_valkey_url(args),
    )
    _emit(
        archive=result.path,
        name=result.name,
        size_mb=round(result.size_bytes / (1024 * 1024), 1),
        schema=result.meta.get("schema_version"),
        pruned=",".join(result.pruned),
    )
    return 0


def cmd_verify(args: argparse.Namespace, settings: Settings) -> int:
    archive = pathlib.Path(args.archive)
    work_dir = pathlib.Path(args.work_dir) if args.work_dir else settings.backups_dir / ".verify"
    report = bk.verify_backup(archive, work_dir, keep=args.keep)
    for problem in report.problems:
        log.error("проверка: %s", problem)
    log.info(
        "проверка %s: %s; %s",
        report.name,
        "без замечаний" if report.ok else f"{len(report.problems)} проблем",
        ", ".join(f"{k}={v}" for k, v in report.checked.items()),
    )
    _emit(
        ok="yes" if report.ok else "no",
        problems=len(report.problems),
        checked=json.dumps(report.checked, ensure_ascii=False),
        work_dir=report.work_dir or "",
    )
    return 0 if report.ok else 1


def cmd_restore(args: argparse.Namespace, settings: Settings) -> int:
    report = bk.restore_backup(
        pathlib.Path(args.archive),
        data_dir=settings.data_dir,
        artifacts_dir=settings.artifacts_dir,
        valkey_url=None if args.valkey == "keep" else _valkey_url(args),
        valkey_mode=args.valkey,
        only_db=args.only_db,
        force=args.force,
    )
    for problem in report.verify.problems:
        log.warning("восстановлено с замечанием: %s", problem)
    _emit(
        name=report.name,
        previous_dir=report.previous_dir or "",
        valkey_keys="" if report.valkey_keys is None else report.valkey_keys,
        schema=report.verify.checked.get("schema_version", ""),
        only_db="yes" if report.only_db else "no",
    )
    return 0


def cmd_gc(args: argparse.Namespace, settings: Settings) -> int:
    state, files, artifacts = _stores(settings, migrate=True)
    report = collect_garbage(
        state,
        files,
        artifacts,
        settings.retention,
        dry_run=args.dry_run,
        grace_override=timedelta(hours=args.grace_hours) if args.grace_hours is not None else None,
        report_path=settings.runs_dir / "gc" / "last.json",
    )
    data = report.to_dict()
    _emit(
        removed=sum(report.removed.values()),
        marked=sum(report.marked.values()),
        waiting=sum(report.kept.values()),
        freed_mb=data["freed_mb"],
        dry_run="yes" if args.dry_run else "no",
    )
    return 0


def cmd_list(args: argparse.Namespace, settings: Settings) -> int:
    out_dir = pathlib.Path(args.dir) if args.dir else settings.backups_dir
    for path in bk.list_backups(out_dir):
        print(f"{path.name}\t{path.stat().st_size / (1024 * 1024):.1f} МБ")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="presentation-designer-maintenance", description="Обслуживание стека"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("migrate", help="применить миграции; перед изменением схемы — копия")
    p.add_argument("--backup-dir", help="куда класть копию перед миграцией")
    p.add_argument("--label", help="метка копии")
    p.add_argument("--no-valkey", action="store_true", help="не включать Valkey в копию")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("backup", help="создать резервную копию")
    p.add_argument("--out", help="каталог копий (по умолчанию paths.backups_dir)")
    p.add_argument("--label", help="метка в имени копии")
    p.add_argument("--no-valkey", action="store_true", help="не включать Valkey в копию")
    p.set_defaults(func=cmd_backup)

    p = sub.add_parser("verify", help="распаковать копию в отдельный каталог и проверить")
    p.add_argument("archive")
    p.add_argument("--work-dir", help="куда распаковывать (по умолчанию backups/.verify)")
    p.add_argument("--keep", action="store_true", help="оставить распакованный каталог")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("restore", help="восстановить копию на место (API и воркеры остановлены)")
    p.add_argument("archive")
    p.add_argument("--only-db", action="store_true", help="только база: откат схемы")
    p.add_argument(
        "--valkey",
        choices=["restore", "flush", "keep"],
        default="restore",
        help="очередь: из копии, очистить или не трогать",
    )
    p.add_argument("--force", action="store_true", help="восстановить даже с замечаниями")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("gc", help="сборка мусора по срокам из config/app.yaml")
    p.add_argument("--dry-run", action="store_true", help="только показать, что было бы удалено")
    p.add_argument(
        "--grace-hours",
        type=float,
        help="заменить сроки из конфига (0 — удалить всех уже отмеченных сирот)",
    )
    p.set_defaults(func=cmd_gc)

    p = sub.add_parser("list-backups", help="список резервных копий")
    p.add_argument("--dir")
    p.set_defaults(func=cmd_list)
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("PD_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    args = build_parser().parse_args(argv)
    settings = get_settings()
    try:
        return int(args.func(args, settings))
    except bk.BackupError as e:
        log.error("%s", e)
        return 2


if __name__ == "__main__":
    sys.exit(main())
