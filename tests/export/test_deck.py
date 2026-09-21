"""Экспорт ревизии: подменная конвертация ONLYOFFICE, миниатюры, автономный HTML с текстом
ComposedDeck, подключение к RealLayers (манифест ревизии, ошибки рендерера), исправление
на настоящих файлах базовой ревизии."""

from __future__ import annotations

import io
import json
import pathlib
import zipfile
from typing import Any

import pytest
from PIL import Image

from presentation_designer.export import deck as deck_export
from presentation_designer.pipeline.artifacts import ArtifactStore
from presentation_designer.pipeline.real import RealLayers
from presentation_designer.pipeline.run import ExportInput, RepairInput, StageError
from presentation_designer.shared.settings import Settings

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "pptx" / "mini_template.pptx"
EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "contracts" / "examples"


def _pdf_bytes(pages: int = 3) -> bytes:
    images = [Image.new("RGB", (1600, 900), c) for c in ("#ff0000", "#00ff00", "#0000ff")][:pages]
    buf = io.BytesIO()
    images[0].save(buf, format="PDF", save_all=True, append_images=images[1:], resolution=100)
    return buf.getvalue()


@pytest.fixture
def fake_converter(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Export orchestration uses a PDF fixture; HTTP is covered in test_pdf."""
    from presentation_designer.export.pdf import PdfResult

    def convert(source: pathlib.Path, out: pathlib.Path, **kwargs: Any) -> PdfResult:
        out.mkdir(parents=True, exist_ok=True)
        target = out / f"{source.stem}.pdf"
        target.write_bytes(_pdf_bytes())
        return PdfResult(target, 0.01)

    monkeypatch.setattr(deck_export, "_convert_to_pdf", convert)
    return tmp_path


def _composed_deck() -> dict[str, Any]:
    deck = json.loads((EXAMPLES / "composed_deck.example.json").read_text())
    deck["slides"] = deck["slides"][:3]
    return deck


def test_export_revision_writes_pdf_thumbnails_and_html(
    fake_converter: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    settings = Settings()
    settings.render.thumbnail_width_px = 640
    out = tmp_path / "r1"
    out.mkdir()
    pptx = out / "deck.pptx"
    pptx.write_bytes(FIXTURE.read_bytes())
    deck = _composed_deck()
    result = deck_export.export_revision(
        pptx,
        out,
        prefix="balanced/r1/",
        composed_deck=deck,
        deck_title="Проверка экспорта",
        settings=settings,
        slide_titles=["Первый", "Второй", "Третий"],
    )
    assert result.pdf_path == out / "deck.pdf" and result.pdf_path.read_bytes()[:4] == b"%PDF"
    assert [t["name"] for t in result.thumbnails] == [
        "balanced/r1/thumbs/slide-01.png",
        "balanced/r1/thumbs/slide-02.png",
        "balanced/r1/thumbs/slide-03.png",
    ]
    assert result.thumbnails[0]["slide_index"] == 0
    assert (result.thumbnails[0]["width_px"], result.thumbnails[0]["height_px"]) == (640, 360)
    assert (out / "thumbs" / "slide-03.png").is_file()
    html = result.html_path.read_text(encoding="utf-8")
    assert html.startswith("<!doctype html>")
    assert "Проверка экспорта" in html
    # Слайд собран объектами, а не выгружен картинкой (ТЗ п.2.7): у каждого
    # объекта своя рамка в процентах холста, и текст на странице выделяется.
    assert result.report["html"] == "native_objects"
    assert html.count('<section class="slide"') == len(deck["slides"])
    assert 'class="o" style="left:' in html
    first_text = next(
        p["text"]
        for o in deck["slides"][0]["objects"]
        if o.get("text")
        for p in o["text"].get("paragraphs", [])
    )
    assert first_text.strip() in html
    assert "http://" not in html and "https://" not in html.replace("http://schemas", "")
    assert result.report["pages"] == 3
    assert set(result.report["timings_ms"]) >= {"pdf", "thumbnails", "html", "total"}


def test_slide_texts_reads_paragraphs_and_tables() -> None:
    deck = {
        "slides": [
            {
                "objects": [
                    {"z_order": 2, "text": {"plain": "Второй", "paragraphs": [{"text": "Второй"}]}},
                    {"z_order": 1, "text": {"plain": "Первый", "paragraphs": [{"text": "Первый"}]}},
                    {"z_order": 3, "table": {"rows": 4, "cols": 3, "dataset_id": "ds_1"}},
                ]
            }
        ]
    }
    assert deck_export.slide_texts(deck) == [["Первый", "Второй", "Таблица 4×3 из набора ds_1"]]
    assert deck_export.slide_texts(None) == []


def test_real_layers_export_and_repair(
    fake_converter: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    settings = Settings()
    settings.paths.artifacts_dir = tmp_path / "artifacts"
    settings.render.thumbnail_width_px = 320
    layers = RealLayers(settings)
    assert layers.modes["export"] == "real"
    store = ArtifactStore(settings.artifacts_dir)
    titles = ["Первый", "Второй", "Третий"]
    with store.stage_revision("job_e", "compact", 1) as staging:
        staging.write_bytes("deck.pptx", FIXTURE.read_bytes())
        out = layers.export(
            ExportInput(
                "job_e", "compact", 1, staging, titles, "Колода", composed_deck=_composed_deck()
            )
        )
    manifest = store.read_manifest("job_e", "compact", 1)
    assert {"compact/r1/deck.pptx", "compact/r1/deck.pdf", "compact/r1/deck.html"} <= set(manifest)
    assert {t["name"] for t in out.thumbnails} <= set(manifest)
    assert out.thumbnails[1]["width_px"] == 320 and layers.last_export_report["pages"] == 3
    base_dir = store.revision_dir("job_e", "compact", 1)
    real_png = (base_dir / "thumbs" / "slide-01.png").read_bytes()

    # Исправление (заглушка отчёта) переносит настоящие миниатюры, PDF и HTML базовой ревизии.
    report = json.loads((EXAMPLES / "audit_report.example.json").read_text())
    issue = report["issues"][0]["issue_id"]
    with store.stage_revision("job_e", "compact", 2) as staging2:
        fixed = layers.repair(
            RepairInput(
                "job_e", "compact", 2, 1, report, base_dir, [issue], staging2, titles, "Колода"
            )
        )
    rev2 = store.revision_dir("job_e", "compact", 2)
    assert (rev2 / "thumbs" / "slide-01.png").read_bytes() == real_png
    assert (rev2 / "deck.pdf").read_bytes() == (base_dir / "deck.pdf").read_bytes()
    assert fixed.thumbnails[0] == {**fixed.thumbnails[0], "width_px": 320, "height_px": 180}
    assert all(i["issue_id"] != issue for i in fixed.report["issues"])


def test_without_renderer_export_fails_explicitly(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real mode never silently falls back to a placeholder PDF."""
    monkeypatch.setenv("PD_QUEUE_MODE", "inline")
    settings = Settings()
    settings.paths.artifacts_dir = tmp_path / "artifacts"
    layers = RealLayers(settings)
    assert layers.modes["export"] == "real"
    store = ArtifactStore(settings.artifacts_dir)
    with pytest.raises(StageError) as error, store.stage_revision("job_n", "compact", 1) as staging:
        staging.write_bytes("deck.pptx", FIXTURE.read_bytes())
        layers.export(ExportInput("job_n", "compact", 1, staging, ["Один"], "Колода"))
    assert error.value.code == "export_renderer_unavailable"
    monkeypatch.setenv("PD_QUEUE_MODE", "rq")
    layers = RealLayers(settings)
    assert layers.modes["export"] == "real"
    with pytest.raises(StageError) as e, store.stage_revision("job_n", "compact", 2) as staging:
        staging.write_bytes("deck.pptx", FIXTURE.read_bytes())
        layers.export(ExportInput("job_n", "compact", 2, staging, ["Один"], "Колода"))
    assert e.value.code == "export_renderer_unavailable" and e.value.retryable


def test_native_html_keeps_geometry_fonts_and_pictures(tmp_path: pathlib.Path) -> None:
    """Страница строится из описания слайда: рамки, кегли, картинки из .pptx."""
    from presentation_designer.export.html import build_html as native

    deck = {
        "slide_size": {"width_emu": 9144000, "height_emu": 5143500},
        "assets": [{"asset_id": "a1", "media_path": "ppt/media/image1.png", "sha256": "x"}],
        "slides": [
            {
                "slide_id": "s1",
                "index": 0,
                "layout_id": "l1",
                "objects": [
                    {
                        "object_id": "1",
                        "kind": "text",
                        "z_order": 2,
                        "bbox": {"x": 0.1, "y": 0.2, "width": 0.5, "height": 0.1},
                        "text": {
                            "plain": "Простой снижен на 34%",
                            "paragraphs": [{"text": "Простой снижен на 34%", "align": "left"}],
                            "computed_style": {
                                "font": {"family": "Play", "size_pt": 24.0, "color": "#101010"}
                            },
                        },
                    },
                    {
                        "object_id": "2",
                        "kind": "picture",
                        "z_order": 1,
                        "bbox": {"x": 0.6, "y": 0.1, "width": 0.3, "height": 0.4},
                        "picture": {"asset_id": "a1", "fit": "cover"},
                    },
                ],
            }
        ],
    }
    pptx = tmp_path / "deck.pptx"
    with zipfile.ZipFile(pptx, "w") as zf:
        zf.writestr("ppt/media/image1.png", _png_bytes())

    page = native("Колода", deck, pptx)

    assert "left:10.000%" in page and "width:50.000%" in page
    # Кегль — в долях ширины слайда: 24pt при холсте 720pt даёт 3.333cqw.
    assert "font-size:3.333cqw" in page
    assert "Простой снижен на 34%" in page
    assert "data:image/png;base64," in page  # картинка взята из самого .pptx
    assert "<img" in page and page.count("<section") == 1


def test_native_html_draws_card_outlines_and_rounding(tmp_path: pathlib.Path) -> None:
    """Карточка шаблона — пустая рамка с контуром и скруглением: без них страница теряет
    сетку, а слайд выглядит не так, как в PowerPoint.

    Радиус считается от меньшей стороны фигуры, как в PPTX, поэтому широкая плашка остаётся
    плашкой: в процентах CSS она превратилась бы в овал.
    """
    from presentation_designer.export.html import build_html as native

    deck = {
        "slide_size": {"width_emu": 9144000, "height_emu": 5143500},
        "slides": [
            {
                "slide_id": "s1",
                "index": 0,
                "layout_id": "l1",
                "objects": [
                    {
                        "object_id": "10",
                        "kind": "shape",
                        "z_order": 1,
                        "bbox": {"x": 0.1, "y": 0.2, "width": 0.25, "height": 0.4},
                        "geometry": "roundRect",
                        "geometry_adjust": 0.09821,
                        "fill": {"kind": "none"},
                        "line": {"color": "#E4002B", "width_pt": 1.5},
                    },
                    {
                        "object_id": "11",
                        "kind": "shape",
                        "z_order": 2,
                        "bbox": {"x": 0.1, "y": 0.05, "width": 0.8, "height": 0.09},
                        "geometry": "roundRect",
                        "geometry_adjust": 0.24229,
                        "fill": {"kind": "solid", "color": "#520977"},
                    },
                ],
            }
        ],
    }
    pptx = tmp_path / "deck.pptx"
    with zipfile.ZipFile(pptx, "w") as zf:
        zf.writestr("ppt/media/none.txt", b"x")

    page = native("Колода", deck, pptx)

    assert "box-shadow:inset 0 0 0 1.500pt #E4002B" in page, "контур карточки нарисован"
    # Меньшая сторона карточки — её высота: 0,4 холста × 0,5625 = 0,225 ширины;
    # 0,09821 × 22,5 % ≈ 2,210cqw.
    assert "border-radius:2.210cqw" in page
    # У широкой плашки меньшая сторона тоже высота: 0,09 × 0,5625 = 0,0506 ширины.
    assert "border-radius:1.227cqw" in page
    assert page.count('<div class="o"') == 2, "пустые рамки не выброшены"


def test_native_html_respects_text_anchor(tmp_path: pathlib.Path) -> None:
    """Подпись, выровненная в PowerPoint по центру рамки, стоит по центру и на странице."""
    from presentation_designer.export.html import build_html as native

    deck = {
        "slide_size": {"width_emu": 9144000, "height_emu": 5143500},
        "slides": [
            {
                "slide_id": "s1",
                "index": 0,
                "layout_id": "l1",
                "objects": [
                    {
                        "object_id": "1",
                        "kind": "text",
                        "z_order": 1,
                        "bbox": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.3},
                        "text": {"plain": "Подпись", "anchor": "middle"},
                    }
                ],
            }
        ],
    }
    pptx = tmp_path / "deck.pptx"
    with zipfile.ZipFile(pptx, "w") as zf:
        zf.writestr("ppt/media/none.txt", b"x")

    page = native("Колода", deck, pptx)
    assert "justify-content:center" in page and "flex-direction:column" in page


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "#0077ff").save(buf, format="PNG")
    return buf.getvalue()


