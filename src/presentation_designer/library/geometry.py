"""Раскладка семейств композиций: сетка в долях холста.

Здесь нет ни одного цвета и ни одного кегля — только места под содержание. Вид задаёт
дизайн-код шаблона на сборке. Координаты считаются от рабочей области (холст минус поля
шаблона), поэтому композиция одинаково ложится на 16:9 и 4:3 и уважает поля автора.

Каждое семейство разворачивается в набор композиций по своим параметрам: `cards_grid` с
колонками 2–4 и рядами 1–2 — это шесть раскладок, и все они измеряются ёмкостью так же, как
паттерны шаблона.
"""

from __future__ import annotations

from typing import Any

from presentation_designer.library.spec import (
    CardSpec,
    Composition,
    CompositionSlot,
    Family,
    composition_id,
)

# Рабочая область в долях холста: поля шаблона накладываются позже, в `fit_to_margins`.
AREA_X, AREA_Y = 0.06, 0.08
AREA_W, AREA_H = 0.88, 0.84
TITLE_H = 0.14
TITLE_GAP = 0.05
GUTTER = 0.03


def _title(required: bool = True) -> CompositionSlot:
    return CompositionSlot(
        slot_id="title",
        kind="title",
        bbox=(AREA_X, AREA_Y, AREA_W, TITLE_H),
        text_role="title",
        required=required,
    )


def _body_top() -> float:
    return AREA_Y + TITLE_H + TITLE_GAP


def _columns(count: int, *, gutter: float = GUTTER) -> list[tuple[float, float]]:
    """Левый край и ширина каждой колонки внутри рабочей области."""
    width = (AREA_W - gutter * (count - 1)) / count
    return [(AREA_X + i * (width + gutter), width) for i in range(count)]


def _rows(count: int, top: float, bottom: float, *, gutter: float = GUTTER) -> list[float]:
    height = (bottom - top - gutter * (count - 1)) / count
    return [top + i * (height + gutter) for i in range(count)]


# ---------- семейства ----------


def build_kpi_row(family: Family, params: dict[str, Any]) -> Composition:
    """Ряд показателей: крупное число и подпись под ним, по желанию на плашке."""
    count = int(params["count"])
    on_card = bool(params.get("card"))
    top = _body_top()
    height = AREA_Y + AREA_H - top
    slots: list[CompositionSlot] = [_title()]
    cards: list[CardSpec] = []
    for index, (x, width) in enumerate(_columns(count), start=1):
        inset = width * 0.08 if on_card else 0.0
        if on_card:
            cards.append(CardSpec(bbox=(x, top, width, height * 0.72), index=index))
        slots.append(
            CompositionSlot(
                slot_id=f"kpi_{index}_value",
                kind="number",
                bbox=(x + inset, top + height * 0.12, width - 2 * inset, height * 0.3),
                text_role="number",
                align="center" if on_card else "left",
                repeat_group="kpi",
                color_role="accent",
                on_card=on_card,
                card_index=index if on_card else 0,
            )
        )
        slots.append(
            CompositionSlot(
                slot_id=f"kpi_{index}_label",
                kind="label",
                bbox=(x + inset, top + height * 0.45, width - 2 * inset, height * 0.22),
                text_role="label",
                align="center" if on_card else "left",
                repeat_group="kpi",
                color_role="muted",
                on_card=on_card,
                card_index=index if on_card else 0,
            )
        )
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=f"{family.title}: {count} показателя" if count < 5 else f"{family.title}: {count}",
        slots=slots,
        cards=cards,
        supports=family.supports,
        min_items=max(2, count - 1),
        max_items=count,
        tags=family.tags,
    )


