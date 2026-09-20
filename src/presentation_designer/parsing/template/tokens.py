"""Токены дизайн-системы: палитра, типографика, шкала кеглей, поля и сетка.

Каждое значение несёт область действия и уверенность: цвета темы применимы везде, цвета с
образцов — там, где встречены; кегли группируются по роли текста, шрифты — по частоте с
отметкой встроенности и доступности в рендерере. Поля и сетка выводятся из повторяющихся
координат образцов содержания; направляющие из viewProps дополняют их.
"""

from __future__ import annotations

import collections
import itertools
import statistics
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.parsing.template.geometry import NS, ShapeInfo
from presentation_designer.parsing.template.package import TemplatePackage
from presentation_designer.parsing.template.styles import ResolvedText, StyleResolver, resolve_color
from presentation_designer.shared import text_metrics

NEUTRAL_SATURATION = 0.12


@dataclass
class TextOccurrence:
    """Один фрагмент текста образца с вычисленным стилем и ролью слота."""

    slide_index: int
    shape: ShapeInfo
    style: ResolvedText
    role: str  # display | title | subtitle | body | caption | kpi | label | code | other
    chars: int


@dataclass
class TokenStats:
    colors_used: collections.Counter[str] = field(default_factory=collections.Counter)
    color_source: dict[str, str] = field(default_factory=dict)
    color_scope: dict[str, set[int]] = field(default_factory=lambda: collections.defaultdict(set))
    fonts_used: collections.Counter[str] = field(default_factory=collections.Counter)
    font_roles: dict[str, collections.Counter[str]] = field(
        default_factory=lambda: collections.defaultdict(collections.Counter)
    )
    sizes: dict[str, collections.Counter[float]] = field(
        default_factory=lambda: collections.defaultdict(collections.Counter)
    )
    size_scope: dict[tuple[str, float], set[int]] = field(
        default_factory=lambda: collections.defaultdict(set)
    )
    occurrences: list[TextOccurrence] = field(default_factory=list)
    # Форма плашек образцов: по ним собственные композиции повторяют пластику шаблона.
    geometries: collections.Counter[str] = field(default_factory=collections.Counter)
    # Скругление roundRect в долях половины меньшей стороны (adj 0…0.5 по формуле OOXML).
    corner_ratios: list[float] = field(default_factory=list)
    line_widths_pt: list[float] = field(default_factory=list)
    shadowed: int = 0
    shapes_seen: int = 0


def _hex_role(hex_color: str, theme_colors: dict[str, str]) -> str:
    inverted = {v.upper(): k for k, v in theme_colors.items()}
    key = inverted.get(hex_color.upper())
    if key in ("lt1", "lt2"):
        return "background"
    if key in ("dk1", "dk2"):
        return "text"
    if key == "accent1":
        return "primary"
    if key == "accent2":
        return "secondary"
    if key and key.startswith("accent"):
        return "accent"
    r, g, b = (int(hex_color[i : i + 2], 16) / 255.0 for i in (1, 3, 5))
    mx, mn = max(r, g, b), min(r, g, b)
    sat = (mx - mn) / mx if mx else 0.0
    lum = (mx + mn) / 2
    if lum > 0.93:
        return "background"
    if sat < NEUTRAL_SATURATION:
        return "text" if lum < 0.25 else "neutral" if lum < 0.75 else "muted"
    return "accent"


def text_role_for(shape: ShapeInfo, style: ResolvedText, slide_h_pt: float) -> str:
    """Роль текста слота по кеглю относительно слайда, типу плейсхолдера и содержимому."""
    text = shape.text.strip()
    if shape.placeholder_type in ("title", "ctrTitle"):
        return "title"
    if shape.placeholder_type == "subTitle":
        return "subtitle"
    ratio = style.size_pt / slide_h_pt if slide_h_pt else 0.05
    digits = sum(ch.isdigit() for ch in text)
    if (
        text
        and len(text) <= 8
        and (digits >= len(text) / 2 or "%" in text or text.lower().startswith(("x", "х")))
    ):
        return "kpi"
    if any(tok in text for tok in ("{", "};", "padding:", "margin:", "=>", "def ", "import ")):
        return "code"
    if ratio >= 0.11:
        return "display"
    if ratio >= 0.05:
        return "title"
    if ratio >= 0.036:
        return "subtitle"
    if ratio < 0.02:
        return "caption"
    return "body"


