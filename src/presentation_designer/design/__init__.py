"""Слой `design`: композиция слайда приводится в соответствие с объёмом содержания.

Вход — план слайдов от модели, профиль шаблона и смысловой план. Выход — тот же
план, в котором ни один слайд не остался полупустым, и отчёт о каждом решении.

Слой намеренно детерминированный и не обращается к модели. Модель уже получила
вместимость слотов в `fit` каждого блока и всё равно оставила слоты пустыми;
надеяться, что следующая редакция промпта это исправит, — значит не иметь
гарантии. Здесь гарантия есть, и она проверяется тестом.

Подробности и связь с ТЗ: `README.md` рядом.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from presentation_designer.design import feedback
from presentation_designer.design.fit import (
    Decision,
    Report,
    choose_pattern,
    enrich,
    fill_ratio,
    remap_blocks,
)
from presentation_designer.design.guard import guard
from presentation_designer.design.measure import Canvas, canvas_of
from presentation_designer.design.refill import refill
from presentation_designer.design.rules import Thresholds, thresholds_for

__all__ = [
    "Canvas",
    "Decision",
    "Report",
    "Thresholds",
    "apply",
    "canvas_of",
    "feedback",
    "fill_ratio",
    "polish",
    "refill",
    "thresholds_for",
]

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]


def apply(
    plan: JsonDict,
    profile: JsonDict,
    story: JsonDict,
    *,
    variant: str = "balanced",
    config: JsonDict | None = None,
    ask: Any = None,
    refill_limit: int = 24,
    package: JsonDict | None = None,
) -> tuple[JsonDict, Report]:
    """Приводит план к нормам плотности варианта.

    Порядок проходов важен и поэтому задан здесь, а не местом вызова:

    1. застава убирает брак — образцы шаблона, повторы, переполнение;
    2. дозапрос заполняет освободившиеся слоты (если дан `ask`);
    3. подгонка композиции и добор из истории закрывают остаток.

    Дозапрос обязан идти ПОСЛЕ заставы: пока слот занят словом «Заголовок»,
    он считается заполненным, и пустоты не видно. В первой версии вызов стоял
    до заставы, и дозапрос не находил ни одного пустого слота.

    План не меняется на месте: возвращается копия, поэтому исходный документ
    остаётся пригодным для сравнения и для отката.
    """
    result = copy.deepcopy(plan)
    report = Report()
    limits = thresholds_for(variant, config)
    # Замена композиции после плана выключается тем же флагом, что и в правке по фактам:
    # подбор по объёму уже сделал планировщик, а перенос блоков по видам слотов терял
    # разметку карточек (пункт уходил в заголовок карточки, текст под ним пустел).
    swap_allowed = bool(
        (((config or {}).get("design") or {}).get("feedback") or {}).get("pattern_swap", True)
    )
    # Холст шаблона, а не «обычный» 16:9: доли рамок не во что переводить без
    # размера слайда, а ошибка в нём — это ошибка каждого замера слоя.
    canvas = canvas_of(profile)
    patterns = {p.get("pattern_id"): p for p in profile.get("patterns", [])}
    markers = {str(m) for m in profile.get("placeholder_markers") or []}

    # 1. Застава — по всей колоде, до всего остального.
    for slide in result.get("slides", []):
        pattern = patterns.get(slide.get("pattern_id") or "")
        if pattern is not None:
            for decision in guard(slide, pattern, markers, canvas):
                report.add(decision)

    # 2. Дозапрос на освободившиеся слоты — одним обращением к модели.
    if ask is None:
        log.info("слой design: дозапрос выключен (модель не передана)")
    else:
        taken = refill(
            result,
            profile,
            ask,
            prompt="",
            story=story,
            limit=refill_limit,
            canvas=canvas,
            package=package,
        )
        log.info("слой design: дозапрос заполнил слотов %d", len(taken))
        if taken:
            report.add(
                Decision(
                    slide_id="колода",
                    action="refill",
                    reason=f"модель заполнила пустых слотов: {len(taken)}",
                    after={"slots": [list(t) for t in taken]},
                )
            )

    # 3. Композиция и добор из истории.
    for slide in result.get("slides", []):
        pattern = patterns.get(slide.get("pattern_id") or "")
        if pattern is None:
            report.add(
                Decision(
                    slide_id=slide.get("slide_id", "?"),
                    action="keep",
                    reason="паттерн не найден в профиле шаблона",
                )
            )
            continue

        before = fill_ratio(slide, pattern)

        # 1. Композиция под объём — только если слайд заметно полупустой.
        #
        # Замер по площади слотов обманчив там, где паттерн скупой: у
        # разделителя заголовок занимает почти всю текстовую площадь, и слайд
        # с одним заголовком показывает 84% при пустом слоте подписи. Поэтому
        # площадь решает лишь вопрос о подмене паттерна, а добор идёт всегда,
        # когда есть пустой слот и есть чем его заполнить.
        chosen, why = (None, "заполненность в норме")
        if swap_allowed and limits.underfilled(before):
            chosen, why = choose_pattern(slide, patterns, limits)
        if chosen is not None:
            remap_blocks(slide, chosen)
            pattern = chosen
            report.add(
                Decision(
                    slide_id=slide.get("slide_id", "?"),
                    action="pattern_swap",
                    reason=why,
                    before={"pattern_id": plan_pattern(plan, slide), "fill": round(before, 3)},
                    after={"pattern_id": chosen.get("pattern_id")},
                )
            )

        # 2. Остаток слотов — из смыслового плана, без обращения к модели.
        added = enrich(slide, pattern, story, canvas)
        after = fill_ratio(slide, pattern)
        if added:
            report.add(
                Decision(
                    slide_id=slide.get("slide_id", "?"),
                    action="enrich",
                    reason=f"добрано слотов: {len(added)}",
                    before={"fill": round(before, 3)},
                    after={"slots": added, "fill": round(after, 3)},
                )
            )
        elif chosen is None:
            report.add(
                Decision(
                    slide_id=slide.get("slide_id", "?"),
                    action="keep",
                    reason=f"заполненность {before:.0%}: {why}, добирать нечего",
                    before={"fill": round(before, 3)},
                )
            )

    log.info("слой design: %s", report.summary())
    return result, report


def _restore_locked(candidate: JsonDict, plan: JsonDict, locked: set[str]) -> None:
    """Возвращает запертые слайды из исходного плана: их полировка не касается."""
    original = {str(s.get("slide_id")): s for s in plan.get("slides") or []}
    slides = candidate.get("slides") or []
    for i, slide in enumerate(slides):
        sid = str(slide.get("slide_id"))
        if sid in locked and sid in original:
            slides[i] = copy.deepcopy(original[sid])


def plan_pattern(plan: JsonDict, slide: JsonDict) -> str | None:
    """Исходный паттерн слайда — для отчёта после подмены."""
    slide_id = slide.get("slide_id")
    for original in plan.get("slides", []):
        if original.get("slide_id") == slide_id:
            pattern = original.get("pattern_id")
            return str(pattern) if pattern is not None else None
    return None


def polish(
    plan: JsonDict,
    profile: JsonDict,
    story: JsonDict,
    *,
    compose: Any,
    variant: str = "balanced",
    config: JsonDict | None = None,
    ask: Any = None,
    package: JsonDict | None = None,
    locked_slide_ids: Any = (),
) -> tuple[JsonDict, JsonDict]:
    """Правка плана по фактам вёрстки: сборка → факты → правка → проверка.

    `locked_slide_ids` — слайды с ручными правками редактора (этап 22): их факты не
    читаются, а сами слайды возвращаются из исходного плана до контрольной сборки, чтобы
    полировка не переписала то, что пользователь поправил руками.

    `compose` — способ собрать колоду по плану, `plan -> ComposedDeck`. Он
    передаётся снаружи, поэтому слой не зависит от вёрстки и проверяется без
    неё: в тестах это словарь, в конвейере — `compose_deck`.

    Возвращается план и отчёт. Правка принимается, **только если счёт дефектов
    уменьшился**: сборка после правки считается заново той же меркой. Иначе
    возвращается исходный план — попытка подгонять вёрстку без общей проверки
    уже приводила к тому, что одних дефектов становилось меньше, а всего
    больше.
    """
    locked = {str(s) for s in locked_slide_ids}
    draft = compose(plan)
    before = feedback.read(draft, plan, profile)
    if locked:
        before = before.without(locked)
    report: JsonDict = {"before": before.summary(), "decisions": [], "verdict": "нет дефектов"}
    if locked:
        report["locked_slide_ids"] = sorted(locked)
    if not before:
        log.info("слой design: вёрстка без дефектов, правка не нужна")
        return plan, report

    # Порядок проходов: сначала то, что не требует модели, затем дозапрос, и
    # только после — правила о ряде целиком. Ряд нельзя судить, пока он ещё
    # заполняется.
    candidate = copy.deepcopy(plan)
    limits = ((config or {}).get("design") or {}).get("feedback") or {}
    decisions = feedback.repair(
        candidate,
        before,
        profile,
        story,
        limits=thresholds_for(variant, config) if limits.get("pattern_swap") else None,
    )
    taken = (
        feedback.ask_slots(
            candidate,
            before,
            profile,
            story,
            ask,
            package=package,
            limit=int(limits.get("max_slots", 24)),
            rounds=int(limits.get("rounds", 2)),
        )
        if ask is not None
        else []
    )
    taken += feedback.complete_rows(candidate, before, profile, story, package)
    dropped = feedback.drop_partial_rows(candidate, before, profile)
    report["decisions"] = [d.to_dict() for d in decisions]
    report["refilled"] = [list(t) for t in taken]
    report["dropped_rows"] = dropped
    if not decisions and not taken and not dropped:
        report["verdict"] = "чинить нечем"
        log.info("слой design: дефекты есть (%s), но починить нечем", before.summary())
        return plan, report

    if locked:
        _restore_locked(candidate, plan, locked)
    after = feedback.read(compose(candidate), candidate, profile)
    if locked:
        after = after.without(locked)
    report["after"] = after.summary()
    if after.score() < before.score():
        report["verdict"] = "правка принята"
        log.info("слой design: правка по фактам %s → %s", before.summary(), after.summary())
        return candidate, report
    report["verdict"] = "правка отклонена: лучше не стало"
    log.info("слой design: правка отклонена, %s → %s", before.summary(), after.summary())
    return plan, report
