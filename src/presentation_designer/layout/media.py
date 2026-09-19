"""Медиа собранной колоды рядом с ревизией: файлы `media/<asset>.<ext>` для интерфейса.

Ресурсы ComposedDeck (`assets[].media_path`) лежат внутри PPTX; холст редактора и
предпросмотр в браузере не умеют читать zip, поэтому байты выкладываются в каталог ревизии
и попадают в манифест артефактов задания как обычные файлы. Имя артефакта записывается в
`assets[].artifact`; ресурсы без файла в пакете пропускаются.
"""

from __future__ import annotations

import logging
import pathlib
import re
import zipfile
from typing import Any

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]
MEDIA_DIR = "media"
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def media_file_name(asset_id: str, media_path: str) -> str:
    """Имя файла ресурса в каталоге ревизии: идентификатор без двоеточий и путей,
    расширение — от медиа-части."""
    suffix = pathlib.Path(media_path).suffix.lower() or ".bin"
    stem = _SAFE.sub("_", asset_id).strip("_") or "asset"
    return f"{stem}{suffix}"


def export_media(
    pptx_path: pathlib.Path, deck: JsonDict, media_dir: pathlib.Path, prefix: str
) -> int:
    """Выкладывает ресурсы колоды из PPTX в `media_dir`, дописывает `artifact` в описание.
    Возвращает число выложенных файлов; ошибка чтения пакета — предупреждение, не отказ."""
    assets = [a for a in deck.get("assets") or [] if a.get("asset_id") and a.get("media_path")]
    if not assets:
        return 0
    written = 0
    try:
        with zipfile.ZipFile(pptx_path) as zf:
            names = set(zf.namelist())
            for asset in assets:
                path = str(asset["media_path"])
                name = path if path in names else f"ppt/media/{pathlib.Path(path).name}"
                if name not in names:
                    continue
                file_name = media_file_name(str(asset["asset_id"]), name)
                target = media_dir / file_name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(name))
                asset["artifact"] = f"{prefix}{MEDIA_DIR}/{file_name}"
                written += 1
    except (OSError, zipfile.BadZipFile):
        log.warning("медиа ревизии не выложены из %s", pptx_path, exc_info=True)
    return written


__all__ = ["MEDIA_DIR", "export_media", "media_file_name"]
