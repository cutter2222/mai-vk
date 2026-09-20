"""Детерминированные проверки по ComposedDeck.

Считаются по тому, что уже есть в файле: рамки объектов в долях холста, вычисленные стили
текста, результат измерения ёмкости, ссылка на макет, происхождение объекта. На одном и том
же слайде проверка всегда даёт один и тот же ответ — это и отличает её от контекстной.

Два правила, общие для всех проверок:

1. Постоянные элементы шаблона (`role: fixed` — логотип, колонтитул, декор) не судятся по
   правилам содержания: они стоят в полях и налезают друг на друга по замыслу автора
   шаблона. Для них есть своя проверка — не сдвинуты ли они с места.
2. Объекты, оставшиеся от образца (`content_source: sample`), проверяются мягче: их текст и
   оформление выбирал не сервис. Исключение — заглушки и пустые слайды, где важен результат,
   а не авторство.
"""

from __future__ import annotations

import math
import pathlib
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any, NamedTuple
from xml.etree import ElementTree as ET

from presentation_designer.audit.registry import threshold

JsonDict = dict[str, Any]

# Мелкие объекты (точки навигации, разделительные линии) в проверках геометрии не участвуют.
TINY_AREA = 0.0015


@dataclass
class Issue:
    """Находка: что не так, где и с каким измерением."""

    check_id: str
    message: str
    slide_id: str | None = None
    slide_index: int | None = None
    bbox: JsonDict | None = None
    element_ids: list[str] = field(default_factory=list)
    evidence: JsonDict = field(default_factory=dict)


@dataclass
class Context:
    """Всё, по чему судят проверки: колода, профиль шаблона и производные множества."""

    deck: JsonDict
    profile: JsonDict

    def __post_init__(self) -> None:
        tokens = self.profile.get("design_tokens") or {}
        typography = tokens.get("typography") or {}
        colors = tokens.get("colors") or {}
        self.fonts = {
            str(f.get("family", "")).strip().lower()
            for f in typography.get("fonts") or []
            if f.get("family")
        }
        # Подмена рендерера — не нарушение шаблона: в файле остаётся исходное имя шрифта.
        self.fonts |= {
            str(f.get("fallback", "")).strip().lower()
            for f in typography.get("fonts") or []
            if f.get("fallback")
        }
        self.sizes = sorted(
            {float(s["size_pt"]) for s in typography.get("scale") or [] if s.get("size_pt")}
        )
        self.palette = [
            _rgb(str(c["hex"]))
            for c in colors.get("palette") or []
            if str(c.get("hex", "")).startswith("#")
        ]
        self.palette += [
            _rgb(str(v)) for v in (colors.get("theme") or {}).values() if str(v).startswith("#")
        ]
        self.layouts = {str(item.get("layout_id")) for item in self.profile.get("layouts") or []}
        self.margins = (tokens.get("spacing") or {}).get("margins") or {}
        self.fixed = _fixed_places(self.profile)


# ---------- вспомогательное ----------


class FixedPlace(NamedTuple):
    """Место постоянного элемента шаблона: вид и рамка в долях холста."""

    kind: str
    x: float
    y: float
    width: float
    height: float


FIXED_KIND_LABELS = {
    "logo": "Логотип",
    "footer": "Колонтитул",
    "page_number": "Номер страницы",
    "date": "Дата",
    "background": "Фон",
}

# Допуск размера при опознании элемента: колонтитул на слайде может быть чуть шире, чем в
# мастере, но не вдвое. Нижняя граница нужна мелким элементам вроде номера страницы.
SIZE_TOLERANCE = 0.2
SIZE_TOLERANCE_MIN = 0.01
# Меньше этого по любой стороне — линия или полоска декора: опознавать её по размеру нечем.
MIN_FIXED_SIZE = 0.004


def _fixed_places(profile: JsonDict) -> list[FixedPlace]:
    """Уникальные места постоянных элементов: один и тот же колонтитул стоит во всех макетах."""
    seen: dict[tuple[str, float, float, float, float], FixedPlace] = {}
    for item in profile.get("fixed_elements") or []:
        box = item.get("bbox") or {}
        if not box:
            continue
        place = FixedPlace(
            str(item.get("kind") or "элемент"),
            round(float(box.get("x", 0.0)), 4),
            round(float(box.get("y", 0.0)), 4),
            round(float(box.get("width", 0.0)), 4),
            round(float(box.get("height", 0.0)), 4),
        )
        if place.width < MIN_FIXED_SIZE or place.height < MIN_FIXED_SIZE:
            continue
        seen[(place.kind, place.x, place.y, place.width, place.height)] = place
    return list(seen.values())