def build_cards_grid(family: Family, params: dict[str, Any]) -> Composition:
    """Сетка карточек: заголовок карточки и текст под ним, по желанию с номером."""
    cols, rows = int(params["cols"]), int(params["rows"])
    numbered = bool(params.get("numbered"))
    top = _body_top()
    bottom = AREA_Y + AREA_H
    row_tops = _rows(rows, top, bottom)
    row_height = (bottom - top - GUTTER * (rows - 1)) / rows
    slots: list[CompositionSlot] = [_title()]
    cards: list[CardSpec] = []
    index = 0
    for row_top in row_tops:
        for x, width in _columns(cols):
            index += 1
            cards.append(
                CardSpec(bbox=(x, row_top, width, row_height), index=index, fill="surface")
            )
            pad = width * 0.07
            cursor = row_top + row_height * 0.12
            if numbered:
                slots.append(
                    CompositionSlot(
                        slot_id=f"card_{index}_number",
                        kind="label",
                        bbox=(x + pad, cursor, width - 2 * pad, row_height * 0.18),
                        text_role="subtitle",
                        repeat_group="cards",
                        color_role="accent",
                        bold=True,
                        on_card=True,
                        card_index=index,
                    )
                )
                cursor += row_height * 0.2
            slots.append(
                CompositionSlot(
                    slot_id=f"card_{index}_title",
                    kind="label",
                    bbox=(x + pad, cursor, width - 2 * pad, row_height * 0.22),
                    text_role="body",
                    bold=True,
                    repeat_group="cards",
                    on_card=True,
                    card_index=index,
                )
            )
            slots.append(
                CompositionSlot(
                    slot_id=f"card_{index}_body",
                    kind="caption",
                    bbox=(
                        x + pad,
                        cursor + row_height * 0.25,
                        width - 2 * pad,
                        row_height * (0.52 if not numbered else 0.38),
                    ),
                    text_role="label",
                    repeat_group="cards",
                    color_role="muted",
                    on_card=True,
                    card_index=index,
                )
            )
    total = cols * rows
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=f"{family.title}: {total} карточки",
        slots=slots,
        cards=cards,
        supports=family.supports,
        min_items=max(2, total - 1),
        max_items=total,
        tags=family.tags,
    )


def build_chart_takeaway(family: Family, params: dict[str, Any]) -> Composition:
    """Диаграмма и вывод рядом: данные слева или справа, вывод текстом на второй половине."""
    side = str(params.get("side") or "left")
    top = _body_top()
    height = AREA_Y + AREA_H - top
    chart_w = AREA_W * 0.58
    text_w = AREA_W - chart_w - GUTTER
    chart_x = AREA_X if side == "left" else AREA_X + text_w + GUTTER
    text_x = AREA_X + chart_w + GUTTER if side == "left" else AREA_X
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=f"{family.title}: диаграмма {'слева' if side == 'left' else 'справа'}",
        slots=[
            _title(),
            CompositionSlot(
                slot_id="chart",
                kind="chart",
                bbox=(chart_x, top, chart_w, height * 0.9),
                required=True,
            ),
            CompositionSlot(
                slot_id="takeaway",
                kind="body",
                bbox=(text_x, top, text_w, height * 0.55),
                text_role="body",
                valign="middle",
            ),
        ],
        supports=family.supports,
        min_items=1,
        max_items=1,
        tags=family.tags,
    )


def build_table_sheet(family: Family, params: dict[str, Any]) -> Composition:
    """Таблица во всю ширину; при `lead` — строка подводки над ней."""
    lead = bool(params.get("lead"))
    top = _body_top()
    bottom = AREA_Y + AREA_H
    slots: list[CompositionSlot] = [_title()]
    if lead:
        slots.append(
            CompositionSlot(
                slot_id="lead",
                kind="body",
                bbox=(AREA_X, top, AREA_W, 0.1),
                text_role="body",
                color_role="muted",
            )
        )
        top += 0.13
    slots.append(
        CompositionSlot(
            slot_id="table",
            kind="table",
            bbox=(AREA_X, top, AREA_W, bottom - top),
            required=True,
        )
    )
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=f"{family.title}{' с подводкой' if lead else ''}",
        slots=slots,
        supports=family.supports,
        min_items=1,
        max_items=1,
        tags=family.tags,
    )


