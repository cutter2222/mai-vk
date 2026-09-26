"""Роутер чата (этап 37): что делать с сообщением к готовой презентации.

Сначала правила — однозначное решается без модели: отмена, логотип, `/edit`, починка
замечаний, картинка во вложении, выделенный объект, номер слайда без названного объекта.
Остальное — скилл `chat_router`: он видит оглавление колоды и объекты слайда и отвечает
шагами для существующих исполнителей, вопросом с кнопками, отказом с альтернативой или
передачей ассистенту. Ответ модели проверяется по снимку: слайды и объекты должны быть.

Исполнители (правка на месте, перестройка слайда, текст колоды, логотип, картинка, починка,
отмена) живут там же, где раньше; роутер только выбирает. Номера фигур копии меняются при
каждом сохранении ONLYOFFICE, поэтому объект шага называется именем и рамкой.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from presentation_designer.generation.snapshot import outline_lines

JsonDict = dict[str, Any]
Action = Literal[
    "object_edit", "slide_rebuild", "deck_text", "logo", "image", "table", "chart", "repair",
    "undo",
]  # fmt: skip
Kind = Literal["run", "question", "refusal", "answer"]
EMU_PER_MM = 36000

# --- числа словами -------------------------------------------------------------------------

_UNITS = {
    "один": 1, "одна": 1, "одно": 1, "одного": 1, "одном": 1,
    "два": 2, "две": 2, "двух": 2, "двум": 2,
    "три": 3, "трех": 3, "трем": 3,
    "четыре": 4, "четырех": 4, "четырем": 4,
    "пять": 5, "пяти": 5, "шесть": 6, "шести": 6, "семь": 7, "семи": 7,
    "восемь": 8, "восьми": 8, "девять": 9, "девяти": 9,
}  # fmt: skip
_TEENS = {
    "десять": 10, "десяти": 10, "одиннадцать": 11, "одиннадцати": 11,
    "двенадцать": 12, "двенадцати": 12, "тринадцать": 13, "тринадцати": 13,
    "четырнадцать": 14, "четырнадцати": 14, "пятнадцать": 15, "пятнадцати": 15,
    "шестнадцать": 16, "шестнадцати": 16, "семнадцать": 17, "семнадцати": 17,
    "восемнадцать": 18, "восемнадцати": 18, "девятнадцать": 19, "девятнадцати": 19,
}  # fmt: skip
_TENS = {
    "двадцать": 20, "двадцати": 20, "тридцать": 30, "тридцати": 30,
    "сорок": 40, "сорока": 40, "пятьдесят": 50, "пятидесяти": 50,
}  # fmt: skip
# Порядковые — по основе: «третьем», «пятого», «двадцатый».
_ORDINAL_STEMS = [
    ("одиннадцат", 11), ("двенадцат", 12), ("тринадцат", 13), ("четырнадцат", 14),
    ("пятнадцат", 15), ("шестнадцат", 16), ("семнадцат", 17), ("восемнадцат", 18),
    ("девятнадцат", 19), ("двадцат", 20), ("тридцат", 30), ("сороков", 40),
    ("пятидесят", 50), ("перв", 1), ("втор", 2), ("трет", 3), ("четверт", 4), ("пят", 5),
    ("шест", 6), ("седьм", 7), ("восьм", 8), ("девят", 9), ("десят", 10),
]  # fmt: skip
_ORDINAL_ENDINGS = (
    "ый", "ой", "ий", "ая", "ое", "ые", "ого", "ому", "ым", "ом", "ую", "ых", "ыми", "ей",
    "ья", "ье", "ьего", "ьему", "ьим", "ьем", "ьей", "ьих", "ью",
)  # fmt: skip


def _ordinal(word: str) -> int | None:
    for stem, value in _ORDINAL_STEMS:
        if word.startswith(stem) and word[len(stem) :] in _ORDINAL_ENDINGS:
            return value
    return None


def _cardinal(word: str) -> int | None:
    return _UNITS.get(word) or _TEENS.get(word) or _TENS.get(word)


def normalize(text: str) -> str:
    """Числа словами — цифрами: «на слайде три» → «на слайде 3», «двадцать пятый» → «25-й».
    Порядковые получают «-й», чтобы «третьем слайде» и «3 слайда» (счёт) различались."""
    words = re.split(r"(\s+|[,.;:!?()«»\"])", text)
    out: list[str] = []
    i = 0
    while i < len(words):
        token = words[i]
        low = token.lower().replace("ё", "е")
        tens = _TENS.get(low)
        if tens is not None and i + 2 < len(words) and not words[i + 1].strip():
            nxt = words[i + 2].lower().replace("ё", "е")
            unit = _UNITS.get(nxt)
            unit_ord = _ordinal(nxt) if not unit else None
            if unit and unit < 10:
                out.append(str(tens + unit))
                i += 3
                continue
            if unit_ord and unit_ord < 10:
                out.append(f"{tens + unit_ord}-й")
                i += 3
                continue
        value = _cardinal(low)
        if value is not None:
            out.append(str(value))
        elif (ordinal := _ordinal(low)) is not None:
            out.append(f"{ordinal}-й")
        else:
            out.append(token)
        i += 1
    return "".join(out)


# --- адрес слайдов -------------------------------------------------------------------------

_SLIDE = r"слайд[а-я]*"


def slides_in(text: str, count: int) -> list[int]:
    """Номера слайдов из нормализованного текста: «слайд 3», «слайды 3, 5 и 7», «слайды
    3–5», «на 3-м слайде», «последний/первый/титульный слайд», «на последнем»."""
    t = text.lower().replace("ё", "е")
    found: list[int] = []
    for group in re.findall(
        _SLIDE + r"\s*(?:№\s*)?(\d{1,3}(?:-й)?(?:\s*(?:,|и|-|–|—|по)\s*\d{1,3}(?:-й)?)*)(?!\d)",
        t,
    ):
        group = group.replace("-й", "")
        for start, end in re.findall(r"(\d{1,3})(?:\s*(?:-|–|—|по)\s*(\d{1,3}))?", group):
            first, last = int(start), int(end or start)
            found.extend(range(first, min(last, first + 50) + 1) if last >= first else [first])
    # «на 3-м слайде», «3-й и 5-й слайды»; «3 слайда» (счёт) адресом не считается.
    for group in re.findall(
        r"((?:\d{1,3}-(?:й|я|е|м|го|му|ом)\s*(?:,|и)?\s*)+)(?:" + _SLIDE + r")", t
    ):
        found.extend(int(n) for n in re.findall(r"(\d{1,3})-", group))
    if count:
        if re.search(r"последн[а-я]*\s+" + _SLIDE + r"|на\s+последнем(?![а-я])", t):
            found.append(count)
        if re.search(r"предпоследн[а-я]*\s+" + _SLIDE, t) and count > 1:
            found.append(count - 1)
        if re.search(r"(титульн|обложк)[а-я]*", t):
            found.append(1)
    return sorted({n for n in found if n >= 1})


# --- правила -------------------------------------------------------------------------------

UNDO = re.compile(
    r"^\s*(отмени(те)?|отмена|откати(ть)?|откат|верни(те)?(\s+(вс[её]|обратно|назад|как\s+было|"
    r"прежн[а-я]*|предыдущ[а-я]*))+|верни\s+как\s+было|вернуть\s+как\s+было|назад)"
    r"(\s+(последн[а-я]*\s+)?(правк[а-я]*|изменени[а-я]*|это|то|пожалуйста))*[\s.!]*$",
    re.I,
)
REPAIR = re.compile(
    r"(исправ|почин|устран|поправ)[а-я]*\s+(все\s+|эти\s+)?(замечани|ошибк[а-я]*\s+проверк|"
    r"находк|проблем[а-я]*\s+проверк|недочет)",
    re.I,
)
LOGO = re.compile(r"(логотип|лого(?![а-яё])|logo)", re.I)
LOGO_REMOVE = re.compile(r"(убер|убра|удал|сним|скрой|спрячь|без\s+логотип)", re.I)
LOGO_REPLACE = re.compile(r"(замен|помен|постав|вставь|обнови|сделай|наш|свой|друг)", re.I)
QUESTION = re.compile(
    r"\?\s*$|^(что|как|почему|зачем|сколько|какой|какая|какие|каких|где|когда|о\s+ч[её]м|"
    r"покажи|расскажи|есть\s+ли)(?![а-я])",
    re.I,
)
# Просьба в вопросительной форме: «а можно две колонки?» — правка, а не вопрос.
ASKS_EDIT = re.compile(
    r"^\s*(а\s+)?(можно|нельзя\s+ли)(?![а-я])|"
    r"(можно|могли\s+бы|можешь|сможешь|давай)[^?]*\b(сдела|помен|замени|убер|убра|удал|добав|"
    r"сократ|увелич|уменьш|перенес|передвин|постав|вставь|переделай|перепиш|раздел)",
    re.I,
)
# Раскладка слайда: число мест или другая подача — перестройка, даже если названа «карточка».
LAYOUT = re.compile(
    r"\b\d+\s*(колон|столб|карточ|пункт|блок|шаг|показател)|карточками|списком|таблицей|"
    r"графиком|диаграммой|показателями|схемой|цитатой|в\s+столбик|в\s+строку",
    re.I,
)
# Недоступное исполнителям или требующее выбора — решает модель (отказ с альтернативой).
BEYOND = re.compile(
    r"анимац|переход|видео|звук|музык|сгенерир|нарисуй|шаблон|удали\w*\s+слайд|"
    r"добав\w*\s+(нов\w+\s+)?слайд|дублир|продублир|перестав\w*\s+слайд|скрой\s+слайд",
    re.I,
)
# Оформление объекта: при выделенном объекте — правка на месте правилом, без выделения —
# модель находит объект по словам.
STYLE = re.compile(
    r"цвет|красн|син(им|ий|ее)|зел[её]н|ж[её]лт|черн|бел(ым|ый)|жирн|курсив|подчерк|шрифт|"
    r"кегл|крупнее|мельче|больше|меньше|по\s+центру|по\s+левому|по\s+правому|выровня|шире|уже|"
    r"выше|ниже|удали|убери",
    re.I,
)
# Объект, названный словами: без модели не понять, какой он на слайде.
NAMES_OBJECT = re.compile(
    r"заголов|подзаголов|подпис|подпиш|картинк|фото|скриншот|изображ|иконк|таблиц|диаграмм|"
    r"график|карточк|пункт|тезис|абзац|блок|кнопк|цифр|числ|показател|плашк|надпис|сноск|"
    r"колонк|столб|спикер",
    re.I,
)
# Диаграммы (этап 40): вложение с просьбой о графике, «сделай редактируемой», смена подачи.
CHART_WORD = re.compile(r"диаграмм|график", re.I)
PICTURE_WORD = re.compile(r"картинк|фото|изображени|скриншот|скрин", re.I)
EDITABLE = re.compile(r"редактируем", re.I)
CONVERT = re.compile(
    r"таблиц(ей|у)|в\s+виде\s+таблиц|график(ом)?(?![а-я])|диаграмм(ой|у)(?![а-я])", re.I
)
DESIGN_REPLIES = {"по шаблону", "смешанный", "все слайды новые"}


@dataclass
class Target:
    """Объект правки на месте: имя фигуры, рамка в мм (у фигуры в группе — от угла группы) и
    подпись для ленты."""

    slide: int
    name: str
    label: str = ""
    box: JsonDict | None = None
    in_group: bool = False

    def as_dict(self) -> JsonDict:
        out: JsonDict = {"slide": self.slide, "name": self.name, "label": self.label}
        if self.box is not None:
            out["box"] = self.box
        if self.in_group:
            out["in_group"] = True
        return out


@dataclass
class Step:
    action: Action
    slides: list[int] = field(default_factory=list)
    instruction: str = ""
    target: Target | None = None
    file_id: str | None = None
    logo: Literal["replace", "remove"] | None = None
    # Диаграмма: из файла, по картинке графика или «сделай редактируемой».
    source: Literal["file", "image", "editable"] | None = None

    def as_dict(self) -> JsonDict:
        out: JsonDict = {"action": self.action, "slides": self.slides}
        if self.instruction:
            out["instruction"] = self.instruction
        if self.target is not None:
            out["target"] = self.target.as_dict()
        if self.file_id:
            out["file_id"] = self.file_id
        if self.logo:
            out["logo"] = self.logo
        if self.source:
            out["source"] = self.source
        return out


@dataclass
class Decision:
    kind: Kind
    steps: list[Step] = field(default_factory=list)
    text: str = ""
    options: list[str] = field(default_factory=list)
    source: Literal["rules", "model"] = "rules"
    normalized: str = ""

    def as_dict(self) -> JsonDict:
        return {
            "kind": self.kind,
            "steps": [s.as_dict() for s in self.steps],
            "text": self.text,
            "options": self.options,
            "source": self.source,
            "normalized": self.normalized,
        }


@dataclass
class Context:
    """Что знает роутер: сообщение, плашка, вложения и открытая презентация."""

    text: str
    slide_count: int = 0
    # Плашка живого редактора: слайд и, если выделен, объект.
    chip_slide: int | None = None
    chip_object: Target | None = None
    # Слайд, открытый в редакторе, даже если плашку сняли: место для картинки.
    current_slide: int | None = None
    pictures: list[str] = field(default_factory=list)  # file_id картинок во вложениях
    sheets: list[str] = field(default_factory=list)  # file_id таблиц (xlsx, csv) во вложениях
    can_rebuild: bool = True  # у открытой презентации есть план варианта
    snapshot: JsonDict | None = None
    last_edit: JsonDict | None = None
    normalized: str = ""

    def __post_init__(self) -> None:
        self.normalized = normalize(self.text)


def by_rules(ctx: Context) -> Decision | None:
    """Решение правилами или None — нужна модель."""
    text = ctx.text.strip()
    norm = ctx.normalized
    if not text:
        return None
    low = norm.lower().replace("ё", "е")
    if UNDO.match(low):
        return Decision("run", [Step("undo")], normalized=norm)
    if low.strip(" .!") in DESIGN_REPLIES:
        return Decision("answer", normalized=norm)
    addressed = slides_in(norm, ctx.slide_count)
    if LOGO.search(low):
        if LOGO_REMOVE.search(low):
            return Decision("run", [Step("logo", logo="remove", instruction=text)], normalized=norm)
        if LOGO_REPLACE.search(low):
            step = Step("logo", logo="replace", instruction=text)
            step.file_id = ctx.pictures[0] if ctx.pictures else None
            return Decision("run", [step], normalized=norm)
    if text.startswith("/edit"):
        instruction = text[len("/edit") :].strip()
        return Decision("run", [Step("deck_text", instruction=instruction)], normalized=norm)
    if REPAIR.search(low):
        return Decision("run", [Step("repair", slides=addressed)], normalized=norm)
    chart = _chart_rule(ctx, low, addressed)
    if chart is not None:
        return chart
    if ctx.sheets and not ctx.pictures:
        slide = addressed[0] if addressed else ctx.chip_slide or ctx.current_slide
        if slide:
            step = Step("table", slides=[slide], instruction=text, file_id=ctx.sheets[0])
            return Decision("run", [step], normalized=norm)
        return Decision(
            "question",
            text="На какой слайд поставить таблицу из файла? Напишите номер и место, например: "
            "«таблицу на слайд 5 справа».",
            normalized=norm,
        )
    if ctx.pictures:
        slide = addressed[0] if addressed else ctx.chip_slide or ctx.current_slide
        if slide:
            step = Step("image", slides=[slide], instruction=text, file_id=ctx.pictures[0])
            if ctx.chip_object is not None and ctx.chip_object.slide == slide:
                step.target = ctx.chip_object
            return Decision("run", [step], normalized=norm)
        return Decision(
            "question",
            text="На какой слайд поставить картинку? Напишите номер и место, например: "
            "«на слайд 3 справа».",
            normalized=norm,
        )
    question = bool(QUESTION.search(low)) and not ASKS_EDIT.search(low)
    if question:
        return Decision("answer", normalized=norm)
    if BEYOND.search(low):
        return None
    if ctx.chip_object is not None and (not addressed or addressed == [ctx.chip_object.slide]):
        return Decision(
            "run",
            [Step("object_edit", slides=[ctx.chip_object.slide], instruction=text,
                  target=ctx.chip_object)],
            normalized=norm,
        )  # fmt: skip
    slides = addressed or ([ctx.chip_slide] if ctx.chip_slide else [])
    if slides and any(n > ctx.slide_count for n in slides) and ctx.slide_count:
        missing = next(n for n in slides if n > ctx.slide_count)
        return Decision(
            "refusal",
            text=f"В презентации {ctx.slide_count} {_plural_slides(ctx.slide_count)}, "
            f"слайда {missing} нет.",
            normalized=norm,
        )
    # Номер слайда — адрес, а не число мест: «на слайд 2 пункт» — не «2 пункта».
    bare = re.sub(_SLIDE + r"\s*(?:№\s*)?\d{1,3}(?:-й)?|\d{1,3}-[а-я]{1,3}\s+" + _SLIDE, " ", low)
    if len(slides) == 1:
        on_place = _chart_or_table_edit(ctx, slides[0], bare, text)
        if on_place is not None:
            return Decision("run", [on_place], normalized=norm)
    if slides and STYLE.search(bare) and not LAYOUT.search(bare):
        return None
    if slides and (LAYOUT.search(bare) or not NAMES_OBJECT.search(bare)):
        if ctx.can_rebuild:
            return Decision(
                "run", [Step("slide_rebuild", slides=slides, instruction=text)], normalized=norm
            )
        return Decision(
            "run", [Step("deck_text", slides=slides, instruction=text)], normalized=norm
        )
    return None


def _chart_rule(ctx: Context, low: str, addressed: list[int]) -> Decision | None:
    """Новая диаграмма: из xlsx/csv, по картинке графика или «сделай редактируемой»."""
    if not CHART_WORD.search(low):
        return None
    source: Literal["file", "image", "editable"] | None = None
    file_id = None
    if ctx.sheets and not ctx.pictures:
        source, file_id = "file", ctx.sheets[0]
    elif ctx.pictures and not PICTURE_WORD.search(low) and not ctx.sheets:
        source, file_id = "image", ctx.pictures[0]
    elif not ctx.pictures and not ctx.sheets and EDITABLE.search(low):
        source = "editable"
    if source is None:
        return None
    slide = addressed[0] if addressed else ctx.chip_slide or ctx.current_slide
    if not slide:
        return Decision(
            "question",
            text="На каком слайде? Напишите номер и место, например: «диаграмму на слайд 5 "
            "справа».",
            normalized=ctx.normalized,
        )
    step = Step("chart", slides=[slide], instruction=ctx.text.strip(), file_id=file_id)
    step.source = source
    return Decision("run", [step], normalized=ctx.normalized)


def _numbers(text: str) -> set[str]:
    return {n.replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", text)}


def _chart_or_table_edit(ctx: Context, slide: int, bare: str, text: str) -> Step | None:
    """Правка на месте единственной диаграммы или таблицы слайда без выделения: числа из её
    данных («2024 — 120, а не 100») или смена подачи в копии без плана («покажи таблицей»)."""
    page = _slide(ctx.snapshot, slide)
    if page is None:
        return None
    charts = [o for o in page.get("objects") or [] if o.get("kind") == "chart"]
    tables = [o for o in page.get("objects") or [] if o.get("kind") == "table"]
    chosen = None
    if len(charts) == 1 and not NAMES_OBJECT.search(re.sub(CHART_WORD, " ", bare)):
        data = charts[0].get("chart") or {}
        known = {str(c) for c in data.get("categories") or []}
        known |= {
            f"{v:g}"
            for s in data.get("series") or []
            for v in s.get("values") or []
            if v is not None
        }
        if _numbers(bare) & known:
            chosen = charts[0]
    if chosen is None and not ctx.can_rebuild and CONVERT.search(bare):
        if len(charts) + len(tables) == 1:
            chosen = (charts or tables)[0]
    if chosen is None:
        return None
    target = target_of(ctx.snapshot or {}, slide, str(chosen["address"]["object_id"]))
    if target is None:
        return None
    return Step("object_edit", slides=[slide], instruction=text, target=target)


def fallback(ctx: Context) -> Decision:
    """Без модели: слайд назван — перестройка (или текст на слайде), иначе — ассистент."""
    slides = slides_in(ctx.normalized, ctx.slide_count) or (
        [ctx.chip_slide] if ctx.chip_slide else []
    )
    if slides:
        action: Action = "slide_rebuild" if ctx.can_rebuild else "deck_text"
        return Decision(
            "run", [Step(action, slides=slides, instruction=ctx.text.strip())],
            normalized=ctx.normalized,
        )  # fmt: skip
    return Decision("answer", normalized=ctx.normalized)


def _plural_slides(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "слайд"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "слайда"
    return "слайдов"


# --- модель --------------------------------------------------------------------------------

STEP_SCHEMA: JsonDict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "slides", "object_id", "instruction"],
    "properties": {
        "action": {"enum": ["object_edit", "slide_rebuild", "deck_text", "repair", "undo"]},
        "slides": {"type": "array", "items": {"type": "integer", "minimum": 1}, "maxItems": 20},
        "object_id": {"type": ["string", "null"], "maxLength": 20},
        "instruction": {"type": "string", "maxLength": 1500},
    },
}
ROUTE_SCHEMA: JsonDict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "steps", "text", "options"],
    "properties": {
        "kind": {"enum": ["run", "question", "refusal", "answer"]},
        "steps": {"type": "array", "items": STEP_SCHEMA, "maxItems": 8},
        "text": {"type": "string", "maxLength": 600},
        "options": {"type": "array", "items": {"type": "string", "maxLength": 80}, "maxItems": 3},
    },
}


def _slide(snapshot: JsonDict | None, index: int) -> JsonDict | None:
    for slide in (snapshot or {}).get("slides") or []:
        if slide.get("index") == index:
            return dict(slide)
    return None


def _objects(slide: JsonDict) -> list[JsonDict]:
    """Объекты слайда для модели: содержательные, без номера, колонтитула и фона."""
    out = []
    for obj in slide.get("objects") or []:
        if obj.get("fixed") or obj.get("role") in ("background", "decoration", "group"):
            continue
        text = ((obj.get("text") or {}).get("plain") or "").strip()
        if obj.get("kind") in ("placeholder_empty", "connector") and not text:
            continue
        box = obj.get("bbox") or {}
        out.append(
            {
                "object_id": obj["address"]["object_id"],
                "role": obj.get("role"),
                "kind": obj.get("kind"),
                "text": text[:160],
                "where": _where(box),
            }
        )
    return out[:40]


def _where(box: JsonDict) -> str:
    """Положение словами: модели проще сопоставить «справа», «внизу», чем доли."""
    x = float(box.get("x", 0)) + float(box.get("width", 0)) / 2
    y = float(box.get("y", 0)) + float(box.get("height", 0)) / 2
    horizontal = "слева" if x < 0.36 else "справа" if x > 0.64 else "по центру"
    vertical = "вверху" if y < 0.33 else "внизу" if y > 0.67 else "посередине"
    return f"{vertical}, {horizontal}"


def target_of(snapshot: JsonDict, slide_index: int, object_id: str) -> Target | None:
    """Объект снимка — адрес правки на месте: имя и рамка в мм (в группе — от угла группы)."""
    slide = _slide(snapshot, slide_index)
    if slide is None:
        return None
    objects = {o["address"]["object_id"]: o for o in slide.get("objects") or []}
    obj = objects.get(object_id)
    if obj is None or not obj.get("name"):
        return None
    size = snapshot.get("slide_size") or {}
    width = float(size.get("width_emu") or 0) / EMU_PER_MM
    height = float(size.get("height_emu") or 0) / EMU_PER_MM
    box = obj.get("bbox") or {}
    x, y = float(box.get("x", 0)), float(box.get("y", 0))
    group_path = obj["address"].get("group_path") or []
    if group_path and (parent := objects.get(group_path[-1])) is not None:
        x -= float(parent["bbox"]["x"])
        y -= float(parent["bbox"]["y"])
    text = ((obj.get("text") or {}).get("plain") or "").strip()
    label = f"«{text[:47]}…»" if len(text) > 48 else f"«{text}»" if text else str(obj["name"])
    return Target(
        slide=slide_index,
        name=str(obj["name"]),
        label=label,
        box={
            "x": round(x * width, 2),
            "y": round(y * height, 2),
            "width": round(float(box.get("width", 0)) * width, 2),
            "height": round(float(box.get("height", 0)) * height, 2),
        }
        if width and height
        else None,
        in_group=bool(group_path),
    )


KIND_WORDS = {
    "table": re.compile(r"таблиц", re.I),
    "chart": re.compile(r"диаграмм|график", re.I),
    "picture": re.compile(r"картинк|фото|изображени", re.I),
}


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[а-яёa-z]{4,}", text.lower().replace("ё", "е"))}


def candidates(ctx: Context) -> list[int]:
    """Слайды, о которых, похоже, речь: названы номером, выделены, совпадают словами заголовка
    («в таблице тарифов» — «Тарифы») или видом объекта («в таблице»). Не больше трёх."""
    named = slides_in(ctx.normalized, ctx.slide_count)
    if named:
        return named[:3]
    if ctx.chip_slide:
        return [ctx.chip_slide]
    words = _stems(ctx.text)
    scored: list[tuple[int, int]] = []
    for entry in (ctx.snapshot or {}).get("outline") or []:
        score = 2 * len(words & _stems(str(entry.get("title") or "")))
        score += sum(
            1
            for kind, rx in KIND_WORDS.items()
            if kind in (entry.get("kinds") or []) and rx.search(ctx.text)
        )
        if score:
            scored.append((score, int(entry["index"])))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [index for _, index in scored[:3]]


def model_input(ctx: Context) -> str:
    """Контекст модели одним JSON: сообщение, адрес, оглавление, объекты слайдов-кандидатов."""
    slides = []
    for index in candidates(ctx):
        slide = _slide(ctx.snapshot, index)
        if slide is not None:
            slides.append({"index": index, "title": slide.get("title"), "objects": _objects(slide)})
    payload: JsonDict = {
        "message": ctx.text.strip()[:2000],
        "normalized": ctx.normalized[:2000] if ctx.normalized != ctx.text else None,
        "chip_slide": ctx.chip_slide,
        "chip_object": ctx.chip_object.label if ctx.chip_object else None,
        "slide_count": ctx.slide_count,
        "can_rebuild": ctx.can_rebuild,
        "outline": outline_lines(ctx.snapshot)[:80] if ctx.snapshot else [],
        "slides": slides or None,
        "last_edit": ctx.last_edit,
    }
    return json.dumps({k: v for k, v in payload.items() if v is not None}, ensure_ascii=False)


def check(value: JsonDict, ctx: Context) -> Decision:
    """Ответ модели → решение; неверный адрес — ValueError (клиент повторит запрос)."""
    kind = value.get("kind")
    text = str(value.get("text") or "").strip()
    options = [str(o).strip() for o in value.get("options") or [] if str(o).strip()][:3]
    if kind == "question":
        if not text:
            raise ValueError("у вопроса нет текста")
        return Decision("question", text=text, options=options, source="model",
                        normalized=ctx.normalized)  # fmt: skip
    if kind == "refusal":
        if not text:
            raise ValueError("у отказа нет причины")
        return Decision("refusal", text=text, options=options, source="model",
                        normalized=ctx.normalized)  # fmt: skip
    if kind == "answer":
        return Decision("answer", source="model", normalized=ctx.normalized)
    steps: list[Step] = []
    for raw in value.get("steps") or []:
        action = raw.get("action")
        slides = sorted({int(n) for n in raw.get("slides") or []})
        instruction = str(raw.get("instruction") or "").strip() or ctx.text.strip()
        if ctx.slide_count and any(n > ctx.slide_count for n in slides):
            raise ValueError(f"слайдов {slides} нет: в колоде {ctx.slide_count}")
        if action == "object_edit":
            object_id = raw.get("object_id")
            if len(slides) != 1 or not object_id:
                raise ValueError("правке объекта нужны один слайд и object_id")
            target = target_of(ctx.snapshot or {}, slides[0], str(object_id))
            if target is None:
                raise ValueError(f"объекта {object_id} нет на слайде {slides[0]}")
            steps.append(Step("object_edit", slides, instruction, target=target))
        elif action == "slide_rebuild":
            if not slides:
                raise ValueError("перестройке нужен слайд")
            if ctx.can_rebuild:
                steps.append(Step("slide_rebuild", slides, instruction))
            else:
                steps.append(Step("deck_text", slides, instruction))
        elif action in ("deck_text", "repair", "undo"):
            steps.append(Step(action, slides, instruction if action == "deck_text" else ""))
        else:
            raise ValueError(f"неизвестный шаг {action}")
    if not steps:
        raise ValueError("у решения run нет шагов")
    return Decision("run", steps, text=text, source="model", normalized=ctx.normalized)


def decide(ctx: Context, client: Any = None, skill: Any = None, deadline_s: float = 30) -> Decision:
    """Правила, затем модель; без модели — простое правило (`fallback`)."""
    ruled = by_rules(ctx)
    if ruled is not None:
        return ruled
    if client is None or skill is None:
        return fallback(ctx)
    from presentation_designer.llm.types import Deadline

    req = skill.request("chat.route", model_input(ctx), schema=ROUTE_SCHEMA, stage="plan")
    req.schema_name = "chat_route"
    req.deadline = Deadline.after(deadline_s)

    def validate(value: object) -> Decision:
        if not isinstance(value, dict):
            raise ValueError("ответ роутера — не объект")
        return check(value, ctx)

    try:
        response = asyncio.run(client.complete(req, validator=validate))
        # Клиент кладёт в parsed то, что вернул валидатор, — готовое решение.
        parsed = response.parsed
        return parsed if isinstance(parsed, Decision) else validate(parsed)
    except Exception:
        return fallback(ctx)


__all__ = [
    "Context", "Decision", "Step", "Target", "by_rules", "check", "decide", "fallback",
    "model_input", "normalize", "slides_in", "target_of",
]  # fmt: skip
