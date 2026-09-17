"""Измерение текста по файлам шрифтов: общий модуль для анализа шаблона, планов, вёрстки и аудита.

Ширина строки берётся у Pillow/FreeType по конкретному файлу шрифта и начертанию, высота строки —
из метрик файла через fontTools (ascender/descender/lineGap), а не из «универсального» множителя.
Модуль не задаёт произвольной ёмкости в символах: он считает, сколько строк заданного кегля
помещается в рамку с учётом внутренних полей и интервалов и сколько символов среднего текста
входит в строку. Вызывающий слой решает, какой запас оставлять, и записывает его в результат.

Шрифты ищутся в каталоге проекта (`docker/fonts`), системных каталогах и каталогах из
`PD_FONT_DIRS`; семейство сопоставляется по таблице `name`. Если файла нет, применяется
метрическая замена (Arial → Liberation Sans и т. п.), а при её отсутствии — эвристика; факт
подмены возвращается вместе с результатом, чтобы попасть в профиль и предупреждения.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import math
import os
import pathlib
from dataclasses import dataclass, field

from presentation_designer.shared.settings import ROOT

log = logging.getLogger(__name__)
# fontTools ругается на старые метки времени в таблице head у половины системных шрифтов.
logging.getLogger("fontTools").setLevel(logging.ERROR)

EMU_PER_PT = 12700
EMU_PER_INCH = 914400

# Метрические замены рендерера (образ воркера: Liberation, Carlito, DejaVu, Noto, Play).
FALLBACKS: dict[str, tuple[str, ...]] = {
    "arial": ("Liberation Sans", "Arimo", "Noto Sans", "DejaVu Sans"),
    "helvetica": ("Liberation Sans", "Noto Sans", "DejaVu Sans"),
    "calibri": ("Carlito", "Liberation Sans", "Noto Sans"),
    "cambria": ("Caladea", "Liberation Serif", "Noto Serif"),
    "times new roman": ("Liberation Serif", "Noto Serif", "DejaVu Serif"),
    "courier new": ("Liberation Mono", "DejaVu Sans Mono"),
    "consolas": ("DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono"),
    "segoe ui": ("Noto Sans", "DejaVu Sans", "Liberation Sans"),
    "vk sans display": ("Play", "Noto Sans", "DejaVu Sans"),
    "vk sans": ("Play", "Noto Sans", "DejaVu Sans"),
    "montserrat": ("Noto Sans", "DejaVu Sans", "Liberation Sans"),
    "poppins": ("Noto Sans", "DejaVu Sans", "Liberation Sans"),
    "lato": ("Noto Sans", "DejaVu Sans", "Liberation Sans"),
    "open sans": ("Noto Sans", "DejaVu Sans", "Liberation Sans"),
    "roboto": ("Noto Sans", "DejaVu Sans", "Liberation Sans"),
}
GENERIC_FALLBACK = ("Noto Sans", "DejaVu Sans", "Liberation Sans", "Arial", "Helvetica")
# Клоны с теми же ширинами глифов: измерение по ним совпадает с оригиналом, рендер отличается
# только рисунком букв, а в PPTX остаётся исходное имя — PowerPoint покажет оригинал.
METRIC_EQUIVALENTS: dict[str, tuple[str, ...]] = {
    "arial": ("Liberation Sans", "Arimo"),
    "helvetica": ("Liberation Sans", "Arimo"),
    "times new roman": ("Liberation Serif", "Tinos"),
    "courier new": ("Liberation Mono", "Cousine"),
    "calibri": ("Carlito",),
    "cambria": ("Caladea",),
}

FONT_DIRS = (
    ROOT / "docker" / "fonts",
    pathlib.Path("/usr/share/fonts"),
    pathlib.Path("/usr/local/share/fonts"),
    pathlib.Path("/opt/lo-profile"),
    pathlib.Path.home() / ".fonts",
    pathlib.Path.home() / "Library" / "Fonts",
    pathlib.Path("/Library/Fonts"),
    pathlib.Path("/System/Library/Fonts"),
    pathlib.Path("/System/Library/Fonts/Supplemental"),
)
# Русский и латинский текст презентаций: средняя ширина считается по этому образцу.
SAMPLE_TEXT = (
    "Оценка результатов пилота и план развития сервиса на следующий квартал "
    "Revenue growth 24% and retention 3 of 5 users"
)


@dataclass(frozen=True)
class FontFace:
    family: str
    style: str
    path: pathlib.Path
    bold: bool
    italic: bool
    weight: int = 400

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class ResolvedFont:
    requested: str
    face: FontFace | None
    substituted: bool

    @property
    def family(self) -> str:
        return self.face.family if self.face else self.requested

    @property
    def file(self) -> str | None:
        return str(self.face.path) if self.face else None

    @property
    def metric_equivalent(self) -> bool:
        """Замена метрически совместимым клоном (Arial → Liberation Sans): ширины те же."""
        return (
            self.substituted
            and self.face is not None
            and self.face.family in METRIC_EQUIVALENTS.get(self.requested.lower(), ())
        )


@dataclass(frozen=True)
class LineMetrics:
    """Высота строки и средняя ширина символа в пунктах для кегля size_pt."""

    size_pt: float
    line_height_pt: float
    avg_char_width_pt: float
    method: str  # font_metrics | heuristic


@dataclass
class Capacity:
    max_lines: int
    chars_per_line: int
    max_chars: int
    font_file: str | None
    method: str
    margin_ratio: float
    line_height_pt: float
    substituted: bool = False
    notes: list[str] = field(default_factory=list)


# ---------- индекс шрифтов ----------


def font_dirs() -> list[pathlib.Path]:
    extra = [pathlib.Path(p) for p in os.environ.get("PD_FONT_DIRS", "").split(os.pathsep) if p]
    return [d for d in (*extra, *FONT_DIRS) if d.is_dir()]


def _iter_font_files(dirs: list[pathlib.Path]) -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for d in dirs:
        for ext in ("*.ttf", "*.otf", "*.TTF", "*.OTF"):
            out.extend(d.rglob(ext))
    return out


@functools.lru_cache(maxsize=1)
def font_index() -> dict[str, list[FontFace]]:
    """Семейство (в нижнем регистре) → начертания. Строится один раз на процесс."""
    from fontTools.ttLib import TTFont, TTLibError

    index: dict[str, list[FontFace]] = {}
    for path in _iter_font_files(font_dirs()):
        try:
            font = TTFont(str(path), lazy=True, fontNumber=0)
            name = font["name"]
            family = name.getDebugName(16) or name.getDebugName(1) or path.stem
            legacy_family = name.getDebugName(1)  # «Poppins Light»: так семейство названо в PPTX
            style = name.getDebugName(17) or name.getDebugName(2) or "Regular"
            os2 = font["OS/2"] if "OS/2" in font else None
            weight = int(getattr(os2, "usWeightClass", 400) or 400)
            selection = int(getattr(os2, "fsSelection", 0) or 0)
            font.close()
        except (TTLibError, KeyError, OSError, IndexError, AssertionError):
            continue
        style_l = style.lower()
        face = FontFace(
            family=str(family),
            style=str(style),
            path=path,
            bold=weight >= 600 or "bold" in style_l,
            italic=bool(selection & 1) or "italic" in style_l or "oblique" in style_l,
            weight=weight,
        )
        index.setdefault(str(family).lower(), []).append(face)
        if legacy_family and legacy_family.lower() != str(family).lower():
            index.setdefault(legacy_family.lower(), []).append(face)
    return index


def available_families() -> list[str]:
    return sorted({faces[0].family for faces in font_index().values()})


@functools.lru_cache(maxsize=1)
def fonts_manifest() -> dict[str, str]:
    """Семейство → sha256 обычного начертания: входит в ключ профиля шаблона и планов, чтобы
    смена файлов шрифтов в рендерере не переиспользовала устаревшую ёмкость. Считается один
    раз на процесс, как и индекс шрифтов."""
    manifest: dict[str, str] = {}
    for key, faces in sorted(font_index().items()):
        regular = _pick(faces, bold=False, italic=False)
        try:
            manifest[faces[0].family] = regular.sha256[:16]
        except OSError:
            continue
        _ = key
    return manifest


def _pick(faces: list[FontFace], *, bold: bool, italic: bool) -> FontFace:
    """Начертание с ближайшей к обычному (400) или жирному (700) плотностью: у семейства с
    Regular, Medium и SemiBold обычный текст меряется по Regular."""
    target = 700 if bold else 400
    exact = sorted(
        (f for f in faces if f.bold == bold and f.italic == italic),
        key=lambda f: abs(f.weight - target),
    )
    if exact:
        return exact[0]
    same_weight = sorted((f for f in faces if f.bold == bold), key=lambda f: abs(f.weight - target))
    if same_weight:
        return same_weight[0]
    return faces[0]


def resolve_font(family: str | None, *, bold: bool = False, italic: bool = False) -> ResolvedFont:
    """Файл начертания для семейства; при отсутствии — метрическая замена с отметкой."""
    requested = (family or "").strip() or "Arial"
    index = font_index()
    faces = index.get(requested.lower())
    if faces:
        return ResolvedFont(requested, _pick(faces, bold=bold, italic=italic), False)
    for candidate in (*FALLBACKS.get(requested.lower(), ()), *GENERIC_FALLBACK):
        faces = index.get(candidate.lower())
        if faces:
            return ResolvedFont(requested, _pick(faces, bold=bold, italic=italic), True)
    return ResolvedFont(requested, None, True)


# ---------- метрики ----------


@functools.lru_cache(maxsize=512)
def _line_metrics_for_file(path: str, size_pt: float) -> LineMetrics:
    from fontTools.ttLib import TTFont
    from PIL import ImageFont

    pixel_size = max(4, round(size_pt * 4))  # измеряем в 4× для точности, делим обратно
    try:
        pil_font = ImageFont.truetype(path, pixel_size)
        width_px = pil_font.getlength(SAMPLE_TEXT)
        avg_char = (width_px / len(SAMPLE_TEXT)) / 4.0
    except OSError:
        return heuristic_metrics(size_pt)
    try:
        font = TTFont(path, lazy=True, fontNumber=0)
        upem = float(font["head"].unitsPerEm or 1000)
        hhea = font["hhea"]
        ascent, descent, gap = float(hhea.ascent), float(hhea.descent), float(hhea.lineGap)
        if "OS/2" in font and getattr(font["OS/2"], "fsSelection", 0) & (1 << 7):
            os2 = font["OS/2"]
            ascent, descent = float(os2.sTypoAscender), float(os2.sTypoDescender)
            gap = float(os2.sTypoLineGap)
        font.close()
        line_height = (ascent - descent + gap) / upem * size_pt
    except (KeyError, OSError, AttributeError):
        line_height = size_pt * 1.2
    return LineMetrics(size_pt, line_height, avg_char, "font_metrics")


def heuristic_metrics(size_pt: float) -> LineMetrics:
    """Без файла шрифта: строка 1,2 кегля, средний символ 0,52 кегля (широко, под кириллицу)."""
    return LineMetrics(size_pt, size_pt * 1.2, size_pt * 0.52, "heuristic")


def line_metrics(font: ResolvedFont, size_pt: float) -> LineMetrics:
    if font.face is None:
        return heuristic_metrics(size_pt)
    return _line_metrics_for_file(str(font.face.path), round(size_pt, 2))


def text_width_pt(text: str, font: ResolvedFont, size_pt: float) -> float:
    """Точная ширина строки в пунктах (кернинг FreeType); без файла — по средней ширине."""
    if font.face is None:
        return len(text) * heuristic_metrics(size_pt).avg_char_width_pt
    from PIL import ImageFont

    pil_font = ImageFont.truetype(str(font.face.path), max(4, round(size_pt * 4)))
    return float(pil_font.getlength(text)) / 4.0


def capacity(
    *,
    width_emu: int,
    height_emu: int,
    size_pt: float,
    font: ResolvedFont,
    line_spacing: float = 1.0,
    space_before_pt: float = 0.0,
    space_after_pt: float = 0.0,
    insets_emu: tuple[int, int, int, int] = (91440, 45720, 91440, 45720),
    bullet_indent_emu: int = 0,
    margin_ratio: float = 0.92,
    paragraphs: int = 1,
) -> Capacity:
    """Сколько строк и символов входит в рамку width×height при данном кегле.

    insets — внутренние поля (левое, верхнее, правое, нижнее) в EMU, по умолчанию как в PowerPoint
    (0,1″ и 0,05″). margin_ratio — запас вызывающего слоя: он записывается в результат, а не
    зашивается как «универсальная гарантия»."""
    metrics = line_metrics(font, size_pt)
    left, top, right, bottom = insets_emu
    avail_w_pt = max(0.0, (width_emu - left - right - bullet_indent_emu) / EMU_PER_PT)
    avail_h_pt = max(0.0, (height_emu - top - bottom) / EMU_PER_PT)
    line_h = metrics.line_height_pt * max(line_spacing, 0.5)
    para_gap = (space_before_pt + space_after_pt) * max(paragraphs - 1, 0)
    max_lines = math.floor((avail_h_pt - para_gap) / line_h) if line_h > 0 else 0
    chars_per_line = math.floor(avail_w_pt * margin_ratio / metrics.avg_char_width_pt)
    max_lines = max(max_lines, 0)
    chars_per_line = max(chars_per_line, 0)
    notes: list[str] = []
    if font.substituted and not font.metric_equivalent:
        notes.append(f"шрифт {font.requested} заменён на {font.family}")
    return Capacity(
        max_lines=max_lines,
        chars_per_line=chars_per_line,
        # Последняя строка редко заполняется целиком: считаем её наполовину.
        max_chars=int(max(max_lines - 0.5, 0) * chars_per_line) if max_lines else 0,
        font_file=font.file,
        method=metrics.method,
        margin_ratio=margin_ratio,
        line_height_pt=round(line_h, 2),
        # Метрически совместимый клон подменой не считается: ширины совпадают с оригиналом.
        substituted=font.substituted and not font.metric_equivalent,
        notes=notes,
    )


def emu_to_pt(value: int | float) -> float:
    return float(value) / EMU_PER_PT