def _size_close(place: FixedPlace, width: float, height: float) -> bool:
    return _same_size(place.width, place.height, width, height)


def _same_size(width_a: float, height_a: float, width_b: float, height_b: float) -> bool:
    return abs(width_b - width_a) <= max(SIZE_TOLERANCE_MIN, width_a * SIZE_TOLERANCE) and abs(
        height_b - height_a
    ) <= max(SIZE_TOLERANCE_MIN, height_a * SIZE_TOLERANCE)


def _rgb(value: str) -> tuple[int, int, int]:
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    try:
        return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    except ValueError:
        return (0, 0, 0)


def _luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.04045 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fore: str, back: str) -> float:
    """Контраст по WCAG 2.1: от 1 (нет разницы) до 21 (чёрное на белом)."""
    a, b = _luminance(_rgb(fore)), _luminance(_rgb(back))
    light, dark = max(a, b), min(a, b)
    return (light + 0.05) / (dark + 0.05)


def _box(obj: JsonDict) -> tuple[float, float, float, float]:
    b = obj.get("bbox") or {}
    return (
        float(b.get("x", 0.0)),
        float(b.get("y", 0.0)),
        float(b.get("width", 0.0)),
        float(b.get("height", 0.0)),
    )


def _area(obj: JsonDict) -> float:
    _, _, w, h = _box(obj)
    return max(w, 0.0) * max(h, 0.0)


def _plain(obj: JsonDict) -> str:
    return str((obj.get("text") or {}).get("plain") or "").strip()


def _is_content(obj: JsonDict) -> bool:
    return obj.get("role") != "fixed"


def _paragraph_styles(obj: JsonDict) -> list[JsonDict]:
    return [p.get("style") or {} for p in ((obj.get("text") or {}).get("paragraphs") or [])]


def _slide_ref(slide: JsonDict) -> dict[str, Any]:
    return {"slide_id": str(slide.get("slide_id") or ""), "slide_index": int(slide.get("index", 0))}


def _user_moved(obj: JsonDict) -> bool:
    """Геометрию объекта задал пользователь: сдвинул его в редакторе или добавил свою надпись."""
    return any(
        str(o.get("op") or "") in ("geometry", "add_text") for o in obj.get("user_overrides") or []
    )


# ---------- вёрстка ----------


def check_out_of_bounds(slide: JsonDict, ctx: Context) -> list[Issue]:
    tol = float(threshold("layout.out_of_bounds", "tolerance", 0.005))
    out: list[Issue] = []
    for obj in slide.get("objects") or []:
        if not _is_content(obj) or _area(obj) < TINY_AREA:
            continue
        x, y, w, h = _box(obj)
        over = max(-x, -y, x + w - 1.0, y + h - 1.0)
        if over > tol:
            out.append(
                Issue(
                    "layout.out_of_bounds",
                    f"Объект «{obj.get('name') or obj.get('object_id')}» выходит за край слайда",
                    bbox=obj.get("bbox"),
                    element_ids=[str(obj.get("object_id"))],
                    evidence={"measured": round(over, 4), "threshold": tol},
                    **_slide_ref(slide),
                )
            )
    return out


def check_overlap(slide: JsonDict, ctx: Context) -> list[Issue]:
    ratio_limit = float(threshold("layout.overlap", "min_overlap_ratio", 0.12))
    objects = [
        o
        for o in slide.get("objects") or []
        if _is_content(o) and _area(o) >= TINY_AREA and (o.get("kind") == "text" and _plain(o))
    ]
    out: list[Issue] = []
    for i, a in enumerate(objects):
        ax, ay, aw, ah = _box(a)
        for b in objects[i + 1 :]:
            bx, by, bw, bh = _box(b)
            ow = min(ax + aw, bx + bw) - max(ax, bx)
            oh = min(ay + ah, by + bh) - max(ay, by)
            if ow <= 0 or oh <= 0:
                continue
            ratio = (ow * oh) / max(min(_area(a), _area(b)), 1e-9)
            if ratio >= ratio_limit:
                out.append(
                    Issue(
                        "layout.overlap",
                        f"Блоки «{a.get('name') or a.get('object_id')}» и "
                        f"«{b.get('name') or b.get('object_id')}» накладываются",
                        bbox={"x": max(ax, bx), "y": max(ay, by), "width": ow, "height": oh},
                        element_ids=[str(a.get("object_id")), str(b.get("object_id"))],
                        evidence={"measured": round(ratio, 3), "threshold": ratio_limit},
                        **_slide_ref(slide),
                    )
                )
    return out


