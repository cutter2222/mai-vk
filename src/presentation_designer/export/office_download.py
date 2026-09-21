"""Exports of an immutable office revision, never the older generation artifacts."""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import Literal

from presentation_designer.export.pdf import convert_to_pdf
from presentation_designer.export.thumbnails import render_thumbnails
from presentation_designer.shared.settings import Settings


def export_revision(pptx: bytes, format: Literal["pdf", "html"], settings: Settings) -> bytes:
    with tempfile.TemporaryDirectory(prefix="office-export-") as tmp:
        root = Path(tmp)
        source = root / "deck.pptx"
        source.write_bytes(pptx)
        pdf = convert_to_pdf(source, root / "pdf", settings=settings).pdf_path
        if format == "pdf":
            return pdf.read_bytes()
        # Self-contained visual slides: preserve office edits, fonts and layout.
        # No scripts, external assets or editable DOM reconstructed from an older plan.
        slides = render_thumbnails(pdf, root / "slides", width_px=1600)
        images = "".join(
            f'<section><img alt="Слайд {i}" '
            f'src="data:image/png;base64,{base64.b64encode(path.read_bytes()).decode()}"'
            "></section>"
            for i, path in enumerate(slides.paths, 1)
        )
        return (
            '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>Презентация</title><style>"
            "body{margin:0;background:#eceef1}main{max-width:1600px;margin:auto}"
            "section{margin:24px 0;break-after:page}img{display:block;width:100%;height:auto}"
            "@media print{body{background:white}section{margin:0}}"
            "</style></head><body><main>" + images + "</main></body></html>"
        ).encode("utf-8")