def build_comparison(family: Family, params: dict[str, Any]) -> Composition:
    """Две колонки друг против друга: «было и стало», «плюсы и минусы»."""
    top = _body_top()
    height = AREA_Y + AREA_H - top
    slots: list[CompositionSlot] = [_title()]
    cards: list[CardSpec] = []
    for index, (x, width) in enumerate(_columns(2), start=1):
        cards.append(CardSpec(bbox=(x, top, width, height * 0.85), index=index, fill="surface"))
        pad = width * 0.06
        slots.append(
            CompositionSlot(
                slot_id=f"side_{index}_title",
                kind="label",
                bbox=(x + pad, top + height * 0.08, width - 2 * pad, height * 0.14),
                text_role="subtitle",
                bold=True,
                repeat_group="sides",
                color_role="accent",
                on_card=True,
                card_index=index,
            )
        )
        slots.append(
            CompositionSlot(
                slot_id=f"side_{index}_body",
                kind="bullets",
                bbox=(x + pad, top + height * 0.26, width - 2 * pad, height * 0.52),
                text_role="body",
                repeat_group="sides",
                on_card=True,
                card_index=index,
            )
        )
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=family.title,
        slots=slots,
        cards=cards,
        supports=family.supports,
        min_items=2,
        max_items=2,
        tags=family.tags,
    )


def build_statement(family: Family, params: dict[str, Any]) -> Composition:
    """Одна мысль крупно: вывод, цитата или итог без карточек."""
    top = _body_top()
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=family.title,
        slots=[
            _title(),
            CompositionSlot(
                slot_id="statement",
                kind="body",
                bbox=(AREA_X, top, AREA_W * 0.82, 0.3),
                text_role="subtitle",
                valign="middle",
            ),
            CompositionSlot(
                slot_id="support",
                kind="caption",
                bbox=(AREA_X, top + 0.34, AREA_W * 0.7, 0.12),
                text_role="label",
                color_role="muted",
            ),
        ],
        supports=family.supports,
        min_items=1,
        max_items=2,
        tags=family.tags,
    )


def build_bullets_pane(family: Family, params: dict[str, Any]) -> Composition:
    """Список в одну или две колонки: самая обычная подача, которой часто нет в шаблоне."""
    cols = int(params["cols"])
    top = _body_top()
    height = AREA_Y + AREA_H - top
    slots: list[CompositionSlot] = [_title()]
    for index, (x, width) in enumerate(_columns(cols), start=1):
        slots.append(
            CompositionSlot(
                slot_id=f"bullets_{index}",
                kind="bullets",
                bbox=(x, top, width, height * 0.82),
                text_role="body",
                repeat_group="bullets" if cols > 1 else "",
            )
        )
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=f"{family.title}: {cols} колонка" if cols == 1 else f"{family.title}: {cols} колонки",
        slots=slots,
        supports=family.supports,
        min_items=2,
        max_items=8 * cols,
        tags=family.tags,
    )


def build_process(family: Family, params: dict[str, Any]) -> Composition:
    """Шаги процесса в ряд: номер, название шага, пояснение."""
    count = int(params["count"])
    top = _body_top()
    height = AREA_Y + AREA_H - top
    slots: list[CompositionSlot] = [_title()]
    cards: list[CardSpec] = []
    for index, (x, width) in enumerate(_columns(count, gutter=GUTTER * 0.8), start=1):
        cards.append(
            CardSpec(bbox=(x, top, width, height * 0.66), index=index, accent_index=index - 1)
        )
        pad = width * 0.08
        slots.append(
            CompositionSlot(
                slot_id=f"step_{index}_number",
                kind="subtitle",
                bbox=(x + pad, top + height * 0.08, width - 2 * pad, height * 0.14),
                text_role="subtitle",
                bold=True,
                repeat_group="steps",
                on_card=True,
                card_index=index,
            )
        )
        slots.append(
            CompositionSlot(
                slot_id=f"step_{index}_title",
                kind="label",
                bbox=(x + pad, top + height * 0.24, width - 2 * pad, height * 0.18),
                text_role="body",
                bold=True,
                repeat_group="steps",
                on_card=True,
                card_index=index,
            )
        )
        slots.append(
            CompositionSlot(
                slot_id=f"step_{index}_body",
                kind="caption",
                bbox=(x + pad, top + height * 0.44, width - 2 * pad, height * 0.18),
                text_role="label",
                repeat_group="steps",
                on_card=True,
                card_index=index,
            )
        )
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=f"{family.title}: {count} шага",
        slots=slots,
        cards=cards,
        supports=family.supports,
        min_items=max(2, count - 1),
        max_items=count,
        tags=family.tags,
    )


