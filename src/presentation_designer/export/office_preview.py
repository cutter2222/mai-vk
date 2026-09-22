"""Content-addressed PDF + first slide, remaining PNGs rendered on demand."""

from __future__ import annotations

import fcntl
import hashlib
import json
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from PIL import Image

from presentation_designer.export.pdf import ConversionError, convert_to_pdf
from presentation_designer.export.thumbnails import pdf_page_count, render_thumbnails
from presentation_designer.parsing.template.embedded_fonts import prepare_fonts
from presentation_designer.shared.settings import Settings


def cache_path(pptx: bytes, settings: Settings) -> Path:
    # Bump the prefix when renderer/font configuration changes.
    digest = hashlib.sha256(pptx).hexdigest()
    return settings.paths.data_dir / "office-previews" / f"v2-{digest}"


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    with path.open("a") as lock:
        deadline = time.monotonic() + 100
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ConversionError("Превью ещё формируется; повторите запрос") from None
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def preview_revision(pptx: bytes, settings: Settings) -> tuple[Path, dict[str, Any]]:
    target = cache_path(pptx, settings)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Cross-process single flight. Publish a complete PDF and the first PNG together.
    with _locked(target.with_suffix(".lock")):
        if not target.is_dir():
            with tempfile.TemporaryDirectory(prefix=".preview-", dir=target.parent) as tmp:
                root = Path(tmp)
                source = root / "deck.pptx"
                source.write_bytes(pptx)
                prepare_fonts(source, settings.paths.data_dir)
                pdf = convert_to_pdf(source, root / "pdf", settings=settings).pdf_path
                output = root / "slides"
                total = pdf_page_count(pdf)
                if not total:
                    raise ConversionError("В презентации нет слайдов")
                slides = render_thumbnails(pdf, output, width_px=1600, pages=[1])
                with Image.open(slides.paths[0]) as image:
                    ratio = image.width / image.height
                digits = max(2, len(str(total)))
                manifest = {
                    "slides": [f"slide-{n:0{digits}d}.png" for n in range(1, total + 1)],
                    "ratio": ratio,
                }
                pdf.replace(output / "deck.pdf")
                (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                output.rename(target)
        return target, json.loads((target / "manifest.json").read_text(encoding="utf-8"))


def preview_page(root: Path, name: str) -> Path:
    """Render one allowlisted page; duplicate requests share an atomic cached PNG.

    PDFium's process-wide lock is retained. Requests are independent, not unsafe
    parallel calls into PDFium. No conversion or editor session is needed here.
    """
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    slides = manifest["slides"]
    if Path(name).name != name or name not in slides:
        raise FileNotFoundError(name)
    target = root / name
    with _locked(root / f".{name}.lock"):
        if not target.is_file():
            number = slides.index(name) + 1
            with tempfile.TemporaryDirectory(prefix=".page-", dir=root) as tmp:
                result = render_thumbnails(
                    root / "deck.pdf", Path(tmp), width_px=1600, pages=[number]
                )
                result.paths[0].replace(target)
    return target
