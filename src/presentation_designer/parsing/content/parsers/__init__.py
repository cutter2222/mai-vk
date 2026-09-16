"""Парсеры материалов по формату и кэш разбора одного файла.

`parse_file` выбирает парсер по формату из хранилища загрузок (`pipeline/files.format_for`),
`ParseCache` хранит результат по ключу sha256 + версия парсера + параметры разбора:
новый пакет после уточнения брифа или удаления одного материала переиспользует уже
разобранные файлы. Байты изображений лежат рядом с JSON описания.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import shutil
import tempfile
from collections.abc import Callable
from typing import Any

from presentation_designer.parsing.content.parsers import (
    docx,
    image,
    markdown,
    pdf,
    pptx,
    spreadsheet,
)
from presentation_designer.parsing.content.parsers.base import (
    ParsedDocument,
    ParserError,
)

log = logging.getLogger(__name__)

# Версии парсеров входят в ключ кэша и в import_meta.parsers пакета.
PARSER_VERSIONS: dict[str, str] = {
    "docx": docx.VERSION,
    "xlsx": spreadsheet.VERSION,
    "csv": spreadsheet.VERSION,
    "pdf": pdf.VERSION,
    "markdown": markdown.VERSION,
    "text": markdown.VERSION,
    "pptx": pptx.VERSION,
    "image": image.VERSION,
}

SUPPORTED_FORMATS = frozenset(PARSER_VERSIONS)


def parse_file(
    path: pathlib.Path,
    fmt: str,
    *,
    max_block_chars: int = 2000,
    min_image_px: int = 64,
    max_rows: int = 5000,
    name: str | None = None,
) -> ParsedDocument:
    """Разбор файла по формату; неизвестный формат — ParserError(unsupported_format).
    `name` — исходное имя файла: в хранилище загрузок путь равен sha256."""
    parsers: dict[str, Callable[[], ParsedDocument]] = {
        "docx": lambda: docx.parse(
            path, max_block_chars=max_block_chars, min_image_px=min_image_px
        ),
        "xlsx": lambda: spreadsheet.parse_xlsx(path, max_rows=max_rows),
        "csv": lambda: spreadsheet.parse_csv(path, max_rows=max_rows, name=name),
        "pdf": lambda: pdf.parse(path, max_block_chars=max_block_chars, min_image_px=min_image_px),
        "markdown": lambda: markdown.parse_markdown(path, max_block_chars=max_block_chars),
        "text": lambda: markdown.parse_text(path, max_block_chars=max_block_chars),
        "pptx": lambda: pptx.parse(
            path, max_block_chars=max_block_chars, min_image_px=min_image_px
        ),
        "image": lambda: image.parse(path, min_image_px=min_image_px, name=name),
    }
    if fmt not in parsers:
        raise ParserError("unsupported_format", f"формат {fmt!r} не импортируется")
    if not path.is_file():
        raise ParserError("file_missing", f"файл {path.name} отсутствует в хранилище")
    return parsers[fmt]()


def parse_key(sha256: str, fmt: str, params: dict[str, Any]) -> str:
    """Ключ кэша разбора: содержимое файла, формат, версия парсера и параметры."""
    material = {
        "sha256": sha256,
        "format": fmt,
        "parser": PARSER_VERSIONS.get(fmt),
        "params": params,
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


class ParseCache:
    """Каталог `<root>/<xx>/<ключ>/`: `doc.json` и `images/<n>.bin`; запись атомарна
    (временный каталог переименовывается). Повреждённая запись считается отсутствующей."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root

    def path(self, key: str) -> pathlib.Path:
        return self.root / key[:2] / key

    def get(self, key: str) -> ParsedDocument | None:
        base = self.path(key)
        doc_path = base / "doc.json"
        if not doc_path.is_file():
            return None
        try:
            data = json.loads(doc_path.read_text(encoding="utf-8"))
            blobs = [
                (base / "images" / f"{i}.bin").read_bytes()
                for i in range(len(data.get("images") or []))
            ]
            return ParsedDocument.from_dict(data, blobs)
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("запись кэша импорта %s повреждена, перечитываю файл", key[:12])
            return None

    def put(self, key: str, doc: ParsedDocument) -> None:
        base = self.path(key)
        base.parent.mkdir(parents=True, exist_ok=True)
        tmp = pathlib.Path(tempfile.mkdtemp(prefix=".tmp-", dir=base.parent))
        try:
            (tmp / "doc.json").write_text(
                json.dumps(doc.as_dict(), ensure_ascii=False), encoding="utf-8"
            )
            if doc.images:
                (tmp / "images").mkdir()
                for i, img in enumerate(doc.images):
                    (tmp / "images" / f"{i}.bin").write_bytes(img.data)
            if base.exists():
                shutil.rmtree(base)
            tmp.rename(base)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise


__all__ = [
    "PARSER_VERSIONS",
    "SUPPORTED_FORMATS",
    "ParseCache",
    "ParsedDocument",
    "ParserError",
    "parse_file",
    "parse_key",
]
