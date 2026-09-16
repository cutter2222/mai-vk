"""Миниатюры из PDF: размеры, выбор страниц, закрытие документа."""

from __future__ import annotations

import io
import pathlib

import pytest
from PIL import Image

from presentation_designer.export import thumbnails


@pytest.fixture
def pdf_file(tmp_path: pathlib.Path) -> pathlib.Path:
    pages = [Image.new("RGB", (1600, 900), color) for color in ("#ff0000", "#00ff00", "#0000ff")]
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:], resolution=100)
    path = tmp_path / "deck.pdf"
    path.write_bytes(buf.getvalue())
    return path


def test_page_count(pdf_file: pathlib.Path) -> None:
    assert thumbnails.pdf_page_count(pdf_file) == 3


def test_render_all_pages_with_fixed_width(pdf_file: pathlib.Path, tmp_path: pathlib.Path) -> None:
    result = thumbnails.render_thumbnails(pdf_file, tmp_path / "thumbs", width_px=640)
    assert [p.name for p in result.paths] == ["slide-01.png", "slide-02.png", "slide-03.png"]
    with Image.open(result.paths[2]) as image:
        assert image.size == (640, 360)
        r, g, b = image.convert("RGB").getpixel((10, 10))
        assert (r, g) == (0, 0) and b >= 250  # JPEG внутри PDF от Pillow


def test_render_selected_pages(pdf_file: pathlib.Path, tmp_path: pathlib.Path) -> None:
    result = thumbnails.render_thumbnails(pdf_file, tmp_path, width_px=320, pages=[2], prefix="p")
    assert [p.name for p in result.paths] == ["p-02.png"]
    with pytest.raises(ValueError, match="страницы 9 нет"):
        thumbnails.render_thumbnails(pdf_file, tmp_path, pages=[9])


def test_lock_released_after_render(pdf_file: pathlib.Path, tmp_path: pathlib.Path) -> None:
    thumbnails.render_thumbnails(pdf_file, tmp_path, width_px=100)
    assert not thumbnails.PDFIUM_LOCK.locked()
