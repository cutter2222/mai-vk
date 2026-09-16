"""Изображение как материал: размеры, MIME, грубый вид (фото, логотип, скриншот)."""

from __future__ import annotations

import pathlib

from presentation_designer.parsing.content.parsers.base import (
    Location,
    ParsedBlock,
    ParsedDocument,
    ParsedImage,
    guess_image_kind,
    image_dimensions,
)

VERSION = "0.1.0"


def parse(path: pathlib.Path, *, min_image_px: int = 64, name: str | None = None) -> ParsedDocument:
    data = path.read_bytes()
    display = pathlib.Path(name or path.name)
    width, height, mime = image_dimensions(data)
    out = ParsedDocument("image")
    if max(width, height) < min_image_px:
        out.extracted = False
        out.warn("image_too_small", f"изображение {width}×{height} px слишком мало для слайда")
        return out
    out.images.append(
        ParsedImage(
            data=data,
            mime=mime,
            name=display.name,
            width_px=width,
            height_px=height,
            caption=display.stem.replace("_", " ").replace("-", " ").strip() or None,
            kind=guess_image_kind(data, width, height, mime),
        )
    )
    out.blocks.append(
        ParsedBlock("figure", image_ref=0, caption=out.images[0].caption, location=Location())
    )
    out.units = {"images": 1}
    return out