REGISTRY: tuple[Family, ...] = (
    Family(
        name="kpi_row",
        role="kpi",
        title="Ряд показателей",
        params={"count": [2, 3, 4], "card": [False, True]},
        builder=build_kpi_row,
        supports=("number",),
        tags=("builtin", "numbers"),
    ),
    Family(
        name="cards_grid",
        role="cards",
        title="Карточки",
        params={"cols": [2, 3], "rows": [1, 2], "numbered": [False, True]},
        builder=build_cards_grid,
        supports=("icon",),
        tags=("builtin", "cards"),
    ),
    Family(
        name="chart_takeaway",
        role="chart",
        title="Диаграмма и вывод",
        params={"side": ["left", "right"]},
        builder=build_chart_takeaway,
        supports=("chart",),
        tags=("builtin", "data"),
    ),
    Family(
        name="table_sheet",
        role="table",
        title="Таблица",
        params={"lead": [False, True]},
        builder=build_table_sheet,
        supports=("table",),
        tags=("builtin", "data"),
    ),
    Family(
        name="comparison",
        role="comparison",
        title="Сравнение двух сторон",
        params={},
        builder=build_comparison,
        tags=("builtin",),
    ),
    Family(
        name="statement",
        role="text",
        title="Одна мысль",
        params={},
        builder=build_statement,
        tags=("builtin",),
    ),
    Family(
        name="bullets_pane",
        role="bullets",
        title="Список",
        params={"cols": [1, 2]},
        builder=build_bullets_pane,
        tags=("builtin",),
    ),
    Family(
        name="process",
        role="process",
        title="Шаги",
        params={"count": [3, 4, 5]},
        builder=build_process,
        tags=("builtin",),
    ),
)


# ---------- семейства с изображениями, схемами и данными ----------


def build_image_split(family: Family, params: dict[str, Any]) -> Composition:
    """Картинка на половине холста и текст рядом: фото продукта, скриншот, иллюстрация."""
    side = str(params.get("side") or "right")
    with_bullets = bool(params.get("bullets"))
    top = _body_top()
    height = AREA_Y + AREA_H - top
    image_w = AREA_W * 0.46
    text_w = AREA_W - image_w - GUTTER
    image_x = AREA_X if side == "left" else AREA_X + text_w + GUTTER
    text_x = AREA_X + image_w + GUTTER if side == "left" else AREA_X
    body = CompositionSlot(
        slot_id="body",
        kind="bullets" if with_bullets else "body",
        bbox=(text_x, top, text_w, height * 0.78),
        text_role="body",
        valign="top" if with_bullets else "middle",
    )
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=f"{family.title}: картинка {'слева' if side == 'left' else 'справа'}",
        slots=[
            _title(),
            CompositionSlot(
                slot_id="image",
                kind="image",
                bbox=(image_x, top, image_w, height * 0.82),
            ),
            body,
        ],
        supports=family.supports,
        min_items=1,
        max_items=6 if with_bullets else 1,
        tags=family.tags,
    )


def build_image_full(family: Family, params: dict[str, Any]) -> Composition:
    """Картинка во всю ширину с подписью под ней: обложка раздела, крупный кадр."""
    caption = bool(params.get("caption"))
    top = _body_top()
    bottom = AREA_Y + AREA_H
    image_h = (bottom - top) * (0.78 if caption else 1.0)
    slots = [
        _title(),
        CompositionSlot(slot_id="image", kind="image", bbox=(AREA_X, top, AREA_W, image_h)),
    ]
    if caption:
        slots.append(
            CompositionSlot(
                slot_id="caption",
                kind="caption",
                bbox=(AREA_X, top + image_h + 0.02, AREA_W, 0.08),
                text_role="label",
                color_role="muted",
            )
        )
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=f"{family.title}{' с подписью' if caption else ''}",
        slots=slots,
        supports=family.supports,
        min_items=1,
        max_items=1,
        tags=family.tags,
    )


