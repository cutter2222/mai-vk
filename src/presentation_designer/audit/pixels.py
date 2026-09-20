"""Измерения по отрендеренной странице: то, чего в файле нет.

Контраст текста к фону, подмену гарнитуры рендерером и фактическое место букв из OOXML не
вывести. Под текстом бывает фотография или градиент, и цвет «под рамкой» тогда не определён:
на живой колоде ЛЦТ ни один из 76 текстовых объектов не поддавался проверке контраста по
цветам из файла, а отчёт при этом писал «проверено, нарушений нет». Гарнитуру подменяет тот,
кто рисует, и в файле остаётся исходное имя.

Ответы на это есть на странице PDF, которую конвейер и так строит для предпросмотра и
миниатюр. Страница читается pdfium: буквы приходят с рамками, кеглем, цветом и именем шрифта,
а подложку под текстом даёт та же страница, отрендеренная без текстовых объектов. Рендер
слоя — единицы миллисекунд, поэтому проверка по пикселям стоит дешевле, чем разбор файла.

Единицы: рамки в долях слайда, как везде в ComposedDeck; начало координат сверху слева.
"""

from __future__ import annotations

import ctypes
import math
import pathlib
from dataclasses import dataclass
from typing import Any

JsonDict = dict[str, Any]

# Разрешение подложки: на 960 pt слайда это ровно пиксель на пункт. Больше не нужно —
# по ней берут медианный цвет, а не разглядывают детали.
BACKGROUND_SCALE = 1.0
# Буквы одной строки стоят на общей базовой линии; разрыв больше половины кегля — новая строка.
LINE_TOLERANCE = 0.5


@dataclass(frozen=True)
class TextRun:
    """Строка текста так, как её нарисовал рендерер."""

    bbox: JsonDict
    text: str
    font: str
    size_pt: float
    color: tuple[int, int, int]


