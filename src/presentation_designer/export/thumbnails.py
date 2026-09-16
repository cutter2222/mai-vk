"""Миниатюры страниц PDF через pypdfium2.

PDFium не допускает одновременных вызовов из разных потоков даже для разных документов,
поэтому все обращения к нему в процессе идут под одной блокировкой `PDFIUM_LOCK`.
Параллельный рендер — только отдельными процессами (например, воркерами очереди).
Документ закрывается сразу после рендера, страницы и растровые буферы — после каждой страницы.
"""

from __future__ import annotations

import pathlib
import threading
import time
from dataclasses import dataclass

PDFIUM_LOCK = threading.Lock()


@dataclass(frozen=True)
class ThumbnailResult:
    paths: list[pathlib.Path]
    seconds: float
    width_px: int


def pdf_page_count(pdf_path: pathlib.Path) -> int:
    import pypdfium2 as pdfium

    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            return len(doc)
        finally:
            doc.close()


def render_thumbnails(
    pdf_path: pathlib.Path,
    out_dir: pathlib.Path,
    width_px: int = 1280,
    pages: list[int] | None = None,
    prefix: str = "slide",
    image_format: str = "png",
) -> ThumbnailResult:
    """Рендерит страницы `pages` (номера с 1; по умолчанию все) в `out_dir/<prefix>-NN.<fmt>`.

    Ширина картинки фиксирована, высота следует пропорции страницы; масштаб
    считается от ширины страницы в пунктах (72 dpi).
    """
    import pypdfium2 as pdfium

    pdf_path = pathlib.Path(pdf_path)
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    paths: list[pathlib.Path] = []
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(str(pdf_path))
        try:
            total = len(doc)
            numbers = pages or list(range(1, total + 1))
            digits = max(2, len(str(total)))
            for number in numbers:
                if not 1 <= number <= total:
                    raise ValueError(f"страницы {number} нет в {pdf_path.name}: всего {total}")
                page = doc[number - 1]
                try:
                    scale = width_px / page.get_width()
                    bitmap = page.render(scale=scale)
                    try:
                        image = bitmap.to_pil()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                target = out_dir / f"{prefix}-{number:0{digits}d}.{image_format}"
                image.save(target)
                image.close()
                paths.append(target)
        finally:
            doc.close()
    return ThumbnailResult(paths, time.perf_counter() - started, width_px)