def build_gallery(family: Family, params: dict[str, Any]) -> Composition:
    """Несколько картинок в ряд с подписями: экраны продукта, этапы, примеры."""
    count = int(params["count"])
    top = _body_top()
    height = AREA_Y + AREA_H - top
    slots: list[CompositionSlot] = [_title()]
    for index, (x, width) in enumerate(_columns(count), start=1):
        slots.append(
            CompositionSlot(
                slot_id=f"shot_{index}",
                kind="image",
                bbox=(x, top, width, height * 0.62),
                repeat_group="shots",
            )
        )
        slots.append(
            CompositionSlot(
                slot_id=f"shot_{index}_caption",
                kind="caption",
                bbox=(x, top + height * 0.66, width, height * 0.18),
                text_role="label",
                repeat_group="shots",
                color_role="muted",
            )
        )
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=f"{family.title}: {count} изображения",
        slots=slots,
        supports=family.supports,
        min_items=2,
        max_items=count,
        tags=family.tags,
    )


def build_icon_cards(family: Family, params: dict[str, Any]) -> Composition:
    """Карточки с пиктограммой: возможности, принципы, преимущества."""
    count = int(params["count"])
    top = _body_top()
    height = AREA_Y + AREA_H - top
    slots: list[CompositionSlot] = [_title()]
    cards: list[CardSpec] = []
    for index, (x, width) in enumerate(_columns(count), start=1):
        cards.append(CardSpec(bbox=(x, top, width, height * 0.8), index=index, fill="surface"))
        pad = width * 0.1
        slots.append(
            CompositionSlot(
                slot_id=f"icon_{index}",
                kind="icon",
                bbox=(x + pad, top + height * 0.08, width * 0.2, height * 0.16),
                repeat_group="icons",
                on_card=True,
                card_index=index,
            )
        )
        slots.append(
            CompositionSlot(
                slot_id=f"icon_{index}_title",
                kind="label",
                bbox=(x + pad, top + height * 0.3, width - 2 * pad, height * 0.16),
                text_role="body",
                bold=True,
                repeat_group="icons",
                on_card=True,
                card_index=index,
            )
        )
        slots.append(
            CompositionSlot(
                slot_id=f"icon_{index}_body",
                kind="caption",
                bbox=(x + pad, top + height * 0.48, width - 2 * pad, height * 0.26),
                text_role="label",
                repeat_group="icons",
                color_role="muted",
                on_card=True,
                card_index=index,
            )
        )
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=f"{family.title}: {count} карточки",
        slots=slots,
        cards=cards,
        supports=family.supports,
        min_items=max(2, count - 1),
        max_items=count,
        tags=family.tags,
    )


def build_dashboard(family: Family, params: dict[str, Any]) -> Composition:
    """Несколько диаграмм рядом: сравнение метрик, срезы одного показателя."""
    count = int(params["count"])
    top = _body_top()
    height = AREA_Y + AREA_H - top
    slots: list[CompositionSlot] = [_title()]
    for index, (x, width) in enumerate(_columns(count), start=1):
        slots.append(
            CompositionSlot(
                slot_id=f"chart_{index}",
                kind="chart",
                bbox=(x, top, width, height * 0.66),
                repeat_group="charts",
            )
        )
        slots.append(
            CompositionSlot(
                slot_id=f"chart_{index}_caption",
                kind="caption",
                bbox=(x, top + height * 0.7, width, height * 0.16),
                text_role="label",
                repeat_group="charts",
                color_role="muted",
            )
        )
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=f"{family.title}: {count} диаграммы",
        slots=slots,
        supports=family.supports,
        min_items=2,
        max_items=count,
        tags=family.tags,
    )


