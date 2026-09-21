from types import SimpleNamespace

from presentation_designer.export import office_download


def test_exports_are_from_same_revision_and_html_is_self_contained(tmp_path, monkeypatch):
    source_bytes = b"saved-pptx-revision"
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"pdf-revision")
    image = tmp_path / "slide.png"
    image.write_bytes(b"png-revision")
    sources = []

    def convert(source, output, *, settings):
        assert source.read_bytes() == source_bytes
        sources.append(source)
        return SimpleNamespace(pdf_path=pdf)

    def render(source, output, *, width_px):
        assert source == pdf
        assert width_px == 1600
        return SimpleNamespace(paths=[image])

    monkeypatch.setattr(office_download, "convert_to_pdf", convert)
    monkeypatch.setattr(office_download, "render_thumbnails", render)
    assert office_download.export_revision(source_bytes, "pdf", None) == b"pdf-revision"
    html = office_download.export_revision(source_bytes, "html", None).decode()
    assert "data:image/png;base64,cG5nLXJldmlzaW9u" in html
    assert 'alt="Слайд 1"' in html
    assert "<script" not in html
    assert all(not path.exists() for path in sources)
