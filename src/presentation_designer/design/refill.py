"""Дозапрос модели на пустые слоты: точечно и с указанием вместимости.

Почему отдельным вызовом, а не улучшением основного промпта. К моменту, когда
план построен и застава убрала брак, мы **точно знаем**: какие слоты пусты,
какого они вида и сколько знаков в каждый влезает. Модель, получившая такую
задачу, решает её надёжно — это дописать подпись заданной длины, а не
спроектировать презентацию. Основной же промпт вынужден держать в голове всю
композицию сразу, и именно там слоты остаются пустыми.

Вызов один на всю колоду: слоты передаются пачкой, ответ — отображение
`slide_id → {slot_id: текст}`. Цена — примерно один запрос к модели на прогон,
что укладывается в бюджет ТЗ (пять минут на колоду).

Ответ модели проверяется тем же кодом, что и остальной план: не помещается —
не берём, образец шаблона — не берём, повтор — не берём. Доверия здесь нет,
есть проверка.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from typing import Any

from presentation_designer.design.fit import _fits, content_slots
from presentation_designer.design.guard import _norm
from presentation_designer.generation.grounding import CONCEPT_POLICY, is_concept

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]

# Ссылка на факт исходных материалов в тексте плана: подставляется вёрсткой.
_FACT = re.compile(r"\{fact:[^}]+\}")

_BARE_FACT = re.compile(r"(?<![{:\w])(f\d+)\b")

# Числа ответа: сокращение обязано сохранить все показатели исходной строки.
_DIGITS = re.compile(r"\d+")


def _canvas_of(profile: JsonDict) -> Any:
    from presentation_designer.design.measure import canvas_of

    return canvas_of(profile)


# Запас к оценке вместимости: модель почти всегда пишет чуть длиннее, чем
# просили, и жёсткая граница приводила бы к тому, что ответ выбрасывается
# целиком. Просим короче, принимаем по факту замера.
_ASK_RATIO = 0.85


def _capacity_chars(slot: JsonDict, canvas: Any | None = None) -> int | None:
    """Сколько знаков входит в слот — по метрикам шрифта, а не на глаз."""
    from presentation_designer.design.measure import max_chars

    return max_chars(slot, canvas)


def _slide_material(story: JsonDict, refs: list[str]) -> JsonDict:
    """Материал слайда из смыслового плана: тезисы, пояснения, факты.

    Без него дозапрос бессмыслен. Промпт требует брать только то, что есть в
    материалах, — и модель, не получив материалов, честно возвращала пустые
    строки на все слоты. Проверено замером: 12 слотов запрошено, 12 пустых
    ответов.
    """
    theses = {t.get("thesis_id"): t for t in (story or {}).get("theses", [])}
    statements, facts = [], []
    for ref in refs:
        thesis = theses.get(ref) or {}
        for key in ("statement", "explanation", "detail", "evidence"):
            value = thesis.get(key)
            if isinstance(value, str) and value.strip():
                statements.append(value.strip())
        # source_refs addresses content blocks, not entries in package.facts.
        for fact in thesis.get("fact_refs") or []:
            facts.append(str(fact))
    return {"material": statements, "fact_refs": list(dict.fromkeys(facts))}


def collect_gaps(
    plan: JsonDict,
    patterns: dict[str, JsonDict],
    limit: int,
    story: JsonDict | None = None,
    canvas: Any | None = None,
) -> list[JsonDict]:
    """Пустые слоты колоды с их вместимостью — задание для модели.

    Заголовки и числа не запрашиваются: заголовок уже написан планировщиком, а
    число обязано прийти из фактов исходных материалов.
    """
    tasks: list[JsonDict] = []
    budget = limit
    for slide in plan.get("slides", []):
        pattern = patterns.get(slide.get("pattern_id") or "")
        if pattern is None or budget <= 0:
            continue
        material = _slide_material(story or {}, slide.get("thesis_refs") or [])
        if not material["material"] and slide.get("role") in _SERVICE_ROLES:
            # Служебный слайд без тезисов (финал без призыва): материала нет, и модель
            # сочиняет подпись из соседних названий («Масштабирование» под «Спасибо»).
            continue
        filled = {b.get("slot_id") for b in slide.get("blocks", [])}
        gaps = []
        for slot in content_slots(pattern):
            if slot.get("slot_id") in filled or slot.get("kind") in {"title", "number"}:
                continue
            capacity = _capacity_chars(slot, canvas)
            if capacity is None or capacity < 8:
                continue
            gaps.append(
                {
                    "slot_id": slot.get("slot_id"),
                    "kind": slot.get("kind"),
                    "max_chars": int(capacity * _ASK_RATIO),
                }
            )
            budget -= 1
            if budget <= 0:
                break
        if gaps:
            task = {
                "slide_id": slide.get("slide_id"),
                "title": slide.get("title", ""),
                "key_message": slide.get("key_message", ""),
                "filled": [
                    str(b.get("text") or "") for b in slide.get("blocks", []) if b.get("text")
                ],
                "empty_slots": gaps,
            }
            task.update(material)
            tasks.append(task)
    return tasks


_SERVICE_ROLES = frozenset({"title", "section_divider", "thanks", "qr", "agenda"})

# Доля слов ответа, совпавших с уже написанным, после которой ответ считается
# пересказом. Подстрочного сравнения мало: «Масштаб и активность» под
# заголовком «Масштаб подключения и активность» подстрокой не является, а
# сведений не добавляет.
RESTATE_RATIO = 0.7

# Служебные слова в сравнении не участвуют: их совпадение ничего не значит.
_STOP = frozenset({"и", "в", "на", "для", "с", "по", "за", "от", "до", "о", "у", "к"})


def _significant(text: str) -> set[str]:
    """Значимые слова строки без знаков препинания.

    Знаки обязательно снимать: «согласование:» и «согласование» — одно слово,
    а при сравнении по строкам они расходятся, и сокращение с двоеточием
    выглядело потерявшим смысл.
    """
    words = (w.strip(".,:;!?()«»\"'—–-") for w in text.split())
    return {w for w in words if len(w) > 2 and w not in _STOP}


# Окончания русских прилагательных. Одно прилагательное без существительного —
# не подпись, а обрывок: «Высокая» в карточке ничего не сообщает. Модель даёт
# такой ответ, когда просят уложиться в очень короткую строку.
#
# Список намеренно неполный. Окончания «-ие», «-ее», «-ий» отсюда убраны: на
# них кончаются и обычные существительные («Согласование», «Решение»,
# «Сценарий»), а ошибочный отказ стоит дороже пропуска — слот останется
# пустым, и ряд может уйти целиком.
_ADJECTIVE_TAILS = ("ая", "яя", "ое", "ые", "ый", "ой")


# Служебные слова, которыми подпись не заканчивается. Модель, уложившись в
# лимит знаков, обрывает фразу на предлоге: «Самый быстрорастущий сегмент
# бизнеса в с» — ровно сорок знаков, как и просили.
_TAIL = frozenset(
    {
        "в",
        "на",
        "с",
        "со",
        "до",
        "за",
        "от",
        "по",
        "и",
        "или",
        "для",
        "к",
        "у",
        "о",
        "об",
        "при",
        "из",
        "под",
        "над",
        "а",
        "но",
        "что",
        "как",
    }
)


def tidy(value: str, facts: dict[str, JsonDict] | None = None) -> str:
    """Приводит ответ модели в вид, пригодный для слайда.

    Две беды, обе замечены на снимке готовой колоды:

    * «на {fact:f1}%» превращается в «на 34%%» — модель дописывает единицу к
      ссылке, которая её уже содержит. Знак после подстановки удваивается.
    * «…сегмент бизнеса в с» — фраза обрезана по лимиту знаков, и висящий
      предлог выдаёт обрыв.

    Чинится обе по месту: лишняя единица снимается, хвост обрезается по
    последнему значимому слову. Если после этого ничего не остаётся, вернётся
    пустая строка, и ответ будет отвергнут дальше по проверке.
    """
    text = str(value or "").strip()
    # «цель — f11 открываемости»: модель пишет идентификатор факта без скобок (так факты
    # даны ей в материале). Известный идентификатор становится ссылкой и подставится
    # значением; неизвестный ответ не спасает — его отвергнет проверка чисел.
    text = _BARE_FACT.sub(
        lambda m: f"{{fact:{m.group(1)}}}" if m.group(1) in (facts or {}) else m.group(0), text
    )
    for match in list(_FACT.finditer(text)):
        fact = (facts or {}).get(match.group(0)[6:-1])
        raw = str((fact or {}).get("raw") or "")
        unit = str((fact or {}).get("unit") or "")
        tail = text[match.end() : match.end() + len(unit)] if unit else ""
        if unit and raw.endswith(unit) and tail == unit:
            text = text[: match.end()] + text[match.end() + len(unit) :]
    words = text.split()
    while words and words[-1].lower().strip(".,:;") in _TAIL:
        words.pop()
    return " ".join(words).strip(" ,:;—–-")


def _cut_word(text: str, slide: JsonDict, kind: str) -> bool:
    """Обрубок слова вместо подписи: «Ванд» при «вандализм» на том же слайде, «Гео» при
    «география». Модель так «укладывается» в крошечный слот; такой текст хуже пустоты.
    Для основного текста и подписи одно короткое слово — тоже обрубок."""
    words = re.findall(r"[A-Za-zА-Яа-яЁё]+", str(text))
    if not words:
        return False
    if kind in ("body", "caption", "bullets") and len(words) == 1 and len(words[0]) <= 5:
        return True
    around = " ".join(
        [str(slide.get("title") or "")]
        + [str(b.get("text") or "") for b in slide.get("blocks") or []]
        + [
            str(it.get("text") or "")
            for b in slide.get("blocks") or []
            for it in b.get("items") or []
        ]
    ).lower()
    whole = set(re.findall(r"[a-zа-яё]+", around))
    for word in (w.lower() for w in words if 2 <= len(w) <= 5):
        if word not in whole and any(other.startswith(word) for other in whole):
            return True
    return False


def _dangling(text: str) -> bool:
    """Обрывок вместо подписи: одно прилагательное, без числа и существительного."""
    words = [w for w in str(text).split() if w]
    if len(words) != 1 or any(c.isdigit() for c in text):
        return False
    return len(words[0]) > 4 and words[0].lower().endswith(_ADJECTIVE_TAILS)


# Сколько значимых слов исходной строки обязано остаться в сокращении. Порог
# невысокий: длинную фразу и надо ужимать сильно. Смысл держат два других
# условия — слов должно остаться не меньше двух и первое слово (о чём речь)
# обязано уцелеть. Без этого «Итоги пилота логистической платформы»
# превращалось в «Итоги»: по длине подходит, по смыслу — пусто.
KEEP_RATIO = 0.25


def _keeps_meaning(original: str, short: str) -> bool:
    """Осталась ли в сокращении мысль исходной строки."""
    было, стало = _significant(_norm(original)), _significant(_norm(short))
    if not было:
        return bool(стало)
    if len(стало) < min(2, len(было)):
        return False
    first = next((w for w in _norm(original).split() if w in было), None)
    if first is not None and first not in стало:
        return False
    return len(было & стало) / len(было) >= KEEP_RATIO


def _restates(norm: str, title: str, seen: set[str]) -> bool:
    """Пересказывает ли ответ то, что на слайде уже написано.

    Модель охотно отвечает усечением заголовка: «Экономический эффект» под
    «Экономический эффект для партнёров», «Масштаб и активность» под «Масштаб
    подключения и активность». Формально это не повтор, по смыслу — пустая
    строка: слайд остаётся без новых сведений.

    Сравниваем по значимым словам, а не по подстроке: пропущенное слово внутри
    ломает подстрочное сравнение, но не меняет сути пересказа.
    """
    words = _significant(norm)
    if not words:
        return False
    for other in (_norm(title), *seen):
        theirs = _significant(other)
        if not theirs:
            continue
        if len(words & theirs) / len(words) >= RESTATE_RATIO:
            return True
    return False


def _supported_numbers(text: str, facts: dict[str, JsonDict]) -> bool:
    """Refill may restate source quantities, never invent or change their dimensions."""
    from presentation_designer.parsing.content.numbers import find_numbers

    refs = re.findall(r"\{fact:([^}]+)\}", text)
    if any(ref not in facts for ref in refs):
        return False
    aliases = {"дня": "дней", "день": "дней", "часа": "часов", "час": "часов"}

    def signature(number: Any) -> tuple[Any, ...]:
        unit = str(number.unit or "").lower()
        return number.kind, number.value, number.value_to, aliases.get(unit, unit)

    allowed = {
        signature(number)
        for fact in facts.values()
        for number in find_numbers(str(fact.get("raw") or ""))
    }
    return all(signature(n) in allowed for n in find_numbers(_FACT.sub("", text)))


def apply_answer(
    plan: JsonDict,
    patterns: dict[str, JsonDict],
    answer: JsonDict,
    markers: set[str],
    canvas: Any | None = None,
    facts: dict[str, JsonDict] | None = None,
    replace: set[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    """Принимает только то, что прошло проверку. Остальное молча отбрасывается.

    `replace` — слоты, где текст надо заменить, а не дописать: это ответ на
    просьбу сократить не поместившуюся строку. Замена принимается лишь тогда,
    когда в ней сохранены все числа исходной: сокращение не имеет права
    потерять показатель.
    """
    sample_marks = {_norm(m) for m in markers}
    taken: list[tuple[str, str]] = []

    for slide in plan.get("slides", []):
        per_slide = answer.get(str(slide.get("slide_id"))) or {}
        if not isinstance(per_slide, dict):
            continue
        pattern = patterns.get(slide.get("pattern_id") or "")
        if pattern is None:
            continue
        slots = {s.get("slot_id"): s for s in content_slots(pattern)}
        seen = {_norm(b.get("text", "")) for b in slide.get("blocks", [])}
        filled = {b.get("slot_id") for b in slide.get("blocks", [])}
        # Тексты образца этого слайда: модель получает их в `like` как мерку длины и жанра и
        # охотно возвращает дословно («Наблюдение / Объяснение / Вывод», «Методы, Анализ,
        # Выводы» на финале) — на слайде остаётся содержание шаблона, а не колоды.
        samples = {
            _norm(str(s.get("sample_text") or ""))
            for s in pattern.get("slots") or []
            if str(s.get("sample_text") or "").strip()
        }

        for slot_id, text in per_slide.items():
            slot = slots.get(slot_id)
            value = tidy(text, facts)
            if facts is not None and not _supported_numbers(value, facts):
                log.info("отвергнут %s/%s: неподтверждённое число", slide.get("slide_id"), slot_id)
                continue
            norm = _norm(value)
            shorten = (str(slide.get("slide_id")), slot_id) in (replace or set())
            if shorten:
                block = next(
                    (b for b in slide.get("blocks", []) if b.get("slot_id") == slot_id), None
                )
                if block is None or not value or slot is None:
                    continue
                if not _keeps_meaning(str(block.get("text") or ""), value):
                    log.info(
                        "отвергнуто сокращение %s/%s: от строки ничего не осталось — %r",
                        slide.get("slide_id"),
                        slot_id,
                        value[:60],
                    )
                    continue
                if set(_DIGITS.findall(str(block.get("text") or ""))) - set(_DIGITS.findall(value)):
                    log.info(
                        "отвергнуто сокращение %s/%s: потерян показатель — %r",
                        slide.get("slide_id"),
                        slot_id,
                        value[:60],
                    )
                    continue
                if _fits(slot, value, canvas) is not True:
                    log.info(
                        "отвергнуто сокращение %s/%s: всё ещё не помещается — %r",
                        slide.get("slide_id"),
                        slot_id,
                        value[:60],
                    )
                    continue
                block["text"] = value
                block["shortened_by"] = "design.refill"
                taken.append((str(slide.get("slide_id")), slot_id))
                continue
            if not value or slot is None or slot_id in filled:
                log.info(
                    "отвергнут %s/%s: пустой ответ или слот занят",
                    slide.get("slide_id"),
                    slot_id,
                )
                continue
            if norm in sample_marks or norm in seen or norm in samples:
                log.info(
                    "отвергнут %s/%s: образец шаблона или повтор",
                    slide.get("slide_id"),
                    slot_id,
                )
                continue
            if _dangling(value) or _cut_word(value, slide, str(slot.get("kind") or "")):
                log.info("отвергнут %s/%s: обрывок — %r", slide.get("slide_id"), slot_id, value)
                continue
            if _restates(norm, slide.get("title", ""), seen):
                log.info(
                    "отвергнут %s/%s: пересказ заголовка — %r",
                    slide.get("slide_id"),
                    slot_id,
                    value[:50],
                )
                continue
            verdict = _fits(slot, value, canvas)
            if verdict is not True:
                log.info(
                    "отвергнут %s/%s: замер %s, %d знаков — %r",
                    slide.get("slide_id"),
                    slot_id,
                    verdict,
                    len(value),
                    value[:60],
                )
                continue
            slide.setdefault("blocks", []).append(
                {
                    "slot_id": slot_id,
                    "kind": slot.get("kind"),
                    "text": value,
                    "source": "design.refill",
                }
            )
            seen.add(norm)
            taken.append((str(slide.get("slide_id")), slot_id))
    return taken


def refill(
    plan: JsonDict,
    profile: JsonDict,
    ask: Callable[[str, str], str],
    *,
    prompt: str,
    story: JsonDict | None = None,
    facts: list[str] | None = None,
    limit: int = 24,
    canvas: Any | None = None,
    package: JsonDict | None = None,
) -> list[tuple[str, str]]:
    """Заполняет пустые слоты колоды одним обращением к модели.

    `ask(system, user) -> текст` — способ вызвать модель; он передаётся
    снаружи, поэтому слой остаётся проверяемым без сети.
    """
    patterns = {p.get("pattern_id"): p for p in profile.get("patterns", [])}
    canvas = canvas or _canvas_of(profile)
    tasks = collect_gaps(plan, patterns, limit, story, canvas)
    log.info(
        "дозапрос: слайдов с пустотами %d, слотов %d",
        len(tasks),
        sum(len(t["empty_slots"]) for t in tasks),
    )
    if not tasks:
        return []

    if facts:
        for task in tasks:
            task["facts"] = facts

    payload: JsonDict = {"slides": tasks}
    if is_concept(story or {}) or is_concept(plan):
        payload["grounding_policy"] = CONCEPT_POLICY
    try:
        raw = ask(prompt, json.dumps(payload, ensure_ascii=False))
    except Exception as exc:  # сеть, квота, таймаут
        log.warning("дозапрос слотов не удался: %s", exc)
        return []

    try:
        answer = _extract_json(raw)
    except ValueError as exc:
        log.warning("ответ дозапроса не разобран: %s", exc)
        return []

    markers = {str(m) for m in profile.get("placeholder_markers") or []}
    taken = apply_answer(
        plan,
        patterns,
        answer,
        markers,
        canvas,
        facts_index(package) if package is not None else None,
    )
    log.info(
        "дозапрос: модель вернула %d слотов, принято %d",
        sum(len(v) for v in answer.values() if isinstance(v, dict)),
        len(taken),
    )
    return taken


def facts_index(package: JsonDict | None) -> dict[str, JsonDict]:
    """Факты пакета по идентификатору: нужны, чтобы видеть их единицы измерения."""
    return {
        str(f.get("fact_id")): f for f in (package or {}).get("facts") or [] if f.get("fact_id")
    }


def _extract_json(text: str) -> JsonDict:
    """Объект JSON из ответа модели, даже если он обёрнут в ```-блок."""
    body = str(text or "").strip()
    if body.startswith("```"):
        parts = body.split("```")
        body = parts[1] if len(parts) > 1 else body
        if body.startswith("json"):
            body = body[4:]
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("в ответе нет объекта JSON")
    try:
        data = json.loads(body[start : end + 1])
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(data, dict):
        raise ValueError("ожидался объект")
    return data
