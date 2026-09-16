"""Хранилище загрузок: байты адресуются по sha256 и хранятся один раз.

Файл пишется потоком во временный каталог с подсчётом хеша и контролем размера, затем
атомарно переносится в data/uploads/<aa>/<sha256>. Проверка при загрузке зависит от
типа: pptx/docx/xlsx — ZIP, пути внутри архива, распакованный объём и часть содержимого;
pdf, изображения и текст — своим способом; остальные типы принимаются без проверки
и в импорт не попадают. Слои получают файл по идентификатору через это хранилище,
пути от клиента не принимаются.
"""

from __future__ import annotations

import hashlib
import io
import os
import pathlib
import shutil
import tempfile
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from typing import BinaryIO, cast

FORMAT_BY_EXT: dict[str, str] = {
    ".pptx": "pptx",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".csv": "csv",
    ".pdf": "pdf",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
}

MIME_BY_FORMAT: dict[str, str] = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "pdf": "application/pdf",
    "markdown": "text/markdown",
    "text": "text/plain",
    "image": "image/png",
    "other": "application/octet-stream",
}

# Материалы, которые импортируются в содержание; остальное лежит в проекте без импорта.
MATERIAL_FORMATS = frozenset({"docx", "xlsx", "csv", "pdf", "markdown", "text", "image"})

OOXML_MARKERS: dict[str, str] = {
    "pptx": "ppt/presentation.xml",
    "docx": "word/document.xml",
    "xlsx": "xl/workbook.xml",
}


class UploadError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True)
class StoredFile:
    sha256: str
    size_bytes: int
    path: pathlib.Path
    format: str
    check: dict[str, str]


def format_for(name: str) -> str:
    return FORMAT_BY_EXT.get(pathlib.Path(name).suffix.lower(), "other")


def default_kind(name: str) -> str:
    """Вид файла по расширению: pptx уточняется пользователем, поэтому по умолчанию other."""
    fmt = format_for(name)
    return "material" if fmt in MATERIAL_FORMATS else "other"


def safe_name(name: str) -> str:
    """Имя без путей и управляющих символов; пустое имя заменяется."""
    base = pathlib.PurePosixPath(name.replace("\\", "/")).name
    cleaned = "".join(ch for ch in base if ch.isprintable()).strip()
    return cleaned[:255] or "file"


