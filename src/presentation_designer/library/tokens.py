"""Дизайн-код шаблона: из чего собственные композиции берут цвет, шрифт, кегль и пластику.

Это читающая обёртка над `design_tokens` профиля. Композиция ссылается на роли
(`accent`, `text`, `background`, `body`, `title`), а не на конкретные значения, поэтому одна
и та же композиция в разных шаблонах выглядит по-разному, оставаясь в их дизайн-системе.

Когда в шаблоне чего-то нет (пустая палитра, нет шкалы кеглей, нет плашек), берутся
умолчания и об этом говорит `missing`: планировщик по нему понижает доверие к композиции.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from presentation_designer.layout.charts import luminance

JsonDict = dict[str, Any]

# Умолчания нейтральные: их видно только если шаблон не дал ничего своего.
DEFAULT_TEXT = "#111111"
DEFAULT_BACKGROUND = "#FFFFFF"
DEFAULT_ACCENT = "#0077FF"
DEFAULT_BODY_PT = 16.0
DEFAULT_TITLE_PT = 30.0


@dataclass
class DesignCode:
    """Значения дизайн-системы шаблона, которыми одевается композиция."""

    title_font: str | None = None
    body_font: str | None = None
    title_pt: float = DEFAULT_TITLE_PT
    subtitle_pt: float = 20.0
    body_pt: float = DEFAULT_BODY_PT
    caption_pt: float = 12.0
    number_pt: float = 44.0
    text_color: str = DEFAULT_TEXT
    muted_color: str = "#6B6B6B"
    background: str = DEFAULT_BACKGROUND
    accents: list[str] = field(default_factory=lambda: [DEFAULT_ACCENT])
    card_geometry: str = "rect"
    corner_ratio: float = 0.0
    stroke_pt: float = 0.0
    shadow: bool = False
    margins: dict[str, float] = field(
        default_factory=lambda: {"left": 0.06, "right": 0.06, "top": 0.08, "bottom": 0.08}
    )
    gutter: float = 0.03
    missing: list[str] = field(default_factory=list)

    @property
    def accent(self) -> str:
        return self.accents[0] if self.accents else DEFAULT_ACCENT

    def accent_at(self, index: int) -> str:
        return self.accents[index % len(self.accents)] if self.accents else DEFAULT_ACCENT

    def on_accent(self, hex_color: str) -> str:
        """Цвет текста поверх заливки: светлый на тёмной, тёмный на светлой."""
        return "#FFFFFF" if luminance(hex_color) < 0.55 else self.text_color

    def size_for(self, role: str) -> float:
        return {
            "title": self.title_pt,
            "subtitle": self.subtitle_pt,
            "body": self.body_pt,
            "label": self.caption_pt,
            "caption": self.caption_pt,
            "number": self.number_pt,
        }.get(role, self.body_pt)

    def font_for(self, role: str) -> str | None:
        return self.title_font if role in ("title", "subtitle", "number") else self.body_font

    @classmethod
    def from_profile(cls, profile: JsonDict) -> DesignCode:
        tokens = profile.get("design_tokens") or {}
        code = cls()
        _read_typography(code, tokens.get("typography") or {})
        _read_colors(code, tokens.get("colors") or {})
        _read_spacing(code, tokens.get("spacing") or {})
        _read_shape(code, tokens.get("shape") or {})
        return code


def _read_typography(code: DesignCode, typography: JsonDict) -> None:
    fonts = typography.get("fonts") or []
    if not fonts:
        code.missing.append("fonts")
    available = [f for f in fonts if f.get("family")]

    def by_role(role: str) -> str | None:
        for f in available:
            if role in (f.get("roles") or []):
                return str(f["family"])
        return None

    theme_fonts = typography.get("theme_fonts") or {}
    code.title_font = by_role("title") or theme_fonts.get("major") or _first_family(available)
    code.body_font = by_role("body") or theme_fonts.get("minor") or code.title_font

    scale = [s for s in (typography.get("scale") or []) if s.get("size_pt")]
    if not scale:
        code.missing.append("scale")
        return
    by_size_role: dict[str, list[float]] = {}
    for step in scale:
        by_size_role.setdefault(str(step.get("role") or "other"), []).append(float(step["size_pt"]))
    sizes = sorted((float(s["size_pt"]) for s in scale), reverse=True)

    def pick(role: str, fallback: float) -> float:
        values = by_size_role.get(role) or []
        return max(values) if values else fallback

    code.title_pt = pick("title", pick("display", sizes[0] if sizes else DEFAULT_TITLE_PT))
    code.subtitle_pt = pick("subtitle", max(code.title_pt * 0.62, 14.0))
    body_values = by_size_role.get("body") or []
    code.body_pt = min(body_values) if body_values else DEFAULT_BODY_PT
    code.caption_pt = pick("caption", max(code.body_pt - 3, 9.0))
    code.number_pt = pick("kpi", max(code.title_pt * 1.25, 36.0))


def _first_family(fonts: list[JsonDict]) -> str | None:
    return str(fonts[0]["family"]) if fonts else None


def _read_colors(code: DesignCode, colors: JsonDict) -> None:
    theme = colors.get("theme") or {}
    palette = colors.get("palette") or []
    if not palette and not theme:
        code.missing.append("colors")
        return
    by_role: dict[str, list[str]] = {}
    for entry in palette:
        if entry.get("hex"):
            by_role.setdefault(str(entry.get("role") or "neutral"), []).append(str(entry["hex"]))
    code.text_color = _first(by_role.get("text"), theme.get("dk1"), DEFAULT_TEXT)
    code.background = _first(by_role.get("background"), theme.get("lt1"), DEFAULT_BACKGROUND)
    code.muted_color = _first(by_role.get("muted"), by_role.get("neutral"), "#6B6B6B")
    # Акценты: сначала роли палитры, затем accentN темы; белёсые оттенки не годятся под заливку.
    accents = [
        hex_color
        for hex_color in (by_role.get("primary") or []) + (by_role.get("accent") or [])
        if luminance(hex_color) < 0.88
    ]
    for key in sorted(k for k in theme if k.startswith("accent")):
        value = theme.get(key)
        if value and value not in accents and luminance(value) < 0.88:
            accents.append(str(value))
    code.accents = accents or [DEFAULT_ACCENT]


def _first(*values: Any) -> str:
    for value in values:
        if isinstance(value, list) and value:
            return str(value[0])
        if isinstance(value, str) and value:
            return value
    return DEFAULT_TEXT


def _read_spacing(code: DesignCode, spacing: JsonDict) -> None:
    margins = spacing.get("margins") or {}
    if margins:
        code.margins = {side: float(margins.get(side, code.margins[side])) for side in code.margins}
    else:
        code.missing.append("margins")
    grid = spacing.get("column_grid") or {}
    if grid.get("gutter"):
        code.gutter = float(grid["gutter"])


def _read_shape(code: DesignCode, shape: JsonDict) -> None:
    if not shape:
        code.missing.append("shape")
        return
    code.card_geometry = str(shape.get("card_geometry") or "rect")
    code.corner_ratio = float(shape.get("corner_ratio") or 0.0)
    code.stroke_pt = float(shape.get("stroke_pt") or 0.0)
    code.shadow = float(shape.get("shadow_share") or 0.0) >= 0.4