def build_chart_kpi(family: Family, params: dict[str, Any]) -> Composition:
    """Диаграмма и показатели рядом: динамика плюс ключевые числа."""
    count = int(params["count"])
    top = _body_top()
    height = AREA_Y + AREA_H - top
    chart_w = AREA_W * 0.62
    side_x = AREA_X + chart_w + GUTTER
    side_w = AREA_W - chart_w - GUTTER
    slots: list[CompositionSlot] = [
        _title(),
        CompositionSlot(slot_id="chart", kind="chart", bbox=(AREA_X, top, chart_w, height * 0.86)),
    ]
    rows = _rows(count, top, top + height * 0.86)
    row_h = (height * 0.86 - GUTTER * (count - 1)) / count
    for index, row_top in enumerate(rows, start=1):
        slots.append(
            CompositionSlot(
                slot_id=f"kpi_{index}_value",
                kind="number",
                bbox=(side_x, row_top, side_w, row_h * 0.52),
                text_role="number",
                repeat_group="kpi",
                color_role="accent",
            )
        )
        slots.append(
            CompositionSlot(
                slot_id=f"kpi_{index}_label",
                kind="label",
                bbox=(side_x, row_top + row_h * 0.54, side_w, row_h * 0.38),
                text_role="label",
                repeat_group="kpi",
                color_role="muted",
            )
        )
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=f"{family.title}: диаграмма и {count} показателя",
        slots=slots,
        supports=family.supports,
        min_items=1,
        max_items=count,
        tags=family.tags,
    )


def build_diagram_pane(family: Family, params: dict[str, Any]) -> Composition:
    """Схема из фигур во всю ширину: процесс, цикл, иерархия, воронка."""
    lead = bool(params.get("lead"))
    top = _body_top()
    bottom = AREA_Y + AREA_H
    slots: list[CompositionSlot] = [_title()]
    if lead:
        slots.append(
            CompositionSlot(
                slot_id="lead",
                kind="body",
                bbox=(AREA_X, top, AREA_W, 0.1),
                text_role="body",
                color_role="muted",
            )
        )
        top += 0.13
    slots.append(
        CompositionSlot(slot_id="diagram", kind="diagram", bbox=(AREA_X, top, AREA_W, bottom - top))
    )
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=f"{family.title}{' с подводкой' if lead else ''}",
        slots=slots,
        supports=family.supports,
        min_items=3,
        max_items=8,
        tags=family.tags,
    )


def build_matrix(family: Family, params: dict[str, Any]) -> Composition:
    """Матрица два на два: квадранты, риски и меры, сильные и слабые стороны."""
    top = _body_top()
    height = AREA_Y + AREA_H - top
    slots: list[CompositionSlot] = [_title()]
    cards: list[CardSpec] = []
    index = 0
    for row in range(2):
        for x, width in _columns(2):
            index += 1
            cell_h = (height * 0.86 - GUTTER) / 2
            cell_top = top + row * (cell_h + GUTTER)
            cards.append(CardSpec(bbox=(x, cell_top, width, cell_h), index=index, fill="surface"))
            pad = width * 0.06
            slots.append(
                CompositionSlot(
                    slot_id=f"cell_{index}_title",
                    kind="label",
                    bbox=(x + pad, cell_top + cell_h * 0.12, width - 2 * pad, cell_h * 0.26),
                    text_role="body",
                    bold=True,
                    repeat_group="cells",
                    color_role="accent",
                    on_card=True,
                    card_index=index,
                )
            )
            slots.append(
                CompositionSlot(
                    slot_id=f"cell_{index}_body",
                    kind="caption",
                    bbox=(x + pad, cell_top + cell_h * 0.42, width - 2 * pad, cell_h * 0.44),
                    text_role="label",
                    repeat_group="cells",
                    on_card=True,
                    card_index=index,
                )
            )
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=family.title,
        slots=slots,
        cards=cards,
        supports=family.supports,
        min_items=3,
        max_items=4,
        tags=family.tags,
    )


def build_hero_number(family: Family, params: dict[str, Any]) -> Composition:
    """Одно число во весь слайд: главный итог, эффект, объём."""
    top = _body_top()
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=family.title,
        slots=[
            _title(),
            CompositionSlot(
                slot_id="value",
                kind="number",
                bbox=(AREA_X, top + 0.04, AREA_W * 0.6, 0.26),
                text_role="number",
                color_role="accent",
            ),
            CompositionSlot(
                slot_id="value_label",
                kind="label",
                bbox=(AREA_X, top + 0.32, AREA_W * 0.6, 0.1),
                text_role="subtitle",
                color_role="muted",
            ),
            CompositionSlot(
                slot_id="body",
                kind="body",
                bbox=(AREA_X, top + 0.44, AREA_W * 0.78, 0.18),
                text_role="body",
            ),
        ],
        supports=family.supports,
        min_items=1,
        max_items=2,
        tags=family.tags,
    )


