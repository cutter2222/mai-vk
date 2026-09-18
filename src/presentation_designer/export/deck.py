"""Экспорт ревизии варианта: PDF через LibreOffice под слотом рендера, миниатюры страниц
через PDFium, автономный HTML.

HTML здесь промежуточный: каждая страница — PNG-миниатюра, встроенная data-URI, под ней
текст слайда абзацами (выделяется и ищется). Детерминированный рендер из ComposedDeck
(текст CSS, таблицы HTML, фигуры SVG) и кэш рендера — этап 9; до него интерфейс и аудит
получают настоящие картинки собранного PPTX, а не рисунок заглушки.
"""

from __future__ import annotations

import base64
import html
import logging
import pathlib
import time
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.export.html import build_html as native_html
from presentation_designer.export.pdf import ConversionError, RendererUnavailableError
from presentation_designer.export.pdf import convert_to_pdf as _convert_to_pdf
from presentation_designer.export.pdf import find_soffice as _find_soffice
from presentation_designer.export.thumbnails import render_thumbnails
from presentation_designer.shared.settings import Settings

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]

__all__ = ["ConversionError", "ExportResult", "RendererUnavailableError", "export_revision"]

THUMBS_DIR = "thumbs"


@dataclass
class ExportResult:
    pdf_path: pathlib.Path
    html_path: pathlib.Path
    thumbnails: list[JsonDict]
    report: JsonDict = field(default_factory=dict)


def export_revision(
    pptx_path: pathlib.Path,
    out_dir: pathlib.Path,
    *,
    prefix: str,
    composed_deck: JsonDict | None,
    deck_title: str,
    settings: Settings,
    render_slots: Any = None,
    slide_titles: list[str] | None = None,
) -> ExportResult:
    """`out_dir/deck.pdf`, `out_dir/thumbs/slide-NN.png`, `out_dir/deck.html`.

    Имена миниатюр в результате относительны каталогу задания (`prefix` — `<variant>/r<rev>/`),
    как их отдаёт манифест ревизии. Ошибки рендерера пробрасываются вызывающему слою."""
    soffice = _find_soffice()
    if soffice is None or not soffice.exists():
        raise RendererUnavailableError("LibreOffice не найден: экспорт PDF невозможен")
    from presentation_designer.export.render_slots import LocalRenderSlots

    slots = render_slots or LocalRenderSlots(settings.render.slots)
    report: JsonDict = {"timings_ms": {}}
    started = time.perf_counter()
    with slots.acquire(
        timeout_s=settings.timeouts.stage_export_s,
        ttl_s=settings.timeouts.render_convert_s * 2,
    ) as lease:
        report["timings_ms"]["render_slot_wait"] = lease.wait_ms
        pdf = _convert_to_pdf(
            pptx_path, out_dir, timeout_s=settings.timeouts.render_convert_s, soffice=soffice
        )
    report["renderer"] = f"libreoffice ({lease.backend} slots)"
    report["timings_ms"]["pdf"] = int(pdf.seconds * 1000)
    if pdf.pdf_path.name != "deck.pdf":
        pdf.pdf_path.replace(out_dir / "deck.pdf")
    pdf_path = out_dir / "deck.pdf"

    thumbs = render_thumbnails(
        pdf_path, out_dir / THUMBS_DIR, width_px=settings.render.thumbnail_width_px
    )
    report["timings_ms"]["thumbnails"] = int(thumbs.seconds * 1000)
    thumbnails = [
        {
            "slide_index": i,
            "name": f"{prefix}{THUMBS_DIR}/{path.name}",
            "width_px": size[0],
            "height_px": size[1],
        }
        for i, (path, size) in enumerate((p, _png_size(p)) for p in thumbs.paths)
    ]

    html_started = time.perf_counter()
    html_path = out_dir / "deck.html"
    # Нативная страница из описания собранного файла; снимки — только если
    # описания нет (режим заглушек). ТЗ п.2.7: слайд-картинка не засчитывается.
    kind = "native_objects"
    try:
        if not composed_deck or not composed_deck.get("slides"):
            raise ValueError("нет описания собранной колоды")
        markup = native_html(deck_title, composed_deck, pptx_path, slide_titles or [])
    except Exception:
        log.warning("нативный html не построен, остаются снимки", exc_info=True)
        markup = build_html(deck_title, composed_deck, thumbs.paths, slide_titles or [])
        kind = "images_with_text"
    html_path.write_text(markup, encoding="utf-8")
    report["timings_ms"]["html"] = int((time.perf_counter() - html_started) * 1000)
    report["timings_ms"]["total"] = int((time.perf_counter() - started) * 1000)
    report["pages"] = len(thumbnails)
    report["html"] = kind
    return ExportResult(pdf_path, html_path, thumbnails, report)