def test_export_reuses_prerendered_pdf_and_thumbnails(
    fake_converter: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Предварительная ревизия исходной презентации уже отрендерена: полная сборка берёт
    её deck.pdf и миниатюры, ONLYOFFICE не вызывается, HTML строится заново нативно."""
    settings = Settings()
    settings.render.thumbnail_width_px = 640
    preview = tmp_path / "preview"
    preview.mkdir()
    (preview / "deck.pptx").write_bytes(FIXTURE.read_bytes())
    first = deck_export.export_revision(
        preview / "deck.pptx",
        preview,
        prefix="original/r1/",
        composed_deck=None,
        deck_title="Исходная",
        settings=settings,
        slide_titles=["А", "Б", "В"],
    )
    assert first.report["html"] == "images_with_text"

    out = tmp_path / "r1"
    out.mkdir()
    (out / "deck.pptx").write_bytes(FIXTURE.read_bytes())

    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("cached render must not call the converter")

    monkeypatch.setattr(deck_export, "_convert_to_pdf", unexpected)
    second = deck_export.export_revision(
        out / "deck.pptx",
        out,
        prefix="original/r1/",
        composed_deck=_composed_deck(),
        deck_title="Исходная",
        settings=settings,
        slide_titles=["А", "Б", "В"],
        prerendered=preview,
    )
    assert second.report["renderer"] == "prerendered" and second.report["timings_ms"]["pdf"] == 0
    assert second.pdf_path.read_bytes() == first.pdf_path.read_bytes()
    assert [t["name"] for t in second.thumbnails] == [t["name"] for t in first.thumbnails]
    assert (out / "thumbs" / "slide-02.png").read_bytes() == (
        preview / "thumbs" / "slide-02.png"
    ).read_bytes()
    assert second.report["html"] == "native_objects"


def test_export_rerenders_when_prerender_has_other_slide_count(
    fake_converter: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """Слайды без композиции не перенесены: страниц в предварительном рендере больше, чем
    слайдов в собранном файле, и рендер повторяется. Иначе лента и рамки аудита показывали бы
    не те слайды."""
    settings = Settings()
    settings.render.thumbnail_width_px = 640
    preview = tmp_path / "preview"
    preview.mkdir()
    (preview / "deck.pptx").write_bytes(FIXTURE.read_bytes())
    first = deck_export.export_revision(
        preview / "deck.pptx",
        preview,
        prefix="original/r1/",
        composed_deck=None,
        deck_title="Исходная",
        settings=settings,
    )
    assert len(first.thumbnails) == 3

    deck = _composed_deck()
    deck["slides"] = deck["slides"][:2]
    out = tmp_path / "r1"
    out.mkdir()
    (out / "deck.pptx").write_bytes(FIXTURE.read_bytes())
    second = deck_export.export_revision(
        out / "deck.pptx",
        out,
        prefix="original/r1/",
        composed_deck=deck,
        deck_title="Исходная",
        settings=settings,
        prerendered=preview,
    )
    assert second.report["renderer"] != "prerendered"