def _collect_shape_form(stats: TokenStats, shape: ShapeInfo) -> None:
    """Пластика плашки: форма, скругление, толщина обводки, тень.

    Считаются только заметные фигуры: у мелкого декора и волосяных линий пластика своя и
    к карточкам содержания отношения не имеет.
    """
    if shape.element is None or shape.area < 0.004:
        return
    stats.shapes_seen += 1
    if shape.geometry:
        stats.geometries[shape.geometry] += 1
    sp_pr = shape.element.find("p:spPr", NS)
    if sp_pr is None:
        return
    if shape.geometry == "roundRect":
        # adj OOXML задан в тысячных долях половины меньшей стороны: 16667 — «как в PowerPoint».
        gd = sp_pr.find("a:prstGeom/a:avLst/a:gd", NS)
        raw = (gd.get("fmla") or "") if gd is not None else ""
        value = raw.split()[-1] if raw.startswith("val") else ""
        try:
            stats.corner_ratios.append(min(float(value) / 100000.0, 0.5) if value else 0.16667)
        except ValueError:
            stats.corner_ratios.append(0.16667)
    ln = sp_pr.find("a:ln", NS)
    if ln is not None and ln.find("a:noFill", NS) is None and ln.get("w"):
        try:
            stats.line_widths_pt.append(float(ln.get("w") or 0) / 12700.0)
        except ValueError:
            pass
    if sp_pr.find("a:effectLst/a:outerShdw", NS) is not None:
        stats.shadowed += 1


def collect_stats(
    pkg: TemplatePackage, sample_indexes: set[int], resolvers: dict[int, StyleResolver]
) -> TokenStats:
    stats = TokenStats()
    slide_h_pt = pkg.height_emu / text_metrics.EMU_PER_PT
    # Цвета темы каждого мастера: область theme.
    for master in pkg.masters:
        for key, hex_color in master.theme.colors.items():
            stats.color_source.setdefault(hex_color, "theme")
            _ = key
    for slide in pkg.slides:
        if slide.index not in sample_indexes:
            continue
        resolver = resolvers[slide.index]
        for shape in slide.shapes:
            if shape.kind == "group":
                continue
            if shape.fill_hex and shape.fill_hex.startswith("#") and shape.area >= 0.002:
                stats.colors_used[shape.fill_hex] += 1
                stats.color_source.setdefault(shape.fill_hex, "slides")
                stats.color_scope[shape.fill_hex].add(slide.index)
            elif shape.fill_kind == "solid" and shape.element is not None:
                solid = shape.element.find("p:spPr/a:solidFill", NS)
                resolved = resolve_color(solid, resolver.theme)
                if resolved:
                    stats.colors_used[resolved.hex] += 1
                    stats.color_source.setdefault(
                        resolved.hex, "theme" if resolved.theme_ref else "slides"
                    )
                    stats.color_scope[resolved.hex].add(slide.index)
            _collect_shape_form(stats, shape)
            if not shape.has_text_frame:
                continue
            for paragraph in shape.paragraphs:
                if not paragraph.text.strip():
                    continue
                runs: list[dict[str, Any] | None] = [
                    r for r in paragraph.runs if r.get("text", "").strip()
                ] or [None]
                for run in runs:
                    style = resolver.resolve(shape, paragraph, run)
                    role = text_role_for(shape, style, slide_h_pt)
                    chars = len((run or {}).get("text") or paragraph.text)
                    stats.occurrences.append(TextOccurrence(slide.index, shape, style, role, chars))
                    stats.fonts_used[style.family] += 1
                    stats.font_roles[style.family][role] += 1
                    size = round(style.size_pt, 1)
                    stats.sizes[role][size] += 1
                    stats.size_scope[(role, size)].add(slide.index)
                    stats.colors_used[style.color] += 1
                    stats.color_source.setdefault(
                        style.color, "theme" if style.color_theme_ref else "slides"
                    )
                    stats.color_scope[style.color].add(slide.index)
    return stats


