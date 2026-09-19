"""Замер вместимости слота по настоящим метрикам шрифта.

Первая версия слоя считала сама: «полкегля на знак», «строка — 1.2 кегля».
Эта арифметика ошибалась в обе стороны и дорого обошлась: заголовок обложки
был удалён как «не помещается», а текст, который на самом деле не влезал,
проходил проверку и наложился на соседей.

Здесь ничего не изобретается. Ширина строки берётся у Pillow/FreeType по
файлу шрифта с кернингом, высота — из таблиц шрифта через fontTools, перенос
по словам — тот же жадный алгоритм, которым пользуется `generation/capacity.py`.
Единственное, что добавлено, — перевод геометрии слота из долей холста в EMU,
потому что профиль хранит рамки долями, а метрики работают в EMU.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from presentation_designer.generation.capacity import wrap_lines
from presentation_designer.shared import text_metrics

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]

# Холст 16:9 в EMU: 13.333″ × 7.5″. Запасное значение для случая, когда размер
# слайда неизвестен; настоящий всегда берётся из профиля шаблона.
DEFAULT_SLIDE_W_EMU = 12192000
DEFAULT_SLIDE_H_EMU = 6858000


@dataclass(frozen=True)
class Canvas:
    """Размер слайда шаблона в EMU.

    Рамки слотов профиль хранит долями холста, а метрики шрифта работают в
    абсолютных единицах, поэтому без размера слайда доли не во что перевести.
    Умолчание здесь опасно: шаблон ЛЦТ — 10″ × 5.63″ (9144000 × 5143500), и
    замер на холсте 13.33″ давал слоты на треть шире настоящих. Текст при этом
    «помещался» по расчёту и наезжал на соседей в файле.
    """

    width_emu: int = DEFAULT_SLIDE_W_EMU
    height_emu: int = DEFAULT_SLIDE_H_EMU


DEFAULT_CANVAS = Canvas()


def canvas_of(profile: JsonDict | None) -> Canvas:
    """Холст шаблона из профиля; при отсутствии сведений — умолчание 16:9."""
    size = ((profile or {}).get("slide_size") or {}) if isinstance(profile, dict) else {}
    width = int(size.get("width_emu") or 0)
    height = int(size.get("height_emu") or 0)
    if width <= 0 or height <= 0:
        return DEFAULT_CANVAS
    return Canvas(width_emu=width, height_emu=height)


def _font_of(slot: JsonDict) -> tuple[Any, float, float]:
    """Гарнитура, кегль и межстрочный интервал слота из профиля шаблона."""
    style = (slot.get("computed_style") or {}).get("font") or slot.get("font") or {}
    font = text_metrics.resolve_font(
        style.get("family"),
        bold=bool(style.get("bold")),
        italic=bool(style.get("italic")),
    )
    return font, float(style.get("size_pt") or 0), float(style.get("line_spacing") or 1.0)


def slot_capacity(slot: JsonDict, canvas: Canvas | None = None) -> Any | None:
    """`Capacity` слота или `None`, если мерить нечем.

    `None` означает «неизвестно», а не «не помещается»: у слота может не быть
    кегля или рамки, и удалять содержание из-за нехватки сведений о шаблоне
    недопустимо.
    """
    canvas = canvas or DEFAULT_CANVAS
    font, size_pt, spacing = _font_of(slot)
    box = slot.get("bbox") or {}
    width = float(box.get("width") or 0) * canvas.width_emu
    height = float(box.get("height") or 0) * canvas.height_emu
    if size_pt <= 0 or width <= 0 or height <= 0:
        return None
    try:
        cap = text_metrics.capacity(
            width_emu=int(width),
            height_emu=int(height),
            size_pt=size_pt,
            font=font,
            line_spacing=spacing,
        )
    except Exception:  # шрифт не найден, битые метрики
        log.debug("замер слота %s не удался", slot.get("slot_id"), exc_info=True)
        return None
    return _at_least_sample(cap, slot)


def _at_least_sample(cap: Any, slot: JsonDict) -> Any:
    """Слот вмещает не меньше, чем образец шаблона в нём.

    Рамка карточки в шаблоне ЛЦТ имеет высоту 12.3pt при строке 11.45pt: с
    запасом на поля расчёт даёт «ноль строк», и слот нельзя заполнить ничем.
    Но образец в этом слоте — слово «Заголовок» в одну строку, то есть дизайнер
    шаблона считает его пригодным, а PowerPoint рамкой текст не обрезает.

    Поэтому образец имеет приоритет над расчётом: он сделан человеком и виден
    в самом файле. Без этой поправки слайд-вывод уходил в выдачу с четырьмя
    пустыми карточками — их просто некуда было заполнить.
    """
    from dataclasses import replace

    if cap is None or cap.max_lines > 0 or not str(slot.get("sample_text") or "").strip():
        return cap
    per_line = max(int(cap.chars_per_line or 0), 1)
    try:
        return replace(cap, max_lines=1, max_chars=per_line)
    except Exception:  # не dataclass — оставляем как есть
        return cap


# Внутренние поля текстовой рамки PowerPoint: 0,1″ по бокам и 0,05″ сверху и
# снизу. Те же значения по умолчанию берёт `text_metrics.capacity`.
INSET_X_EMU = 91440
INSET_Y_EMU = 45720


def wrap_width_pt(slot: JsonDict, canvas: Canvas | None = None) -> float:
    """Ширина строки в пунктах: рамка слота минус внутренние поля.

    Без запаса `margin_ratio`. Запас нужен, когда текст **заказывают**: просить
    у модели строку впритык — значит получить её длиннее. Но когда строка уже
    написана, решение «влезает или нет» обязано считаться по настоящей рамке.
    Со скидкой 0.92 отвергались подписи карточек «Простой −34%» и «Выручка
    +18%» — они помещаются в рамку, и в файле это видно.
    """
    canvas = canvas or DEFAULT_CANVAS
    box = slot.get("bbox") or {}
    width_emu = float(box.get("width") or 0) * canvas.width_emu
    return max(0.0, (width_emu - 2 * INSET_X_EMU) / text_metrics.EMU_PER_PT)


def fits(slot: JsonDict, text: str, canvas: Canvas | None = None) -> bool | None:
    """Помещается ли текст: да, нет или **неизвестно**.

    Считается не по числу знаков, а по числу строк после настоящего переноса
    по словам: «Логистическая платформа» и «Логистическая платформа для
    среднего бизнеса» дают разное число строк при одинаковой длине в знаках.
    """
    cap = slot_capacity(slot, canvas)
    if cap is None or not str(text).strip():
        return None if cap is None else True
    if cap.max_lines <= 0:
        return False

    font, size_pt, _ = _font_of(slot)
    width_pt = wrap_width_pt(slot, canvas)
    if width_pt <= 0:
        return None
    return bool(wrap_lines(str(text), width_pt, font, size_pt) <= cap.max_lines)


def lines_needed(slot: JsonDict, text: str, canvas: Canvas | None = None) -> tuple[int, int] | None:
    """(нужно строк, помещается строк) или `None`, если мерить нечем."""
    cap = slot_capacity(slot, canvas)
    if cap is None:
        return None
    font, size_pt, _ = _font_of(slot)
    width_pt = wrap_width_pt(slot, canvas)
    if width_pt <= 0:
        return None
    return wrap_lines(str(text), width_pt, font, size_pt), cap.max_lines


# Во сколько раз текст должен превышать рамку, чтобы считать это дефектом.
# PowerPoint рамкой текст НЕ обрезает, и шаблоны этим пользуются: подпись
# кеглем 18pt в рамке высотой 16pt — обычное решение дизайнера, а не брак.
# Удалять содержание за небольшой выход нельзя; за двукратный — уже можно.
OVERFLOW_RATIO = 2.0


def overflows_badly(slot: JsonDict, text: str, canvas: Canvas | None = None) -> bool:
    """Выходит ли текст за рамку настолько, что это видно как дефект.

    Порог отличается от `fits` намеренно: добавлять содержание надо
    осторожно (`fits`), а удалять — очень осторожно (эта проверка). Ошибка
    первого рода стоит пустого слота, ошибка второго — потерянного смысла.
    """
    measured = lines_needed(slot, text, canvas)
    if measured is None:
        return False  # нечем мерить — не трогаем
    needed, available = measured
    return needed > max(available, 1) * OVERFLOW_RATIO


def max_chars(slot: JsonDict, canvas: Canvas | None = None) -> int | None:
    """Сколько знаков среднего текста входит в слот — для задания модели."""
    cap = slot_capacity(slot, canvas)
    return cap.max_chars if cap and cap.max_chars > 0 else None
