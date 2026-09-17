"""Тон образца: светлый или тёмный фон слайда и откуда он взят.

Порядок поиска повторяет наследование фона в PowerPoint: заливка самого слайда (`p:bg` —
сплошная, градиент, картинка или ссылка на цвет темы) → картинка или фигура слайда, закрывающая
не меньше 0,85 площади → фон макета и его закрывающие объекты → фон мастера → `lt1` темы через
карту цветов мастера. Тон — относительная яркость (WCAG) итогового цвета или средняя яркость
непрозрачных пикселей картинки; порог 0,5. Источник записывается в профиль: по большой
фотографии тон может быть оценён неверно, и планировщик знает, чему верить.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from presentation_designer.parsing.template.geometry import NS, ShapeInfo
from presentation_designer.parsing.template.package import (
    LayoutInfo,
    MasterInfo,
    SlideInfo,
    TemplatePackage,
)
from presentation_designer.parsing.template.styles import Theme, resolve_color

COVER_AREA = 0.85
LIGHT_THRESHOLD = 0.5


@dataclass
class Tone:
    background: str  # light | dark | unknown
    luminance: float | None
    source: str

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"background": self.background, "source": self.source}
        if self.luminance is not None:
            out["luminance"] = round(self.luminance, 3)
        return out


UNKNOWN = Tone("unknown", None, "none")


def relative_luminance(hex_color: str) -> float:
    """Относительная яркость sRGB по WCAG 2: 0 — чёрный, 1 — белый."""
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return 0.0
    channels = []
    for i in (0, 2, 4):
        c = int(value[i : i + 2], 16) / 255.0
        channels.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def image_luminance(blob: bytes) -> float | None:
    """Средняя относительная яркость непрозрачных пикселей уменьшенной картинки."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(blob)) as img:
            small = img.convert("RGBA")
            small.thumbnail((64, 64))
            raw: Any = getattr(small, "get_flattened_data", small.getdata)()
            pixels = list(raw)
    except Exception:
        return None
    opaque = [(r, g, b) for r, g, b, a in pixels if a > 40]
    if not opaque:
        return None
    total = sum(relative_luminance(f"#{r:02X}{g:02X}{b:02X}") for r, g, b in opaque)
    return total / len(opaque)


def tone_of(luminance: float | None, source: str) -> Tone:
    if luminance is None:
        return Tone("unknown", None, source)
    return Tone("light" if luminance >= LIGHT_THRESHOLD else "dark", luminance, source)


# ---------- фон части (слайд, макет, мастер) ----------


def _fill_luminance(fill_parent: Any, theme: Theme | None) -> float | None:
    """Яркость сплошной заливки или среднего цвета градиента."""
    if fill_parent is None:
        return None
    solid = fill_parent.find("a:solidFill", NS)
    if solid is not None:
        color = resolve_color(solid, theme, "background")
        return relative_luminance(color.hex) if color else None
    grad = fill_parent.find("a:gradFill", NS)
    if grad is not None:
        stops = [resolve_color(gs, theme, "background") for gs in grad.findall("a:gsLst/a:gs", NS)]
        values = [relative_luminance(c.hex) for c in stops if c is not None]
        return sum(values) / len(values) if values else None
    return None


def _blip_blob(fill_parent: Any, part: Any) -> bytes | None:
    if fill_parent is None or part is None:
        return None
    blip = fill_parent.find("a:blipFill/a:blip", NS)
    if blip is None:
        return None
    rid = blip.get(f"{{{NS['r']}}}embed")
    if not rid:
        return None
    try:
        related = part.related_part(rid)
        return bytes(related.blob)
    except (KeyError, AttributeError):
        return None


def part_background(element: Any, theme: Theme | None, part: Any, level: str) -> Tone | None:
    """Фон элемента `p:cSld/p:bg`: bgPr (заливка, градиент, картинка) или bgRef (цвет темы).
    None — фон наследуется."""
    if element is None:
        return None
    bg = element.find("p:cSld/p:bg", NS)
    if bg is None:
        return None
    bg_pr = bg.find("p:bgPr", NS)
    if bg_pr is not None:
        blob = _blip_blob(bg_pr, part)
        if blob is not None:
            return tone_of(image_luminance(blob), f"{level}_picture")
        lum = _fill_luminance(bg_pr, theme)
        if lum is not None:
            return tone_of(lum, f"{level}_fill")
        if bg_pr.find("a:noFill", NS) is not None:
            return None
        return Tone("unknown", None, f"{level}_fill")
    bg_ref = bg.find("p:bgRef", NS)
    if bg_ref is not None:
        color = resolve_color(bg_ref, theme, "background")
        if color is not None:
            return tone_of(relative_luminance(color.hex), f"{level}_fill")
    return None


def covering_object(
    shapes: list[ShapeInfo],
    theme: Theme | None,
    assets_by_sha: dict[str, Any],
    level: str,
) -> Tone | None:
    """Самый верхний объект, закрывающий слайд: картинка (яркость из индекса ресурсов или по
    байтам) или фигура со сплошной заливкой."""
    covering = [
        s for s in shapes if s.area >= COVER_AREA and s.kind in ("picture", "shape", "text")
    ]
    for s in sorted(covering, key=lambda m: -m.z_order):
        if s.kind == "picture":
            asset = assets_by_sha.get(s.media_sha256 or "")
            lum = getattr(asset, "mean_luminance", None) if asset is not None else None
            if lum is None and s.media_blob:
                lum = image_luminance(s.media_blob)
            return tone_of(lum, f"{level}_picture")
        if s.fill_kind == "solid" and s.fill_hex:
            hex_color = s.fill_hex
            if hex_color.startswith("scheme:"):
                key = hex_color[len("scheme:") :]
                mapped = theme.clr_map.get(key, key) if theme else key
                hex_color = (theme.colors.get(mapped) if theme else None) or ""
            if hex_color.startswith("#"):
                return tone_of(relative_luminance(hex_color), f"{level}_shape")
    return None


def _part_of(pkg: TemplatePackage | None, part_name: str) -> Any:
    if pkg is None or not part_name:
        return None
    for part in pkg.prs.part.package.iter_parts():
        if str(part.partname).lstrip("/") == part_name:
            return part
    return None


def slide_tone(
    slide: SlideInfo,
    layout: LayoutInfo | None,
    master: MasterInfo | None,
    assets_by_sha: dict[str, Any],
    *,
    pkg: TemplatePackage | None = None,
) -> Tone:
    theme = master.theme if master else None
    slide_part = slide.slide.part if slide.slide is not None else None
    found = part_background(slide.element, theme, slide_part, "slide")
    if found is None:
        found = covering_object(slide.shapes, theme, assets_by_sha, "slide")
    if found is None and layout is not None:
        found = part_background(layout.element, theme, _part_of(pkg, layout.part), "layout")
        if found is None:
            found = covering_object(layout.shapes, theme, assets_by_sha, "layout")
    if found is None and master is not None:
        found = part_background(master.element, theme, _part_of(pkg, master.part), "master")
    if found is None and theme is not None:
        key = theme.clr_map.get("bg1", "lt1")
        hex_color = theme.colors.get(key) or theme.colors.get("lt1")
        if hex_color:
            found = tone_of(relative_luminance(hex_color), "theme")
    return found or UNKNOWN


__all__ = [
    "COVER_AREA",
    "LIGHT_THRESHOLD",
    "Tone",
    "image_luminance",
    "relative_luminance",
    "slide_tone",
    "tone_of",
]
