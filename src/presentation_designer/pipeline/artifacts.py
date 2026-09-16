"""Артефакты заданий: каталог ревизии, атомарная публикация, манифест.

Файлы ревизии собираются во временном каталоге рядом с целевым и переименовываются
одним вызовом после того, как записан манифест. Пока переименование не произошло,
ревизии не существует: частично записанный файл не появляется в списке готовых.
Имена в манифесте относительны каталогу задания (`<variant>/r<rev>/deck.pptx`), и только
они разрешены для выдачи через API.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

CONTENT_TYPES: dict[str, str] = {
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".pdf": "application/pdf",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
}

MANIFEST_NAME = "manifest.json"


def content_type_for(name: str) -> str:
    return CONTENT_TYPES.get(pathlib.PurePosixPath(name).suffix.lower(), "application/octet-stream")


def sha256_of(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class Staging:
    """Каталог ревизии: сначала временный, после публикации — целевой.

    Публиковать можно не один раз: файлы после экспорта становятся доступны раньше отчёта
    аудита, а манифест каждый раз переписывается атомарно.
    """

    dir: pathlib.Path
    final: pathlib.Path
    prefix: str
    manifest: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def published(self) -> bool:
        return self.dir == self.final

    def path(self, name: str) -> pathlib.Path:
        target = self.dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def write_json(self, name: str, document: Any) -> pathlib.Path:
        target = self.path(name)
        target.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def write_bytes(self, name: str, data: bytes) -> pathlib.Path:
        target = self.path(name)
        target.write_bytes(data)
        return target

    def build_manifest(self) -> dict[str, dict[str, Any]]:
        manifest: dict[str, dict[str, Any]] = {}
        for path in sorted(self.dir.rglob("*")):
            if not path.is_file() or path.name == MANIFEST_NAME or path.name.startswith("."):
                continue
            rel = path.relative_to(self.dir).as_posix()
            manifest[f"{self.prefix}{rel}"] = {
                "content_type": content_type_for(rel),
                "size_bytes": path.stat().st_size,
                "sha256": sha256_of(path),
            }
        self.manifest = manifest
        return manifest


class ArtifactStore:
    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------- каталоги ----------

    def job_dir(self, job_id: str) -> pathlib.Path:
        return self.root / "jobs" / job_id

    def revision_dir(self, job_id: str, variant_id: str, revision: int) -> pathlib.Path:
        return self.job_dir(job_id) / variant_id / f"r{revision}"

    def template_dir(self, template_id: str) -> pathlib.Path:
        return self.root / "templates" / template_id

    def package_dir(self, package_id: str) -> pathlib.Path:
        return self.root / "packages" / package_id

    @staticmethod
    def prefix(variant_id: str, revision: int) -> str:
        return f"{variant_id}/r{revision}/"

    # ---------- публикация ----------

    @contextmanager
    def stage_revision(self, job_id: str, variant_id: str, revision: int) -> Iterator[Staging]:
        """Собирает ревизию во временном каталоге и публикует при выходе без ошибки.

        При ошибке неопубликованный каталог удаляется; уже опубликованная ревизия остаётся
        с последним записанным манифестом.
        """
        final = self.revision_dir(job_id, variant_id, revision)
        tmp = final.parent / f".r{revision}.tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True, exist_ok=True)
        staging = Staging(dir=tmp, final=final, prefix=self.prefix(variant_id, revision))
        try:
            yield staging
            self.publish(staging)
        except BaseException:
            if not staging.published:
                shutil.rmtree(tmp, ignore_errors=True)
            raise

    def publish(self, staging: Staging) -> dict[str, dict[str, Any]]:
        """Делает каталог ревизии публичным: манифест пишется атомарно,
        каталог переименовывается один раз."""
        manifest = staging.build_manifest()
        manifest_tmp = staging.dir / f".{MANIFEST_NAME}.tmp"
        manifest_tmp.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(manifest_tmp, staging.dir / MANIFEST_NAME)
        if not staging.published:
            if staging.final.exists():
                shutil.rmtree(staging.final)
            os.replace(staging.dir, staging.final)
            staging.dir = staging.final
        return manifest

    @contextmanager
    def stage_dir(self, final: pathlib.Path) -> Iterator[pathlib.Path]:
        """Атомарная запись произвольного каталога (превью шаблона, ресурсы пакета)."""
        tmp = final.parent / f".{final.name}.tmp"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            yield tmp
            if final.exists():
                shutil.rmtree(final)
            os.replace(tmp, final)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise

    def read_manifest(
        self, job_id: str, variant_id: str, revision: int
    ) -> dict[str, dict[str, Any]]:
        path = self.revision_dir(job_id, variant_id, revision) / MANIFEST_NAME
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]

    def resolve(self, job_id: str, name: str) -> pathlib.Path:
        """Путь к артефакту по имени из манифеста; имя уже проверено вызывающим кодом."""
        rel = pathlib.PurePosixPath(name)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(name)
        return self.job_dir(job_id) / pathlib.Path(*rel.parts)

    def delete_job(self, job_id: str) -> None:
        shutil.rmtree(self.job_dir(job_id), ignore_errors=True)
