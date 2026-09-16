"""Пиктограммы из ресурсов шаблона в слоты icon.

Иконка — медиа-часть исходного пакета (`assets[].media_path` профиля): объект образца
получает связь с этой частью, без копирования байтов. Перекраска разрешена только для
монохромных масок (один значимый цвет и прозрачность): Pillow заменяет цвет, сохраняя
альфа-канал, и новая картинка становится отдельной частью. Многоцветные иллюстрации и
логотипы не перекрашиваются. Внешние библиотеки иконок (`icon.query`) на этом этапе не
подключены: слот сохраняет иконку образца, а в результат попадает предупреждение.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from presentation_designer.layout.images import place_image, relate_image_part
from presentation_designer.layout.ooxml import NS_R
from presentation_designer.layout.shapes import NS, drop_unreferenced_rels, part_by_name


@dataclass
class IconResult:
    asset_id: str
    recolored: bool
    rid: str


def is_monochrome_mask(blob: bytes) -> bool:
    """Одна значимая краска с прозрачностью — маска, которую можно перекрасить."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(blob)) as img:
            if img.mode not in ("RGBA", "LA", "P"):
                return False
            rgba = img.convert("RGBA")
            if rgba.width * rgba.height > 4_000_000:
                return False
            opaque = [px[:3] for px in list(rgba.getdata()) if px[3] > 32]
            if not opaque:
                return False
            colors = {(r // 32, g // 32, b // 32) for r, g, b in opaque}
            return len(colors) <= 2
    except Exception:
        return False


def recolor_mask(blob: bytes, hex_color: str) -> bytes:
    from PIL import Image

    r, g, b = (int(hex_color.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    with Image.open(io.BytesIO(blob)) as img:
        rgba = img.convert("RGBA")
        alpha = rgba.getchannel("A")
        solid = Image.new("RGBA", rgba.size, (r, g, b, 255))
        solid.putalpha(alpha)
        out = io.BytesIO()
        solid.save(out, format="PNG")
        return out.getvalue()


def place_icon(
    slide: Any,
    element: Any,
    asset: dict[str, Any],
    *,
    color: str | None = None,
) -> IconResult:
    """Иконка шаблона в объект образца: ссылка на существующую часть или перекрашенная копия."""
    package = slide.part.package
    part = part_by_name(package, str(asset.get("media_path", "")))
    if part is None:
        raise ValueError(f"в пакете нет медиа-части {asset.get('media_path')}")
    blob = bytes(part.blob)
    blip = element.find(".//a:blip", NS)
    if blip is None:
        raise ValueError("объект образца не является картинкой")
    if color and is_monochrome_mask(blob):
        result = place_image(slide, element, recolor_mask(blob, color), fit="contain")
        return IconResult(str(asset.get("asset_id", "")), True, result.rid)
    rid = relate_image_part(slide, part)
    blip.set(f"{{{NS_R}}}embed", rid)
    drop_unreferenced_rels(slide)
    return IconResult(str(asset.get("asset_id", "")), False, rid)


__all__ = ["IconResult", "is_monochrome_mask", "place_icon", "recolor_mask"]
