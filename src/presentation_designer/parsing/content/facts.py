"""Реестр фактов: кандидаты чисел, дат, процентов и денег с контекстом.

Кандидаты находятся регулярными выражениями (`numbers.py`); контекст — показатель, период,
субъект, единица, сравнение — выводится из предложения вокруг числа детерминированно.
Факты из текста обязательны к сохранению (`must_keep`), факты из ячеек таблиц и производные
показатели (разница «с 31 % до 44 %», изменение первой и последней строки набора данных) —
нет: их несёт сам набор данных. Неоднозначный контекст (показатель не найден в предложении)
уточняет модель по скиллу `content_importer` со ссылкой на исходный фрагмент и отметкой
уверенности; значения модель не переписывает — только подписи.
"""

from __future__ import annotations

import itertools
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.parsing.content.datasets import Dataset, dataset_cell_ref
from presentation_designer.parsing.content.numbers import (
    NumberMatch,
    find_numbers,
    format_value,
)

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]

# Слова перед числом, которые не являются частью показателя.
_TRAILING_STOP = {
    "составил",
    "составила",
    "составили",
    "составляет",
    "составит",
    "вырос",
    "выросла",
    "выросли",
    "вырастет",
    "выросло",
    "снизился",
    "снизилась",
    "снизились",
    "упал",
    "упала",
    "упали",
    "достиг",
    "достигла",
    "достигли",
    "равен",
    "равна",
    "равно",
    "около",
    "порядка",
    "более",
    "менее",
    "почти",
    "свыше",
    "примерно",
    "до",
    "на",
    "в",
    "с",
    "со",
    "по",
    "к",
    "от",
    "за",
    "у",
    "из",
    "уже",
    "ещё",
    "еще",
    "всего",
    "лишь",
    "только",
    "и",
    "а",
    "но",
    "или",
    "это",
    "—",
    "–",
    "-",
    ":",
    "рост",
    "роста",
    "росте",
    "снижение",
    "снижении",
    "падение",
    "уровня",
    "уровне",
    "показатель",
    "значение",
    "объём",
    "объем",
    "сумма",
    "число",
    "количество",
    "доля",
    "которые",
    "который",
    "которая",
    "что",
    "чем",
    "как",
    "где",
    "при",
    "для",
    "стал",
    "стала",
    "стали",
    "будет",
    "был",
    "была",
    "были",
    "есть",
    "нет",
    "мы",
    "они",
    "он",
    "она",
    "оно",
    "получил",
    "получила",
    "получили",
    "показал",
    "показала",
    "показали",
    "дал",
    "дала",
    "дали",
    "принёс",
    "принесла",
    "принесли",
    "запланирован",
    "запланирована",
    "запланировано",
    "составляют",
    "равняется",
    "достигает",
    "достигают",
    "превышает",
    "растёт",
    "растет",
    "падает",
    "снижается",
    "увеличился",
    "увеличилась",
    "уменьшился",
    "уменьшилась",
    "занимает",
    "приносит",
    "стоит",
    "обходится",
    "требует",
    "включает",
    "насчитывает",
    "охватывает",
    "покрывает",
    "получает",
    "получают",
    "тратит",
    "тратят",
    "экономит",
    "оценивается",
    "планируется",
    "ожидается",
    "прогнозируется",
    "выделено",
    "потрачено",
    "заработано",
    "продано",
    "привлечено",
    "сделано",
    "выполнено",
    "открыто",
    "подключено",
    "против",
    "теперь",
    "сейчас",
    "затем",
    "потом",
    "также",
    "тоже",
    "не",
    "ни",
}
_LEADING_STOP = {
    "и",
    "а",
    "но",
    "что",
    "который",
    "которая",
    "которые",
    "при",
    "этом",
    "поэтому",
    "также",
    "в",
    "на",
    "с",
    "по",
    "к",
    "от",
    "у",
    "за",
    "для",
    "из",
    "мы",
    "они",
    "он",
    "она",
    "то",
    "это",
    "если",
    "когда",
    "где",
    "как",
    "чем",
    "так",
    "потому",
    "т.е.",
    "например",
}
_PERIOD_PATTERNS = [
    re.compile(
        r"\b(?:за|в течение|на протяжении)\s+"
        r"(?:прошл\w+\s+|текущ\w+\s+|следующ\w+\s+|последн\w+\s+)?"
        r"(?:\d+\s+)?(?:год[а]?|квартал[а]?|месяц[а]?|месяцев|недел[юи]|полугоди[ея]|пилот[а]?"
        r"|сезон[а]?|период[а]?|сутки|день|дня|дней|лет)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:ежемесячно|ежегодно|еженедельно|ежедневно|в месяц|в год|в неделю|в день|в сутки"
        r"|в квартал)\b",
        re.I,
    ),
    re.compile(r"\b(?:с|от)\s+\S+\s+(?:по|до)\s+\S+\s+\d{4}\b", re.I),
    re.compile(
        r"\b(?:[1-4]|I{1,3}|IV)\s*(?:-?[йгм]?\s*)?кв(?:\.|артал[ае]?)(?:\s+\d{4}(?:\s*(?:года|году|годы|год|гг\.|г\.))?)?",
        re.I,
    ),
    re.compile(
        r"\b(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*(?:[–—-]\w+)?(?:\s+\d{4})?",
        re.I,
    ),
    re.compile(
        r"\b(?:19|20)\d{2}(?:\s*[–—-]\s*(?:19|20)?\d{2})?(?:\s*(?:года|году|годы|год|гг\.|г\.))?",
        re.I,
    ),
    re.compile(r"\b(?:H[12]|Q[1-4])\s*(?:19|20)?\d{2}\b", re.I),
    re.compile(
        r"\b(?:годом|месяцем|кварталом|неделей)\s+ранее\b|\b(?:год|месяц|квартал|неделю)\s+назад\b|\bранее\b",
        re.I,
    ),
]
_COMPARISON_PATTERNS = [
    re.compile(r"\bгод к году\b|\bг/г\b|\byoy\b", re.I),
    re.compile(r"\bмесяц к месяцу\b|\bм/м\b|\bmom\b", re.I),
    re.compile(r"\bквартал к кварталу\b|\bкв/кв\b|\bqoq\b", re.I),
    re.compile(r"\bк плану\b|\bпротив плана\b|\bот плана\b|\bплан\w*\b", re.I),
    re.compile(
        r"\b(?:по сравнению с|относительно|против|в сравнении с|versus|vs\.?)\s+[^,.;]{2,40}", re.I
    ),
    re.compile(r"\bк (?:прошлому|предыдущему|аналогичному)\s+\w+\b", re.I),
    re.compile(r"\bдо (?:запуска|внедрения|пилота|изменений)\b", re.I),
    re.compile(r"\bпосле (?:запуска|внедрения|пилота|изменений)\b", re.I),
]
_SUBJECT_QUOTED = re.compile(r"«([^»]{2,60})»|\"([^\"]{2,60})\"")
_SUBJECT_CAPS = re.compile(
    r"\b(?:[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9.-]+(?:\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё0-9.-]+)*)\b"
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+(?=[А-ЯЁA-Z«\"(\d])")
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]*")
_PAIR_FROM_TO = re.compile(r"\b(?:с|от)\s+$")


@dataclass
class Fact:
    fact_id: str
    source_id: str
    raw: str
    kind: str
    value: float | str | None
    unit: str | None = None
    label: str | None = None
    block_id: str | None = None
    must_keep: bool = True
    context: dict[str, str] = field(default_factory=dict)
    source_location: JsonDict = field(default_factory=dict)
    derived: JsonDict | None = None
    uncertainty: JsonDict = field(default_factory=dict)
    # Служебное: нужен ли контекст от модели
    ambiguous: bool = False
    sentence: str = ""

    def as_dict(self) -> JsonDict:
        out: JsonDict = {
            "fact_id": self.fact_id,
            "source_id": self.source_id,
            "raw": self.raw,
            "kind": self.kind,
            "must_keep": self.must_keep,
        }
        if self.block_id:
            out["block_id"] = self.block_id
        if self.value is not None:
            out["value"] = format_value(self.value)
        if self.unit:
            out["unit"] = self.unit
        if self.label:
            out["label"] = self.label
        context = {k: v for k, v in self.context.items() if v}
        if context:
            out["context"] = context
        if self.source_location:
            out["source_location"] = self.source_location
        if self.derived:
            out["derived"] = self.derived
        if self.uncertainty:
            out["uncertainty"] = self.uncertainty
        return out


@dataclass
class TextUnit:
    """Один фрагмент текста для поиска фактов: блок или пункт списка."""

    block_id: str
    source_id: str
    text: str
    location: JsonDict = field(default_factory=dict)
    heading: str | None = None  # ближайший заголовок выше блока


# ---------- факты из текста ----------


# Номер модели/версии остаётся в исходном тексте, но не становится KPI.
# Намеренно узкий словарь: произвольное «прибыль 18» нельзя считать названием.
_IDENTIFIER_PREFIX = re.compile(
    r"\b(?:iphone|ipad|ios|macos|windows|android|playstation|"
    r"верси[яи]|version|модель|model|gpt)[\s-]*$",
    re.IGNORECASE,
)


def _is_identifier(text: str, match: NumberMatch) -> bool:
    return (
        match.kind == "number"
        and not match.unit
        and bool(_IDENTIFIER_PREFIX.search(text[: match.start]))
    )


def extract_text_facts(
    units: list[TextUnit], *, start_index: int = 1, max_facts: int = 300
) -> list[Fact]:
    facts: list[Fact] = []
    index = start_index
    for unit in units:
        matches = find_numbers(unit.text)
        if not matches:
            continue
        sentences = _sentences(unit.text)
        created: list[tuple[NumberMatch, Fact]] = []
        segment_start = 0
        last_in_sentence: Fact | None = None
        for m in matches:
            if _is_identifier(unit.text, m):
                continue
            if m.kind == "date" and not _date_is_fact(unit.text, m):
                # Даты-периоды («в 2025 году») попадают в контекст соседних фактов, но сами
                # фактом не считаются, если рядом нет показателя.
                continue
            sentence = _sentence_for(sentences, m.start)
            sentence_start = unit.text.find(sentence)
            if last_in_sentence is not None and last_in_sentence.sentence != sentence:
                last_in_sentence = None
            context = fact_context(
                unit.text,
                m,
                sentence,
                unit.heading,
                segment_start=max(segment_start, sentence_start),
            )
            if (
                not context.get("metric")
                and last_in_sentence is not None
                and last_in_sentence.context.get("metric")
            ):
                # «Выручка выросла на 25 % и достигла 1 200 млн ₽»: показатель общий
                # для всего предложения.
                context["metric"] = last_in_sentence.context["metric"]
                context["_inherited"] = "1"
            fact = Fact(
                fact_id=f"f{index}",
                source_id=unit.source_id,
                raw=m.raw,
                kind=m.kind,
                value=_value_of(m),
                unit=m.unit,
                block_id=unit.block_id,
                must_keep=True,
                context=context,
                # Место факта по контракту: страница и лист; номер слайда материала-презентации
                # остаётся у блока (block_id) — у факта схема его не допускает.
                source_location={
                    **{k: v for k, v in unit.location.items() if k in ("page", "sheet")},
                    "fragment": _fragment(unit.text, m),
                    "char_offset": m.start,
                },
                uncertainty={
                    "level": "confirmed",
                    "extracted_by": "regex",
                    "confidence": 0.95
                    if context.get("metric") and "_inherited" not in context
                    else 0.7,
                },
                ambiguous=not context.get("metric")
                or "_inherited" in context
                or _weak_metric(context.get("metric", ""), m.unit),
                sentence=sentence,
            )
            fact.context.pop("_inherited", None)
            fact.label = _label(fact)
            facts.append(fact)
            created.append((m, fact))
            if m.kind != "date":
                segment_start = m.end
            last_in_sentence = fact
            index += 1
            if len(facts) >= max_facts:
                return facts
        # Пары «с X до Y» → производная разница.
        for (m1, f1), (m2, f2) in itertools.pairwise(created):
            if _is_from_to_pair(unit.text, m1, m2) and f1.kind == f2.kind and f1.unit == f2.unit:
                derived = derived_change(f"f{index}", f1, f2, unit.source_id, unit.block_id)
                if derived is not None:
                    f1.context.setdefault("comparison", f"с {m1.raw} до {m2.raw}")
                    f2.context.setdefault("comparison", f"с {m1.raw} до {m2.raw}")
                    if not f2.context.get("metric") and f1.context.get("metric"):
                        f2.context["metric"] = f1.context["metric"]
                        f2.ambiguous = False
                        f2.label = _label(f2)
                    facts.append(derived)
                    index += 1
    return facts


def _sentences(text: str) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    pos = 0
    for piece in _SENTENCE_SPLIT.split(text):
        start = text.find(piece, pos)
        if start < 0:
            start = pos
        out.append((start, start + len(piece), piece))
        pos = start + len(piece)
    return out or [(0, len(text), text)]


def _sentence_for(sentences: list[tuple[int, int, str]], pos: int) -> str:
    for start, end, piece in sentences:
        if start <= pos < end:
            return piece
    return sentences[-1][2] if sentences else ""


def _fragment(text: str, m: NumberMatch, width: int = 80) -> str:
    start = max(0, m.start - width)
    end = min(len(text), m.end + width)
    fragment = text[start:end].strip()
    if start > 0:
        fragment = "…" + fragment
    if end < len(text):
        fragment = fragment + "…"
    return fragment


def _value_of(m: NumberMatch) -> float | str | None:
    if m.kind == "range":
        return f"{format_value(m.value)}–{format_value(m.value_to)}"
    return m.value


def _date_is_fact(text: str, m: NumberMatch) -> bool:
    """Дата — факт, если это дата события (день или «запуск в … 2026»), а не период показателя."""
    if re.match(r"\d{4}-\d{2}-\d{2}", str(m.value)):
        return True
    before = text[max(0, m.start - 40) : m.start].lower()
    return bool(
        re.search(
            r"(запуск|старт|дедлайн|срок|запланирован|начал|заверш|релиз|выход|до|к)\s*\S*\s*$",
            before,
        )
    )


def fact_context(
    text: str, m: NumberMatch, sentence: str, heading: str | None, *, segment_start: int = 0
) -> dict[str, str]:
    """Показатель, период, субъект, единица, сравнение из предложения вокруг числа.
    Показатель ищется в отрезке между предыдущим числом и текущим."""
    context: dict[str, str] = {}
    before = _SUBJECT_QUOTED.sub(" ", text[max(0, segment_start) : m.start])
    sentence_end = text.find(sentence) + len(sentence) if sentence in text else len(text)
    after = text[m.end : max(m.end, sentence_end)]
    metric = _metric_before(_strip_phrases(before)) or _metric_after(_strip_phrases(after), m)
    if metric:
        context["metric"] = metric
    period = _find_first(_PERIOD_PATTERNS, sentence, exclude=m.raw)
    if period:
        context["period"] = period
    comparison = _find_first(_COMPARISON_PATTERNS, sentence, exclude=m.raw)
    if comparison:
        context["comparison"] = comparison
    subject = _subject(sentence)
    if subject:
        context["subject"] = subject
    if m.unit:
        context["unit"] = m.unit
    return context


# Окончания, почти не встречающиеся у существительных перед числом: прошедшее время
# и 3-е лицо множественного числа. «-ет/-ит/-ать» не годятся: бюджет, кредит, объём.
_VERB_ENDINGS = ("ют", "ят", "ла", "ли", "ло", "лся", "лась", "лись", "лось", "ешь", "ишь")
# Союзы и «сильные» предлоги обрывают именную группу показателя; простые предлоги
# («конверсия в покупку», «выручка от подписок») остаются внутри неё.
_BOUNDARY = {
    "и",
    "а",
    "но",
    "что",
    "чтобы",
    "когда",
    "где",
    "при",
    "между",
    "через",
    "после",
    "перед",
    "или",
    "либо",
    "если",
    "хотя",
    "также",
    "тоже",
    "как",
    "против",
    "среди",
    "благодаря",
    "вследствие",
    "несмотря",
    "потому",
    "поэтому",
    "который",
    "которая",
    "которые",
}
_MEASURE_WORDS = {
    "рост",
    "роста",
    "росте",
    "снижение",
    "снижении",
    "падение",
    "доля",
    "объём",
    "объем",
    "число",
    "количество",
    "уровень",
    "значение",
    "динамика",
    "изменение",
    "прирост",
    "выручка",
    "прибыль",
}


def _strip_phrases(segment: str) -> str:
    """Убирает из отрезка периоды и сравнения, чтобы они не попали в показатель."""
    out = segment
    for pattern in (*_PERIOD_PATTERNS, *_COMPARISON_PATTERNS):
        out = pattern.sub(" ", out)
    return out


def _verb_like(word: str) -> bool:
    w = word.lower()
    return len(w) > 4 and w.endswith(_VERB_ENDINGS) and w not in _MEASURE_WORDS


def _metric_before(before: str) -> str:
    metric = _metric_phrase(before)
    if metric:
        return metric
    # «Глубина погружений — до 3000 м»: после тире остался только предлог, показатель стоит
    # перед тире или двоеточием.
    tail = before.rstrip()
    cut = max(tail.rfind("—"), tail.rfind(":"), tail.rfind("–"))
    if (cut > 0 and not _WORD.findall(tail[cut + 1 :].strip())) or (
        cut > 0
        and all(
            w.lower() in _TRAILING_STOP or w.lower() in _LEADING_STOP
            for w in _WORD.findall(tail[cut + 1 :])
        )
    ):
        return _metric_phrase(tail[:cut])
    return ""


def _metric_phrase(before: str) -> str:
    tail = before.rstrip().rstrip(" —–-:")
    # Показатель — конец фразы до числа: после последнего знака препинания.
    boundary = max(
        tail.rfind(","),
        tail.rfind(";"),
        tail.rfind(":"),
        tail.rfind("("),
        tail.rfind("—"),
        tail.rfind("«"),
    )
    words = _WORD.findall(tail[boundary + 1 :] if boundary >= 0 else tail[-200:])
    while words and (words[-1].lower() in _TRAILING_STOP or _verb_like(words[-1])):
        words.pop()
    # После последнего предлога/союза остаётся именная группа показателя.
    cut = 0
    for i, word in enumerate(words):
        if word.lower() in _BOUNDARY:
            cut = i + 1
    phrase = words[cut:]
    while phrase and phrase[0].lower() in _LEADING_STOP:
        phrase.pop(0)
    phrase = phrase[-6:]
    metric = " ".join(phrase).strip(" -–—:")
    return metric if len(metric) >= 3 else ""


def _weak_metric(metric: str, unit: str | None = None) -> bool:
    """Подпись, которая не называет измеряемое: одно слово с заглавной буквы (скорее
    подлежащее), сама единица («5 дней» → «дней», «3000 м» → «м») или одно слово-связка
    («даёт», «все»). Такие факты уточняет модель по фрагменту источника."""
    words = metric.split()
    if not words:
        return True
    if len(words) == 1 and words[0][:1].isupper() and words[0].isalpha() and not words[0].isupper():
        return True
    low = metric.lower().strip()
    if unit and (low == unit.lower() or low[:4] == unit.lower()[:4]):
        return True
    if len(words) > 1:
        return False
    if low in _UNIT_WORDS or any(low.startswith(u) for u in _UNIT_STEMS):
        return True
    return len(low) <= 4 or _verb_like(low) or low in _WEAK_WORDS


# Единицы-существительные, которые эвристика «после числа» принимает за показатель.
_UNIT_STEMS = (
    "дня",
    "дней",
    "день",
    "недел",
    "месяц",
    "лет",
    "год",
    "час",
    "минут",
    "секунд",
    "человек",
    "раз",
    "штук",
    "единиц",
    "процент",
)
_UNIT_WORDS = {
    "м",
    "км",
    "км²",
    "м²",
    "кг",
    "т",
    "мм",
    "см",
    "л",
    "шт",
    "руб",
    "млн",
    "млрд",
    "тыс",
}
_WEAK_WORDS = {"все", "всего", "даёт", "дает", "около", "более", "менее", "плюс-минус", "итого"}


def _metric_after(after: str, m: NumberMatch) -> str:
    if m.unit and m.unit not in ("%", "п. п.", "₽", "$", "€", "£", "×"):
        # «15 000 клиентов»: единица уже называет, что посчитано.
        return m.unit
    cut = re.split(r"[,.;:!?—–()«»]", after, maxsplit=1)[0]
    words = _WORD.findall(cut[:80])
    phrase: list[str] = []
    for word in words[:4]:
        if word.lower() in _TRAILING_STOP or word.lower() in _LEADING_STOP:
            break
        phrase.append(word)
    metric = " ".join(phrase)
    return metric if len(metric) >= 4 else ""


def _find_first(patterns: list[re.Pattern[str]], sentence: str, *, exclude: str) -> str:
    for pattern in patterns:
        for found in pattern.finditer(sentence):
            value = found.group(0).strip(" ,.;")
            if value and value != exclude and value not in exclude:
                return value
    return ""


def _subject(sentence: str) -> str:
    quoted = _SUBJECT_QUOTED.search(sentence)
    if quoted:
        return (quoted.group(1) or quoted.group(2)).strip()
    # Имена с заглавной буквы не в начале предложения: компании, продукты, сегменты.
    # Аббревиатуры-показатели перед числом (DAU 2,4 млн) субъектом не считаются.
    for found in _SUBJECT_CAPS.finditer(sentence):
        if found.start() == 0:
            continue
        candidate = found.group(0)
        following = sentence[found.end() : found.end() + 4].strip()
        if following[:1].isdigit():
            continue
        if candidate.isupper() and len(candidate) <= 5:
            continue
        if " " in candidate or any(ch.isupper() for ch in candidate[1:]):
            return candidate[:60]
    return ""


def _label(fact: Fact) -> str:
    metric = fact.context.get("metric")
    period = fact.context.get("period")
    if metric:
        return f"{metric} ({period})" if period and period not in metric else metric
    fragment = fact.sentence.strip() or fact.raw
    return fragment[:80]


def _is_from_to_pair(text: str, m1: NumberMatch, m2: NumberMatch) -> bool:
    between = text[m1.end : m2.start].strip().lower()
    before = text[max(0, m1.start - 6) : m1.start].lower()
    return between in ("до", "к", "→", "->") and bool(re.search(r"\b(с|со|от)\s*$", before))


def display_number(value: Any) -> str:
    """Число для слайда по-русски: «4,1», «120 500», «−1,4». Значение факта остаётся числом;
    это только его запись, как её набрал бы автор презентации."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        return str(value)
    number = format_value(value)
    if isinstance(number, float):
        text = f"{number:.6f}".rstrip("0").rstrip(".")
    else:
        text = str(number)
    whole, _, frac = text.partition(".")
    sign = "−" if whole.startswith("-") else ""
    whole = whole.lstrip("-")
    if len(whole) > 4:
        groups: list[str] = []
        while whole:
            groups.insert(0, whole[-3:])
            whole = whole[:-3]
        whole = "\u00a0".join(groups)
    return f"{sign}{whole}{',' + frac if frac else ''}"


def derived_change(
    fact_id: str, f1: Fact, f2: Fact, source_id: str, block_id: str | None
) -> Fact | None:
    if not isinstance(f1.value, int | float) or not isinstance(f2.value, int | float):
        return None
    delta = round(float(f2.value) - float(f1.value), 6)
    unit = "п. п." if f1.kind == "percent" else f1.unit
    metric = f1.context.get("metric") or f2.context.get("metric") or ""
    sign = "+" if delta >= 0 else "−"
    fact = Fact(
        fact_id=fact_id,
        source_id=source_id,
        raw=f"{sign}{display_number(abs(delta))}{(' ' + unit) if unit else ''}",
        kind="percent" if f1.kind == "percent" and unit == "%" else "number",
        value=delta,
        unit=unit,
        block_id=block_id,
        must_keep=False,
        context={
            "metric": f"изменение {metric}".strip() if metric else "изменение",
            "comparison": f"с {f1.raw} до {f2.raw}",
            **({"period": f1.context["period"]} if f1.context.get("period") else {}),
            **({"unit": unit} if unit else {}),
        },
        derived={
            "formula": f"{format_value(f2.value)} - {format_value(f1.value)}",
            "inputs": [f1.fact_id, f2.fact_id],
        },
        uncertainty={"level": "inferred", "extracted_by": "parser", "confidence": 0.9},
    )
    fact.label = f"изменение: {metric}" if metric else "изменение показателя"
    return fact


# ---------- факты из наборов данных ----------


def extract_dataset_facts(
    dataset: Dataset, *, start_index: int, max_columns: int = 4, max_facts: int = 300
) -> list[Fact]:
    """Первая и последняя строки каждой числовой колонки как факты, плюс производное
    изменение между ними: набор данных сам остаётся источником всех значений."""
    facts: list[Fact] = []
    index = start_index
    label_col = next(
        (i for i, c in enumerate(dataset.columns) if c.type in ("string", "date")), None
    )
    period = dataset.title if dataset.title and len(dataset.title) <= 60 else None
    labels = (
        [str(row[label_col]) for row in dataset.rows if row[label_col] is not None]
        if label_col is not None
        else []
    )
    # Разница первой и последней строки — изменение, только если строки идут во времени
    # (годы, кварталы, месяцы, даты). У категорий («Северный», «Речной», «Заводской») это
    # бессмысленное «изменение: −700 пассажиров», а подпись строки — субъект, не период.
    temporal = label_col is not None and (
        dataset.columns[label_col].type == "date" or _temporal_labels(labels)
    )
    for col_index, values in list(dataset.numeric_columns.items())[:max_columns]:
        if len(values) < 1:
            continue
        column = dataset.columns[col_index]
        picks = [values[0], values[-1]] if len(values) > 1 else [values[0]]
        made: list[Fact] = []
        for row_index, value in picks:
            row_label = (
                str(dataset.rows[row_index][label_col])
                if label_col is not None
                and row_index < len(dataset.rows)
                and dataset.rows[row_index][label_col] is not None
                else f"строка {row_index + 1}"
            )
            cell = dataset_cell_ref(dataset, row_index, col_index)
            location: JsonDict = {}
            if dataset.location.sheet:
                location["sheet"] = dataset.location.sheet
            if dataset.location.page:
                location["page"] = dataset.location.page
            if cell:
                location["cell"] = cell
            location["fragment"] = f"{column.name}: {row_label} = {format_value(value)}" + (
                f" {column.unit}" if column.unit else ""
            )
            unit = column.unit or (
                "%" if column.type == "percent" else "₽" if column.type == "money" else None
            )
            fact = Fact(
                fact_id=f"f{index}",
                source_id=dataset.source_id,
                raw=f"{display_number(value)}{(' ' + unit) if unit else ''}",
                kind=column.type if column.type in ("percent", "money") else "number",
                value=value,
                unit=unit,
                block_id=dataset.block_id,
                must_keep=False,
                context={
                    "metric": column.name,
                    "period": row_label if temporal else (period or ""),
                    **(
                        {"subject": dataset.title}
                        if dataset.title and temporal and dataset.title != row_label
                        else {"subject": row_label}
                        if label_col is not None and not temporal
                        else {}
                    ),
                    **({"unit": unit} if unit else {}),
                },
                source_location=location,
                uncertainty={"level": "confirmed", "extracted_by": "parser", "confidence": 0.98},
            )
            fact.label = f"{column.name} — {row_label}"
            facts.append(fact)
            made.append(fact)
            index += 1
            if len(facts) >= max_facts:
                return facts
        if len(made) == 2 and (temporal or label_col is None):
            derived = derived_change(
                f"f{index}", made[0], made[1], dataset.source_id, dataset.block_id
            )
            if derived is not None:
                derived.context["metric"] = f"изменение: {column.name}"
                derived.context["comparison"] = (
                    f"{made[0].context.get('period')} → {made[1].context.get('period')}"
                )
                derived.label = f"изменение: {column.name}"
                derived.source_location = {
                    k: v for k, v in made[0].source_location.items() if k == "sheet"
                }
                facts.append(derived)
                index += 1
    return facts


# ---------- уточнение контекста моделью ----------

FACT_CONTEXT_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact_id": {"type": "string"},
                    "metric": {"type": "string"},
                    "period": {"type": "string"},
                    "subject": {"type": "string"},
                    "comparison": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["fact_id", "metric", "confidence"],
            },
        }
    },
    "required": ["items"],
}


def refine_facts_with_model(
    facts: list[Fact],
    client: Any,
    skill: Any,
    *,
    batch: int = 25,
    budget_s: float = 20.0,
    min_confidence: float = 0.6,
) -> JsonDict:
    """Показатель/период/субъект для фактов без найденного показателя. Подписи принимаются,
    только если их слова есть в исходном фрагменте (модель не выдумывает контекст) и
    уверенность не ниже порога; значения фактов не меняются. Возвращает сводку."""
    import asyncio

    from presentation_designer.llm.types import Deadline, LlmError

    summary: JsonDict = {"asked": 0, "accepted": 0, "rejected": 0, "calls": 0, "errors": []}
    # Регулярное выражение находит число надёжно, а подпись к нему — нет («62 %» → «придёт
    # автобус», «14 млн руб.» → «даёт»): подпись каждого факта из текста сверяется моделью.
    # Её ответ принимается, только если слова есть во фрагменте, иначе остаётся эвристика.
    candidates = [
        f
        for f in facts
        if f.derived is None
        and (f.ambiguous or (f.uncertainty or {}).get("extracted_by") == "regex")
    ]
    if not candidates or client is None or skill is None:
        return summary
    chunks = [candidates[i : i + batch] for i in range(0, len(candidates), batch)]
    deadline = Deadline.after(budget_s)

    def make_request(chunk: list[Fact]) -> Any:
        lines = [
            f"{f.fact_id}: «{f.raw}» — предложение: «{_source_text(f)[:400]}»"
            f" — подпись эвристики: «{f.context.get('metric', '')}»"
            for f in chunk
        ]
        req = skill.request(
            "import.fact_context",
            "Факты и предложения источника:\n" + "\n".join(lines),
            schema=FACT_CONTEXT_SCHEMA,
            stage="import",
        )
        req.deadline = deadline
        req.schema_name = "fact_context"
        return req

    async def run_all() -> list[Any]:
        return await asyncio.gather(
            *(client.complete(make_request(chunk)) for chunk in chunks), return_exceptions=True
        )

    outcomes = asyncio.run(run_all())
    by_id = {f.fact_id: f for f in candidates}
    for chunk, outcome in zip(chunks, outcomes, strict=True):
        summary["calls"] += 1
        if isinstance(outcome, BaseException):
            summary["errors"].append(f"{type(outcome).__name__}: {outcome}"[:200])
            if not isinstance(outcome, LlmError):
                log.exception("уточнение контекста фактов: неожиданная ошибка", exc_info=outcome)
            continue
        summary["asked"] += len(chunk)
        items = outcome.parsed.get("items", []) if isinstance(outcome.parsed, dict) else []
        for item in items:
            fact = by_id.get(str(item.get("fact_id", "")))
            if fact is None:
                continue
            try:
                confidence = float(item.get("confidence", 0))
            except (TypeError, ValueError):
                continue
            metric = str(item.get("metric", "")).strip()
            fragment = _source_text(fact).lower()
            if confidence < min_confidence or not metric or not _grounded(metric, fragment):
                summary["rejected"] += 1
                continue
            fact.context["metric"] = metric[:80]
            for key in ("period", "subject", "comparison"):
                value = str(item.get(key, "") or "").strip()
                if value and _grounded(value, fragment):
                    fact.context[key] = value[:80]
            fact.uncertainty = {
                "level": "inferred",
                "extracted_by": "model",
                "confidence": round(min(confidence, 0.95), 2),
            }
            fact.ambiguous = False
            fact.label = _label(fact)
            summary["accepted"] += 1
    return summary


_TEMPORAL_LABEL = re.compile(
    r"^\s*(?:(?:19|20)\d{2}(?:\s*(?:г\.?|год[а-я]*))?"
    r"|[IVX]{1,4}\s*(?:кв|квартал)[а-я.]*(?:\s*(?:19|20)\d{2})?"
    r"|[1-4]\s*(?:кв|квартал|q)[а-я.]*(?:\s*(?:19|20)\d{2})?"
    r"|q[1-4](?:\s*(?:19|20)\d{2})?"
    r"|(?:янв|фев|мар|апр|ма[йя]|июн|июл|авг|сен|окт|ноя|дек|jan|feb|mar|apr|may|jun|jul|aug"
    r"|sep|oct|nov|dec)[а-яa-z.]*(?:\s*(?:19|20)?\d{2})?"
    r"|\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?"
    r"|(?:неделя|месяц|день|week|month|day)\s*\d+|\d+\s*(?:неделя|месяц|день))\s*$",
    re.IGNORECASE,
)


def _temporal_labels(labels: list[str]) -> bool:
    """Подписи строк — моменты или периоды времени (все, кроме пустых и «итого»)."""
    meaningful = [
        x
        for x in labels
        if x.strip() and not x.strip().lower().startswith(("итог", "всего", "total"))
    ]
    return len(meaningful) >= 2 and all(_TEMPORAL_LABEL.match(x) for x in meaningful)


def _source_text(fact: Fact) -> str:
    """Предложение с фактом и окно вокруг числа: предложение может оборваться на
    сокращении, окно — на середине показателя."""
    fragment = str(fact.source_location.get("fragment") or "")
    sentence = fact.sentence.strip()
    if sentence and fragment and fragment.rstrip("…") not in sentence:
        return f"{sentence} … {fragment}"
    return sentence or fragment


def _grounded(phrase: str, fragment: str) -> bool:
    """Не меньше половины значимых слов подписи есть во фрагменте (по основе из 5 символов):
    модель может переформулировать («доля пропущенных уведомлений»), но не придумывать."""
    words = [w.lower() for w in _WORD.findall(phrase) if len(w) >= 3]
    if not words:
        return False
    hits = sum(1 for w in words if w[:5] in fragment)
    return hits >= max(1, (len(words) + 1) // 2)


weak_metric = _weak_metric

__all__ = [
    "FACT_CONTEXT_SCHEMA",
    "Fact",
    "TextUnit",
    "derived_change",
    "extract_dataset_facts",
    "extract_text_facts",
    "fact_context",
    "refine_facts_with_model",
    "weak_metric",
]
