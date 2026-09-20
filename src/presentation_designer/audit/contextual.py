"""Контекстные проверки: 11 вопросов Приложения 1 моделью по картинке слайда.

Детерминированная часть считает файл: координаты, цвета, ссылки на макет. Эти проверки — про
смысл, и судить о нём может только модель, поэтому слой устроен иначе:

1. **Что спрашивать, решает код.** Вопрос про таблицу не задаётся слайду без таблицы, вопрос
   про картинки — слайду без картинок: такие проверки честно помечаются `not_applicable`, а не
   «пройдены». Так покрытие показывает реальную картину, а модель не тратит время на пустое.
2. **Один вызов на слайд.** Вопросы уровня слайда идут вместе с его картинкой и текстом, а два
   вопроса уровня колоды (язык и связность соседей) — одним отдельным вызовом по тексту всей
   колоды, потому что по одному слайду на них ответить нельзя.
3. **Неуверенность — не «пройдено».** Ответ `unsure`, отсутствующий ответ и сбой вызова дают
   `not_checked` с причиной; находка появляется только из явного «нет».

Факты пакета уходят в запрос списком: вопрос 4 («все ли цифры со слайда есть в материалах»)
без них превращается в угадывание.
"""

from __future__ import annotations

import asyncio
import collections
import logging
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.audit.deterministic import Issue
from presentation_designer.audit.registry import BY_ID, CONTENT_QUESTIONS

JsonDict = dict[str, Any]

log = logging.getLogger(__name__)

# Сколько текста уходит в запрос. Слайд целиком не нужен — нужен смысл, а длинные запросы
# к модели с картинкой стоят времени, которого в бюджете задания мало.
SLIDE_TEXT_LIMIT = 700
DECK_SLIDE_TEXT_LIMIT = 220
FACTS_LIMIT = 40

QUESTIONS: dict[int, tuple[str, str]] = {n: (cid, text) for n, cid, text in CONTENT_QUESTIONS}
SLIDE_QUESTION_IDS: tuple[int, ...] = tuple(
    n for n, cid, _ in CONTENT_QUESTIONS if BY_ID[cid].scope == "slide"
)
DECK_QUESTION_IDS: tuple[int, ...] = tuple(
    n for n, cid, _ in CONTENT_QUESTIONS if BY_ID[cid].scope == "deck"
)

ANSWER_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "q": {"type": "integer"},
                    "answer": {"enum": ["yes", "no", "unsure"]},
                    "confidence": {"type": "number"},
                    "why": {"type": "string"},
                    "slide": {"type": "integer"},
                },
                "required": ["q", "answer", "confidence", "why"],
            },
        }
    },
    "required": ["items"],
}


@dataclass
class ContextualResult:
    """Что получилось спросить и что ответила модель."""

    answers: list[JsonDict] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    # (check_id, slide_index) → (исход, причина). Записываются только неспрошенные и
    # непроверенные: пройденные и найденные выводятся из ответов и находок.
    outcomes: dict[tuple[str, int | None], tuple[str, str]] = field(default_factory=dict)
    metrics: JsonDict = field(default_factory=dict)
    missing_inputs: list[str] = field(default_factory=list)
    # Слой отработал: запросы ушли к модели. Отличается от `asked` тем, что остаётся True,
    # даже когда все вызовы упали, — тогда в отчёте у областей стоит причина, а не общий пропуск.
    ran: bool = False
    asked: int = 0
    errors: list[str] = field(default_factory=list)


def slide_title(slide: JsonDict) -> str:
    title = str(slide.get("title") or "").strip()
    if title:
        return title
    for obj in slide.get("objects") or []:
        if obj.get("slot_kind") == "title" or obj.get("slot_id") == "title":
            return _plain(obj)[:160]
    return ""


def _plain(obj: JsonDict) -> str:
    text = obj.get("text") or {}
    return str(text.get("plain") or "").strip()


