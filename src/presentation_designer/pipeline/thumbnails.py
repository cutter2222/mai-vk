"""Миниатюры файлов проекта для сетки «Файлы»: картинка, первая страница PDF, обложка PPTX.

Миниатюра строится один раз и лежит рядом с хранилищем в `uploads/.thumbs/<aa>/<sha256>.webp`:
те же байты в другом проекте берут готовую. Каталог с точкой не виден сборке мусора
и резервной копии как файл хранилища, а удаление байтов (`FileStore.remove`) убирает и его
миниатюру.
"""

from __future__ import annotations

import io
import os
import pathlib
import tempfile
import zipfile
from collections.abc import Callable

from PIL import Image, ImageOps

from presentation_designer.pipeline.files import FileStore

# Длинная сторона: карточка сетки около 200 px на экране с двойной плотностью.
THUMB_PX = 480


def thumbnail(
    files: FileStore, sha256: str, fmt: str, fallback: pathlib.Path | None = None
) -> pathlib.Path | None:
    """Путь к миниатюре файла хранилища или None, если показать нечего.

    `fallback` — готовая картинка на случай, когда в самом файле обложки нет (PPTX без
    `docProps/thumbnail.jpeg`, но с разобранным шаблоном)."""
    target = files.thumbnail_path(sha256)
    if target.is_file():
        return target
    return render(files.open(sha256), fmt, target, fallback)


def render(
    source: pathlib.Path, fmt: str, target: pathlib.Path, fallback: pathlib.Path | None = None
) -> pathlib.Path | None:
    """Миниатюра `source` в `target` (WebP), если её там ещё нет."""
    if target.is_file():
        return target
    image = _source_image(source, fmt)
    if image is None and fallback is not None and fallback.is_file():
        image = _loaded(lambda: Image.open(fallback))
    if image is None:
        return None
    with image:
        thumb = ImageOps.exif_transpose(image)
        thumb.thumbnail((THUMB_PX, THUMB_PX))
        if thumb.mode not in {"RGB", "RGBA"}:
            thumb = thumb.convert("RGBA" if thumb.has_transparency_data else "RGB")
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="thumb-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as out:
                thumb.save(out, format="WEBP", quality=82)
            os.replace(tmp, target)
        finally:
            pathlib.Path(tmp).unlink(missing_ok=True)
    return target


def _source_image(path: pathlib.Path, fmt: str) -> Image.Image | None:
    if fmt == "image":
        return _loaded(lambda: _draft(Image.open(path)))
    if fmt == "pdf":
        return _loaded(lambda: _pdf_page(path))
    if fmt == "pptx":
        return _loaded(lambda: _office_cover(path))
    return None


def _loaded(open_image: Callable[[], Image.Image | None]) -> Image.Image | None:
    """Картинка, уже прочитанная целиком: битый файл или формат без декодера (WMF-обложка
    старого Office) дают None, а не ошибку посреди ответа."""
    try:
        image = open_image()
        if image is not None:
            image.load()
        return image
    except (
        OSError,
        ValueError,
        RuntimeError,
        SyntaxError,
        zipfile.BadZipFile,
        Image.DecompressionBombError,
    ):
        return None


def _draft(image: Image.Image) -> Image.Image:
    # JPEG декодируется сразу в уменьшенном масштабе: большая фотография не разворачивается
    # в память целиком ради миниатюры.
    image.draft("RGB", (THUMB_PX, THUMB_PX))
    return image


def _pdf_page(path: pathlib.Path) -> Image.Image | None:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    try:
        if len(pdf) == 0:
            return None
        page = pdf[0]
        width, height = page.get_size()
        scale = THUMB_PX / max(width, height, 1)
        image: Image.Image = page.render(scale=scale).to_pil()
        page.close()
        return image
    finally:
        pdf.close()


def _office_cover(path: pathlib.Path) -> Image.Image | None:
    """Обложка, которую PowerPoint кладёт в пакет при сохранении."""
    with zipfile.ZipFile(path) as zf:
        name = next((n for n in zf.namelist() if n.lower().startswith("docprops/thumbnail.")), None)
        if name is None:
            return None
        return Image.open(io.BytesIO(zf.read(name)))
