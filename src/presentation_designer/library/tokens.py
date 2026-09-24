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
from presentation_designer.parsing.template.tone import relative_luminance

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
    # Бледные акценты палитры: под заливку не годятся, но читаются текстом на тёмном фоне.
    light_accents: list[str] = field(default_factory=list)
    theme: dict[str, str] = field(default_factory=dict)
    card_geometry: str = "rect"
    corner_ratio: float = 0.0
    stroke_pt: float = 0.0
    shadow: bool = False
    margins: dict[str, float] = field(
        default_factory=lambda: {"left": 0.06, "right": 0.06, "top": 0.08, "bottom": 0.08}
    )
    gutter: float = 0.03
    # Заголовки содержательных образцов жирные: собственная композиция пишет так же.
    title_bold: bool = False
    # Цвет заголовка, если он у образцов свой (иначе — цвет текста).
    title_color: str | None = None
    # Акценты сняты со слайдов, а не с темы (тема файла с оформлением не связана).
    accents_from_slides: bool = False
    # Самый частый цвет объектов слайдов: заливка шапки таблицы и т. п.
    primary: str | None = None
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
        _read_content_sizes(code, profile)
        _read_colors(code, tokens.get("colors") or {})
        _read_sample_styles(code, profile)
        _read_spacing(code, tokens.get("spacing") or {})
        _read_shape(code, tokens.get("shape") or {})
        return code


# Кегли на дюйм ширины слайда: нижняя граница читаемости основного текста и подписей и
# коридор заголовка содержательного слайда. На широком слайде 13,33″ это 14 pt текста,
# 11 pt подписи и заголовок 21–40 pt; на 10″ — 10,5 pt, 8 pt и 16–30 pt.
BODY_PT_PER_INCH = 1.05
CAPTION_PT_PER_INCH = 0.8
TITLE_PT_PER_INCH = (1.6, 3.0)
SERVICE_ROLES = ("title", "section_divider", "thanks", "qr", "agenda")


