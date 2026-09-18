"""Обратная связь вёрстки: план правится по фактам сборки, а не по оценкам.

До этого модуля слой `design` работал вслепую. Он оценивал заполненность по
площади слотов профиля и на слайде-выводе получал 82%: заголовок занимает
восьмую часть холста, а четыре подписи в карточках — по полпроцента каждая.
По площади слайд «наполнен». В файле — четыре пустые карточки с номерами
01–04 и больше ничего. Замер на снимке подтвердил: дефект грубый, а оценка
его не видит и не может увидеть.

Вёрстка же знает точно — она эти карточки и рисует. `compose_deck` возвращает
`ComposedDeck`: у каждого объекта слайда есть `slot_id`, рамка в долях холста,
вычисленный кегль и сам текст. Этого достаточно, чтобы ответить на два
вопроса без единой догадки:

* какие слоты остались пустыми — слот паттерна, которому не отвечает ни один
  объект собранного файла;
* какой текст не помещается — по метрикам шрифта в **фактической** рамке.

Отсюда порядок работы: черновая сборка → факты → правка по фактам → сборка
начисто. Сборка колоды стоит около секунды, поэтому двойной проход не спорит
с лимитом ТЗ в пять минут.

Правка проверяется той же меркой, которой найдена: после правки колода
собирается снова и факты считаются заново. Стало хуже — берётся исходный план.
Это не перестраховка, а вывод из прошлой попытки: подгонка кегля по ступеням
шкалы убрала одни дефекты и добавила больше других, и заметно это стало лишь
по общему счёту.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.design.fit import Decision, content_slots, remap_blocks
from presentation_designer.design.guard import _norm, _shorten
from presentation_designer.design.measure import (
    Canvas,
    canvas_of,
    lines_needed,
    max_chars,
    slot_capacity,
)
from presentation_designer.design.rules import NOT_ENRICHABLE

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]

# Слот, куда входит меньше этого числа знаков, — часть оформления: точка на
# карте, единица измерения у числа. Пустым его считать нельзя: осмысленного
# текста такой длины не бывает, а попытка заполнить даёт обрубок.
#
# Числу столько знаков не нужно: «34%» — четыре. С общим порогом пустые места
# под показатели вообще не попадали в факты, и слайд с двумя числами в
# композиции на шестнадцать полос считался безупречным.
MIN_FILLABLE_CHARS = 8
MIN_FILLABLE_BY_KIND = {"number": 2}

# Автоподбор кегля PowerPoint («сжать текст при переполнении») спасает
# примерно двукратный выход за рамку — дальше текст становится нечитаемым.
# До этой границы переполнение считается мягким: в файле оно выглядит как
# уменьшенный кегль, а не как наложение.
SHRINK_RESCUES = 2.0

# Вес дефекта в общем счёте колоды.
#
# Пустые слоты неравноценны, и это видно на снимках. Ряд карточек, где не
# заполнена ни одна, читается как незаполненная форма — это `hole`. Список из
# трёх пунктов в макете на пять выглядит просто списком из трёх: слоты того же
# вида рядом заполнены, макет отработал — это `gap`, и цена ему невелика.
# Разделять их обязательно: иначе счёт колоды определяют слайды с россыпью
# мелких подписей, а не слайд с четырьмя голыми карточками.
WEIGHTS = {"hole": 3.0, "gap": 1.0, "overflow": 2.0, "shrunk": 0.5, "spill": 0.25}

# Виды фактов, означающих незаполненный слот.
EMPTY_KINDS = ("hole", "gap")

# Ряд — от двух слотов до шести. Дальше это уже не ряд карточек, а плотная
# инфографика вроде «Цифры × 16»: требовать заполнить её целиком неверно, для
# неё есть подбор паттерна поменьше.
MAX_ROW_SLOTS = 6

# Назначения, где одинаковые слоты — это список, а не ряд: список из трёх
# пунктов в макете на пять выглядит списком из трёх, и это не дефект.
LIST_ROLES = frozenset({"agenda", "bullets", "toc"})


def rows(pattern: JsonDict) -> list[set[str]]:
    """Ряды одинаковых мест: карточки, плитки, колонки — списком идентификаторов.

    Ряд узнаётся по одинаковому размеру рамки при одинаковом виде слота.
    Размер здесь важнее вида: в паттерне «Карточки × 4» восемь слотов вида
    `body` — четыре заголовка карточек и четыре описания под ними. Это два
    разных ряда, и заполнять их надо порознь. Пока признак был по виду
    целиком, ряд описаний не опознавался: дозапрос заполнял одно описание из
    четырёх, и три карточки оставались с одним заголовком.

    Ряд заполняется целиком: пустое место среди заполненных читается как
    недоделка. Список (роли `agenda`, `bullets`) этому правилу не подчиняется
    — там короткий список просто короче.
    """
    if str(pattern.get("role") or "") in LIST_ROLES:
        return []
    groups: dict[tuple[str, int, int], set[str]] = {}
    for slot in content_slots(pattern):
        box = slot.get("bbox") or {}
        width, height = float(box.get("width") or 0), float(box.get("height") or 0)
        if width <= 0 or height <= 0:
            continue
        key = (str(slot.get("kind") or ""), round(width, 2), round(height, 2))
        groups.setdefault(key, set()).add(str(slot.get("slot_id")))
    return [ids for ids in groups.values() if 2 <= len(ids) <= MAX_ROW_SLOTS]


@dataclass(frozen=True)
class Fact:
    """Один факт о собранном слайде: что именно не в порядке и насколько."""

    slide_id: str
    slide_index: int
    slot_id: str
    kind: str                      # hole | gap | overflow | shrunk
    slot_kind: str = ""
    capacity_chars: int | None = None
    needed_lines: int = 0
    fits_lines: int = 0
    text: str = ""

    def to_dict(self) -> JsonDict:
        out: JsonDict = {
            "slide_id": self.slide_id,
            "slide_index": self.slide_index,
            "slot_id": self.slot_id,
            "kind": self.kind,
        }
        if self.kind in EMPTY_KINDS:
            out["capacity_chars"] = self.capacity_chars
        else:
            out["lines"] = [self.needed_lines, self.fits_lines]
        return out


@dataclass
class Facts:
    """Факты по всей колоде и общий счёт дефектов."""

    items: list[Fact] = field(default_factory=list)
    canvas: Canvas | None = None
    # Заголовки как они встали в файл: подстановки фактов уже раскрыты.
    # В плане на их месте стоит «{fact:f2}», и как материал для подписи такая
    # строка бесполезна.
    titles: dict[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.items)

    def of_slide(self, slide_id: str) -> list[Fact]:
        return [f for f in self.items if f.slide_id == slide_id]

    def score(self) -> float:
        """Общий счёт: чем меньше, тем лучше собрана колода."""
        return sum(WEIGHTS.get(f.kind, 1.0) for f in self.items)

    def summary(self) -> JsonDict:
        from collections import Counter

        counts = Counter(f.kind for f in self.items)
        return {"score": round(self.score(), 1), "by_kind": dict(counts)}

    def to_dict(self) -> JsonDict:
        return {**self.summary(), "items": [f.to_dict() for f in self.items]}


# -- чтение фактов -----------------------------------------------------------

def _probe(obj: JsonDict) -> JsonDict:
    """Объект собранного слайда в виде, понятном замеру.

    Замер ждёт рамку и вычисленный стиль на верхнем уровне, у объекта колоды
    стиль лежит внутри `text`. Кроме перекладки здесь ничего не происходит:
    важно, что это рамка и кегль **после** вёрстки, а не из профиля.
    """
    text = obj.get("text") or {}
    return {
        "slot_id": obj.get("slot_id"),
        "bbox": obj.get("bbox") or {},
        "computed_style": text.get("computed_style") or {},
    }


def _filled_slots(slide: JsonDict) -> set[str]:
    """Слоты, за которыми в собранном файле стоит объект.

    Текст образца шаблона тоже считается заполнением: вёрстка оставляет его
    осознанно (`content_source: sample`), и дыры в макете он не создаёт.
    Образцы-заглушки убирает застава, а не этот модуль.
    """
    out: set[str] = set()
    for obj in slide.get("objects", []):
        slot_id = obj.get("slot_id")
        if not slot_id:
            continue
        text = str((obj.get("text") or {}).get("plain") or "").strip()
        if obj.get("kind") == "text" and not text:
            continue
        out.add(str(slot_id))
    return out


def read(deck: JsonDict, plan: JsonDict, profile: JsonDict) -> Facts:
    """Факты о собранной колоде: пустые слоты и переполненный текст."""
    canvas = _canvas(deck, profile)
    patterns = {p.get("pattern_id"): p for p in profile.get("patterns", [])}
    planned = {s.get("slide_id"): s for s in plan.get("slides", [])}
    facts: list[Fact] = []
    titles: dict[str, str] = {}

    for index, slide in enumerate(deck.get("slides", [])):
        slide_id = str(slide.get("slide_id") or index)
        planned_slide = planned.get(slide.get("slide_id")) or {}
        pattern = patterns.get(slide.get("pattern_id") or planned_slide.get("pattern_id") or "")
        filled = _filled_slots(slide)
        title = _title_of(slide)
        if title:
            titles[slide_id] = title

        if pattern is not None:
            # Заполнен ли на слайде хоть один слот того же вида: ряд карточек,
            # где пусты все, и ряд, где пуста одна, — разные дефекты.
            kinds_filled = {
                str(s.get("kind"))
                for s in content_slots(pattern)
                if str(s.get("slot_id")) in filled
            }
            row_sets = rows(pattern)
            for slot in content_slots(pattern):
                slot_id = str(slot.get("slot_id"))
                if slot_id in filled:
                    continue
                slot_kind = str(slot.get("kind") or "")
                capacity = max_chars(slot, canvas)
                floor = MIN_FILLABLE_BY_KIND.get(slot_kind, MIN_FILLABLE_CHARS)
                if capacity is None or capacity < floor:
                    continue            # оформление, а не место под содержание
                facts.append(
                    Fact(
                        slide_id=slide_id,
                        slide_index=index,
                        slot_id=slot_id,
                        # Пустое место в ряду — дыра всегда: ряд обязан быть
                        # полным. В остальных случаях заполненный сосед того
                        # же вида говорит, что макет отработал.
                        kind=(
                            "gap"
                            if slot_kind in kinds_filled
                            and not any(slot_id in row for row in row_sets)
                            else "hole"
                        ),
                        slot_kind=slot_kind,
                        capacity_chars=capacity,
                    )
                )

        for obj in slide.get("objects", []):
            if obj.get("kind") != "text" or not obj.get("slot_id"):
                continue
            text = str((obj.get("text") or {}).get("plain") or "").strip()
            if not text:
                continue
            measured = lines_needed(_probe(obj), text, canvas)
            if measured is None:
                continue                # нечем мерить — не выдумываем дефект
            needed, available = measured
            available = max(available, 1)
            if needed <= available:
                continue
            autofit = str((obj.get("text") or {}).get("autofit") or "none")
            soft = autofit in ("shrink", "resize_shape") and needed <= available * SHRINK_RESCUES
            if soft:
                kind = "shrunk"
            elif _hits_something(obj, slide, needed - available, canvas):
                kind = "overflow"
            else:
                kind = "spill"
            facts.append(
                Fact(
                    slide_id=slide_id,
                    slide_index=index,
                    slot_id=str(obj.get("slot_id")),
                    kind=kind,
                    slot_kind=str(obj.get("name") or ""),
                    needed_lines=needed,
                    fits_lines=available,
                    text=text,
                )
            )

    return Facts(items=facts, canvas=canvas, titles=titles)


# Какая доля площади под текстом должна быть занята чужим объектом, чтобы
# считать это столкновением. Совсем малое пересечение — обычное дело: рамки в
# шаблонах перекрываются на волос.
_HIT_RATIO = 0.05


def _hits_something(
    obj: JsonDict, slide: JsonDict, extra_lines: int, canvas: Canvas | None
) -> bool:
    """Наезжает ли вышедший за рамку текст на соседей или на край слайда.

    Выход за рамку сам по себе не дефект. PowerPoint текст не обрезает, и
    шаблоны этим пользуются: подпись в карточке объявлена высотой в три
    строки, а места под ней — на десять. Проверено снимком: слайд, где замер
    насчитал шесть строк при трёх, выглядит безупречно.

    Дефект — столкновение. Считается полоса, в которую уходит лишний текст, и
    проверяется, занята ли она другим содержанием. Пустые рамки оформления
    (подложки карточек) препятствием не считаются: текст в них и должен лежать.
    """
    spill = _spill_rect(obj, extra_lines, canvas)
    if spill is None:
        return False
    x, y, width, height = spill
    if x + width > 1.0 or y + height > 1.0:
        return True                     # текст уходит за край слайда

    area = width * height
    if area <= 0:
        return False
    for other in slide.get("objects", []):
        if other is obj or str(other.get("role") or "") in ("background", "decoration"):
            continue
        if other.get("kind") == "text" and not str(
            (other.get("text") or {}).get("plain") or ""
        ).strip():
            continue                    # пустая рамка-подложка: не препятствие
        box = other.get("bbox") or {}
        ox, oy = float(box.get("x") or 0), float(box.get("y") or 0)
        ow, oh = float(box.get("width") or 0), float(box.get("height") or 0)
        overlap_w = min(x + width, ox + ow) - max(x, ox)
        overlap_h = min(y + height, oy + oh) - max(y, oy)
        if overlap_w > 0 and overlap_h > 0 and overlap_w * overlap_h > area * _HIT_RATIO:
            return True
    return False


def _spill_rect(
    obj: JsonDict, extra_lines: int, canvas: Canvas | None
) -> tuple[float, float, float, float] | None:
    """Полоса в долях холста, в которую уходит не поместившийся текст.

    Направление зависит от текста. Абзац переносится и растёт вниз. Одно слово
    перенести некуда: PowerPoint оставит его в одну строку, и выйдет оно вбок.
    Без этого различения число «120» в плотной сетке объявлялось наехавшим на
    строку снизу, хотя в файле ничего не наезжало.
    """
    from presentation_designer.shared import text_metrics

    cap = slot_capacity(_probe(obj), canvas)
    if cap is None or cap.line_height_pt <= 0 or extra_lines <= 0:
        return None
    canvas = canvas or Canvas()
    box = obj.get("bbox") or {}
    x, y = float(box.get("x") or 0), float(box.get("y") or 0)
    width, height = float(box.get("width") or 0), float(box.get("height") or 0)
    text = str((obj.get("text") or {}).get("plain") or "").strip()

    if " " in text:
        line = cap.line_height_pt * text_metrics.EMU_PER_PT / canvas.height_emu
        return x, y + height, width, line * extra_lines

    style = (_probe(obj).get("computed_style") or {}).get("font") or {}
    size_pt = float(style.get("size_pt") or 0)
    if size_pt <= 0:
        return None
    font = text_metrics.resolve_font(style.get("family"))
    need_emu = text_metrics.text_width_pt(text, font, size_pt) * text_metrics.EMU_PER_PT
    extra = need_emu - width * canvas.width_emu
    if extra <= 0:
        return None
    return x + width, y, extra / canvas.width_emu, height


def _title_of(slide: JsonDict) -> str:
    """Заголовок слайда из собранного файла, а не из плана."""
    for obj in slide.get("objects", []):
        if str(obj.get("slot_id") or "").startswith("title"):
            text = str((obj.get("text") or {}).get("plain") or "").strip()
            if text:
                return text
    return str(slide.get("title") or "").strip()


def _canvas(deck: JsonDict, profile: JsonDict) -> Canvas:
    """Холст собранного файла; при отсутствии сведений — холст профиля."""
    size = deck.get("slide_size") or {}
    width, height = int(size.get("width_emu") or 0), int(size.get("height_emu") or 0)
    if width > 0 and height > 0:
        return Canvas(width_emu=width, height_emu=height)
    return canvas_of(profile)


# -- правка по фактам --------------------------------------------------------

def repair(
    plan: JsonDict,
    facts: Facts,
    profile: JsonDict,
    story: JsonDict,
    *,
    limits: Any = None,
) -> list[Decision]:
    """Чинит план по фактам вёрстки, не обращаясь к модели. Меняет план на месте.

    Порядок правок — от сохраняющих смысл к меняющим композицию: сократить
    текст, добрать содержание из смыслового плана, и лишь потом менять
    паттерн. Обратный порядок выбрасывал бы слоты, которые есть чем заполнить.
    """
    from presentation_designer.design.fit import choose_pattern, enrich

    canvas = facts.canvas
    patterns = {p.get("pattern_id"): p for p in profile.get("patterns", [])}
    decisions: list[Decision] = []

    for slide in plan.get("slides", []):
        slide_id = str(slide.get("slide_id") or "")
        mine = facts.of_slide(slide_id)
        if not mine:
            continue
        pattern = patterns.get(slide.get("pattern_id") or "")
        if pattern is None:
            continue
        slots = {s.get("slot_id"): s for s in pattern.get("slots", [])}

        # 1. Текст, не влезающий в фактическую рамку: первое предложение, если
        #    оно помещается. Не помещается — оставляем: обрубок хуже тесноты, а
        #    кегль правит лестница ёмкости вёрстки.
        for fact in [f for f in mine if f.kind == "overflow"]:
            block = _block_of(slide, fact.slot_id)
            slot = slots.get(fact.slot_id)
            if block is None or slot is None:
                continue
            short = _fit_text(str(block.get("text") or ""), slot, canvas)
            if short:
                block["text"] = short
                block["shortened_by"] = "design.feedback"
                decisions.append(
                    Decision(
                        slide_id=slide_id,
                        action="shorten",
                        reason=(
                            f"{fact.slot_id}: по факту вёрстки {fact.needed_lines} строк "
                            f"при {fact.fits_lines}"
                        ),
                        after={"text": short},
                    )
                )

        # 2. Пустые слоты — содержанием из смыслового плана.
        empty = [f for f in mine if f.kind in EMPTY_KINDS]
        if empty:
            added = enrich(slide, pattern, story, canvas)
            if added:
                decisions.append(
                    Decision(
                        slide_id=slide_id,
                        action="enrich",
                        reason=f"по факту вёрстки пусто слотов {len(empty)}; добрано {len(added)}",
                        after={"slots": added},
                    )
                )

        # 3. Остаток пустот — сменой паттерна, если в профиле есть теснее.
        still = [f.slot_id for f in empty if _block_of(slide, f.slot_id) is None]
        if still and limits is not None:
            chosen, why = choose_pattern(slide, patterns, limits)
            if chosen is not None:
                remap_blocks(slide, chosen)
                decisions.append(
                    Decision(
                        slide_id=slide_id,
                        action="pattern_swap",
                        reason=f"пусто по факту: {', '.join(still)}; {why}",
                        after={"pattern_id": chosen.get("pattern_id")},
                    )
                )
    return decisions


# Границы частей предложения: заголовок в одну фразу сокращается по ним.
_CLAUSE = re.compile(r"\s*[,;:—–]\s*| для | в целях | с целью ")


def _fit_text(text: str, slot: JsonDict, canvas: Canvas | None) -> str | None:
    """Укороченный вариант текста, помещающийся в слот, или `None`.

    Сначала первое предложение — оно сохраняет мысль целиком. Если предложение
    одно (а заголовки почти всегда такие), отрезается придаточная часть:
    «Платформа демонстрирует высокую эффективность в оптимизации логистики для
    среднего бизнеса» → «Платформа демонстрирует высокую эффективность».
    Обрезков по знакам здесь нет: не нашлось границы — текст остаётся как был,
    а кегль подберёт лестница ёмкости вёрстки.
    """
    from presentation_designer.design.fit import _fits

    candidates = []
    first = _shorten(text)
    if first:
        candidates.append(first)
    parts = [p for p in _CLAUSE.split(first or text) if p and p.strip()]
    for count in range(len(parts) - 1, 0, -1):
        candidates.append(" ".join(parts[:count]).strip())

    # Отбрасывание хвостовых слов: «Итоги пилота логистической платформы» →
    # «Итоги пилота». Проверка смысла ниже не даст дойти до «Итоги».
    words = (first or text).split()
    candidates.extend(" ".join(words[:count]) for count in range(len(words) - 1, 0, -1))

    from presentation_designer.design.refill import _keeps_meaning

    floor = max(6, int(len(text) * 0.3))
    for candidate in candidates:
        if len(candidate) < floor or not _keeps_meaning(text, candidate):
            continue
        if _fits(slot, candidate, canvas) is True:
            return candidate
    return None


def _block_of(slide: JsonDict, slot_id: str) -> JsonDict | None:
    for block in slide.get("blocks", []):
        if block.get("slot_id") == slot_id and str(block.get("text") or "").strip():
            return block
    return None


# -- дозапрос по фактам ------------------------------------------------------

def ask_slots(
    plan: JsonDict,
    facts: Facts,
    profile: JsonDict,
    story: JsonDict,
    ask: Callable[[str, str], str],
    *,
    limit: int = 24,
    rounds: int = 2,
    package: JsonDict | None = None,
) -> list[tuple[str, str]]:
    """Просит модель дописать ровно те слоты, которые пусты **в файле**.

    Отличие от `refill` — в источнике задания и в материале. Задание берётся
    из фактов сборки, а не из оценки по плану. Материал: если у слайда нет
    своих тезисов, ему передаются ключевые результаты колоды — так слайд-вывод
    получает, что обобщать. Без этого модель честно возвращала пустые строки:
    материала у неё не было, а выдумывать промпт запрещает.
    """
    from presentation_designer.design.refill import (
        _extract_json,
        apply_answer,
        facts_index,
    )

    patterns = {p.get("pattern_id"): p for p in profile.get("patterns", [])}
    markers = {str(m) for m in profile.get("placeholder_markers") or []}
    taken: list[tuple[str, str]] = []

    for attempt in range(max(rounds, 1)):
        tasks = _tasks(plan, facts, patterns, story, limit, tight=attempt > 0, package=package)
        if not tasks:
            break
        log.info(
            "дозапрос по фактам, круг %d: слайдов %d, слотов %d",
            attempt + 1, len(tasks), sum(len(t["empty_slots"]) for t in tasks),
        )
        try:
            raw = ask("", json.dumps({"slides": tasks}, ensure_ascii=False))
            answer = _extract_json(raw)
        except Exception as exc:                # сеть, квота, неразбираемый ответ
            log.warning("дозапрос по фактам не удался: %s", exc)
            break
        replace = {
            (t["slide_id"], slot["slot_id"])
            for t in tasks
            for slot in t.get("overflow_slots") or []
        }
        taken += apply_answer(
            plan, patterns, answer, markers, facts.canvas, facts_index(package), replace
        )

    log.info("дозапрос по фактам: принято слотов %d", len(taken))
    return taken


def _tasks(
    plan: JsonDict,
    facts: Facts,
    patterns: dict[str, JsonDict],
    story: JsonDict,
    limit: int,
    *,
    tight: bool,
    package: JsonDict | None = None,
) -> list[JsonDict]:
    """Задание модели: слоты, пустые **в файле** и всё ещё пустые в плане.

    Второй круг (`tight`) просит заметно короче и говорит об этом прямо.
    Модель отвечает по длине неточно: на слот в 12 знаков она прислала
    «Согласование 1 день» — 19. Замер такой ответ отвергает, слот остаётся
    пустым, и слайд теряет карточку из-за семи лишних знаков. Повторная
    просьба с честной причиной обходится в один вызов и слот возвращает.
    """
    from presentation_designer.design.refill import _ASK_RATIO, _slide_material

    ratio = _ASK_RATIO * 0.7 if tight else _ASK_RATIO
    tasks: list[JsonDict] = []
    budget = limit

    for slide in plan.get("slides", []):
        slide_id = str(slide.get("slide_id") or "")
        pattern = patterns.get(slide.get("pattern_id") or "")
        if pattern is None or budget <= 0:
            continue
        slots = {s.get("slot_id"): s for s in content_slots(pattern)}
        gaps, holes = [], False
        for fact in facts.of_slide(slide_id):
            slot = slots.get(fact.slot_id)
            if (
                fact.kind not in EMPTY_KINDS
                or slot is None
                or slot.get("kind") in NOT_ENRICHABLE
                or _block_of(slide, fact.slot_id) is not None
                or not fact.capacity_chars
            ):
                continue
            holes = holes or fact.kind == "hole"
            gap: JsonDict = {
                "slot_id": fact.slot_id,
                "kind": slot.get("kind"),
                "max_chars": _ask_chars(fact.capacity_chars, ratio),
            }
            # Что стоит рядом: подпись под числом обязана пояснять это число, а
            # не соседнее. Без подсказки под «Январь: 1800» появлялось
            # «+1600 рейсов», а под «Изменение: +1600» — «5 дней».
            neighbour = _nearest_filled(slide, pattern, fact.slot_id)
            if neighbour:
                gap["near"] = neighbour
            # Образец шаблона в этом слоте — готовый пример нужной длины и
            # жанра, сделанный дизайнером. Число знаков модель соблюдает
            # неточно, образец «Заголовок» рядом с числом 12 работает лучше.
            sample = " ".join(str(slot.get("sample_text") or "").split())
            if sample:
                gap["like"] = sample[:40]
            gaps.append(gap)
            budget -= 1
            if budget <= 0:
                break
        # Слоты, где текст не поместился и наехал на соседа: их надо не
        # дописать, а переписать короче. Задача та же по сути — уложиться в
        # рамку, — поэтому идёт одним запросом вместе с пустыми.
        long_slots = []
        for fact in facts.of_slide(slide_id):
            if fact.kind != "overflow":
                continue
            slot = slots.get(fact.slot_id)
            block = _block_of(slide, fact.slot_id)
            if slot is None or block is None:
                continue
            capacity = max_chars(slot, facts.canvas)
            if not capacity:
                continue
            long_slots.append(
                {
                    "slot_id": fact.slot_id,
                    "kind": slot.get("kind"),
                    "max_chars": _ask_chars(capacity, ratio),
                    "text": str(block.get("text") or ""),
                }
            )

        if not gaps and not long_slots:
            continue

        task: JsonDict = {
            "slide_id": slide_id,
            "title": slide.get("title", ""),
            "key_message": slide.get("key_message", ""),
            "filled": [str(b.get("text") or "") for b in slide.get("blocks", []) if b.get("text")],
            "empty_slots": gaps,
        }
        if long_slots:
            task["overflow_slots"] = long_slots
        task.update(_slide_material(story or {}, slide.get("thesis_refs") or []))
        if not task.get("material"):
            task["material"] = deck_material(plan, story, slide_id, facts.titles)
        if package:
            task["facts"] = fact_labels(package)[:12]
        if tight:
            task["retry"] = True
        # Пустому ряду материал раздаётся по слотам сразу: ряд принимается
        # целиком, и «напиши что-нибудь ещё» на четвёртую карточку приводит к
        # тому, что снимается весь ряд вместе с тремя удачными подписями.
        if tight or holes:
            _pair_material(task, slide)
        tasks.append(task)

    return tasks


def fact_labels(package: JsonDict | None) -> list[str]:
    """Короткие подписи из фактов пакета: число и то, что оно измеряет.

    Лучший источник подписи для карточки — не предложение, а сам факт: у него
    уже разобраны величина, единица и метрика. «3400» и «рейсов (в месяц)»
    складываются в «3400 рейсов» — одиннадцать знаков, которые и нужны. Резать
    для этого предложение на куски не приходится, а значит, не появляются
    обрывки вроде «дней до 1 дня».

    Требование ТЗ соблюдается по построению: число берётся из фактов
    исходных материалов, а не из формулировки модели.
    """
    out: list[str] = []
    for fact in (package or {}).get("facts") or []:
        raw = str(fact.get("raw") or "").strip()
        metric = str((fact.get("context") or {}).get("metric") or fact.get("label") or "").strip()
        metric = metric.split("(")[0].strip()
        if not raw:
            continue
        forms = [f"{raw} {metric}", f"{metric} {raw}", metric, raw] if metric else [raw]
        out.extend(f for f in forms if f)
    return out


# Слова, которыми подпись не заканчивается: предлог в конце выдаёт обрубок.
_TAIL_WORDS = frozenset({"в", "на", "с", "до", "за", "от", "по", "и", "или", "для", "к", "у", "о"})


def condense(text: str, slot: JsonDict, canvas: Canvas | None = None) -> str | None:
    """Самая содержательная часть строки, помещающаяся в слот.

    Не обрезание по знакам, а выбор куска по словам: из «120 перевозчиков
    делают 3400 рейсов в месяц» в подпись карточки на двенадцать знаков
    входит «3400 рейсов». Предпочтение отдаётся куску с числом — на слайде
    выводов число говорит больше, чем общее слово, — и при равенстве более
    длинному: он сообщает больше.

    Ничего не сочиняется: это слова самой колоды в исходном порядке.
    """
    from presentation_designer.design.measure import fits

    words = [w for w in str(text or "").split() if w]
    # Кусок берётся с начала строки или после знака препинания: иначе выходит
    # обрывок вроде «дней до 1 дня», формально проходящий все проверки.
    starts = [0] + [i for i in range(1, len(words)) if words[i - 1][-1] in ",;:—–."]
    best: tuple[tuple[int, int, int], str] | None = None
    for start in starts:
        for end in range(len(words), start, -1):
            chunk = words[start:end]
            # Служебные слова по краям выдают обрубок: «на 34%» вместо
            # «3400 рейсов» формально влезало и было выбрано первым замером.
            while chunk and chunk[-1].lower().strip(".,:;") in _TAIL_WORDS:
                chunk = chunk[:-1]
            while chunk and chunk[0].lower().strip(".,:;") in _TAIL_WORDS:
                chunk = chunk[1:]
            candidate = " ".join(chunk).strip(" ,:;—–-")
            if len(candidate) < 4:
                continue
            if fits(slot, candidate, canvas) is not True:
                continue
            has_digit = any(c.isdigit() for c in candidate)
            has_word = any(len(w) > 2 and w.isalpha() for w in candidate.split())
            # Число со словом — лучшая подпись: «3400 рейсов» говорит больше,
            # чем «3400» и чем «рейсов» по отдельности.
            rank = (int(has_digit and has_word), int(has_digit), len(candidate))
            if best is None or rank > best[0]:
                best = (rank, candidate)
    return best[1] if best else None


def complete_rows(
    plan: JsonDict,
    facts: Facts,
    profile: JsonDict,
    story: JsonDict,
    package: JsonDict | None = None,
) -> list[tuple[str, str]]:
    """Дозаполняет ряд, которому не хватило одной-двух подписей.

    Модель отвечает по длине неточно, и ряд из четырёх карточек не раз
    оставался с тремя: «120 перевозчиков» — шестнадцать знаков там, где
    помещается двенадцать. Отдавать из-за этого весь ряд обратно в пустоту
    расточительно: материал есть, он уже проверен по источникам, и сократить
    его до нужной длины можно без модели.
    """
    from presentation_designer.design.refill import _restates

    patterns = {p.get("pattern_id"): p for p in profile.get("patterns", [])}
    added: list[tuple[str, str]] = []
    for slide in plan.get("slides", []):
        slide_id = str(slide.get("slide_id") or "")
        pattern = patterns.get(slide.get("pattern_id") or "")
        if pattern is None:
            continue
        # Ряд, который уже начат — планом или дозапросом, — дозаполняется до
        # конца. Ряд, где нет ни одной подписи, не трогаем: придумать весь
        # смысл слайда из обрывков нельзя, это работа модели.
        filled = {
            str(b.get("slot_id"))
            for b in slide.get("blocks", [])
            if str(b.get("text") or "").strip()
        }
        started_rows = [r for r in rows(pattern) if r & filled and not r <= filled]
        wanted = {sid for row in started_rows for sid in row - filled}
        empty = [
            f
            for f in facts.of_slide(slide_id)
            if f.kind == "hole" and f.slot_id in wanted
        ]
        if not empty:
            continue

        slots = {s.get("slot_id"): s for s in content_slots(pattern)}
        seen = {_norm(str(b.get("text") or "")) for b in slide.get("blocks", []) if b.get("text")}
        # Готовые подписи из фактов пробуются целиком и только потом режутся:
        # «5 дней Срок» получилось именно из сокращения готовой формы, где от
        # метрики «Срок согласования рейса» осталось одно слово.
        labels = fact_labels(package)
        material = deck_material(plan, story, slide_id, facts.titles)
        spare = [m for m in labels + material if not _restates(_norm(m), "", seen)]
        for fact in empty:
            slot = slots.get(fact.slot_id)
            if slot is None:
                continue
            for source in list(spare):
                short = source if _fits_as_is(source, slot, facts.canvas) else None
                if short is None and source not in labels:
                    short = condense(source, slot, facts.canvas)
                if not short or _restates(_norm(short), slide.get("title", ""), seen):
                    continue
                if _same_number(short, seen):
                    continue
                slide.setdefault("blocks", []).append(
                    {
                        "slot_id": fact.slot_id,
                        "kind": slot.get("kind"),
                        "text": short,
                        "source": "design.condense",
                    }
                )
                seen.add(_norm(short))
                spare.remove(source)
                added.append((slide_id, fact.slot_id))
                log.info("ряд %s/%s дозаполнен без модели: %r", slide_id, fact.slot_id, short)
                break
    return added


def _fits_as_is(text: str, slot: JsonDict, canvas: Canvas | None) -> bool:
    """Помещается ли строка целиком — тогда резать её незачем."""
    from presentation_designer.design.measure import fits

    return fits(slot, text, canvas) is True


_DIGITS = re.compile(r"\d+")


def _same_number(text: str, seen: set[str]) -> bool:
    """Повторяет ли подпись число, уже сказанное на слайде.

    Для короткой подписи число и есть содержание: «Простой −34%» и «34%
    Простой» — один результат, сказанный дважды. Сравнение по значимым словам
    этого не ловит: общее слово всего одно из двух, порог не достигнут.
    """
    numbers = set(_DIGITS.findall(text))
    if not numbers:
        return False
    return any(numbers & set(_DIGITS.findall(other)) for other in seen)


def drop_partial_rows(plan: JsonDict, facts: Facts, profile: JsonDict | None = None) -> int:
    """Ряд одинаковых мест заполняется целиком или не заполняется вовсе.

    Четыре карточки, где заполнены две, выглядят хуже четырёх пустых: пустая
    пара читается как недоделка, а не как замысел. Проверено снимком дважды —
    на ряду подписей и на ряду описаний под заголовками карточек.

    Снимается только то, что дописали мы (`source: design.*`). Содержание
    планировщика не трогается никогда: неполный ряд — меньшая беда, чем
    потерянная мысль.
    """
    patterns = {p.get("pattern_id"): p for p in (profile or {}).get("patterns", [])}
    dropped = 0
    for slide in plan.get("slides", []):
        slide_id = str(slide.get("slide_id") or "")
        ours = {
            str(b.get("slot_id"))
            for b in slide.get("blocks", [])
            if str(b.get("source") or "").startswith("design.")
        }
        if not ours:
            continue
        pattern = patterns.get(slide.get("pattern_id") or "")
        groups = rows(pattern) if pattern else []
        if not groups:                  # без профиля — по виду слота, как раньше
            holes: dict[str, set[str]] = {}
            for fact in facts.of_slide(slide_id):
                if fact.kind == "hole":
                    holes.setdefault(fact.slot_kind, set()).add(fact.slot_id)
            groups = list(holes.values())
        filled = {
            str(b.get("slot_id"))
            for b in slide.get("blocks", [])
            if str(b.get("text") or "").strip()
        }
        for group in groups:
            done = group & ours
            if not done or group <= filled:
                continue                # ряд не наш или заполнен целиком
            slide["blocks"] = [b for b in slide.get("blocks", []) if b.get("slot_id") not in done]
            filled -= done
            dropped += 1
            log.info(
                "снят неполный ряд %s: заполнено %d из %d (%s)",
                slide_id, len(done), len(group), ", ".join(sorted(done)),
            )
    return dropped


# Ниже этой длины запас смысла не имеет: на подпись карточки в 12 знаков
# просьба «не длиннее 10» отсекает верные ответы вроде «Простой −34%», а
# замер всё равно проверяет результат по настоящей рамке.
_ASK_FLOOR_CHARS = 12


def _ask_chars(capacity: int, ratio: float) -> int:
    """Сколько знаков просить у модели: с запасом, но не до бессмыслицы."""
    return max(min(int(capacity * ratio), capacity), min(capacity, _ASK_FLOOR_CHARS))


def _nearest_filled(slide: JsonDict, pattern: JsonDict, slot_id: str) -> str | None:
    """Текст ближайшего заполненного слота — тот, к которому подпись относится."""
    slots = {str(s.get("slot_id")): s for s in pattern.get("slots", [])}
    target = slots.get(slot_id)
    if target is None:
        return None
    box = target.get("bbox") or {}
    cx = float(box.get("x") or 0) + float(box.get("width") or 0) / 2
    cy = float(box.get("y") or 0) + float(box.get("height") or 0) / 2
    best: tuple[float, str] | None = None
    for block in slide.get("blocks", []):
        text = str(block.get("text") or "").strip()
        other = slots.get(str(block.get("slot_id")))
        if not text or other is None or block.get("kind") == "title":
            continue
        their = other.get("bbox") or {}
        ox = float(their.get("x") or 0) + float(their.get("width") or 0) / 2
        oy = float(their.get("y") or 0) + float(their.get("height") or 0) / 2
        distance = ((cx - ox) ** 2 + (cy - oy) ** 2) ** 0.5
        if distance > 0.25:               # через полслайда сосед уже не сосед
            continue
        if best is None or distance < best[0]:
            best = (distance, text)
    return best[1] if best else None


def _pair_material(task: JsonDict, slide: JsonDict) -> None:
    """Каждому пустому слоту — своя исходная формулировка на сокращение.

    Во втором круге просить «напиши ещё что-нибудь короче» бесполезно: модель
    отвечала обрывками уже сказанного («-34%», «+18%»). Задача формулируется
    иначе — вот строка, сократи её до N знаков. Сокращение готовой строки
    модель выполняет надёжно, и проверка по источникам сохраняется: строка
    взята из этой же колоды.
    """
    from presentation_designer.design.refill import _restates

    seen = {_norm(str(b.get("text") or "")) for b in slide.get("blocks", []) if b.get("text")}
    # Сравнение по значимым словам, а не по строке: «Простой −34%» в соседнем
    # слоте и строка «Простой снижен на 34%» — один и тот же результат. Раздав
    # его дважды, мы получали от модели «Простой» на второй слот и сами же
    # отвергали ответ как пересказ.
    spare = [m for m in task.get("material") or [] if not _restates(_norm(m), "", seen)]
    # Короткие строки ближе к подписи, длинные — к пересказу всей колоды.
    # Главная мысль смыслового плана в 158 знаков как источник для слота в 12
    # знаков бесполезна: сокращать её не до чего.
    spare.sort(key=len)
    for slot, source in zip(task.get("empty_slots") or [], spare, strict=False):
        limit = int(slot.get("max_chars") or 0)
        if limit and len(source) > limit * 6:
            continue
        slot["from"] = source


def deck_material(
    plan: JsonDict, story: JsonDict, slide_id: str, titles: dict[str, str] | None = None
) -> list[str]:
    """Материал для слайда, у которого своего нет: результаты самой колоды.

    Слайд-вывод в конце колоды не несёт собственных тезисов — он обобщает
    сказанное. Брать нечего только на первый взгляд: сказанное лежит рядом, в
    заголовках содержательных слайдов и в главной мысли смыслового плана.
    Это не выдумывание — это те же формулировки, уже проверенные по источникам.
    """
    out: list[str] = []
    takeaway = str((story or {}).get("key_takeaway") or "").strip()
    if takeaway:
        out.append(takeaway)
    seen = {_norm(takeaway)}
    for slide in plan.get("slides", []):
        if str(slide.get("slide_id") or "") == slide_id:
            continue
        if str(slide.get("role") or "") in ("title", "agenda", "thanks", "section_divider"):
            continue
        title = str((titles or {}).get(str(slide.get("slide_id") or "")) or "").strip()
        if not title:
            title = str(slide.get("title") or "").strip()
        if not title:
            for block in slide.get("blocks", []):
                if block.get("kind") == "title":
                    title = str(block.get("text") or "").strip()
                    break
        if title and _norm(title) not in seen:
            out.append(title)
            seen.add(_norm(title))
    return out