def check_text_overflow(slide: JsonDict, ctx: Context) -> list[Issue]:
    out: list[Issue] = []
    for obj in slide.get("objects") or []:
        fit = obj.get("fit") or {}
        if not fit or obj.get("kind") != "text":
            continue
        lines, max_lines = fit.get("lines"), fit.get("max_lines")
        overflow = str(fit.get("action") or "") == "overflow" or (
            isinstance(lines, int)
            and isinstance(max_lines, int)
            and max_lines
            and lines > max_lines
        )
        if overflow:
            out.append(
                Issue(
                    "layout.text_overflow",
                    f"Текст в «{obj.get('slot_id') or obj.get('name')}» не помещается в рамку",
                    bbox=obj.get("bbox"),
                    element_ids=[str(obj.get("object_id"))],
                    evidence={
                        "measured": lines,
                        "threshold": max_lines,
                        "details": str(fit.get("action") or ""),
                    },
                    **_slide_ref(slide),
                )
            )
    return out


def check_clipped(slide: JsonDict, ctx: Context) -> list[Issue]:
    """Текст, край рамки которого уходит за холст: буквы срезаются границей слайда."""
    tol = float(threshold("layout.clipped", "tolerance", 0.005))
    out: list[Issue] = []
    for obj in slide.get("objects") or []:
        if obj.get("kind") != "text" or not _plain(obj) or not _is_content(obj):
            continue
        x, y, w, h = _box(obj)
        if x < -tol or y < -tol or x + w > 1.0 + tol or y + h > 1.0 + tol:
            out.append(
                Issue(
                    "layout.clipped",
                    f"Текст «{_plain(obj)[:40]}…» обрезан краем слайда",
                    bbox=obj.get("bbox"),
                    element_ids=[str(obj.get("object_id"))],
                    evidence={"measured": {"x": round(x, 3), "y": round(y, 3)}},
                    **_slide_ref(slide),
                )
            )
    return out


def check_margins(slide: JsonDict, ctx: Context) -> list[Issue]:
    if not ctx.margins:
        return []
    tol = float(threshold("layout.margins", "tolerance", 0.01))
    left = float(ctx.margins.get("left", 0)) - tol
    right = 1.0 - float(ctx.margins.get("right", 0)) + tol
    top = float(ctx.margins.get("top", 0)) - tol
    bottom = 1.0 - float(ctx.margins.get("bottom", 0)) + tol
    out: list[Issue] = []
    for obj in slide.get("objects") or []:
        # Проверять есть смысл только там, где геометрию задал сервис или пользователь.
        # Объект, заполнивший слот образца (у него есть source_object_id), стоит на месте,
        # которое выбрал автор шаблона: предъявлять ему его же вёрстку незачем — пока
        # пользователь не сдвинул его в редакторе.
        inherited = bool(obj.get("source_object_id")) and not _user_moved(obj)
        if (
            obj.get("content_source") not in ("plan", "generated", "user")
            or inherited
            or _area(obj) < TINY_AREA
        ):
            continue
        x, y, w, h = _box(obj)
        if x < left or y < top or x + w > right or y + h > bottom:
            out.append(
                Issue(
                    "layout.margins",
                    f"Объект «{obj.get('name') or obj.get('object_id')}» заходит в поля шаблона",
                    bbox=obj.get("bbox"),
                    element_ids=[str(obj.get("object_id"))],
                    evidence={
                        "measured": {"x": round(x, 3), "y": round(y, 3)},
                        "threshold": ctx.margins,
                    },
                    **_slide_ref(slide),
                )
            )
    return out


def check_image_distorted(slide: JsonDict, ctx: Context) -> list[Issue]:
    """Картинка вставлена с иными пропорциями, чем у исходного файла."""
    limit = float(threshold("layout.image_distorted", "max_ratio_delta", 0.08))
    size = ctx.deck.get("slide_size") or {}
    slide_ratio = float(size.get("width_emu") or 0) / max(float(size.get("height_emu") or 1), 1)
    out: list[Issue] = []
    for obj in slide.get("objects") or []:
        pic = obj.get("picture") or {}
        nw = float(pic.get("natural_width_px") or 0)
        nh = float(pic.get("natural_height_px") or 0)
        if obj.get("kind") != "picture" or nw <= 0 or nh <= 0 or pic.get("fit") == "cover":
            continue
        _, _, w, h = _box(obj)
        if w <= 0 or h <= 0:
            continue
        placed = (w * slide_ratio) / h
        delta = abs(placed - nw / nh) / max(nw / nh, 1e-9)
        if delta > limit:
            out.append(
                Issue(
                    "layout.image_distorted",
                    "Картинка растянута: пропорции не совпадают с исходником",
                    bbox=obj.get("bbox"),
                    element_ids=[str(obj.get("object_id"))],
                    evidence={"measured": round(delta, 3), "threshold": limit},
                    **_slide_ref(slide),
                )
            )
    return out