def build_quote(family: Family, params: dict[str, Any]) -> Composition:
    """Цитата с автором: отзыв заказчика, мнение эксперта."""
    with_photo = bool(params.get("photo"))
    top = _body_top()
    text_x = AREA_X + (0.2 if with_photo else 0.0)
    text_w = AREA_W - (0.2 if with_photo else 0.0)
    slots: list[CompositionSlot] = [
        _title(),
        CompositionSlot(
            slot_id="quote",
            kind="body",
            bbox=(text_x, top, text_w, 0.3),
            text_role="subtitle",
            valign="middle",
        ),
        CompositionSlot(
            slot_id="author",
            kind="label",
            bbox=(text_x, top + 0.34, text_w * 0.7, 0.1),
            text_role="label",
            color_role="muted",
        ),
    ]
    if with_photo:
        slots.append(CompositionSlot(slot_id="photo", kind="image", bbox=(AREA_X, top, 0.15, 0.26)))
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=f"{family.title}{' с портретом' if with_photo else ''}",
        slots=slots,
        supports=family.supports,
        min_items=1,
        max_items=2,
        tags=family.tags,
    )


def build_agenda(family: Family, params: dict[str, Any]) -> Composition:
    """Оглавление: пункты колоды с номерами в одну или две колонки."""
    cols = int(params["cols"])
    top = _body_top()
    height = AREA_Y + AREA_H - top
    slots: list[CompositionSlot] = [_title()]
    for index, (x, width) in enumerate(_columns(cols), start=1):
        slots.append(
            CompositionSlot(
                slot_id=f"agenda_{index}",
                kind="bullets",
                bbox=(x, top, width, height * 0.8),
                text_role="subtitle" if cols == 1 else "body",
                repeat_group="agenda" if cols > 1 else "",
            )
        )
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=f"{family.title}: {cols} колонка" if cols == 1 else f"{family.title}: {cols} колонки",
        slots=slots,
        supports=family.supports,
        min_items=2,
        max_items=6 * cols,
        tags=family.tags,
    )


def build_section(family: Family, params: dict[str, Any]) -> Composition:
    """Разделитель: номер раздела и его название крупно."""
    numbered = bool(params.get("numbered"))
    slots: list[CompositionSlot] = []
    top = 0.34
    if numbered:
        slots.append(
            CompositionSlot(
                slot_id="section_number",
                kind="number",
                bbox=(AREA_X, top - 0.12, 0.2, 0.12),
                text_role="number",
                color_role="accent",
            )
        )
    slots.append(
        CompositionSlot(
            slot_id="title",
            kind="title",
            bbox=(AREA_X, top, AREA_W * 0.8, 0.2),
            text_role="title",
            required=True,
            valign="middle",
        )
    )
    slots.append(
        CompositionSlot(
            slot_id="lead",
            kind="caption",
            bbox=(AREA_X, top + 0.22, AREA_W * 0.6, 0.1),
            text_role="label",
            color_role="muted",
        )
    )
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=f"{family.title}{' с номером' if numbered else ''}",
        slots=slots,
        supports=family.supports,
        min_items=1,
        max_items=1,
        tags=family.tags,
    )


def build_title_slide(family: Family, params: dict[str, Any]) -> Composition:
    """Титульный слайд: тема, подзаголовок, автор и дата."""
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=family.title,
        slots=[
            CompositionSlot(
                slot_id="title",
                kind="title",
                bbox=(AREA_X, 0.3, AREA_W * 0.82, 0.22),
                text_role="title",
                required=True,
                valign="middle",
            ),
            CompositionSlot(
                slot_id="subtitle",
                kind="subtitle",
                bbox=(AREA_X, 0.54, AREA_W * 0.7, 0.12),
                text_role="subtitle",
                color_role="muted",
            ),
            CompositionSlot(
                slot_id="date",
                kind="date",
                bbox=(AREA_X, 0.72, AREA_W * 0.4, 0.08),
                text_role="label",
                color_role="muted",
            ),
        ],
        supports=family.supports,
        min_items=1,
        max_items=1,
        tags=family.tags,
    )