def _read_content_sizes(code: DesignCode, profile: JsonDict) -> None:
    """Кегли содержательных слайдов — медианы по слотам образцов шаблона, а не крайние ступени
    шкалы: у шкалы наибольший «заголовок» — это кегль обложки (66 pt у VK WorkSpace), а
    наименьший «текст» — сноски (8 pt у VK Tech). В своей композиции первый не помещает
    заголовок-вывод, второй не читается."""
    width_in = float((profile.get("slide_size") or {}).get("width_emu") or 12192000) / 914400
    sizes: dict[str, list[float]] = {}
    for pattern in profile.get("patterns") or []:
        if (pattern.get("source") or {}).get("kind") == "builtin":
            continue
        if pattern.get("role") in SERVICE_ROLES:
            continue
        for slot in pattern.get("slots") or []:
            size = (slot.get("font") or {}).get("size_pt")
            if size:
                sizes.setdefault(str(slot.get("kind")), []).append(float(size))

    def median(kind: str) -> float | None:
        values = sorted(sizes.get(kind) or [])
        return values[len(values) // 2] if values else None

    scale = sorted(
        {
            float(step["size_pt"])
            for step in ((profile.get("design_tokens") or {}).get("typography") or {}).get("scale")
            or []
            if step.get("size_pt")
        }
    )

    def snap(value: float, floor: float = 0.0, ceiling: float = 1000.0) -> float:
        """Ближайшая ступень шкалы шаблона в коридоре; без подходящей — само значение."""
        steps = [s for s in scale if floor - 0.05 <= s <= ceiling + 0.05]
        return min(steps, key=lambda s: abs(s - value)) if steps else round(value, 1)

    low, high = (k * width_in for k in TITLE_PT_PER_INCH)
    title = min(max(median("title") or code.title_pt, low), high)
    code.title_pt = snap(title, low, high)
    body_floor = BODY_PT_PER_INCH * width_in
    body = max(median("body") or median("bullets") or code.body_pt, body_floor)
    code.body_pt = snap(body, body_floor, max(body, body_floor) * 1.3)
    caption_floor = CAPTION_PT_PER_INCH * width_in
    code.caption_pt = snap(
        max(min(code.caption_pt, code.body_pt), caption_floor), caption_floor, code.body_pt
    )
    subtitle = min(max(code.subtitle_pt, code.body_pt * 1.2), code.title_pt * 0.8)
    code.subtitle_pt = snap(subtitle, code.body_pt, code.title_pt)
    code.number_pt = snap(max(code.number_pt, code.title_pt * 1.4), code.title_pt * 1.2)


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
    # A neutral swatch can be a pale fill, not a legible caption color.
    bg = relative_luminance(code.background)
    fg = relative_luminance(code.muted_color)
    if (max(bg, fg) + 0.05) / (min(bg, fg) + 0.05) < 4.5:
        code.muted_color = code.text_color
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
    code.light_accents = [
        str(hex_color)
        for hex_color in (by_role.get("primary") or [])
        + (by_role.get("accent") or [])
        + [str(theme[k]) for k in sorted(theme) if k.startswith("accent") and theme.get(k)]
        if luminance(hex_color) >= 0.88
    ]
    code.theme = {str(k): str(v) for k, v in theme.items() if isinstance(v, str)}


def content_samples(profile: JsonDict) -> list[JsonDict]:
    """Содержательные образцы шаблона (без обложки, разделителей и финала) преобладающего
    тона. Их вид — то, как автор оформляет обычный слайд, и собственная композиция обязана
    выглядеть так же, а не как пустой макет."""
    samples = [
        p
        for p in profile.get("patterns") or []
        if (p.get("source") or {}).get("kind") == "sample_slide"
        and p.get("role") not in SERVICE_ROLES
    ]
    tones = [str((p.get("tone") or {}).get("background") or "") for p in samples]
    known = [t for t in tones if t in ("light", "dark")]
    if not known:
        return samples
    major = max(("light", "dark"), key=known.count)
    return [p for p, t in zip(samples, tones, strict=True) if t == major]


def content_tone(profile: JsonDict) -> JsonDict | None:
    """Тон фона содержательных образцов: светлый или тёмный и медианная яркость."""
    values = sorted(
        float((p.get("tone") or {})["luminance"])
        for p in content_samples(profile)
        if (p.get("tone") or {}).get("luminance") is not None
    )
    if not values:
        return None
    luminance = values[len(values) // 2]
    return {"background": "light" if luminance >= 0.5 else "dark", "luminance": luminance}


def _most_common(values: list[str]) -> str | None:
    counted = [v for v in values if v]
    return max(dict.fromkeys(counted), key=counted.count) if counted else None


def _read_sample_styles(code: DesignCode, profile: JsonDict) -> None:
    """Начертание заголовка — с содержательных образцов; цвета — тоже с них, если тема файла
    с оформлением не связана.

    Тема часто не имеет к оформлению отношения: шаблон, собранный генератором или перенесённый
    из другого файла, несёт тему Office (синий акцент, чёрный текст, белый фон), а настоящие
    цвета заданы прямо на объектах слайдов. Без этого собственная композиция выходила белой с
    чёрным текстом и синими плашками Office рядом с тёмно-синими заголовками образцов.
    Признак отвязанной темы — ни один акцент темы не встречается на слайдах; у шаблонов, где
    тема и слайды согласованы (VK: фирменный синий — accent1 и он же на объектах), цвета
    остаются из темы и палитры, как раньше.
    """
    samples = content_samples(profile)
    if not samples:
        return
    title_colors: list[str] = []
    body_colors: list[str] = []
    bold: list[bool] = []
    for pattern in samples:
        for slot in pattern.get("slots") or []:
            font = slot.get("font") or {}
            color = str(font.get("color") or "").upper()
            if not color.startswith("#") or len(color) != 7:
                color = ""
            if slot.get("kind") == "title":
                title_colors.append(color)
                bold.append(bool(font.get("bold")))
            elif slot.get("kind") in ("body", "bullets", "subtitle", "caption"):
                body_colors.append(color)
    if bold:
        code.title_bold = sum(bold) * 2 > len(bold)
    if not theme_detached(profile):
        return
    tone = content_tone(profile)
    dark = tone is not None and tone["background"] == "dark"

    def readable(color: str | None) -> bool:
        return bool(color) and (relative_luminance(str(color)) < 0.5) != dark

    title = _most_common(title_colors)
    if readable(title):
        code.title_color = str(title)
        code.text_color = str(title)
    body = _most_common([c for c in body_colors if c != code.title_color])
    if readable(body):
        code.muted_color = str(body)
    palette = ((profile.get("design_tokens") or {}).get("colors") or {}).get("palette") or []
    text = {code.text_color.upper(), code.muted_color.upper()}
    used = sorted(
        (
            e
            for e in palette
            if e.get("source") != "theme"
            and e.get("role") not in ("muted", "background", "text")
            and e.get("hex")
            and str(e["hex"]).upper() not in text
            and luminance(str(e["hex"])) < 0.88
        ),
        key=lambda e: -int(e.get("usage_count") or 0),
    )
    own = list(dict.fromkeys(str(e["hex"]).upper() for e in used))
    if len(own) >= 3:
        # Порядок цветов для рядов — как у нумерованных шагов образцов (01 красный, 02
        # оранжевый…): так карточки и узлы схемы окрашиваются в той же последовательности.
        code.primary = own[0]
        code.accents = list(dict.fromkeys([*_series(samples), *own]))
        code.accents_from_slides = True


def _series(samples: list[JsonDict]) -> list[str]:
    """Самая длинная последовательность цветов номеров шагов одного образца."""
    best: list[str] = []
    for pattern in samples:
        numbers = [
            slot
            for slot in pattern.get("slots") or []
            if slot.get("kind") == "number"
            and str((slot.get("font") or {}).get("color") or "").startswith("#")
        ]
        numbers.sort(key=lambda slot: _natural(str(slot.get("slot_id") or "")))
        colors = list(
            dict.fromkeys(str((slot.get("font") or {})["color"]).upper() for slot in numbers)
        )
        colors = [c for c in colors if luminance(c) < 0.88]
        if len(colors) > len(best):
            best = colors
    return best if len(best) >= 3 else []


def _natural(value: str) -> tuple[str, int]:
    head = value.rstrip("0123456789")
    tail = value[len(head) :]
    return head, int(tail) if tail else 0


def theme_detached(profile: JsonDict) -> bool:
    """Акценты темы не встречаются ни на одном слайде шаблона: тема досталась файлу от
    генератора или другой презентации и о его оформлении ничего не говорит."""
    palette = ((profile.get("design_tokens") or {}).get("colors") or {}).get("palette") or []
    theme = ((profile.get("design_tokens") or {}).get("colors") or {}).get("theme") or {}
    accents = {str(v).upper() for k, v in theme.items() if k.startswith("accent") and v}
    if not accents:
        return False
    for entry in palette:
        if str(entry.get("hex") or "").upper() in accents and int(entry.get("usage_count") or 0):
            return False
    return any(e.get("source") != "theme" for e in palette)


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