def build_design_tokens(
    pkg: TemplatePackage,
    stats: TokenStats,
    sample_indexes: set[int],
    *,
    renderer_families: set[str] | None = None,
) -> dict[str, Any]:
    theme = pkg.masters[0].theme if pkg.masters else None
    theme_colors = dict(theme.colors) if theme else {}
    palette: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_uses = sum(stats.colors_used.values()) or 1
    for key, hex_color in theme_colors.items():
        if hex_color in seen:
            continue
        seen.add(hex_color)
        uses = stats.colors_used.get(hex_color, 0)
        palette.append(
            {
                "hex": hex_color,
                "role": _hex_role(hex_color, theme_colors),
                "usage_count": uses,
                "source": "theme",
                "scope": {"level": "theme", "applies_to_new_content": True},
                "confidence": 0.9 if uses else 0.6,
                "style_source": {
                    "level": "theme",
                    "theme_ref": key,
                    "part": theme.part if theme else "",
                },
            }
        )
    for hex_color, uses in stats.colors_used.most_common():
        if hex_color in seen or not hex_color.startswith("#"):
            continue
        seen.add(hex_color)
        share = uses / total_uses
        scope_slides = sorted(stats.color_scope.get(hex_color, set()))
        palette.append(
            {
                "hex": hex_color,
                "role": _hex_role(hex_color, theme_colors),
                "usage_count": uses,
                "source": "slides",
                "scope": {
                    "level": "pattern" if len(scope_slides) < 3 else "master",
                    "slide_indexes": scope_slides[:20],
                    "applies_to_new_content": len(scope_slides) >= 3 or share >= 0.05,
                },
                "confidence": round(min(0.9, 0.3 + share * 3 + 0.1 * min(len(scope_slides), 4)), 2),
                "style_source": {"level": "shape"},
            }
        )
        if len(palette) >= 24:
            break

    fonts: list[dict[str, Any]] = []
    families = (
        renderer_families
        if renderer_families is not None
        else set(text_metrics.available_families())
    )
    for family, uses in stats.fonts_used.most_common():
        resolved = text_metrics.resolve_font(family)
        roles = [r for r, _ in stats.font_roles[family].most_common(3)]
        entry: dict[str, Any] = {
            "family": family,
            "usage_count": uses,
            "embedded": family in pkg.embedded_fonts,
            "available_in_renderer": family in families
            or (resolved.face is not None and not resolved.substituted),
            "roles": [_font_role(r) for r in roles],
            "scope": {"level": "master", "applies_to_new_content": True},
        }
        if resolved.face is not None:
            entry["file"] = resolved.file
            if resolved.substituted:
                entry["fallback"] = resolved.family
        fonts.append(entry)
    for family in pkg.embedded_fonts:
        if family not in stats.fonts_used:
            resolved = text_metrics.resolve_font(family)
            fonts.append(
                {
                    "family": family,
                    "usage_count": 0,
                    "embedded": True,
                    "available_in_renderer": resolved.face is not None and not resolved.substituted,
                    "roles": [],
                    "scope": {"level": "master", "applies_to_new_content": False},
                }
            )

    scale: list[dict[str, Any]] = []
    for role, counter in stats.sizes.items():
        for size, uses in counter.most_common(4):
            slides = sorted(stats.size_scope.get((role, size), set()))
            scale.append(
                {
                    "size_pt": size,
                    "role": role
                    if role in ("display", "title", "subtitle", "body", "caption", "kpi")
                    else "other",
                    "usage_count": uses,
                    "scope": {
                        "level": "text_role",
                        "text_roles": [
                            role
                            if role
                            in (
                                "display",
                                "title",
                                "subtitle",
                                "body",
                                "caption",
                                "kpi",
                                "label",
                                "code",
                            )
                            else "other"
                        ],
                        "slide_indexes": slides[:20],
                        "applies_to_new_content": uses >= 2,
                    },
                    "confidence": round(min(0.95, 0.4 + 0.1 * min(uses, 5)), 2),
                }
            )
    scale.sort(key=lambda s: (-s["size_pt"], -s["usage_count"]))

    spacing = _spacing(pkg, sample_indexes)
    tokens: dict[str, Any] = {
        "colors": {"theme": theme_colors, "palette": palette},
        "typography": {
            "theme_fonts": {"major": theme.major_font, "minor": theme.minor_font} if theme else {},
            "fonts": fonts,
            "scale": scale,
            "max_font_families": max(1, min(3, len([f for f in fonts if f["usage_count"] > 0]))),
        },
        "spacing": spacing,
        "shape": _shape_tokens(stats),
    }
    return tokens