def build_closing(family: Family, params: dict[str, Any]) -> Composition:
    """Финальный слайд: благодарность и контакты."""
    return Composition(
        composition_id=composition_id(family.name, params),
        family=family.name,
        role=family.role,
        name=family.title,
        slots=[
            CompositionSlot(
                slot_id="title",
                kind="title",
                bbox=(AREA_X, 0.38, AREA_W * 0.8, 0.18),
                text_role="title",
                required=True,
                valign="middle",
            ),
            CompositionSlot(
                slot_id="contacts",
                kind="caption",
                bbox=(AREA_X, 0.58, AREA_W * 0.6, 0.12),
                text_role="label",
                color_role="muted",
            ),
        ],
        supports=family.supports,
        min_items=1,
        max_items=1,
        tags=family.tags,
    )


EXTRA_REGISTRY: tuple[Family, ...] = (
    Family(
        name="image_split",
        role="two_column",
        title="Картинка и текст",
        params={"side": ["left", "right"], "bullets": [False, True]},
        builder=build_image_split,
        supports=("image",),
        tags=("builtin", "media"),
    ),
    Family(
        name="image_full",
        role="image_full",
        title="Картинка во всю ширину",
        params={"caption": [False, True]},
        builder=build_image_full,
        supports=("image",),
        tags=("builtin", "media"),
    ),
    Family(
        name="gallery",
        role="screenshot",
        title="Галерея",
        params={"count": [2, 3, 4]},
        builder=build_gallery,
        supports=("image",),
        tags=("builtin", "media"),
    ),
    Family(
        name="icon_cards",
        role="cards",
        title="Карточки с пиктограммами",
        params={"count": [3, 4]},
        builder=build_icon_cards,
        supports=("icon",),
        tags=("builtin", "cards"),
    ),
    Family(
        name="dashboard",
        role="chart",
        title="Несколько диаграмм",
        params={"count": [2, 3]},
        builder=build_dashboard,
        supports=("chart",),
        tags=("builtin", "data"),
    ),
    Family(
        name="chart_kpi",
        role="chart",
        title="Диаграмма и показатели",
        params={"count": [2, 3]},
        builder=build_chart_kpi,
        supports=("chart", "number"),
        tags=("builtin", "data"),
    ),
    Family(
        name="diagram_pane",
        role="process",
        title="Схема",
        params={"lead": [False, True]},
        builder=build_diagram_pane,
        supports=("diagram",),
        tags=("builtin", "diagram"),
    ),
    Family(
        name="matrix",
        role="comparison",
        title="Матрица 2 × 2",
        params={},
        builder=build_matrix,
        tags=("builtin",),
    ),
    Family(
        name="hero_number",
        role="kpi",
        title="Главное число",
        params={},
        builder=build_hero_number,
        supports=("number",),
        tags=("builtin", "numbers"),
    ),
    Family(
        name="quote",
        role="quote",
        title="Цитата",
        params={"photo": [False, True]},
        builder=build_quote,
        supports=("image",),
        tags=("builtin",),
    ),
    Family(
        name="agenda",
        role="agenda",
        title="Оглавление",
        params={"cols": [1, 2]},
        builder=build_agenda,
        tags=("builtin", "service"),
    ),
    Family(
        name="section",
        role="section_divider",
        title="Разделитель",
        params={"numbered": [False, True]},
        builder=build_section,
        tags=("builtin", "service"),
    ),
    Family(
        name="title_slide",
        role="title",
        title="Титульный слайд",
        params={},
        builder=build_title_slide,
        tags=("builtin", "service"),
    ),
    Family(
        name="closing",
        role="thanks",
        title="Финальный слайд",
        params={},
        builder=build_closing,
        tags=("builtin", "service"),
    ),
)

REGISTRY = REGISTRY + EXTRA_REGISTRY