def slide_text(slide: JsonDict, limit: int = SLIDE_TEXT_LIMIT) -> str:
    """Текст слайда без постоянных элементов шаблона: колонтитул и номер страницы к смыслу
    слайда отношения не имеют, а место в запросе занимают."""
    parts: list[str] = []
    for obj in slide.get("objects") or []:
        if obj.get("role") in {"fixed", "decoration", "background"}:
            continue
        text = _plain(obj)
        if text:
            parts.append(" ".join(text.split()))
    joined = " | ".join(parts)
    return joined[:limit] + ("…" if len(joined) > limit else "")


def _kinds(slide: JsonDict) -> collections.Counter[str]:
    return collections.Counter(
        str(obj.get("kind") or "other")
        for obj in slide.get("objects") or []
        if obj.get("role") not in {"fixed", "decoration", "background"}
    )


def not_asked(question_id: int, slide: JsonDict, *, has_image: bool) -> tuple[str, str] | None:
    """Почему вопрос не задаётся этому слайду. None — задаётся."""
    kinds = _kinds(slide)
    if question_id == 6:
        if not (kinds["picture"] + kinds["chart"] + kinds["diagram"]):
            return ("not_applicable", "на слайде нет картинок и иконок")
        if not has_image:
            return ("not_checked", "нет картинки слайда: смотреть не на что")
    if question_id == 10 and not (kinds["table"] + kinds["chart"]):
        return ("not_applicable", "на слайде нет таблицы и диаграммы")
    return None


def _facts_digest(package: JsonDict, limit: int = FACTS_LIMIT) -> str:
    lines: list[str] = []
    for fact in (package.get("facts") or [])[:limit]:
        label = str(fact.get("label") or "").strip()
        raw = str(fact.get("raw") or fact.get("value") or "").strip()
        period = str((fact.get("context") or {}).get("period") or "").strip()
        line = f"{raw} — {label}" if label else raw
        lines.append(f"{line} ({period})" if period else line)
    return "\n".join(f"- {line}" for line in lines if line.strip(" -"))


def _questions_block(ids: tuple[int, ...] | list[int]) -> str:
    return "\n".join(f"{n}. {QUESTIONS[n][1]}" for n in ids)


def _slide_request(
    skill: Any,
    slide: JsonDict,
    ids: list[int],
    *,
    image: bytes | None,
    total: int,
    facts: str,
    takeaway: str,
    deadline: Any,
) -> Any:
    from presentation_designer.llm.types import Image, Message

    index = int(slide.get("index", 0))
    kinds = ", ".join(f"{k}×{n}" for k, n in sorted(_kinds(slide).items()))
    lines = [
        f"Слайд {index + 1} из {total}.",
        f"Заголовок: {slide_title(slide) or '(нет)'}",
        f"Текст слайда: {slide_text(slide) or '(пусто)'}",
        f"Объекты: {kinds or '(нет)'}",
    ]
    if takeaway:
        lines.append(f"Главная мысль всей презентации: {takeaway}")
    if facts:
        lines.append("Факты из исходных материалов:\n" + facts)
    if image is None:
        lines.append("Картинки слайда нет: суди по тексту и составу объектов.")
    lines.append("Вопросы:\n" + _questions_block(ids))
    req = skill.request(
        "audit.content_questions", "\n".join(lines), schema=ANSWER_SCHEMA, stage="audit"
    )
    if image is not None:
        req.messages[1] = Message("user", req.messages[1].text, (Image(image, "image/png"),))
    req.schema_name = "content_questions"
    req.deadline = deadline
    req.slide_ids = [str(slide.get("slide_id") or "")]
    return req


def _deck_request(
    skill: Any, slides: list[JsonDict], ids: list[int], *, language: str, deadline: Any
) -> Any:
    lines = [f"Презентация из {len(slides)} слайдов, тексты по порядку:"]
    for slide in slides:
        index = int(slide.get("index", 0))
        title = slide_title(slide) or "(без заголовка)"
        lines.append(f"{index + 1}. {title} — {slide_text(slide, DECK_SLIDE_TEXT_LIMIT)}")
    if language:
        lines.append(f"Язык, на котором презентацию просили сделать: {language}.")
    lines.append("Вопросы:\n" + _questions_block(ids))
    lines.append(
        "Для вопроса 11 укажи в поле slide номер слайда, который выпадает из логики "
        "соседей; если всё связно, поле не заполняй."
    )
    req = skill.request(
        "audit.content_questions", "\n".join(lines), schema=ANSWER_SCHEMA, stage="audit"
    )
    req.schema_name = "content_questions"
    req.deadline = deadline
    return req


