"""Разрешение стилей: тема → мастер → макет → плейсхолдер → абзац → фрагмент.

Цвета темы читаются из `a:clrScheme` с картой `p:clrMap` мастера; модификаторы `lumMod`,
`lumOff`, `tint`, `shade`, `alpha` применяются к базовому цвету, а исходная ссылка (`accent1`)
и список модификаторов сохраняются как источник свойства. Шрифты `+mj-lt`/`+mn-lt` разрешаются
через `a:fontScheme`. Текстовые свойства фрагмента вычисляются по цепочке наследования с
отметкой уровня, на котором свойство найдено; отсутствие свойства на всех уровнях даёт
умолчание PowerPoint (18 пт, тёмный текст темы) с источником `default`.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.parsing.template.geometry import NS, Paragraph, ShapeInfo

THEME_COLOR_KEYS = (
    "dk1",
    "lt1",
    "dk2",
    "lt2",
    "accent1",
    "accent2",
    "accent3",
    "accent4",
    "accent5",
    "accent6",
    "hlink",
    "folHlink",
)
DEFAULT_CLR_MAP = {
    "bg1": "lt1",
    "tx1": "dk1",
    "bg2": "lt2",
    "tx2": "dk2",
    "accent1": "accent1",
    "accent2": "accent2",
    "accent3": "accent3",
    "accent4": "accent4",
    "accent5": "accent5",
    "accent6": "accent6",
    "hlink": "hlink",
    "folHlink": "folHlink",
}
SYSTEM_COLORS = {"windowText": "#000000", "window": "#FFFFFF"}


@dataclass
class Theme:
    name: str
    colors: dict[str, str]  # dk1..folHlink → #RRGGBB
    major_font: str
    minor_font: str
    clr_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CLR_MAP))
    part: str = ""
    # Толщины линий темы (a:fmtScheme/a:lnStyleLst) в пунктах: фигура ссылается на них
    # номером в p:style/a:lnRef@idx и своей толщины не хранит.
    line_widths_pt: list[float] = field(default_factory=list)


@dataclass
class ResolvedColor:
    hex: str
    theme_ref: str | None = None
    modifiers: list[str] = field(default_factory=list)
    level: str = "shape"


@dataclass
class ResolvedText:
    """Вычисленные свойства текста фрагмента/абзаца с источником каждого."""

    family: str
    size_pt: float
    bold: bool
    italic: bool
    color: str
    all_caps: bool = False
    family_source: str = "default"
    size_source: str = "default"
    color_source: str = "default"
    color_theme_ref: str | None = None
    color_modifiers: list[str] = field(default_factory=list)
    line_spacing: float | None = None
    space_before_pt: float | None = None
    space_after_pt: float | None = None
    bullet: str | None = None
    bullet_char: str | None = None
    indent_emu: int | None = None
    part: str = ""

    def font_spec(self) -> dict[str, Any]:
        spec: dict[str, Any] = {
            "family": self.family,
            "size_pt": round(self.size_pt, 1),
            "bold": self.bold,
            "italic": self.italic,
            "color": self.color,
        }
        if self.line_spacing and self.line_spacing > 0:
            spec["line_spacing"] = round(self.line_spacing, 2)
        if self.all_caps:
            spec["all_caps"] = True
        return spec

    def computed_style(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "font": self.font_spec(),
            "font_source": {"level": self.family_source, "part": self.part},
            "size_source": {"level": self.size_source, "part": self.part},
            "color_source": {
                "level": self.color_source,
                "part": self.part,
                **({"theme_ref": self.color_theme_ref} if self.color_theme_ref else {}),
                **({"modifiers": self.color_modifiers} if self.color_modifiers else {}),
            },
        }
        if self.space_before_pt is not None:
            out["space_before_pt"] = self.space_before_pt
        if self.space_after_pt is not None:
            out["space_after_pt"] = self.space_after_pt
        if self.indent_emu is not None:
            out["indent_emu"] = self.indent_emu
        if self.bullet:
            bullet: dict[str, Any] = {"kind": self.bullet}
            if self.bullet_char:
                bullet["char"] = self.bullet_char
            out["bullet"] = bullet
        return out


# ---------- тема ----------


def parse_theme(theme_part: Any) -> Theme:
    """Часть темы python-pptx (обычная Part с blob) или уже разобранный элемент."""
    if hasattr(theme_part, "_element"):
        root = theme_part._element
    elif hasattr(theme_part, "blob"):
        from lxml import etree

        root = etree.fromstring(theme_part.blob)
    else:
        root = theme_part
    colors: dict[str, str] = {}
    scheme = root.find(".//a:clrScheme", NS)
    if scheme is not None:
        for key in THEME_COLOR_KEYS:
            el = scheme.find(f"a:{key}", NS)
            if el is None:
                continue
            srgb = el.find("a:srgbClr", NS)
            sys_clr = el.find("a:sysClr", NS)
            if srgb is not None:
                colors[key] = f"#{srgb.get('val', '000000').upper()}"
            elif sys_clr is not None:
                last = sys_clr.get("lastClr")
                colors[key] = (
                    f"#{last.upper()}"
                    if last
                    else SYSTEM_COLORS.get(sys_clr.get("val", ""), "#000000")
                )
    fonts = root.find(".//a:fontScheme", NS)
    major = minor = "Calibri"
    if fonts is not None:
        mj = fonts.find("a:majorFont/a:latin", NS)
        mn = fonts.find("a:minorFont/a:latin", NS)
        major = (mj.get("typeface") if mj is not None else None) or major
        minor = (mn.get("typeface") if mn is not None else None) or minor
    theme_el = root if root.tag.endswith("}theme") else root.find(".//a:theme", NS)
    name = (theme_el.get("name") if theme_el is not None else None) or "Тема"
    widths: list[float] = []
    for ln in root.findall(".//a:fmtScheme/a:lnStyleLst/a:ln", NS):
        try:
            widths.append(round(int(ln.get("w", "9525")) / 12700, 3))
        except ValueError:
            widths.append(0.75)
    return Theme(
        name=name,
        colors=colors,
        major_font=major,
        minor_font=minor,
        line_widths_pt=widths,
    )


def clr_map_of(master_element: Any) -> dict[str, str]:
    mapping = dict(DEFAULT_CLR_MAP)
    el = master_element.find("p:clrMap", NS)
    if el is not None:
        for key in mapping:
            if el.get(key):
                mapping[key] = el.get(key)
    return mapping


def _apply_modifiers(hex_color: str, mods: list[tuple[str, float]]) -> str:
    r, g, b = (int(hex_color[i : i + 2], 16) / 255.0 for i in (1, 3, 5))
    h, lum, s = colorsys.rgb_to_hls(r, g, b)
    for name, val in mods:
        if name == "lumMod":
            lum = lum * val
        elif name == "lumOff":
            lum = lum + val
        elif name == "tint":
            # PowerPoint: tint приближает к белому
            r, g, b = colorsys.hls_to_rgb(h, lum, s)
            r, g, b = (1 - val * (1 - c) for c in (r, g, b))
            h, lum, s = colorsys.rgb_to_hls(r, g, b)
        elif name == "shade":
            r, g, b = colorsys.hls_to_rgb(h, lum, s)
            r, g, b = (c * val for c in (r, g, b))
            h, lum, s = colorsys.rgb_to_hls(r, g, b)
        elif name == "satMod":
            s = s * val
    lum = min(max(lum, 0.0), 1.0)
    s = min(max(s, 0.0), 1.0)
    r, g, b = colorsys.hls_to_rgb(h, lum, s)
    return "#{:02X}{:02X}{:02X}".format(*(round(c * 255) for c in (r, g, b)))


def resolve_color(
    color_parent: Any, theme: Theme | None, level: str = "shape"
) -> ResolvedColor | None:
    """Цвет из a:solidFill/a:rPr и т. п.: srgbClr, schemeClr с модификаторами, sysClr, prstClr."""
    if color_parent is None:
        return None
    srgb = color_parent.find("a:srgbClr", NS)
    scheme = color_parent.find("a:schemeClr", NS)
    sys_clr = color_parent.find("a:sysClr", NS)
    prst = color_parent.find("a:prstClr", NS)
    base: str | None = None
    theme_ref: str | None = None
    node = None
    if srgb is not None:
        base, node = f"#{srgb.get('val', '000000').upper()}", srgb
    elif scheme is not None:
        node = scheme
        key = scheme.get("val", "")
        theme_ref = key
        if theme is not None:
            mapped = theme.clr_map.get(key, key)
            base = theme.colors.get(mapped) or theme.colors.get(key)
        if base is None:
            base = (
                "#000000"
                if key.startswith(("tx", "dk"))
                else "#FFFFFF"
                if key.startswith(("bg", "lt"))
                else "#808080"
            )
    elif sys_clr is not None:
        base, node = f"#{sys_clr.get('lastClr', '000000').upper()}", sys_clr
    elif prst is not None:
        base, node = (
            {"black": "#000000", "white": "#FFFFFF"}.get(prst.get("val", ""), "#808080"),
            prst,
        )
    if base is None or node is None:
        return None
    mods: list[tuple[str, float]] = []
    names: list[str] = []
    for child in node:
        tag = child.tag.split("}")[-1]
        if tag in ("lumMod", "lumOff", "tint", "shade", "satMod", "alpha"):
            val = float(child.get("val", 100000)) / 100000.0
            names.append(f"{tag}={child.get('val')}")
            if tag != "alpha":
                mods.append((tag, val))
    return ResolvedColor(_apply_modifiers(base, mods) if mods else base, theme_ref, names, level)


def resolve_font_name(name: str | None, theme: Theme | None) -> str | None:
    if not name:
        return None
    if theme is None:
        return None if name.startswith("+") else name
    if name.startswith("+mj"):
        return theme.major_font
    if name.startswith("+mn"):
        return theme.minor_font
    return name


# ---------- наследование текста ----------


@dataclass
class _Level:
    """Свойства текста одного уровня списка (a:lvlNpPr / a:defRPr) с именем уровня наследования."""

    level: str
    part: str
    family: str | None = None
    size_pt: float | None = None
    bold: bool | None = None
    italic: bool | None = None
    color: ResolvedColor | None = None
    all_caps: bool | None = None
    line_spacing: float | None = None
    space_before_pt: float | None = None
    space_after_pt: float | None = None
    bullet: str | None = None
    bullet_char: str | None = None
    indent_emu: int | None = None


def _read_rpr(r_pr: Any, theme: Theme | None, level: str, part: str) -> _Level:
    out = _Level(level, part)
    if r_pr is None:
        return out
    if r_pr.get("sz"):
        out.size_pt = int(r_pr.get("sz")) / 100.0
    if r_pr.get("b") is not None:
        out.bold = r_pr.get("b") in ("1", "true")
    if r_pr.get("i") is not None:
        out.italic = r_pr.get("i") in ("1", "true")
    if r_pr.get("cap"):
        out.all_caps = r_pr.get("cap") == "all"
    latin = r_pr.find("a:latin", NS)
    if latin is not None and latin.get("typeface"):
        out.family = resolve_font_name(latin.get("typeface"), theme)
    solid = r_pr.find("a:solidFill", NS)
    if solid is not None:
        out.color = resolve_color(solid, theme, level)
    return out


def _read_ppr(p_pr: Any, theme: Theme | None, level: str, part: str) -> _Level:
    """a:pPr или a:lvlNpPr: интервалы, буллеты, отступы и defRPr."""
    out = _read_rpr(p_pr.find("a:defRPr", NS) if p_pr is not None else None, theme, level, part)
    if p_pr is None:
        return out
    ln = p_pr.find("a:lnSpc/a:spcPct", NS)
    if ln is not None:
        out.line_spacing = float(ln.get("val", 100000)) / 100000.0
    sb = p_pr.find("a:spcBef/a:spcPts", NS)
    if sb is not None:
        out.space_before_pt = float(sb.get("val", 0)) / 100.0
    sa = p_pr.find("a:spcAft/a:spcPts", NS)
    if sa is not None:
        out.space_after_pt = float(sa.get("val", 0)) / 100.0
    if p_pr.find("a:buNone", NS) is not None:
        out.bullet = "none"
    elif p_pr.find("a:buChar", NS) is not None:
        out.bullet, out.bullet_char = "char", p_pr.find("a:buChar", NS).get("char")
    elif p_pr.find("a:buAutoNum", NS) is not None:
        out.bullet = "number"
    elif p_pr.find("a:buBlip", NS) is not None:
        out.bullet = "picture"
    if p_pr.get("indent") is not None:
        out.indent_emu = int(p_pr.get("indent"))
    return out


def _list_style_levels(
    lst_style: Any, lvl: int, theme: Theme | None, level: str, part: str
) -> _Level | None:
    if lst_style is None:
        return None
    p_pr = lst_style.find(f"a:lvl{lvl + 1}pPr", NS)
    if p_pr is None:
        return None
    return _read_ppr(p_pr, theme, level, part)


class StyleResolver:
    """Цепочка наследования для конкретного слайда: его макет, мастер и тема."""

    def __init__(
        self,
        theme: Theme | None,
        master_element: Any,
        layout_element: Any | None,
        *,
        master_part: str = "",
        layout_part: str = "",
    ) -> None:
        self.theme = theme
        self.master = master_element
        self.layout = layout_element
        self.master_part = master_part
        self.layout_part = layout_part

    def _placeholder_chain(self, shape: ShapeInfo) -> list[tuple[Any, str, str]]:
        """Плейсхолдеры макета и мастера того же типа/idx: (lstStyle, уровень, часть)."""
        chain: list[tuple[Any, str, str]] = []
        if not shape.is_placeholder:
            return chain
        for container, level, part in (
            (self.layout, "layout", self.layout_part),
            (self.master, "master", self.master_part),
        ):
            if container is None:
                continue
            match = _find_placeholder(container, shape.placeholder_type, shape.placeholder_idx)
            if match is not None:
                lst = match.find("p:txBody/a:lstStyle", NS)
                chain.append((lst, level, part))
        return chain

    def _master_text_style(self, shape: ShapeInfo) -> tuple[Any, str]:
        tx_styles = self.master.find("p:txStyles", NS) if self.master is not None else None
        if tx_styles is None:
            return None, "master"
        if shape.placeholder_type in ("title", "ctrTitle"):
            return tx_styles.find("p:titleStyle", NS), "master"
        if shape.placeholder_type in ("body", "subTitle", "obj"):
            return tx_styles.find("p:bodyStyle", NS), "master"
        return tx_styles.find("p:otherStyle", NS), "master"

    def resolve(
        self, shape: ShapeInfo, paragraph: Paragraph, run: dict[str, Any] | None
    ) -> ResolvedText:
        lvl = paragraph.level
        chain: list[_Level] = []
        # фрагмент
        if run:
            r = _Level("run", "")
            r.size_pt = run.get("size_pt")
            r.bold = run.get("bold")
            r.italic = run.get("italic")
            r.family = resolve_font_name(run.get("family"), self.theme)
            r.all_caps = run.get("cap") == "all" if run.get("cap") else None
            if run.get("color") or run.get("scheme_color"):
                if run.get("color"):
                    r.color = ResolvedColor(run["color"], None, [], "run")
                else:
                    fake = _SchemeHolder(run["scheme_color"])
                    r.color = resolve_color(fake, self.theme, "run")
            chain.append(r)
        # абзац
        p = _Level("paragraph", "")
        p.line_spacing = paragraph.line_spacing
        p.space_before_pt = paragraph.space_before_pt
        p.space_after_pt = paragraph.space_after_pt
        p.bullet, p.bullet_char = paragraph.bullet, paragraph.bullet_char
        p.indent_emu = paragraph.indent_emu
        chain.append(p)
        # a:lstStyle самой фигуры
        el = shape.element
        if el is not None:
            lst = el.find(".//a:lstStyle", NS)
            own = _list_style_levels(lst, lvl, self.theme, "shape", "")
            if own:
                chain.append(own)
            # a:endParaRPr абзаца без фрагментов несёт кегль пустого плейсхолдера
            if not run:
                for p_el in el.findall(".//a:p", NS):
                    end = p_el.find("a:endParaRPr", NS)
                    if end is not None and end.get("sz"):
                        e = _read_rpr(end, self.theme, "paragraph", "")
                        chain.append(e)
                        break
        # плейсхолдеры макета и мастера
        for lst, level, part in self._placeholder_chain(shape):
            found = _list_style_levels(lst, lvl, self.theme, level, part)
            if found:
                chain.append(found)
        # стили текста мастера
        style, level = self._master_text_style(shape)
        if style is not None:
            found = _list_style_levels(style, lvl, self.theme, level, self.master_part)
            if found:
                chain.append(found)
            if lvl > 0:
                base = _list_style_levels(style, 0, self.theme, level, self.master_part)
                if base:
                    chain.append(base)
        return self._merge(chain, shape)

    def _merge(self, chain: list[_Level], shape: ShapeInfo) -> ResolvedText:
        theme = self.theme
        default_family = theme.minor_font if theme else "Calibri"
        if shape.placeholder_type in ("title", "ctrTitle") and theme:
            default_family = theme.major_font
        dark = theme.colors.get(theme.clr_map.get("tx1", "dk1"), "#000000") if theme else "#000000"
        out = ResolvedText(
            family=default_family,
            size_pt=18.0,
            bold=False,
            italic=False,
            color=dark,
            part=self.master_part,
        )

        def first(attr: str) -> _Level | None:
            for lvl in chain:
                if getattr(lvl, attr) is not None:
                    return lvl
            return None

        if (lvl := first("family")) is not None:
            out.family, out.family_source = str(lvl.family), lvl.level
        if (lvl := first("size_pt")) is not None:
            out.size_pt, out.size_source = float(lvl.size_pt or 18.0), lvl.level
        if (lvl := first("bold")) is not None:
            out.bold = bool(lvl.bold)
        if (lvl := first("italic")) is not None:
            out.italic = bool(lvl.italic)
        if (lvl := first("all_caps")) is not None:
            out.all_caps = bool(lvl.all_caps)
        if (lvl := first("color")) is not None and lvl.color is not None:
            out.color = lvl.color.hex
            out.color_source = lvl.level
            out.color_theme_ref = lvl.color.theme_ref
            out.color_modifiers = lvl.color.modifiers
        for attr in (
            "line_spacing",
            "space_before_pt",
            "space_after_pt",
            "bullet",
            "bullet_char",
            "indent_emu",
        ):
            if (lvl := first(attr)) is not None:
                setattr(out, attr, getattr(lvl, attr))
        if out.bullet is None and shape.placeholder_type in ("body", "obj"):
            out.bullet = "char"  # bodyStyle мастера по умолчанию маркированный
        return out


class _SchemeHolder:
    """Обёртка, чтобы resolve_color принял цвет схемы из словаря фрагмента."""

    def __init__(self, val: str) -> None:
        from lxml import etree

        self._el = etree.Element(f"{{{NS['a']}}}solidFill")
        etree.SubElement(self._el, f"{{{NS['a']}}}schemeClr").set("val", val)

    def find(self, path: str, ns: dict[str, str]) -> Any:
        return self._el.find(path, ns)


def _find_placeholder(container: Any, ph_type: str | None, idx: int | None) -> Any:
    """Плейсхолдер макета/мастера: сначала по idx, затем по типу; body ищет и obj."""
    candidates = container.findall(".//p:sp", NS)
    by_idx = None
    by_type = None
    for sp in candidates:
        ph = sp.find("p:nvSpPr/p:nvPr/p:ph", NS)
        if ph is None:
            continue
        t = ph.get("type", "body")
        i = int(ph.get("idx", 0))
        if idx is not None and idx == i and by_idx is None:
            by_idx = sp
        if by_type is None and (
            t == ph_type
            or (ph_type in ("body", "obj") and t in ("body", "obj"))
            or (ph_type == "ctrTitle" and t == "title")
            or (ph_type == "title" and t == "ctrTitle")
        ):
            by_type = sp
    return by_idx if by_idx is not None else by_type