class SlidePixels:
    """Один слайд: строки текста, подложка под ними и занятая площадь."""

    def __init__(self, runs: list[TextRun], background: Any, width: float, height: float) -> None:
        self.runs = runs
        self._background = background
        self.width_pt = width
        self.height_pt = height

    def background_under(self, bbox: JsonDict) -> tuple[int, int, int] | None:
        """Медианный цвет подложки под рамкой: страница без текста, серединные пиксели."""
        image = self._background
        if image is None:
            return None
        w, h = image.size
        x = float(bbox.get("x") or 0)
        y = float(bbox.get("y") or 0)
        left = max(0, min(w - 1, round(x * w)))
        top = max(0, min(h - 1, round(y * h)))
        right = max(left + 1, min(w, round((x + float(bbox.get("width") or 0)) * w)))
        bottom = max(top + 1, min(h, round((y + float(bbox.get("height") or 0)) * h)))
        region = image.crop((left, top, right, bottom))
        pixels = list(region.getdata())
        if not pixels:
            return None
        # Медиана по яркости: средний цвет градиента врёт меньше, чем крайние точки, а на
        # фотографии выбирает тон, к которому текст и правда прижат.
        pixels.sort(key=lambda p: 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2])
        middle = pixels[len(pixels) // 2]
        return (int(middle[0]), int(middle[1]), int(middle[2]))

    def coverage(self) -> float | None:
        """Доля слайда, занятая чем-либо, кроме подложки. Пока не используется проверками."""
        return None


class DeckPixels:
    """PDF ревизии как источник измерений. Открывается на время аудита и закрывается."""

    def __init__(self, path: pathlib.Path) -> None:
        import pypdfium2 as pdfium

        self._doc = pdfium.PdfDocument(str(path))
        self._cache: dict[int, SlidePixels] = {}

    def __enter__(self) -> DeckPixels:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self._doc.close()
        except Exception:
            pass

    def __len__(self) -> int:
        return len(self._doc)

    def slide(self, index: int) -> SlidePixels | None:
        if index < 0 or index >= len(self._doc):
            return None
        if index not in self._cache:
            self._cache[index] = self._read(index)
        return self._cache[index]

    # ---------- чтение страницы ----------

    def _read(self, index: int) -> SlidePixels:
        page = self._doc[index]
        width, height = page.get_size()
        runs = _runs_of(page, width, height)
        background = _background_image(self._doc, index)
        return SlidePixels(runs, background, width, height)


@dataclass
class _Line:
    """Накопитель строки: буквы одного шрифта, кегля и цвета на одной базовой линии."""

    text: str
    font: str
    size: float
    color: tuple[int, int, int]
    box: tuple[float, float, float, float]  # left, top, right, bottom в пунктах страницы


def _runs_of(page: Any, width: float, height: float) -> list[TextRun]:
    """Буквы страницы, собранные в строки по шрифту, кеглю, цвету и базовой линии."""
    import pypdfium2.raw as raw

    text_page = page.get_textpage()
    try:
        lines = _lines_of(text_page, raw)
    finally:
        text_page.close()

    runs: list[TextRun] = []
    for line in lines:
        text = line.text.strip()
        if not text:
            continue
        runs.append(
            TextRun(
                bbox={
                    "x": round(line.box[0] / width, 4),
                    "y": round(1 - line.box[1] / height, 4),
                    "width": round((line.box[2] - line.box[0]) / width, 4),
                    "height": round((line.box[1] - line.box[3]) / height, 4),
                },
                text=text,
                font=line.font,
                size_pt=round(line.size, 1),
                color=line.color,
            )
        )
    return runs


def _lines_of(text_page: Any, raw: Any) -> list[_Line]:
    """Буквы страницы, склеенные в строки."""
    count = raw.FPDFText_CountChars(text_page.raw)
    lines: list[_Line] = []
    current: _Line | None = None
    for i in range(count):
        char = chr(raw.FPDFText_GetUnicode(text_page.raw, i))
        left, right, bottom, top = (ctypes.c_double() for _ in range(4))
        raw.FPDFText_GetCharBox(text_page.raw, i, left, right, bottom, top)
        box = (left.value, top.value, right.value, bottom.value)
        if char in "\r\n":
            current = None
            continue
        if char.isspace() and current is not None:
            # Пробел между словами приходит со своим шрифтом и нулевой рамкой: если считать
            # его новой строкой, строка рассыпается на слова.
            current.text += char
            continue
        size = float(raw.FPDFText_GetFontSize(text_page.raw, i))
        font = _font_name(text_page, i)
        color = _text_color(text_page, i)
        if current is not None and _same_line(current, font, size, color, box):
            current.text += char
            current.box = _union(current.box, box)
            continue
        current = _Line(char, font, size, color, box)
        lines.append(current)
    return lines


def _same_line(
    current: _Line,
    font: str,
    size: float,
    color: tuple[int, int, int],
    box: tuple[float, float, float, float],
) -> bool:
    if current.font != font or abs(current.size - size) > 0.1 or current.color != color:
        return False
    # Та же строка: верх буквы недалеко от верха строки и буква идёт не левее начала строки.
    return (
        abs(current.box[1] - box[1]) <= current.size * LINE_TOLERANCE
        and box[0] >= current.box[0] - current.size
    )


def _union(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    return (min(a[0], b[0]), max(a[1], b[1]), max(a[2], b[2]), min(a[3], b[3]))


def _font_name(text_page: Any, index: int) -> str:
    import pypdfium2.raw as raw

    flags = ctypes.c_int()
    size = raw.FPDFText_GetFontInfo(text_page.raw, index, None, 0, flags)
    if size <= 0:
        return ""
    buffer = ctypes.create_string_buffer(size)
    raw.FPDFText_GetFontInfo(text_page.raw, index, buffer, size, flags)
    name = buffer.value.decode("utf-8", "ignore")
    # Встроенный шрифт приходит с префиксом подмножества: «ABCDEE+Montserrat-Bold».
    if "+" in name[:8]:
        name = name.split("+", 1)[1]
    return name


def _text_color(text_page: Any, index: int) -> tuple[int, int, int]:
    import pypdfium2.raw as raw

    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    ok = raw.FPDFText_GetFillColor(text_page.raw, index, r, g, b, a)
    if not ok:
        return (0, 0, 0)
    return (int(r.value), int(g.value), int(b.value))


def _background_image(doc: Any, index: int) -> Any:
    """Страница без текстовых объектов: то, что лежит под буквами."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw

    try:
        copy = pdfium.PdfDocument.new()
        copy.import_pages(doc, [index])
        page = copy[0]
        for obj in list(page.get_objects(max_depth=1)):
            if obj.type != raw.FPDF_PAGEOBJ_TEXT:
                obj.close()
                continue
            page.remove_obj(obj)
            # Снятый со страницы объект больше никому не принадлежит: его закрывает тот,
            # кто снял, иначе pdfium держит память до конца процесса.
            obj.close()
        page.gen_content()
        image = page.render(scale=BACKGROUND_SCALE, draw_annots=False).to_pil().convert("RGB")
        page.close()
        copy.close()
        return image
    except Exception:
        return None


def relative_luminance(color: tuple[int, int, int]) -> float:
    channels = []
    for value in color:
        c = value / 255.0
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return round((hi + 0.05) / (lo + 0.05), 2)


# Слова начертания: в PDF они приклеены к имени («Montserrat-Bold»), в профиле шаблона стоят
# отдельным словом («Poppins Light»). Для сравнения гарнитур они лишние.
WEIGHT_WORDS = {
    "bold", "italic", "bolditalic", "oblique", "regular", "book", "roman",
    "medium", "light", "thin", "black", "heavy", "semibold", "demibold", "extrabold",
    "extralight", "ultralight", "condensed", "narrow", "mt", "ps", "ttf",
}


def normalized_family(name: str) -> str:
    """Имя гарнитуры без начертания и подмножества: «ABCDEE+Montserrat-Bold» → «montserrat»."""
    cleaned = name.split("+", 1)[-1]
    words = [w for w in cleaned.replace("_", " ").replace("-", " ").split() if w]
    kept = [w for w in words if w.lower() not in WEIGHT_WORDS]
    if not kept:
        kept = words
    # «ArialMT», «TimesNewRomanPSMT»: начертание приклеено без разделителя.
    out = " ".join(kept).lower()
    for suffix in ("mt", "ps", "psmt"):
        while out.endswith(suffix) and len(out) > len(suffix) + 2:
            out = out[: -len(suffix)]
    return out.strip()


def distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))


__all__ = [
    "DeckPixels",
    "SlidePixels",
    "TextRun",
    "contrast",
    "distance",
    "normalized_family",
    "relative_luminance",
]
