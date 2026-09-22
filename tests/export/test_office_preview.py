from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from PIL import Image

from presentation_designer.export import office_preview
from presentation_designer.export.pdf import ConversionError


def test_atomic_preview_cache_and_new_content(tmp_path, monkeypatch):
    settings = SimpleNamespace(paths=SimpleNamespace(data_dir=tmp_path))
    converted = []
    monkeypatch.setattr(office_preview, "prepare_fonts", lambda *args: None)
    monkeypatch.setattr(office_preview, "pdf_page_count", lambda _: 1)

    def convert(source, output, **kwargs):
        converted.append(source.read_bytes())
        return SimpleNamespace(pdf_path=source)

    def render(pdf, output, **kwargs):
        assert not office_preview.cache_path(pdf.read_bytes(), settings).exists()
        output.mkdir()
        image = output / "slide-01.png"
        Image.new("RGB", (160, 90)).save(image)
        return SimpleNamespace(paths=[image])

    monkeypatch.setattr(office_preview, "convert_to_pdf", convert)
    monkeypatch.setattr(office_preview, "render_thumbnails", render)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: office_preview.preview_revision(b"v0", settings), range(4))
        )
    assert all(result == results[0] for result in results)
    root, manifest = results[0]
    assert manifest == {"slides": ["slide-01.png"], "ratio": 160 / 90}
    assert (root / "slide-01.png").is_file()
    assert converted == [b"v0"]
    new_root, _ = office_preview.preview_revision(b"v1", settings)
    assert new_root != root
    assert converted == [b"v0", b"v1"]


def test_failed_preview_does_not_publish_partial_cache(tmp_path, monkeypatch):
    settings = SimpleNamespace(paths=SimpleNamespace(data_dir=tmp_path))
    monkeypatch.setattr(office_preview, "prepare_fonts", lambda *args: None)

    def fail(*args, **kwargs):
        raise ConversionError("unavailable")

    monkeypatch.setattr(office_preview, "convert_to_pdf", fail)
    with pytest.raises(ConversionError):
        office_preview.preview_revision(b"pptx", settings)
    assert not office_preview.cache_path(b"pptx", settings).exists()
    assert not list((tmp_path / "office-previews").glob(".preview-*"))


def test_progressive_pages_single_flight_and_order(tmp_path, monkeypatch):
    settings = SimpleNamespace(paths=SimpleNamespace(data_dir=tmp_path))
    pdf = tmp_path / "source.pdf"
    images = [Image.new("RGB", (160, 90), color) for color in ("red", "green", "blue")]
    images[0].save(pdf, format="PDF", save_all=True, append_images=images[1:])
    for image in images:
        image.close()
    monkeypatch.setattr(office_preview, "prepare_fonts", lambda *args: None)
    monkeypatch.setattr(
        office_preview, "convert_to_pdf", lambda *args, **kwargs: SimpleNamespace(pdf_path=pdf)
    )
    root, manifest = office_preview.preview_revision(b"pptx", settings)
    assert manifest["slides"] == ["slide-01.png", "slide-02.png", "slide-03.png"]
    assert (root / "deck.pdf").is_file()
    assert (root / "slide-01.png").is_file()
    assert not (root / "slide-02.png").exists()
    assert not (root / "slide-03.png").exists()

    render = office_preview.render_thumbnails
    calls = []

    def counted(*args, **kwargs):
        calls.append(kwargs["pages"])
        return render(*args, **kwargs)

    monkeypatch.setattr(office_preview, "render_thumbnails", counted)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda _: office_preview.preview_page(root, "slide-03.png"), range(4))
        )
    assert len(set(results)) == 1
    assert calls == [[3]]
    assert not (root / "slide-02.png").exists()
    with Image.open(results[0]) as image:
        assert image.size == (1600, 900)
        assert image.convert("RGB").getpixel((10, 10))[2] >= 250
    for invalid in ("../deck.pdf", "deck.pdf", "manifest.json", "slide-04.png"):
        with pytest.raises(FileNotFoundError):
            office_preview.preview_page(root, invalid)

    def fail(*args, **kwargs):
        (args[1] / "slide-02.png").write_bytes(b"partial")
        raise ConversionError("failed page")

    monkeypatch.setattr(office_preview, "render_thumbnails", fail)
    with pytest.raises(ConversionError):
        office_preview.preview_page(root, "slide-02.png")
    assert not (root / "slide-02.png").exists()
    assert not list(root.glob(".page-*"))
    assert (root / "slide-01.png").is_file()
    monkeypatch.setattr(office_preview, "render_thumbnails", render)
    assert office_preview.preview_page(root, "slide-02.png").is_file()