class FileStore:
    def __init__(self, root: pathlib.Path, *, max_upload_mb: int, max_unzipped_mb: int) -> None:
        self.root = root
        self.tmp = root / "tmp"
        self.max_bytes = max_upload_mb * 1024 * 1024
        self.max_unzipped = max_unzipped_mb * 1024 * 1024
        self.root.mkdir(parents=True, exist_ok=True)
        self.tmp.mkdir(parents=True, exist_ok=True)

    def path_for(self, sha256: str) -> pathlib.Path:
        return self.root / sha256[:2] / sha256

    def exists(self, sha256: str) -> bool:
        return self.path_for(sha256).is_file()

    def open(self, sha256: str) -> pathlib.Path:
        path = self.path_for(sha256)
        if not path.is_file():
            raise FileNotFoundError(sha256)
        return path

    def store(self, stream: BinaryIO | Iterable[bytes], name: str) -> StoredFile:
        """Читает поток во временный файл, проверяет и переносит в хранилище."""
        digest = hashlib.sha256()
        size = 0
        fd, tmp_name = tempfile.mkstemp(prefix="upload-", dir=self.tmp)
        tmp_path = pathlib.Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as out:
                for chunk in _chunks(stream):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise UploadError(
                            "file_too_large",
                            f"Файл больше {self.max_bytes // (1024 * 1024)} МБ",
                            413,
                        )
                    digest.update(chunk)
                    out.write(chunk)
            if size == 0:
                raise UploadError("file_empty", "Пустой файл")
            fmt = format_for(name)
            check = self.check(tmp_path, fmt)
            if check["status"] == "rejected":
                raise UploadError(
                    "file_rejected", check.get("message", "Файл не прошёл проверку"), 422
                )
            sha = digest.hexdigest()
            final = self.path_for(sha)
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists():
                tmp_path.unlink(missing_ok=True)
            else:
                os.replace(tmp_path, final)
            return StoredFile(sha256=sha, size_bytes=size, path=final, format=fmt, check=check)
        finally:
            tmp_path.unlink(missing_ok=True)

    def store_bytes(self, data: bytes, name: str) -> StoredFile:
        return self.store(iter([data]), name)

    def check(self, path: pathlib.Path, fmt: str) -> dict[str, str]:
        """Проверка содержимого по формату. Возвращает {status, format, message?}."""
        try:
            if fmt in OOXML_MARKERS:
                self._check_ooxml(path, fmt)
            elif fmt == "pdf":
                with path.open("rb") as f:
                    if f.read(5) != b"%PDF-":
                        raise UploadError("not_pdf", "Файл не является PDF")
            elif fmt == "image":
                from PIL import Image

                with Image.open(path) as img:
                    img.verify()
            elif fmt in {"text", "markdown", "csv"}:
                with path.open("rb") as f:
                    head = f.read(1024 * 1024)
                try:
                    head.decode("utf-8")
                except UnicodeDecodeError:
                    head.decode("cp1251")
            else:
                return {"status": "skipped", "format": "other"}
        except UploadError as e:
            return {"status": "rejected", "format": fmt, "message": str(e)}
        except Exception as e:
            return {
                "status": "rejected",
                "format": fmt,
                "message": f"Файл не читается как {fmt}: {e}",
            }
        return {"status": "ok", "format": fmt}

    def _check_ooxml(self, path: pathlib.Path, fmt: str) -> None:
        if not zipfile.is_zipfile(path):
            raise UploadError("not_zip", f"Файл {fmt} должен быть ZIP-пакетом")
        with zipfile.ZipFile(path) as zf:
            total = 0
            names = zf.namelist()
            for info in zf.infolist():
                member = info.filename
                if (
                    member.startswith("/")
                    or ".." in pathlib.PurePosixPath(member).parts
                    or "\\" in member
                ):
                    raise UploadError(
                        "zip_unsafe_path", f"Недопустимый путь внутри архива: {member}"
                    )
                total += info.file_size
                if total > self.max_unzipped:
                    raise UploadError(
                        "zip_too_big",
                        f"Распакованный объём больше {self.max_unzipped // (1024 * 1024)} МБ",
                    )
            if "[Content_Types].xml" not in names:
                raise UploadError("not_ooxml", "В архиве нет [Content_Types].xml")
            if OOXML_MARKERS[fmt] not in names:
                raise UploadError(
                    "wrong_ooxml", f"Архив не содержит {OOXML_MARKERS[fmt]}: это не {fmt}"
                )
            bad = zf.testzip()
            if bad is not None:
                raise UploadError("zip_corrupt", f"Повреждён элемент архива: {bad}")

    def remove(self, sha256: str) -> None:
        self.path_for(sha256).unlink(missing_ok=True)

    def cleanup_tmp(self) -> int:
        """Удаляет обрывки неудачных загрузок; возвращает число удалённых файлов."""
        count = 0
        for item in self.tmp.iterdir():
            if item.is_file():
                item.unlink(missing_ok=True)
                count += 1
        return count

    def copy_to(self, sha256: str, target: pathlib.Path) -> pathlib.Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.open(sha256), target)
        return target


def _chunks(stream: BinaryIO | Iterable[bytes], size: int = 1024 * 1024) -> Iterable[bytes]:
    if isinstance(stream, io.IOBase) or hasattr(stream, "read"):
        reader = cast(BinaryIO, stream)
        while True:
            chunk = reader.read(size)
            if not chunk:
                return
            yield chunk
    else:
        yield from stream