# ---------- шаблон ----------


def check_fonts(slide: JsonDict, ctx: Context) -> list[Issue]:
    """Гарнитура не из шаблона и больше двух гарнитур на слайде."""
    if not ctx.fonts:
        return []
    out: list[Issue] = []
    families: set[str] = set()
    for obj in slide.get("objects") or []:
        if obj.get("kind") != "text" or not _plain(obj):
            continue
        for style in _paragraph_styles(obj):
            family = str((style.get("font") or {}).get("family") or "").strip()
            if not family:
                continue
            families.add(family)
            if family.lower() not in ctx.fonts and _is_content(obj):
                out.append(
                    Issue(
                        "template.font_not_in_template",
                        f"Шрифт «{family}» не из шаблона",
                        bbox=obj.get("bbox"),
                        element_ids=[str(obj.get("object_id"))],
                        evidence={"measured": family, "threshold": sorted(ctx.fonts)},
                        **_slide_ref(slide),
                    )
                )
    max_families = int(threshold("template.font_families", "max_families", 2))
    if len(families) > max_families:
        out.append(
            Issue(
                "template.font_families",
                f"На слайде {len(families)} гарнитуры: {', '.join(sorted(families))}",
                evidence={"measured": len(families), "threshold": max_families},
                **_slide_ref(slide),
            )
        )
    return out


def check_sizes(slide: JsonDict, ctx: Context) -> list[Issue]:
    if not ctx.sizes:
        return []
    tol = float(threshold("template.size_not_in_scale", "tolerance_pt", 0.6))
    out: list[Issue] = []
    for obj in slide.get("objects") or []:
        if obj.get("kind") != "text" or not _plain(obj) or not _is_content(obj):
            continue
        if obj.get("content_source") != "plan":
            continue
        # Лестница ёмкости понижает кегль ступенями от размера слота: это предусмотренное
        # поведение, и промежуточное значение здесь не нарушение шкалы шаблона.
        fit = obj.get("fit") or {}
        if str(fit.get("action") or "") in ("font_step", "shrink", "split"):
            continue
        allowed = list(ctx.sizes)
        if isinstance(fit.get("slot_size_pt"), (int, float)):
            allowed.append(float(fit["slot_size_pt"]))
        for style in _paragraph_styles(obj):
            size = (style.get("font") or {}).get("size_pt")
            if not isinstance(size, (int, float)):
                continue
            if min(abs(float(size) - s) for s in allowed) > tol:
                out.append(
                    Issue(
                        "template.size_not_in_scale",
                        f"Кегль {size} pt не из шкалы шаблона",
                        bbox=obj.get("bbox"),
                        element_ids=[str(obj.get("object_id"))],
                        evidence={"measured": size, "threshold": allowed},
                        **_slide_ref(slide),
                    )
                )
                break
    return out


def check_colors(slide: JsonDict, ctx: Context) -> list[Issue]:
    if not ctx.palette:
        return []
    limit = float(threshold("template.color_not_in_palette", "max_distance", 24))
    out: list[Issue] = []
    for obj in slide.get("objects") or []:
        if obj.get("kind") != "text" or not _plain(obj) or not _is_content(obj):
            continue
        if obj.get("content_source") == "sample":
            continue
        for style in _paragraph_styles(obj):
            color = (style.get("font") or {}).get("color")
            if not isinstance(color, str) or not color.startswith("#"):
                continue
            rgb = _rgb(color)
            distance = min(math.dist(rgb, p) for p in ctx.palette)
            if distance > limit:
                out.append(
                    Issue(
                        "template.color_not_in_palette",
                        f"Цвет текста {color} не из палитры шаблона",
                        bbox=obj.get("bbox"),
                        element_ids=[str(obj.get("object_id"))],
                        evidence={
                            "measured": color,
                            "threshold": limit,
                            "details": f"расстояние {distance:.0f}",
                        },
                        **_slide_ref(slide),
                    )
                )
                break
    return out


def check_layout(slide: JsonDict, ctx: Context) -> list[Issue]:
    layout_id = str(slide.get("layout_id") or "")
    if not ctx.layouts or not layout_id or layout_id in ctx.layouts:
        return []
    return [
        Issue(
            "template.layout_not_from_template",
            f"Слайд собран на макете «{layout_id}», которого нет в шаблоне",
            evidence={"measured": layout_id, "threshold": sorted(ctx.layouts)[:8]},
            **_slide_ref(slide),
        )
    ]