def _answer_entry(
    slide: JsonDict | None,
    question_id: int,
    item: JsonDict,
    *,
    model: JsonDict | None,
    prompt: tuple[str, str] | None,
    inputs: list[str],
) -> JsonDict:
    question = QUESTIONS[question_id][1]
    answer = str(item.get("answer") or "unsure")
    outcome = {"yes": "passed", "no": "failed"}.get(answer, "not_checked")
    entry: JsonDict = {
        "slide_id": str((slide or {}).get("slide_id") or "deck"),
        "question_id": question_id,
        "question": question,
        "answer": answer,
        "outcome": outcome,
        "inputs": inputs,
    }
    if slide is not None:
        entry["slide_index"] = int(slide.get("index", 0))
    try:
        entry["confidence"] = round(float(item.get("confidence", 0)), 2)
    except (TypeError, ValueError):
        entry["confidence"] = 0.0
    why = str(item.get("why") or "").strip()
    if why:
        entry["explanation"] = why[:400]
    if model:
        entry["model"] = model
    if prompt:
        entry["prompt"] = {"name": prompt[0], "version": prompt[1]}
    return entry


def _issue_from(answer: JsonDict, slide: JsonDict | None) -> Issue:
    check_id, question = QUESTIONS[int(answer["question_id"])]
    message = str(answer.get("explanation") or "").strip() or f"Ответ «нет» на вопрос: {question}"
    return Issue(
        check_id=check_id,
        message=message[:300],
        slide_id=str((slide or {}).get("slide_id") or "") or None,
        slide_index=int(slide.get("index", 0)) if slide is not None else None,
        evidence={
            "question": question,
            "answer": "no",
            "confidence": answer.get("confidence", 0.0),
        },
    )