def _png_size(path: pathlib.Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as image:
        return int(image.width), int(image.height)


# ---------- HTML ----------


def slide_texts(composed_deck: JsonDict | None) -> list[list[str]]:
    """Абзацы каждого слайда из ComposedDeck в порядке чтения (по z-order объектов)."""
    if not composed_deck:
        return []
    out: list[list[str]] = []
    for slide in composed_deck.get("slides") or []:
        paragraphs: list[str] = []
        objects = sorted(slide.get("objects") or [], key=lambda o: int(o.get("z_order") or 0))
        for obj in objects:
            text = obj.get("text") or {}
            for para in text.get("paragraphs") or []:
                value = str(para.get("text") or "").strip()
                if value:
                    paragraphs.append(value)
            if not text.get("paragraphs") and str(text.get("plain") or "").strip():
                paragraphs.append(str(text["plain"]).strip())
            table = obj.get("table") or {}
            if table.get("dataset_id"):
                # Ячейки таблицы в ComposedDeck не хранятся (только набор данных и размер):
                # в HTML остаётся отметка, откуда таблица.
                paragraphs.append(
                    f"Таблица {table.get('rows', '?')}×{table.get('cols', '?')}"
                    f" из набора {table['dataset_id']}"
                )
        out.append(paragraphs)
    return out


def build_html(
    title: str,
    composed_deck: JsonDict | None,
    pages: list[pathlib.Path],
    slide_titles: list[str],
) -> str:
    """Автономная страница: слайд картинкой и его текст под ним; внешних ресурсов нет."""
    texts = slide_texts(composed_deck)
    sections: list[str] = []
    for i, page in enumerate(pages):
        data = base64.b64encode(page.read_bytes()).decode("ascii")
        heading = slide_titles[i] if i < len(slide_titles) else f"Слайд {i + 1}"
        paragraphs = texts[i] if i < len(texts) else []
        body = "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs if p != heading)
        sections.append(
            f'<section class="slide" id="slide-{i + 1}">'
            f'<img src="data:image/png;base64,{data}" alt="{html.escape(heading)}">'
            f'<div class="text"><h2>{html.escape(heading)}</h2>{body}</div>'
            f"<footer>{i + 1} / {len(pages)}</footer></section>"
        )
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title>"
        "<style>"
        "body{margin:0;padding:24px 16px;font-family:system-ui,-apple-system,sans-serif;"
        "background:#f3f4f6;color:#1d1f25}"
        ".slide{max-width:960px;margin:0 auto 32px;background:#fff;border:1px solid #e2e4e9;"
        "border-radius:10px;overflow:hidden;position:relative}"
        ".slide img{display:block;width:100%;height:auto;aspect-ratio:16/9;background:#fff}"
        ".text{padding:16px 24px 28px;border-top:1px solid #eeeff2;font-size:14px;line-height:1.5}"
        ".text h2{margin:0 0 8px;font-size:18px}.text p{margin:0 0 6px;color:#4e525c}"
        "footer{position:absolute;right:16px;bottom:8px;color:#8c909c;font-size:12px}"
        "</style></head><body>"
        f'<h1 style="max-width:960px;margin:0 auto 20px;font-size:22px">{html.escape(title)}</h1>'
        f"{''.join(sections)}</body></html>"
    )