def check_fixed_elements(slide: JsonDict, ctx: Context) -> list[Issue]:
    """Логотип, колонтитул и номер страницы должны стоять там же, где в шаблоне.

    Элемент на слайде узнаётся по размеру, а не по номеру фигуры. Номера постоянных
    элементов профиля взяты из мастера и макетов, номера объектов слайда — из слайда: это
    разные части пакета, и совпадение номеров ничего не значит. Со сверкой по номеру
    карточка композиции с номером 10 сравнивалась с колонтитулом мастера и попадала в отчёт
    как «колонтитул сдвинут» — 54 находки из 80 на шаблоне ЛЦТ были такими.
    """
    if not ctx.fixed:
        return []
    limit = float(threshold("template.fixed_element_moved", "max_shift", 0.01))
    boxes = [_box(o) for o in slide.get("objects") or [] if o.get("role") == "fixed"]
    out: list[Issue] = []
    for obj in slide.get("objects") or []:
        if obj.get("role") != "fixed":
            continue
        x, y, w, h = _box(obj)
        # Линии и другие фигуры без толщины: сравнивать по размеру нечего, а рамка находки
        # высотой в ноль пикселей в интерфейсе всё равно не видна.
        if w < MIN_FIXED_SIZE or h < MIN_FIXED_SIZE:
            continue
        # Того же размера, что и место в шаблоне: иначе это не тот элемент, а другая фигура.
        same_size = [place for place in ctx.fixed if _size_close(place, w, h)]
        if not same_size:
            continue
        # На слайде несколько фигур такого же размера — какая из них постоянный элемент
        # шаблона, определить нечем: ряд одинаковых иконок иначе целиком уходил в отчёт
        # как сдвинутые логотипы.
        if sum(1 for b in boxes if _same_size(b[2], b[3], w, h)) > 1:
            continue
        best = min(same_size, key=lambda place: max(abs(x - place.x), abs(y - place.y)))
        shift = max(abs(x - best.x), abs(y - best.y))
        if shift <= limit:
            continue
        out.append(
            Issue(
                "template.fixed_element_moved",
                f"{FIXED_KIND_LABELS.get(best.kind, best.kind)} сдвинут с места шаблона",
                bbox=obj.get("bbox"),
                element_ids=[str(obj.get("object_id"))],
                evidence={
                    "measured": round(shift, 4),
                    "threshold": limit,
                    "details": (
                        f"в шаблоне стоит на {best.x:.2f} × {best.y:.2f} холста, "
                        f"на слайде — на {x:.2f} × {y:.2f}"
                    ),
                },
                **_slide_ref(slide),
            )
        )
    return out


def check_contrast(slide: JsonDict, ctx: Context) -> list[Issue]:
    """Контраст текста к тому, что под ним: заливке блока, фону слайда или макета."""
    minimum = float(threshold("template.contrast", "min_ratio", 4.5))
    background = _slide_background(slide, ctx)
    out: list[Issue] = []
    for obj in slide.get("objects") or []:
        if obj.get("kind") != "text" or not _plain(obj) or not _is_content(obj):
            continue
        under = _fill_under(obj, slide) or background
        if not under:
            continue
        for style in _paragraph_styles(obj):
            font = style.get("font") or {}
            color = font.get("color")
            if not isinstance(color, str) or not color.startswith("#"):
                continue
            ratio = contrast_ratio(color, under)
            # WCAG различает обычный и крупный текст: от 24 pt (или 18 pt полужирного)
            # достаточно 3:1. Иначе крупные числа акцентом шаблона считались бы ошибкой,
            # хотя читаются они отлично.
            size = float(font.get("size_pt") or 0)
            large = size >= 24 or (size >= 18 and bool(font.get("bold")))
            limit = 3.0 if large else minimum
            if ratio < limit:
                out.append(
                    Issue(
                        "template.contrast",
                        f"Контраст текста {color} к фону {under} — {ratio:.1f}:1",
                        bbox=obj.get("bbox"),
                        element_ids=[str(obj.get("object_id"))],
                        evidence={
                            "measured": round(ratio, 2),
                            "threshold": limit,
                            "details": f"{'крупный' if large else 'обычный'} текст, {size:g} pt",
                        },
                        **_slide_ref(slide),
                    )
                )
                break
    return out


