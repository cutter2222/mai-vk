"""Разбор Markdown (markdown-it-py) и простого текста.

Markdown: заголовки по уровням, абзацы, маркированные и нумерованные списки (вложенные
пункты выравниваются в один список), цитаты, код, таблицы GFM. Ссылки на картинки
`![alt](path)` остаются предупреждением: файлы рядом с материалом сервису недоступны.
Текст: абзацы по пустым строкам, маркированные строки — списки, короткая строка без точки
в начале — заголовок.
"""

from __future__ import annotations

import pathlib
from typing import Any

from presentation_designer.parsing.content.parsers.base import (
    Location,
    ParsedBlock,
    ParsedDocument,
    ParsedTable,
    ParserError,
    blocks_from_text,
    clean_text,
    decode_text,
    split_long,
)

VERSION = "0.1.0"


def parse_text(path: pathlib.Path, *, max_block_chars: int = 2000) -> ParsedDocument:
    text, encoding = decode_text(path.read_bytes())
    out = ParsedDocument("text")
    if encoding != "utf-8":
        out.warn("encoding_guessed", f"кодировка определена как {encoding}")
    out.blocks = blocks_from_text(text, max_block_chars=max_block_chars)
    # Первый заголовок файла считается заголовком первого уровня.
    for block in out.blocks:
        if block.kind == "heading":
            block.level = 1
            break
    out.units = {"chars": out.text_chars}
    if not out.blocks:
        out.extracted = False
        out.warn("no_content", "файл пуст")
    return out


def parse_markdown(path: pathlib.Path, *, max_block_chars: int = 2000) -> ParsedDocument:
    try:
        from markdown_it import MarkdownIt
    except ImportError as e:  # pragma: no cover
        raise ParserError("parser_unavailable", "markdown-it-py не установлен") from e
    text, encoding = decode_text(path.read_bytes())
    out = ParsedDocument("markdown")
    if encoding != "utf-8":
        out.warn("encoding_guessed", f"кодировка определена как {encoding}")
    md = MarkdownIt("commonmark").enable("table")
    tokens = md.parse(text)
    unresolved_images: list[str] = []
    list_items: list[str] = []
    list_depth = 0
    quote_depth = 0
    table_rows: list[list[Any]] = []
    current_row: list[Any] | None = None
    pending_heading_level: int | None = None

    last_loc = Location()

    def flush_list() -> None:
        nonlocal list_items
        if list_items:
            out.blocks.append(ParsedBlock("bullets", items=list_items, location=last_loc))
            list_items = []

    for token in tokens:
        t = token.type
        last_loc = _loc(token)
        if t == "heading_open":
            flush_list()
            pending_heading_level = int(token.tag[1:])
        elif t == "inline":
            content, images = _inline_text(token)
            unresolved_images.extend(images)
            if pending_heading_level is not None:
                out.blocks.append(
                    ParsedBlock(
                        "heading",
                        text=content,
                        level=pending_heading_level,
                        location=_loc(token),
                    )
                )
                pending_heading_level = None
            elif current_row is not None:
                current_row.append(content or None)
            elif list_depth > 0:
                if content:
                    list_items.append(content)
            elif quote_depth > 0:
                if content:
                    out.blocks.append(ParsedBlock("quote", text=content, location=_loc(token)))
            elif content:
                for piece in split_long(content, max_block_chars):
                    out.blocks.append(ParsedBlock("paragraph", text=piece, location=_loc(token)))
        elif t in ("bullet_list_open", "ordered_list_open"):
            list_depth += 1
        elif t in ("bullet_list_close", "ordered_list_close"):
            list_depth -= 1
            if list_depth == 0:
                flush_list()
        elif t == "blockquote_open":
            flush_list()
            quote_depth += 1
        elif t == "blockquote_close":
            quote_depth -= 1
        elif t in ("fence", "code_block"):
            flush_list()
            code = token.content.rstrip("\n")
            if code:
                out.blocks.append(ParsedBlock("code", text=code, location=_loc(token)))
        elif t == "table_open":
            flush_list()
            table_rows = []
        elif t == "tr_open":
            current_row = []
        elif t == "tr_close":
            if current_row is not None:
                table_rows.append(current_row)
            current_row = None
        elif t == "table_close":
            rows = [r for r in table_rows if any(c is not None for c in r)]
            if rows:
                width = max(len(r) for r in rows)
                rows = [r + [None] * (width - len(r)) for r in rows]
                title = _previous_heading(out)
                out.tables.append(ParsedTable(rows=rows, title=title, location=_loc(token)))
                out.blocks.append(
                    ParsedBlock("table", table_ref=len(out.tables) - 1, location=_loc(token))
                )
            table_rows = []
        elif t == "hr":
            flush_list()
    flush_list()
    if unresolved_images:
        out.warn(
            "image_reference_unresolved",
            "ссылки на изображения не разрешены (файлы рядом с материалом недоступны): "
            + ", ".join(unresolved_images[:5]),
        )
    out.units = {"tables": len(out.tables), "chars": out.text_chars}
    if not out.blocks:
        out.extracted = False
        out.warn("no_content", "файл пуст")
    return out


def _loc(token: Any) -> Location:
    line = token.map[0] if getattr(token, "map", None) else None
    return Location(char_offset=line)


def _inline_text(token: Any) -> tuple[str, list[str]]:
    """Текст inline-токена без разметки; ссылки на картинки — отдельным списком."""
    images: list[str] = []
    parts: list[str] = []
    for child in token.children or []:
        if child.type == "image":
            src = child.attrGet("src") or ""
            alt = child.content or ""
            images.append(src or alt)
            if alt:
                parts.append(alt)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
        elif child.type == "code_inline":
            parts.append(child.content)
        elif child.content:
            parts.append(child.content)
    return clean_text("".join(parts)), images


def _previous_heading(doc: ParsedDocument) -> str | None:
    for block in reversed(doc.blocks):
        if block.kind == "heading":
            return block.text
        if block.kind in ("table",):
            break
    return None
