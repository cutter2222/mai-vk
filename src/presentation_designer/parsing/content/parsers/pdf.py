"""Разбор PDF с текстовым слоем (pdfplumber): заголовки по кеглю, абзацы, таблицы;
изображения страниц — через pypdfium2. Скан без текстового слоя явно помечается:
OCR не подключён, содержание такого файла в пакет не попадает."""

from __future__ import annotations

import io
import pathlib
import statistics
from typing import Any

from presentation_designer.parsing.content.parsers.base import (
    Location,
    ParsedBlock,
    ParsedDocument,
    ParsedImage,
    ParsedTable,
    ParserError,
    clean_text,
    guess_image_kind,
    is_bullet_line,
    split_long,
    strip_bullet,
)

VERSION = "0.1.0"


def parse(
    path: pathlib.Path,
    *,
    max_block_chars: int = 2000,
    min_image_px: int = 64,
    max_pages: int = 200,
) -> ParsedDocument:
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover
        raise ParserError("parser_unavailable", "pdfplumber не установлен") from e
    out = ParsedDocument("pdf")
    pages = 0
    chars = 0
    try:
        with pdfplumber.open(str(path)) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                if index > max_pages:
                    out.warn("pages_truncated", f"прочитано {max_pages} страниц")
                    break
                pages += 1
                try:
                    lines = page.extract_text_lines(return_chars=True) or []
                except Exception:
                    lines = []
                page_chars = sum(len(line.get("text", "")) for line in lines)
                chars += page_chars
                tables_bboxes: list[tuple[float, float, float, float]] = []
                try:
                    for found in page.find_tables():
                        rows = [[clean_text(c) or None for c in row] for row in found.extract()]
                        rows = [r for r in rows if any(c is not None for c in r)]
                        if len(rows) >= 2 and len(rows[0]) >= 2:
                            tables_bboxes.append(found.bbox)
                            out.tables.append(ParsedTable(rows=rows, location=Location(page=index)))
                            out.blocks.append(
                                ParsedBlock(
                                    "table",
                                    table_ref=len(out.tables) - 1,
                                    location=Location(page=index),
                                )
                            )
                except Exception as e:
                    out.warn("tables_skipped", f"страница {index}: таблицы не извлечены ({e})")
                text_lines = [line for line in lines if not _inside_tables(line, tables_bboxes)]
                out.blocks.extend(_blocks_from_lines(text_lines, index, max_block_chars))
    except ParserError:
        raise
    except Exception as e:
        raise ParserError("pdf_unreadable", f"PDF не открывается: {e}") from e
    _extract_images(path, out, min_image_px, max_pages)
    out.units = {
        "pages": pages,
        "tables": len(out.tables),
        "images": len(out.images),
        "chars": chars,
    }
    if pages and chars < 20:
        out.warn(
            "no_text_layer",
            "в PDF нет текстового слоя (скан): текст не извлечён, распознавание не подключено",
        )
    if not out.blocks and not out.images:
        out.extracted = False
        if not out.warnings:
            out.warn("no_content", "в PDF не найдено текста, таблиц и изображений")
    return out


def _inside_tables(line: dict[str, Any], bboxes: list[tuple[float, float, float, float]]) -> bool:
    x0, top = line.get("x0", 0), line.get("top", 0)
    return any(bx0 <= x0 <= bx1 and btop <= top <= bbottom for bx0, btop, bx1, bbottom in bboxes)


def _blocks_from_lines(
    lines: list[dict[str, Any]], page: int, max_block_chars: int
) -> list[ParsedBlock]:
    """Заголовок — строка с кеглем заметно больше медианного или жирная короткая строка;
    абзац заканчивается на строке с завершающей пунктуацией или перед заголовком."""
    if not lines:
        return []
    sizes = []
    for line in lines:
        char_sizes = [c.get("size", 0) for c in line.get("chars", []) if c.get("size")]
        line["_size"] = max(char_sizes) if char_sizes else 0
        line["_bold"] = any(
            "bold" in str(c.get("fontname", "")).lower() for c in line.get("chars", [])[:3]
        )
        if line["_size"]:
            sizes.append(line["_size"])
    median = statistics.median(sizes) if sizes else 0
    blocks: list[ParsedBlock] = []
    paragraph: list[str] = []
    bullets: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            text = clean_text(" ".join(paragraph))
            for piece in split_long(text, max_block_chars):
                blocks.append(ParsedBlock("paragraph", text=piece, location=Location(page=page)))
            paragraph = []

    def flush_bullets() -> None:
        nonlocal bullets
        if bullets:
            blocks.append(ParsedBlock("bullets", items=bullets, location=Location(page=page)))
            bullets = []

    for line in lines:
        text = clean_text(line.get("text", ""))
        if not text:
            continue
        big = median and line["_size"] >= median * 1.25
        if (
            (big or (line["_bold"] and len(text) <= 80))
            and len(text) <= 120
            and not text.endswith(".")
        ):
            flush_paragraph()
            flush_bullets()
            level = 1 if (median and line["_size"] >= median * 1.6) else 2
            blocks.append(
                ParsedBlock("heading", text=text, level=level, location=Location(page=page))
            )
            continue
        if is_bullet_line(text):
            flush_paragraph()
            bullets.append(strip_bullet(text))
            continue
        if bullets:
            # Продолжение пункта списка на следующей строке.
            if text[:1].islower():
                bullets[-1] = f"{bullets[-1]} {text}"
                continue
            flush_bullets()
        paragraph.append(text)
        if text[-1] in ".!?…:" and len(text) > 40:
            flush_paragraph()
    flush_paragraph()
    flush_bullets()
    return blocks


def _extract_images(
    path: pathlib.Path, out: ParsedDocument, min_image_px: int, max_pages: int
) -> None:
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_c
    except ImportError:
        out.warn("images_not_extracted", "pypdfium2 не установлен: изображения PDF пропущены")
        return
    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as e:
        out.warn("images_not_extracted", f"изображения PDF не извлечены: {e}")
        return
    try:
        for index in range(min(len(pdf), max_pages)):
            page = pdf[index]
            try:
                objects = list(page.get_objects(max_depth=2))
            except Exception:
                continue
            for obj in objects:
                if getattr(obj, "type", None) != pdfium_c.FPDF_PAGEOBJ_IMAGE:
                    continue
                try:
                    pil = obj.get_bitmap().to_pil()
                except Exception:
                    continue
                width, height = pil.size
                if max(width, height) < min_image_px:
                    continue
                buf = io.BytesIO()
                pil.convert("RGB").save(buf, format="PNG", optimize=True)
                data = buf.getvalue()
                out.images.append(
                    ParsedImage(
                        data=data,
                        mime="image/png",
                        name=f"page-{index + 1}-image-{len(out.images) + 1}.png",
                        width_px=width,
                        height_px=height,
                        location=Location(page=index + 1),
                        kind=guess_image_kind(data, width, height, "image/png"),
                    )
                )
                out.blocks.append(
                    ParsedBlock(
                        "figure", image_ref=len(out.images) - 1, location=Location(page=index + 1)
                    )
                )
            page.close()
    finally:
        pdf.close()