def _slide_background(slide: JsonDict, ctx: Context) -> str | None:
    """Цвет фона слайда или None, если цвета нет.

    Картинку во весь слайд одним цветом не опишешь, а прежний запасной путь подставлял
    светлый цвет темы: светло-серый заголовок на тёмной фотографии сверялся с белым и
    попадал в отчёт как контраст 1,1:1. Текст на фотографии проверяет контекстная часть
    аудита по картинке слайда, детерминированная о нём молчит.
    """
    bg = slide.get("background") or {}
    if isinstance(bg.get("color"), str) and bg["color"].startswith("#"):
        return str(bg["color"])
    if bg.get("kind") in ("image", "gradient", "pattern"):
        return None
    tokens = (ctx.profile.get("design_tokens") or {}).get("colors") or {}
    for entry in tokens.get("palette") or []:
        if entry.get("role") == "background" and str(entry.get("hex", "")).startswith("#"):
            return str(entry["hex"])
    theme = tokens.get("theme") or {}
    return str(theme.get("lt1")) if str(theme.get("lt1", "")).startswith("#") else None


def _fill_under(obj: JsonDict, slide: JsonDict) -> str | None:
    """Заливка, на которой лежит текст: своя заливка фигуры или плашка под ней.

    Плашкой может быть и фигура с текстом: в шаблонах заголовок часто стоит на скруглённом
    прямоугольнике, у которого собственный текст пуст. Пока такие фигуры пропускались,
    белый заголовок на фирменной плашке сверялся с белым фоном слайда и попадал в отчёт с
    контрастом 1,0:1. Берётся ближайшая плашка сверху стопки, но ниже самого текста.
    """
    own = (obj.get("fill") or {}).get("color")
    if isinstance(own, str) and own.startswith("#"):
        return own
    x, y, w, h = _box(obj)
    cx, cy = x + w / 2, y + h / 2
    own_z = int(obj.get("z_order") or 0)
    best: tuple[int, str] | None = None
    for other in slide.get("objects") or []:
        if other is obj:
            continue
        z = int(other.get("z_order") or 0)
        if z >= own_z:
            continue
        fill = (other.get("fill") or {}).get("color")
        if not isinstance(fill, str) or not fill.startswith("#"):
            continue
        ox, oy, ow, oh = _box(other)
        if ox <= cx <= ox + ow and oy <= cy <= oy + oh and (best is None or z > best[0]):
            best = (z, fill)
    return best[1] if best else None


# ---------- плотность ----------


def check_density(slide: JsonDict, ctx: Context) -> list[Issue]:
    """Буллеты, длина пункта, размер таблицы, число серий диаграммы."""
    out: list[Issue] = []
    max_bullets = int(threshold("density.bullets", "max_bullets", 6))
    max_words = int(threshold("density.bullet_length", "max_words", 15))
    bullets = 0
    for obj in slide.get("objects") or []:
        if obj.get("kind") == "text":
            for para in (obj.get("text") or {}).get("paragraphs") or []:
                text = str(para.get("text") or "").strip()
                if not text:
                    continue
                if para.get("bullet"):
                    bullets += 1
                    words = len(text.split())
                    if words > max_words:
                        out.append(
                            Issue(
                                "density.bullet_length",
                                f"Пункт из {words} слов: «{text[:50]}…»",
                                bbox=obj.get("bbox"),
                                element_ids=[str(obj.get("object_id"))],
                                evidence={"measured": words, "threshold": max_words},
                                **_slide_ref(slide),
                            )
                        )
        table = obj.get("table") or {}
        if table:
            rows, cols = int(table.get("rows") or 0), int(table.get("cols") or 0)
            max_rows = int(threshold("density.table_size", "max_rows", 7))
            max_cols = int(threshold("density.table_size", "max_cols", 5))
            if rows > max_rows or cols > max_cols:
                out.append(
                    Issue(
                        "density.table_size",
                        f"Таблица {rows}×{cols}",
                        bbox=obj.get("bbox"),
                        element_ids=[str(obj.get("object_id"))],
                        evidence={
                            "measured": {"rows": rows, "cols": cols},
                            "threshold": {"rows": max_rows, "cols": max_cols},
                        },
                        **_slide_ref(slide),
                    )
                )
        chart = obj.get("chart") or {}
        if chart:
            series = len(chart.get("series") or []) or int(chart.get("series_count") or 0)
            max_series = int(threshold("density.chart_series", "max_series", 5))
            if series > max_series:
                out.append(
                    Issue(
                        "density.chart_series",
                        f"На диаграмме {series} серий",
                        bbox=obj.get("bbox"),
                        element_ids=[str(obj.get("object_id"))],
                        evidence={"measured": series, "threshold": max_series},
                        **_slide_ref(slide),
                    )
                )
    if bullets > max_bullets:
        out.append(
            Issue(
                "density.bullets",
                f"На слайде {bullets} буллетов",
                evidence={"measured": bullets, "threshold": max_bullets},
                **_slide_ref(slide),
            )
        )
    return out