def _shape_tokens(stats: TokenStats) -> dict[str, Any]:
    """Пластика шаблона для собственных композиций: форма плашки, скругление, обводка, тень.

    Значения — медианы по заметным фигурам образцов, а не первое встреченное: одна
    декоративная плашка не должна задавать вид всей колоды. Без образцов ветка пустая,
    и построитель берёт свои умолчания.
    """
    if not stats.shapes_seen:
        return {}
    rounded = stats.geometries.get("roundRect", 0)
    ellipse = stats.geometries.get("ellipse", 0)
    rect = stats.geometries.get("rect", 0)
    total = max(rounded + ellipse + rect, 1)
    corner = statistics.median(stats.corner_ratios) if stats.corner_ratios else 0.0
    out: dict[str, Any] = {
        "card_geometry": "roundRect" if rounded >= rect else "rect",
        "corner_ratio": round(corner, 4),
        "rounded_share": round(rounded / total, 2),
        "shadow_share": round(stats.shadowed / stats.shapes_seen, 2),
        "confidence": round(min(0.9, 0.3 + 0.1 * min(stats.shapes_seen, 6)), 2),
    }
    if stats.line_widths_pt:
        out["stroke_pt"] = round(statistics.median(stats.line_widths_pt), 2)
    return out


def _font_role(role: str) -> str:
    if role in ("display", "title", "subtitle"):
        return "title"
    if role == "code":
        return "code"
    return "body"


def _spacing(pkg: TemplatePackage, sample_indexes: set[int]) -> dict[str, Any]:
    """Поля по 10-му процентилю краёв текстовых объектов образцов; сетка — по повторяющимся x."""
    lefts: list[float] = []
    rights: list[float] = []
    tops: list[float] = []
    bottoms: list[float] = []
    xs: collections.Counter[float] = collections.Counter()
    for slide in pkg.slides:
        if slide.index not in sample_indexes:
            continue
        for shape in slide.shapes:
            if shape.kind != "text" or not shape.text or shape.area < 0.002:
                continue
            if shape.placeholder_type in ("sldNum", "dt", "ftr"):
                continue
            lefts.append(shape.x)
            rights.append(1 - (shape.x + shape.width))
            tops.append(shape.y)
            bottoms.append(1 - (shape.y + shape.height))
            xs[round(shape.x, 2)] += 1
    if not lefts:
        return {}

    def pct(values: list[float], q: float) -> float:
        values = sorted(values)
        return round(max(0.0, values[min(len(values) - 1, int(q * (len(values) - 1)))]), 3)

    margins = {
        "left": pct(lefts, 0.1),
        "right": pct(rights, 0.1),
        "top": pct(tops, 0.1),
        "bottom": pct(bottoms, 0.1),
    }
    common_x = [x for x, n in xs.most_common(12) if n >= 3 and margins["left"] - 0.01 <= x <= 0.9]
    grid: dict[str, Any] = {}
    if len(common_x) >= 2:
        common_x.sort()
        steps = [round(b - a, 3) for a, b in itertools.pairwise(common_x) if b - a > 0.05]
        if steps:
            step = statistics.median(steps)
            usable = 1 - margins["left"] - margins["right"]
            columns = max(1, round(usable / step)) if step else 1
            grid = {
                "columns": int(min(columns, 12)),
                "gutter": round(max(0.0, step - usable / max(columns, 1)), 3) if columns else 0.0,
            }
    out: dict[str, Any] = {"margins": margins}
    if grid:
        out["column_grid"] = grid
    return out


def guides_of(pkg: TemplatePackage, sample_indexes: set[int]) -> list[dict[str, Any]]:
    out = [{"orientation": g.orientation, "pos": g.pos, "source": g.source} for g in pkg.guides]
    if out:
        return out
    # Без viewProps выводим направляющие из повторяющихся левых/верхних краёв.
    xs: collections.Counter[float] = collections.Counter()
    ys: collections.Counter[float] = collections.Counter()
    for slide in pkg.slides:
        if slide.index not in sample_indexes:
            continue
        for shape in slide.shapes:
            if shape.kind == "group" or shape.area < 0.002:
                continue
            xs[round(shape.x, 2)] += 1
            ys[round(shape.y, 2)] += 1
    for x, n in xs.most_common(4):
        if n >= 4:
            out.append({"orientation": "vertical", "pos": x, "source": "inferred"})
    for y, n in ys.most_common(3):
        if n >= 4:
            out.append({"orientation": "horizontal", "pos": y, "source": "inferred"})
    return out
