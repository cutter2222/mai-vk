"""Реестр проверок аудита: что именно сервис проверяет и почему.

Список собран по Приложению 1 ТЗ и разделён так же, как там: вёрстка, шаблон, плотность,
целостность, содержание. Приложение названо «расширяемым ориентиром», поэтому пороги вынесены
в сам реестр — их видно и можно обсуждать, не читая код проверок.

Вид проверки — не то же самое, что её серьёзность:

* `deterministic` — считается по файлу и ComposedDeck: координаты, размеры, коды цветов,
  ссылки на макеты. На одном и том же слайде всегда даёт один и тот же ответ.
* `contextual` — про смысл, поэтому выполняется моделью по картинке слайда и на повторном
  запуске может ответить иначе.

`severity` задаётся здесь, а не в месте находки: одна и та же проверка не должна в разных
слайдах считаться то ошибкой, то замечанием.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

JsonDict = dict[str, Any]


@dataclass(frozen=True)
class Check:
    """Одна проверка реестра."""

    check_id: str
    name: str
    category: str  # layout | template | density | integrity | content
    kind: str  # deterministic | contextual
    severity: str  # blocking | error | warning | info
    scope: str = "slide"  # slide | deck
    threshold: JsonDict = field(default_factory=dict)
    note: str = ""
    # Чем чинится находка. `cost` говорит, во что обойдётся: cheap — правка файла,
    # llm — перегенерация текста моделью, rerender — пересборка и повторный рендер.
    fix_strategy: str = "none"
    fix_cost: str = "cheap"
    fix_note: str = ""

    def as_dict(self) -> JsonDict:
        out: JsonDict = {
            "check_id": self.check_id,
            "name": self.name,
            "category": self.category,
            "kind": self.kind,
            "severity": self.severity,
            "scope": self.scope,
        }
        if self.threshold:
            out["threshold"] = dict(self.threshold)
        if self.note:
            out["note"] = self.note
        return out


# ---------- вёрстка ----------

LAYOUT = (
    Check(
        "layout.out_of_bounds",
        "Элемент вышел за границы слайда",
        "layout",
        "deterministic",
        "error",
        threshold={"tolerance": 0.005},
        note="Допуск в полпроцента холста: округление EMU не считается выходом за край.",
        fix_strategy="move_element",
        fix_cost="cheap",
        fix_note="Вернуть объект в границы холста",
    ),
    Check(
        "layout.overlap",
        "Два блока наложились друг на друга",
        "layout",
        "deterministic",
        "error",
        threshold={"min_overlap_ratio": 0.12},
        note="Считается только заметное перекрытие непустых текстовых блоков: тень, подложка "
        "и рамка карточки лежат под текстом по замыслу.",
        fix_strategy="move_element",
        fix_cost="cheap",
        fix_note="Развести блоки или взять композицию просторнее",
    ),
    Check(
        "layout.text_overflow",
        "Текст не поместился в свою рамку",
        "layout",
        "deterministic",
        "error",
        note="Берётся результат измерения из плана (fit.action) и число строк сверх ёмкости.",
        fix_strategy="shrink_text",
        fix_cost="cheap",
        fix_note="Ступень кегля ниже, иначе сократить текст моделью",
    ),
    Check(
        "layout.clipped",
        "Текст обрезан краем слайда",
        "layout",
        "deterministic",
        "error",
        threshold={"tolerance": 0.005},
        fix_strategy="move_element",
        fix_cost="cheap",
        fix_note="Сдвинуть рамку внутрь холста",
    ),
    Check(
        "layout.margins",
        "Контент заходит в поля у краёв",
        "layout",
        "deterministic",
        "warning",
        threshold={"tolerance": 0.01},
        note="Поля берутся из дизайн-системы шаблона; постоянные элементы (логотип, "
        "колонтитул) по замыслу стоят в полях и не проверяются.",
        fix_strategy="move_element",
        fix_cost="cheap",
        fix_note="Подвинуть объект к сетке шаблона",
    ),
    Check(
        "layout.image_distorted",
        "Картинка растянута, пропорции нарушены",
        "layout",
        "deterministic",
        "warning",
        threshold={"max_ratio_delta": 0.08},
        fix_strategy="resize_element",
        fix_cost="cheap",
        fix_note="Вернуть исходные пропорции картинки",
    ),
)

# ---------- шаблон ----------

TEMPLATE = (
    Check(
        "template.font_not_in_template",
        "Шрифт не из шаблона",
        "template",
        "deterministic",
        "error",
        note="Сравнение с гарнитурами профиля. Подмена рендерера (font_substituted) "
        "ошибкой не считается: в файле остаётся имя шрифта шаблона.",
        fix_strategy="replace_font",
        fix_cost="cheap",
        fix_note="Поставить гарнитуру шаблона",
    ),
    Check(
        "template.font_families",
        "Больше двух гарнитур на слайде",
        "template",
        "deterministic",
        "warning",
        threshold={"max_families": 2},
        fix_strategy="replace_font",
        fix_cost="cheap",
        fix_note="Свести слайд к двум гарнитурам",
    ),
    Check(
        "template.size_not_in_scale",
        "Кегль не из типографической шкалы шаблона",
        "template",
        "deterministic",
        "warning",
        threshold={"tolerance_pt": 0.6},
        note="Лестница ёмкости уменьшает кегль ступенями шкалы; промежуточные значения "
        "означают, что размер выбран мимо неё.",
        fix_strategy="shrink_text",
        fix_cost="cheap",
        fix_note="Ближайшая ступень шкалы шаблона",
    ),
    Check(
        "template.color_not_in_palette",
        "Цвет не из палитры шаблона",
        "template",
        "deterministic",
        "warning",
        threshold={"max_distance": 24},
        note="Сравнение по расстоянию в RGB: оттенок, полученный затемнением цвета палитры, "
        "остаётся её цветом.",
        fix_strategy="recolor",
        fix_cost="cheap",
        fix_note="Ближайший цвет палитры шаблона",
    ),
    Check(
        "template.layout_not_from_template",
        "Слайд собран не на макете из шаблона",
        "template",
        "deterministic",
        "blocking",
        fix_strategy="change_pattern",
        fix_cost="rerender",
        fix_note="Пересобрать слайд на макете шаблона",
    ),
    Check(
        "template.fixed_element_moved",
        "Логотип или колонтитул сдвинуты с положенного места",
        "template",
        "deterministic",
        "error",
        threshold={"max_shift": 0.01},
        fix_strategy="move_element",
        fix_cost="cheap",
        fix_note="Вернуть элемент на место из шаблона",
    ),
    Check(
        "template.contrast",
        "Контраст текста к фону ниже 4,5:1",
        "template",
        "deterministic",
        "error",
        threshold={"min_ratio": 4.5, "min_ratio_large_text": 3.0},
        note="Формула контраста WCAG 2.1. Для крупного текста (от 24 pt либо от 18 pt "
        "полужирного) стандарт допускает 3:1. Фон измеряется по отрендеренной странице: "
        "страница без текста даёт то, что лежит под буквами, поэтому фотография и градиент "
        "считаются честно. Без файла PDF рядом с отчётом фон берётся из заливки под текстом, "
        "а где её нет — проверка помечается невыполненной.",
        fix_strategy="recolor",
        fix_cost="cheap",
        fix_note="Цвет текста с достаточным контрастом к фону",
    ),
    Check(
        "template.font_substituted",
        "Рендерер подменил гарнитуру: в файле одна, на странице другая",
        "template",
        "deterministic",
        "warning",
        note="Имя гарнитуры в отрендеренной странице сверяется с тем, что задано в файле. "
        "Подмена значит, что у того, кто открывает файл, шрифта шаблона нет: метрики "
        "разъезжаются, и вёрстка у заказчика будет не такой, как на экране. Проверка "
        "выполняется только при наличии PDF ревизии.",
        fix_strategy="none",
        fix_cost="cheap",
        fix_note="Положить шрифт шаблона рядом с сервисом или выбрать гарнитуру из имеющихся",
    ),
)

# ---------- плотность ----------

DENSITY = (
    Check(
        "density.bullets",
        "Больше шести буллетов на слайде",
        "density",
        "deterministic",
        "warning",
        threshold={"max_bullets": 6},
        fix_strategy="split_slide",
        fix_cost="rerender",
        fix_note="Разделить слайд или сократить список",
    ),
    Check(
        "density.bullet_length",
        "Буллет длиннее пятнадцати слов",
        "density",
        "deterministic",
        "warning",
        threshold={"max_words": 15},
        fix_strategy="rewrite_shorter",
        fix_cost="llm",
        fix_note="Сократить пункт, не теряя фактов",
    ),
    Check(
        "density.table_size",
        "Таблица больше семи строк или пяти колонок",
        "density",
        "deterministic",
        "warning",
        threshold={"max_rows": 7, "max_cols": 5},
        fix_strategy="split_slide",
        fix_cost="rerender",
        fix_note="Разбить таблицу на два слайда",
    ),
    Check(
        "density.chart_series",
        "Больше пяти серий на диаграмме",
        "density",
        "deterministic",
        "warning",
        threshold={"max_series": 5},
        fix_strategy="change_pattern",
        fix_cost="rerender",
        fix_note="Оставить значимые серии или сменить подачу",
    ),
    Check(
        "density.fill_ratio",
        "Слайд заполнен меньше четверти или больше трёх четвертей",
        "density",
        "deterministic",
        "warning",
        threshold={"min_ratio": 0.25, "max_ratio": 0.75},
        note="Считается доля холста под содержательными объектами; постоянные элементы "
        "шаблона в расчёт не входят.",
        fix_strategy="change_pattern",
        fix_cost="rerender",
        fix_note="Композиция по объёму содержания",
    ),
)

# ---------- целостность ----------

INTEGRITY = (
    Check(
        "integrity.package",
        "Файл открывается и связи целы",
        "integrity",
        "deterministic",
        "blocking",
        scope="deck",
        fix_strategy="none",
        fix_cost="cheap",
        fix_note="Требует разбора: файл собран неверно",
    ),
    Check(
        "integrity.placeholder_text",
        "Остался текст-заглушка",
        "integrity",
        "deterministic",
        "error",
        threshold={"markers": ["lorem ipsum", "todo", "вставьте текст", "xxx", "ххх"]},
        fix_strategy="regenerate_text",
        fix_cost="llm",
        fix_note="Заменить заглушку содержанием",
    ),
    Check(
        "integrity.empty_slide",
        "Пустой слайд или слайд с одним заголовком",
        "integrity",
        "deterministic",
        "error",
        fix_strategy="regenerate_text",
        fix_cost="llm",
        fix_note="Наполнить слайд или убрать его из колоды",
    ),
    Check(
        "integrity.raster_slide",
        "Слайд оказался картинкой, а не редактируемыми объектами",
        "integrity",
        "deterministic",
        "blocking",
        note="Прямое требование ТЗ к экспорту PPTX: слайд единым растром не засчитывается.",
        fix_strategy="none",
        fix_cost="rerender",
        fix_note="Слайд должен собираться объектами, а не картинкой",
    ),
    Check(
        "integrity.chart_labels",
        "У диаграммы нет подписей осей, единиц или легенды",
        "integrity",
        "deterministic",
        "warning",
        fix_strategy="add_labels",
        fix_cost="cheap",
        fix_note="Добавить легенду, подписи и единицы",
    ),
    Check(
        "integrity.duplicate_slides",
        "Два слайда дублируют друг друга",
        "integrity",
        "deterministic",
        "error",
        scope="deck",
        note="Сравниваются композиция и текст: одинаковый разделитель дубликатом не считается.",
        fix_strategy="remove_slide",
        fix_cost="rerender",
        fix_note="Убрать повтор из колоды",
    ),
)

# ---------- содержание (модель по картинке слайда) ----------

CONTENT_QUESTIONS: tuple[tuple[int, str, str], ...] = (
    (1, "content.title_is_takeaway", "Заголовок содержит вывод, а не просто называет тему?"),
    (2, "content.title_matches_body", "Содержимое слайда соответствует заголовку?"),
    (3, "content.one_sentence", "Слайд пересказывается одним предложением?"),
    (4, "content.facts_grounded", "Все цифры и факты со слайда есть в исходных материалах?"),
    (5, "content.has_content", "На слайде есть содержание, а не только заголовок?"),
    (6, "content.visuals_relevant", "Картинки и иконки относятся к теме слайда?"),
    (7, "content.no_garbage", "Нет служебного мусора: реплик спикера, кусков промпта?"),
    (8, "content.no_typos", "Текст без опечаток?"),
    (9, "content.one_language", "Вся колода на одном языке?"),
    (10, "content.table_works", "Все строки таблицы и элементы легенды работают на мысль слайда?"),
    (11, "content.slides_connected", "Соседние слайды связаны между собой по логике?"),
)

# Вопросы про факты и мусор — ошибки: выдуманное число или кусок промпта на слайде
# недопустимы. Остальное — замечания, потому что ответ модели недетерминирован.
_CONTENT_SEVERITY = {
    "content.facts_grounded": "error",
    "content.no_garbage": "error",
    "content.has_content": "error",
}
_DECK_SCOPE = {"content.one_language", "content.slides_connected"}

CONTENT = tuple(
    Check(
        check_id,
        question,
        "content",
        "contextual",
        _CONTENT_SEVERITY.get(check_id, "warning"),
        scope="deck" if check_id in _DECK_SCOPE else "slide",
        threshold={"question_id": number},
        fix_strategy="regenerate_text",
        fix_cost="llm",
        fix_note="Переписать содержание слайда по фактам пакета",
    )
    for number, check_id, question in CONTENT_QUESTIONS
)

ALL_CHECKS: tuple[Check, ...] = LAYOUT + TEMPLATE + DENSITY + INTEGRITY + CONTENT
BY_ID: dict[str, Check] = {c.check_id: c for c in ALL_CHECKS}


def deterministic_checks() -> tuple[Check, ...]:
    return tuple(c for c in ALL_CHECKS if c.kind == "deterministic")


def contextual_checks() -> tuple[Check, ...]:
    return tuple(c for c in ALL_CHECKS if c.kind == "contextual")


def threshold(check_id: str, key: str, default: Any = None) -> Any:
    check = BY_ID.get(check_id)
    return check.threshold.get(key, default) if check else default
