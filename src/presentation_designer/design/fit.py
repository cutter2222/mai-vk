"""Подгонка композиции слайда под объём содержания.

Три прохода над планом, каждый — чистая функция, каждый отчитывается о том, что
изменил. Порядок важен: сначала подбирается паттерн под объём, потом остаток
слотов добирается из истории. Обратный порядок добирал бы текст в слоты, от
которых мы всё равно откажемся.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.design.rules import (
    NOT_ENRICHABLE,
    TEXT_KINDS,
    Thresholds,
)

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]


@dataclass
class Decision:
    """Одно решение слоя: что и почему изменено на слайде."""

    slide_id: str
    action: str  # pattern_swap | enrich | keep
    reason: str
    before: JsonDict = field(default_factory=dict)
    after: JsonDict = field(default_factory=dict)

    def to_dict(self) -> JsonDict:
        return {
            "slide_id": self.slide_id,
            "action": self.action,
            "reason": self.reason,
            "before": self.before,
            "after": self.after,
        }


@dataclass
class Report:
    decisions: list[Decision] = field(default_factory=list)

    def add(self, decision: Decision) -> None:
        self.decisions.append(decision)

    def summary(self) -> JsonDict:
        from collections import Counter

        counts = Counter(d.action for d in self.decisions)
        return {"decisions": len(self.decisions), "by_action": dict(counts)}

    def to_dict(self) -> JsonDict:
        return {**self.summary(), "items": [d.to_dict() for d in self.decisions]}


# -- замер -------------------------------------------------------------------


def _area(slot: JsonDict) -> float:
    box = slot.get("bbox") or {}
    return float(box.get("width", 0.0)) * float(box.get("height", 0.0))


def _data_slots(pattern: JsonDict) -> list[JsonDict]:
    from presentation_designer.generation.matching import ordinal_slots

    ordinals = ordinal_slots(pattern)
    return [s for s in pattern.get("slots", []) if s.get("slot_id") not in ordinals]


def content_slots(pattern: JsonDict) -> list[JsonDict]:
    """Слоты паттерна, которые несут содержание.

    Иконки и подложки исключены: их заполненность от плана не зависит, а в
    паттернах шаблона их бывает больше, чем текстовых (у «Карточек × 3» шесть
    иконок на три текста).
    """
    return [s for s in _data_slots(pattern) if s.get("kind") in TEXT_KINDS]


def fill_ratio(slide: JsonDict, pattern: JsonDict) -> float:
    """Какая доля площади текстовых слотов получила содержание.

    Считается по площади, а не по числу слотов: заголовок на три четверти
    ширины и подпись на шестую часть — это очень разный вклад в то, выглядит
    слайд наполненным или пустым. Ровно это и меряет раздел «Плотность»
    Приложения 1.
    """
    slots = content_slots(pattern)
    total = sum(_area(s) for s in slots)
    if total <= 0:
        return 1.0
    filled_ids = {
        b.get("slot_id") for b in slide.get("blocks", []) if b.get("text") or b.get("number")
    }
    used = sum(_area(s) for s in slots if s.get("slot_id") in filled_ids)
    return used / total


# -- проход 1: подбор паттерна ----------------------------------------------


def _kinds_needed(slide: JsonDict) -> dict[str, int]:
    from collections import Counter

    return dict(Counter(b.get("kind") for b in slide.get("blocks", []) if b.get("kind")))


# Родственные виды слотов: подпись можно поставить в слот основного текста, а
# не только в слот подписи. Различие между `caption`, `body` и `label` — в
# кегле, и кегль приходит из шаблона вместе со слотом. Заголовок и число сюда
# не входят: заголовок держит структуру слайда, число обязано стоять там, где
# его ждёт оформление.
#
# Без этого замена паттерна не работала там, где нужнее всего: слайд с одним
# показателем занимал инфографику на пятнадцать слотов, а единственные
# паттерны со слотом `caption` — такие же большие.
_KIN = {
    "caption": ("caption", "body", "label"),
    "label": ("label", "caption", "body"),
    # Основной текст — сначала в слот текста, и только потом в подпись: в сетке карточек
    # `label` — заголовок карточки, и пункт в нём оставлял пустым слот текста под ним.
    "body": ("body", "caption", "label"),
    "quote": ("quote", "body"),
}


def _kin(kind: str) -> tuple[str, ...]:
    return _KIN.get(kind, (kind,))


def _can_host(pattern: JsonDict, needed: dict[str, int]) -> bool:
    """Вместит ли паттерн все блоки слайда по видам, считая родственные."""
    from collections import Counter

    have = Counter(s.get("kind") for s in _data_slots(pattern))
    for kind, count in needed.items():
        for alt in _kin(str(kind)):
            take = min(count, have.get(alt, 0))
            have[alt] -= take
            count -= take
            if count <= 0:
                break
        if count > 0:
            return False
    return True


# Родственные назначения: показатели, карточки и колонки — разные способы
# показать одно и то же содержание, и переход между ними допустим. Титул,
# разделитель и финал остаются при своём: там роль несёт смысл места в колоде.
#
# Без этого слайд с тремя числами оставался в инфографике на шестнадцать
# полос: все «числовые» паттерны шаблона такого же размера, и замены в своей
# роли не находилось. На снимке это худший слайд колоды — полосы не связаны
# с числами, а крайнее значение обрезается краем слайда.
_ROLE_FAMILY = {
    "numbers": ("numbers", "kpi", "cards", "two_column"),
    "kpi": ("kpi", "numbers", "cards", "two_column"),
    "cards": ("cards", "kpi", "two_column", "numbers"),
    "two_column": ("two_column", "cards", "kpi"),
    "bullets": ("bullets", "cards", "text"),
    "text": ("text", "bullets", "cards"),
}


_SERVICE_ROLES = ("title", "section_divider", "thanks", "qr", "agenda")


def _builtin(pattern: JsonDict) -> bool:
    return (pattern.get("source") or {}).get("kind") == "builtin"


def _roles_for(role: str) -> tuple[str, ...]:
    return _ROLE_FAMILY.get(role, (role,))


def choose_pattern(
    slide: JsonDict,
    patterns: dict[str, JsonDict],
    limits: Thresholds,
) -> tuple[JsonDict | None, str]:
    """Паттерн, чья вместимость ближе к объёму содержания слайда.

    Кандидаты берутся среди паттернов того же назначения: разделитель не должен
    превратиться в сетку карточек, даже если по числу слотов она подходит
    лучше. Если подходящего нет, возвращается `None` — это нормальный исход,
    а не ошибка.
    """
    current = patterns.get(slide.get("pattern_id") or "")
    if current is None:
        return None, "паттерн слайда не найден в профиле"

    needed = _kinds_needed(slide)
    blocks = sum(needed.values())
    if blocks == 0:
        return None, "на слайде нет блоков"

    role = str(current.get("role") or "")
    family = _roles_for(role)
    keep_template = role in _SERVICE_ROLES and _builtin(current) is False
    best, best_score = None, None
    for pattern in patterns.values():
        if str(pattern.get("role") or "") not in family or not _can_host(pattern, needed):
            continue
        if keep_template and _builtin(pattern):
            # Обложка, разделитель и финал — лицо колоды, нарисованное автором шаблона:
            # пустой слот подписи не повод менять их на свою белую композицию.
            continue
        slots = len(content_slots(pattern))
        if slots < blocks:
            continue
        # Чем ближе число слотов к числу блоков, тем меньше пустот. При равенстве
        # предпочитается паттерн с большей площадью под содержание: слайд
        # выглядит наполненнее.
        # Своя роль предпочтительнее родственной: замена внутри назначения
        # ближе к замыслу шаблона.
        score = (
            slots - blocks,
            0 if str(pattern.get("role") or "") == role else 1,
            -sum(_area(s) for s in content_slots(pattern)),
        )
        if best_score is None or score < best_score:
            best, best_score = pattern, score

    if best is None or best.get("pattern_id") == current.get("pattern_id"):
        return None, "подходящего паттерна в профиле нет"

    have_now = len(content_slots(current))
    if have_now <= blocks * limits.max_slots_ratio:
        return None, "текущий паттерн в пределах допуска"

    # Допуск обязан выдерживать и кандидат. Иначе замена лишь уменьшает число
    # пустот, оставляя слайд полупустым: «Цифры × 26» менялись на «Цифры × 11»
    # под один показатель, и две карточки из трёх всё равно оставались голыми.
    have_new = len(content_slots(best))
    if have_new > blocks * limits.max_slots_ratio:
        # Допуск обязан выдерживать и кандидат. Послабление здесь пробовали и
        # откатили: слайд с одним показателем ушёл из инфографики в паттерн на
        # четыре карточки, счёт дефектов упал — а на снимке остался одинокий
        # прямоугольник на пустом холсте. Пустой слот счёт видит, пустое
        # полотно — нет, поэтому решает снимок, а не число.
        return None, (
            f"лучший кандидат {best.get('pattern_id')} на {have_new} слотов "
            f"тоже избыточен при {blocks} блоках"
        )
    return best, (
        f"слотов {have_now} при {blocks} блоках; "
        f"выбран {best.get('pattern_id')} на {len(content_slots(best))} слотов"
    )


def remap_blocks(slide: JsonDict, pattern: JsonDict) -> None:
    """Переносит блоки слайда на слоты нового паттерна, сохраняя вид.

    Порядок внутри вида сохраняется: первый `body` плана попадает в первый
    `body` паттерна. Это важнее «умного» сопоставления — так результат
    предсказуем и объясним.
    """
    by_kind: dict[str, list[str]] = {}
    for slot in _data_slots(pattern):
        by_kind.setdefault(str(slot.get("kind")), []).append(str(slot.get("slot_id")))

    used: dict[str, int] = {}
    for block in slide.get("blocks", []):
        kind = str(block.get("kind"))
        # Свой вид слота — первым, родственный — если своих не хватило.
        for alt in _kin(kind):
            index = used.get(alt, 0)
            candidates = by_kind.get(alt, [])
            if index < len(candidates):
                block["slot_id"] = candidates[index]
                used[alt] = index + 1
                break
    slide["pattern_id"] = pattern.get("pattern_id")


# -- проход 2: добор содержания ---------------------------------------------


def _thesis_texts(story: JsonDict, refs: list[str]) -> list[str]:
    """Пояснения и подтверждения тезисов слайда — источник добора.

    Ничего не сочиняется: берётся то, что стадия `story` уже построила из
    исходных материалов и снабдила ссылками на источник. Пояснение раздела — описание
    части рассказа («Введение в раздел с планом действий»), а не материал: слайд без
    разделителя закрывает тезис-раздел, но его пояснение на слайд не идёт.
    """
    theses = {t.get("thesis_id"): t for t in story.get("theses", [])}
    out: list[str] = []
    for ref in refs:
        thesis = theses.get(ref) or {}
        if thesis.get("kind") == "section":
            continue
        for key in ("explanation", "detail", "evidence"):
            value = thesis.get(key)
            if isinstance(value, str) and value.strip():
                out.append(value.strip())
    return out


def _section_lead(story: JsonDict, refs: list[str]) -> list[str]:
    """Подводка для слайда-раздела: о чём будет речь дальше.

    Раздел открывает группу слайдов, и его тезис несёт только название. Материал
    для подписи всё же есть — это формулировка следующего тезиса, то есть первое
    утверждение самого раздела. Берём её, а не сочиняем.
    """
    theses = story.get("theses") or []
    order = {t.get("thesis_id"): i for i, t in enumerate(theses)}
    positions = [order[r] for r in refs if r in order]
    if not positions:
        return []
    nxt = max(positions) + 1
    if nxt >= len(theses):
        return []
    statement = theses[nxt].get("statement")
    return [statement.strip()] if isinstance(statement, str) and statement.strip() else []


def _fits(slot: JsonDict, text: str, canvas: Any | None = None) -> bool | None:
    """Помещается ли текст в слот: да, нет или неизвестно.

    Замер делегирован `design/measure.py`, который считает по файлам шрифтов
    с кернингом и по настоящему переносу слов. Своей арифметики здесь больше
    нет: она ошибалась в обе стороны — удаляла заголовок обложки и пропускала
    текст, который наложился на соседей.
    """
    from presentation_designer.design.measure import fits

    return fits(slot, text, canvas)


def _overlap(a: str, b: str) -> float:
    """Доля общих основ слов (по пять букв) — пересказ той же мысли, а не новое."""
    left = {w[:5] for w in a.split() if len(w) > 3}
    right = {w[:5] for w in b.split() if len(w) > 3}
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _plain(text: Any) -> str:
    return re.sub(r"\W+", " ", str(text or "")).strip().lower()


def enrich(
    slide: JsonDict, pattern: JsonDict, story: JsonDict, canvas: Any | None = None
) -> list[str]:
    """Заполняет пустые текстовые слоты пояснениями тезисов слайда.

    Заголовки и числа не трогаются: заголовок — работа планировщика, а число,
    взятое не из фактов, нарушило бы вопрос 4 Приложения 1.
    """
    filled = {b.get("slot_id") for b in slide.get("blocks", [])}
    free = [
        s
        for s in content_slots(pattern)
        if s.get("slot_id") not in filled and s.get("kind") not in NOT_ENRICHABLE
    ]
    if not free:
        return []

    refs = slide.get("thesis_refs") or []
    texts = _thesis_texts(story, refs)
    if not texts and str(slide.get("role", "")).startswith("section"):
        texts = _section_lead(story, refs)
    # Пояснение тезиса часто уже стоит на слайде основным текстом: второй раз мелкой
    # сноской под ним оно выглядит как сбой вёрстки.
    on_slide = [_plain(b.get("text")) for b in slide.get("blocks", [])] + [
        _plain(it.get("text")) for b in slide.get("blocks", []) for it in b.get("items") or []
    ]
    texts = [
        t
        for t in texts
        if not any(
            _plain(t) and (_plain(t) in o or o in _plain(t) or _overlap(_plain(t), o) >= 0.5)
            for o in on_slide
            if o
        )
    ]
    added: list[str] = []
    for slot, text in zip(free, texts, strict=False):
        if _fits(slot, text, canvas) is not True:  # при «неизвестно» не рискуем
            continue
        slide.setdefault("blocks", []).append(
            {
                "slot_id": slot.get("slot_id"),
                "kind": slot.get("kind"),
                "text": text,
                "source": "design.enrich",
            }
        )
        added.append(str(slot.get("slot_id")))
    return added