def run_contextual_checks(
    deck: JsonDict,
    package: JsonDict,
    story: JsonDict,
    *,
    images: dict[int, bytes],
    client: Any,
    skill: Any,
    concurrency: int = 4,
    deadline_s: float = 120.0,
) -> ContextualResult:
    """Спрашивает модель по каждому слайду и один раз по всей колоде. Сбои вызовов не рушат
    аудит: их области остаются `not_checked` с причиной, а детерминированная часть уже есть."""
    from presentation_designer.llm.types import Deadline, LlmError

    result = ContextualResult()
    slides = list(deck.get("slides") or [])
    if not slides:
        return result
    if client is None or skill is None:
        result.missing_inputs.append("vlm_unavailable")
        return result

    deadline = Deadline.after(deadline_s)
    facts = _facts_digest(package or {})
    takeaway = str((story or {}).get("key_takeaway") or "").strip()[:200]
    language = str((story or {}).get("language") or "").strip()
    if not images:
        result.missing_inputs.append("slide_render")

    plan: list[tuple[JsonDict | None, list[int], Any]] = []
    for slide in slides:
        index = int(slide.get("index", 0))
        image = images.get(index)
        ids: list[int] = []
        for question_id in SLIDE_QUESTION_IDS:
            skip = not_asked(question_id, slide, has_image=image is not None)
            if skip is not None:
                result.outcomes[(QUESTIONS[question_id][0], index)] = skip
                continue
            ids.append(question_id)
        if not ids:
            continue
        plan.append(
            (
                slide,
                ids,
                _slide_request(
                    skill,
                    slide,
                    ids,
                    image=image,
                    total=len(slides),
                    facts=facts,
                    takeaway=takeaway,
                    deadline=deadline,
                ),
            )
        )

    deck_ids = list(DECK_QUESTION_IDS)
    if len(slides) < 2:
        reason = "в колоде один слайд: сравнивать не с чем"
        result.outcomes[(QUESTIONS[11][0], None)] = ("not_applicable", reason)
        deck_ids = [n for n in deck_ids if n != 11]
    if deck_ids:
        request = _deck_request(skill, slides, deck_ids, language=language, deadline=deadline)
        plan.append((None, deck_ids, request))

    if not plan:
        return result

    result.ran = True
    gate = asyncio.Semaphore(max(1, concurrency))

    async def ask(request: Any) -> Any:
        async with gate:
            return await client.complete(request)

    async def run_all() -> list[Any]:
        return await asyncio.gather(*(ask(req) for _, _, req in plan), return_exceptions=True)

    outcomes = asyncio.run(run_all())

    model: JsonDict | None = None
    try:
        from presentation_designer.llm import skill_model_ref

        model = skill_model_ref(client, skill)
    except Exception:
        log.debug("модель аудита не определена", exc_info=True)

    calls = 0
    prompt_tokens = 0
    completion_tokens = 0
    for (slide, ids, request), outcome in zip(plan, outcomes, strict=True):
        area: int | None = int(slide.get("index", 0)) if slide is not None else None
        if isinstance(outcome, BaseException):
            reason = f"{type(outcome).__name__}: {outcome}"[:200]
            result.errors.append(reason)
            if not isinstance(outcome, LlmError):
                log.exception("контекстный аудит: неожиданная ошибка", exc_info=outcome)
            for question_id in ids:
                result.outcomes[(QUESTIONS[question_id][0], area)] = (
                    "not_checked",
                    f"вызов модели не выполнен ({reason})",
                )
            continue
        calls += 1
        prompt_tokens += outcome.usage.prompt_tokens
        completion_tokens += outcome.usage.completion_tokens
        items = outcome.parsed.get("items", []) if isinstance(outcome.parsed, dict) else []
        by_question: dict[int, JsonDict] = {}
        for item in items:
            try:
                by_question[int(item.get("q", 0))] = item
            except (TypeError, ValueError):
                continue
        for question_id in ids:
            check_id = QUESTIONS[question_id][0]
            item = by_question.get(question_id)
            if item is None:
                result.outcomes[(check_id, area)] = ("not_checked", "модель не ответила на вопрос")
                continue
            target = slide
            if slide is None and question_id == 11 and item.get("slide") is not None:
                target = _slide_by_number(slides, item.get("slide"))
            answer = _answer_entry(
                target if slide is None else slide,
                question_id,
                item,
                model=model,
                prompt=request.prompt,
                inputs=_inputs_of(check_id),
            )
            if slide is None:
                # Вопрос уровня колоды: область — вся колода, даже когда модель показала слайд.
                answer["slide_id"] = str((target or {}).get("slide_id") or "deck")
                if target is not None:
                    answer["slide_index"] = int(target.get("index", 0))
                else:
                    answer.pop("slide_index", None)
            result.answers.append(answer)
            result.asked += 1
            if answer["outcome"] == "failed":
                result.issues.append(_issue_from(answer, target if slide is None else slide))
            elif answer["outcome"] == "not_checked":
                result.outcomes[(check_id, area)] = (
                    "not_checked",
                    "модель не уверена в ответе",
                )

    if result.errors:
        result.missing_inputs.append("vlm_error")
    result.metrics = {
        "llm_calls": calls,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    return result


def _slide_by_number(slides: list[JsonDict], number: Any) -> JsonDict | None:
    try:
        index = int(number) - 1
    except (TypeError, ValueError):
        return None
    for slide in slides:
        if int(slide.get("index", 0)) == index:
            return slide
    return None


def _inputs_of(check_id: str) -> list[str]:
    """Что модель получила кроме картинки — то же, что заявлено в реестре для AUDIT.md."""
    from presentation_designer.audit.report import inputs_for

    check = BY_ID.get(check_id)
    return inputs_for(check) if check else ["render"]


__all__ = [
    "ANSWER_SCHEMA",
    "ContextualResult",
    "run_contextual_checks",
    "slide_text",
    "slide_title",
]
