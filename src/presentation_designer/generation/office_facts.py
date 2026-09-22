"""Conservative numeric and lexical guards, not a general semantic QA system.

Known Russian units/periods must stay verbatim (ignoring case/whitespace).
Unknown units and paraphrases are not inferred. Translation and explicit ordinary
edits remain unrestricted; mixed shortening/fact changes require a separate edit.
"""

from __future__ import annotations

import re
from collections import Counter

SHORTENING_RULE = (
    "При сокращении сохраняй каждое число вместе с единицей и периодом дословно. "
    "Например, '27 сообщений в день' нельзя заменять на '27 сообщений'. "
    "Это относится и к выражениям, разбитым на несколько runs одного абзаца. "
    "Остальные слова можно сокращать и переформулировать внутри тех же runs: "
    "это не изменение структуры PPTX. Старайся выполнить сокращение, а не отказать. "
    "Проверяй полный текст каждого абзаца после склейки runs: не дублируй в одном run "
    "фразу из соседнего. При сокращении ни один абзац не должен стать длиннее. "
    "paragraphs показывает границы абзацев; parts — runs и неизменяемые переносы break. "
    "Склеивай parts без добавления пробелов. before копируй только из text указанного run, "
    "а не из label объекта или всего абзаца, сохраняя начальные и конечные пробелы. "
    "Неизменённые runs не включай в patches. Сохраняй все пункты и их смысл. "
    "В JSON текста невидимые пробелы показаны escape-последовательностями (например, "
    "\\u00a0 и \\u202f): в before сохраняй именно эти символы, не заменяй обычным пробелом. "
    "Сохраняй дословно маркеры условий, отрицания, исключений и модальности: "
    "'можно', 'если', 'если возможно', 'не', 'кроме', 'исключения', 'задан'. "
    "Разрешение нельзя превращать в обязанность: 'оси можно использовать' не 'оси нужны'. "
    "Сохраняй законченные мысли, включая сказуемые и уточнения: "
    "'размер шрифта уже задан' → 'размер шрифта задан', не 'размер шрифта'. "
    "'Синий, но можно использовать дополнительные цвета' нельзя сокращать до "
    "'Синий, дополнительные'. Перенос break — не конец предложения. "
    "Не удаляй перечисленные категории, уточнения, условия и отрицания ради краткости; "
    "Перед ответом сопоставь каждое исходное утверждение с результатом: субъект, действие, "
    "аудитория, характеристика, цель и каждый элемент перечисления должны остаться. "
    "Сокращение — не пересказ главного: нельзя выбросить отдельную программу из списка, "
    "преимущества предложения, характеристику экспертов или целевую аудиторию. "
    "Сохраняй названия программ, организаций, шрифтов и направлений дословно. "
    "Запрошенная доля сокращения приблизительна; полнота фактов важнее длины. "
    "убирай только избыточные связующие слова. Если это невозможно, оставь run неизменным. "
    "Наличие чисел не повод для отказа: сокращай связующие слова вокруг них. "
    "Например, 'Доход составил 8 млн ₽ за год, а расходы составили 3 млн ₽.' → "
    "'Доход — 8 млн ₽ за год, расходы — 3 млн ₽.' Числовые выражения не меняются. "
    "Если сокращение невозможно без потери фактов, верни пустую правку и объясни. "
)

_SHORTEN = re.compile(r"сократ|сокращ|короче|кратк|лаконич|shorten|concise|summari", re.I)
_UNIT = (
    r"(?:%|₽|\$|€|сообщени[а-яё]*|уведомлени[а-яё]*|пользовател[а-яё]*|"
    r"руб(?:лей|ля|ль)?|процент[а-яё]*|час(?:а|ов)?|минут[а-яё]*|секунд[а-яё]*|"
    r"млн|млрд|тыс|кг|км|шт)(?!\w)"
)
_PERIOD = r"(?:в|за)\s+(?:день|сутки|неделю|месяц|квартал|год|час|минуту)(?!\w)"
_NUMBER = r"[±+−-]?(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?!\d)|\d+)(?:[.,]\d+)?"
_DATE = r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4})(?!\d)"
_RANGE = rf"{_NUMBER}(?:\s*[–—−-]\s*{_NUMBER})?"
_QUANTITY = re.compile(
    rf"(?<!\w)(?:{_DATE}|(?:[$€₽]\s*)?{_RANGE}"
    rf"(?:\s*(?:млн|млрд|тыс)\.?(?!\w))?(?:\s*{_UNIT})?(?:\s+{_PERIOD})?)",
    re.I,
)


def validate_shortening(instruction: str, before: str, after: str) -> None:
    """Reject loss, addition or reassignment of recognized numeric expressions."""
    if not _SHORTEN.search(instruction):
        return

    def quantities(text: str) -> Counter[str]:
        return Counter(" ".join(m.group().lower().split()) for m in _QUANTITY.finditer(text))

    if quantities(before) != quantities(after):
        raise ValueError(
            "Сокращение изменило число, единицу или период. "
            "Сохрани числовые выражения дословно вместе с единицами и периодами "
            "или верни пустую правку с объяснением."
        )


def validate_shortening_length(instruction: str, before: str, after: str) -> None:
    """Reject paragraph growth, including duplicated text across neighbouring runs."""
    if _SHORTEN.search(instruction) and len(" ".join(after.split())) > len(
        " ".join(before.split())
    ):
        raise ValueError(
            "Сокращение увеличило длину абзаца. Убери повторы, учитывай соседние runs "
            "и сократи текст без потери смысла либо верни пустую правку с объяснением."
        )


# Intentionally lexical: safe paraphrases may be retried with the original marker.
# Compare full paragraphs, not individual runs (a word can span styled runs).
_MEANING_ANCHORS = re.compile(
    r"\b(?:если\s+возможно|по\s+возможности|если|при\s+условии|можно|нельзя|"
    r"не|ни|кроме|только|исключени[а-яё]*|долж[а-яё]*|необходимо|нужно|"
    r"задан[а-яё]*|установлен[а-яё]*)\b",
    re.I,
)
_DANGLING = re.compile(r"(?:[,;:]|\b(?:и|или|но|а|для|при|если|чтобы|потому что))\s*[.!?]*$", re.I)


def validate_shortening_meaning(instruction: str, before: str, after: str) -> None:
    """Reject known losses; preserving markers is necessary, not semantic proof."""
    if not _SHORTEN.search(instruction) or before == after:
        return

    def anchors(text: str) -> Counter[str]:
        return Counter(" ".join(m.group().lower().split()) for m in _MEANING_ANCHORS.finditer(text))

    if anchors(before) != anchors(after):
        raise ValueError(
            "Сокращение изменило условие, отрицание, исключение или модальность. "
            "Сохрани дословно маркеры исходного абзаца и их смысл: "
            f"{dict(anchors(before))}. Не превращай разрешение в требование."
        )
    if (before.strip() and not after.strip()) or (
        _DANGLING.search(after) and not _DANGLING.search(before)
    ):
        raise ValueError(
            "Сокращение оставило пустой или оборванный абзац. Сохрани законченную мысль "
            "после склейки всех runs и переносов либо оставь абзац неизменным."
        )