def check_fill_ratio(slide: JsonDict, ctx: Context) -> list[Issue]:
    """Доля холста под содержательными объектами."""
    low = float(threshold("density.fill_ratio", "min_ratio", 0.25))
    high = float(threshold("density.fill_ratio", "max_ratio", 0.75))
    objects = [o for o in slide.get("objects") or [] if _is_content(o)]
    if not objects:
        return []
    filled = sum(_area(o) for o in objects if o.get("kind") != "text" or _plain(o))
    if low <= filled <= high:
        return []
    verdict = "заполнен меньше четверти" if filled < low else "заполнен больше трёх четвертей"
    return [
        Issue(
            "density.fill_ratio",
            f"Слайд {verdict} холста",
            evidence={"measured": round(filled, 3), "threshold": {"min": low, "max": high}},
            **_slide_ref(slide),
        )
    ]


# ---------- целостность ----------


def check_placeholder_text(slide: JsonDict, ctx: Context) -> list[Issue]:
    markers = [str(m).lower() for m in threshold("integrity.placeholder_text", "markers", []) or []]
    out: list[Issue] = []
    for obj in slide.get("objects") or []:
        text = _plain(obj)
        if not text:
            continue
        low = text.lower()
        hit = next((m for m in markers if m in low), None)
        if hit:
            out.append(
                Issue(
                    "integrity.placeholder_text",
                    f"Остался текст-заглушка: «{text[:60]}»",
                    bbox=obj.get("bbox"),
                    element_ids=[str(obj.get("object_id"))],
                    evidence={"measured": hit},
                    **_slide_ref(slide),
                )
            )
    return out


def check_empty_slide(slide: JsonDict, ctx: Context) -> list[Issue]:
    content = [o for o in slide.get("objects") or [] if _is_content(o)]
    texts = [o for o in content if o.get("kind") == "text" and _plain(o)]
    visuals = [o for o in content if o.get("kind") in ("picture", "chart", "table", "group")]
    if texts or visuals:
        only_title = len(texts) == 1 and not visuals and texts[0].get("slot_kind") == "title"
        if not only_title:
            return []
        message = "На слайде только заголовок"
    else:
        message = "Слайд пустой"
    return [Issue("integrity.empty_slide", message, **_slide_ref(slide))]


def check_raster_slide(slide: JsonDict, ctx: Context) -> list[Issue]:
    """Слайд-картинка: одно изображение во весь холст и никакого текста.

    Прямое требование ТЗ: слайд, выгруженный единым растром, не засчитывается.
    """
    content = [o for o in slide.get("objects") or [] if _is_content(o)]
    pictures = [o for o in content if o.get("kind") == "picture"]
    texts = [o for o in content if o.get("kind") == "text" and _plain(o)]
    if texts or len(pictures) != 1:
        return []
    if _area(pictures[0]) < 0.85:
        return []
    return [
        Issue(
            "integrity.raster_slide",
            "Слайд состоит из одной картинки без редактируемых объектов",
            bbox=pictures[0].get("bbox"),
            element_ids=[str(pictures[0].get("object_id"))],
            evidence={"measured": round(_area(pictures[0]), 3)},
            **_slide_ref(slide),
        )
    ]


def check_chart_labels(slide: JsonDict, ctx: Context) -> list[Issue]:
    out: list[Issue] = []
    for obj in slide.get("objects") or []:
        chart = obj.get("chart") or {}
        if not chart:
            continue
        missing = []
        if not chart.get("has_legend", chart.get("legend")):
            missing.append("легенды")
        if not chart.get("categories_count"):
            missing.append("подписей категорий")
        if not chart.get("units") and not chart.get("has_axis_titles"):
            missing.append("единиц")
        if len(missing) >= 2:
            out.append(
                Issue(
                    "integrity.chart_labels",
                    f"У диаграммы нет {', '.join(missing)}",
                    bbox=obj.get("bbox"),
                    element_ids=[str(obj.get("object_id"))],
                    evidence={"measured": missing},
                    **_slide_ref(slide),
                )
            )
    return out


def check_duplicate_slides(deck: JsonDict, ctx: Context) -> list[Issue]:
    """Два слайда с одной композицией и одинаковым текстом."""
    seen: dict[tuple[str, str], JsonDict] = {}
    out: list[Issue] = []
    for slide in deck.get("slides") or []:
        texts = " ".join(
            sorted(
                _plain(o)
                for o in slide.get("objects") or []
                if _is_content(o) and o.get("kind") == "text" and _plain(o)
            )
        )
        if not texts:
            continue
        key = (str(slide.get("pattern_id") or ""), re.sub(r"\s+", " ", texts).strip().lower())
        first = seen.get(key)
        if first is not None:
            out.append(
                Issue(
                    "integrity.duplicate_slides",
                    f"Слайд повторяет слайд {int(first.get('index', 0)) + 1}",
                    evidence={"measured": int(first.get("index", 0)) + 1},
                    **_slide_ref(slide),
                )
            )
        else:
            seen[key] = slide
    return out


_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_PRES_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _rels_of(part: str) -> str:
    """Имя файла связей части пакета:
    `ppt/slides/slide1.xml` → `ppt/slides/_rels/slide1.xml.rels`."""
    directory, name = posixpath.split(part)
    return posixpath.join(directory, "_rels", name + ".rels")


def check_package(pptx: pathlib.Path) -> list[Issue]:
    """Файл открывается как пакет OOXML, части читаются, внутренние связи ведут на
    существующие части, а список слайдов презентации ссылается на известные связи.

    Ровно то, из-за чего PowerPoint предлагает «восстановить» файл; python-pptx при сборке
    такие ошибки не ловит, а рендерер в PDF их прощает."""

    def issue(message: str, **evidence: Any) -> list[Issue]:
        return [Issue("integrity.package", message, evidence=evidence)]

    try:
        archive = zipfile.ZipFile(pptx)
    except (OSError, zipfile.BadZipFile) as e:
        return issue(f"Файл не открывается как пакет: {e}", measured="bad_zip")
    with archive:
        broken = archive.testzip()
        if broken is not None:
            return issue(f"Часть {broken} повреждена", measured="crc", part=broken)
        names = set(archive.namelist())
        for required in ("[Content_Types].xml", "_rels/.rels", "ppt/presentation.xml"):
            if required not in names:
                return issue(f"В пакете нет части {required}", measured="missing", part=required)
        dangling: list[str] = []
        rels_by_part: dict[str, dict[str, str]] = {}
        for name in sorted(names):
            if not name.endswith(".rels"):
                continue
            try:
                root = ET.fromstring(archive.read(name))
            except ET.ParseError as e:
                return issue(f"Связи {name} не разбираются: {e}", measured="bad_xml", part=name)
            base = posixpath.dirname(posixpath.dirname(name))
            ids: dict[str, str] = {}
            for rel in root.findall(f"{{{_RELS_NS}}}Relationship"):
                target = rel.get("Target") or ""
                ids[rel.get("Id") or ""] = target
                if rel.get("TargetMode") == "External" or not target:
                    continue
                resolved = (
                    target.lstrip("/")
                    if target.startswith("/")
                    else posixpath.normpath(posixpath.join(base, target))
                )
                if resolved not in names:
                    dangling.append(f"{name} → {target}")
            rels_by_part[name] = ids
        if dangling:
            return issue(
                f"Связи ведут на отсутствующие части: {', '.join(dangling[:5])}",
                measured=len(dangling),
                dangling=dangling[:20],
            )
        try:
            pres = ET.fromstring(archive.read("ppt/presentation.xml"))
        except ET.ParseError as e:
            return issue(f"presentation.xml не разбирается: {e}", measured="bad_xml")
        known = rels_by_part.get(_rels_of("ppt/presentation.xml"), {})
        unknown = [
            sld.get(f"{{{_R_NS}}}id") or ""
            for sld in pres.iter(f"{{{_PRES_NS}}}sldId")
            if (sld.get(f"{{{_R_NS}}}id") or "") not in known
        ]
        if unknown:
            return issue(
                f"Список слайдов ссылается на неизвестные связи: {', '.join(unknown[:5])}",
                measured=len(unknown),
            )
    return []


# Проверки уровня слайда в порядке реестра.
SLIDE_CHECKS = (
    check_out_of_bounds,
    check_overlap,
    check_text_overflow,
    check_clipped,
    check_margins,
    check_image_distorted,
    check_fonts,
    check_sizes,
    check_colors,
    check_layout,
    check_fixed_elements,
    check_contrast,
    check_density,
    check_fill_ratio,
    check_placeholder_text,
    check_empty_slide,
    check_raster_slide,
    check_chart_labels,
)

DECK_CHECKS = (check_duplicate_slides,)


def run_slide_checks(deck: JsonDict, profile: JsonDict) -> list[Issue]:
    """Все детерминированные проверки по колоде."""
    ctx = Context(deck=deck, profile=profile)
    issues: list[Issue] = []
    for slide in deck.get("slides") or []:
        for check in SLIDE_CHECKS:
            issues.extend(check(slide, ctx))
    for deck_check in DECK_CHECKS:
        issues.extend(deck_check(deck, ctx))
    return issues
