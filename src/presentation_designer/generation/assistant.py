"""Разговор о проекте: ассистент отвечает на вопросы и переспрашивает, когда чего-то не хватает.

До этого чат отвечал только заготовленными фразами: «не нашёл в сообщении ничего про
презентацию». На вопрос «а сколько будет слайдов?» или «что ты умеешь?» ответа не было. Здесь
сообщение уходит модели вместе с коротким состоянием проекта — что загружено, что собрано, что
нашёл аудит, — и она отвечает двумя-тремя фразами и предлагает варианты следующего шага.

Без модели слой не молчит: тот же ответ собирается правилами по состоянию проекта. Вопросы
пользователя правила не понимают, поэтому отвечают честно — что есть и чего не хватает.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.generation.snapshot import outline_lines, slide_lines
from presentation_designer.shared.text import plural

JsonDict = dict[str, Any]

log = logging.getLogger(__name__)

REPLY_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    },
    "required": ["reply"],
}

# Длиннее модель писать не должна: это чат, а не справка.
MAX_REPLY = 400
MAX_OPTIONS = 3


@dataclass
class ProjectState:
    """Что ассистент знает о проекте. Ровно то, что нужно, чтобы ответить по делу."""

    template: str | None = None
    template_ready: bool = False
    materials: list[str] = field(default_factory=list)
    brief: JsonDict = field(default_factory=dict)
    slide_count: str = ""
    job_status: str | None = None
    variants: list[str] = field(default_factory=list)
    issues: int | None = None
    audit_status: str | None = None
    slides: int | None = None
    design_mode: str | None = None
    # Снимок открытой презентации (офисная копия или ревизия варианта): оглавление и тексты.
    deck: JsonDict | None = None

    def lines(self) -> list[str]:
        out = [
            f"Шаблон: {self.template}{'' if self.template_ready else ' (ещё разбирается)'}"
            if self.template
            else "Шаблон: не выбран",
            f"Материалы: {', '.join(self.materials)}" if self.materials else "Материалы: нет",
        ]
        brief = {k: v for k, v in (self.brief or {}).items() if v}
        out.append(f"Задача: {brief}" if brief else "Задача: не описана")
        if self.job_status:
            variants = ", ".join(self.variants) if self.variants else "—"
            out.append(f"Генерация: {self.job_status}, варианты: {variants}")
            if self.slides is not None:
                out.append(f"Слайдов в готовом варианте: {self.slides}")
            if self.issues is not None:
                out.append(f"Замечаний аудита: {self.issues}")
            out.append(f"Статус аудита: {self.audit_status or 'неизвестен'}")
        else:
            out.append("Генерация: не запускалась")
        if self.slide_count:
            out.append(f"Просили слайдов: {self.slide_count}")
        out.append(f"Режим композиций: {self.design_mode or 'не выбран (по умолчанию смешанный)'}")
        return out


# Сколько символов содержимого слайдов уходит модели: слайд из вопроса — целиком, остальные —
# началом, чтобы «где таблица?» и «о чём слайд 7?» находились и в длинной колоде.
NAMED_SLIDE_CHARS = 2500
OTHER_SLIDE_CHARS = 300
DECK_CHARS = 9000
ORDINALS = ["перв", "втор", "трет", "четверт", "пят", "шест", "седьм", "восьм", "девят", "десят"]
QUESTION = re.compile(
    r"\?\s*$|^(что|как|какой|какая|какие|каких|где|сколько|о\s+ч[её]м|покажи|расскажи|есть\s+ли)"
    r"(?![а-я])"
)
FIND = {
    "table": re.compile(r"таблиц"),
    "chart": re.compile(r"диаграмм|график"),
    "picture": re.compile(r"картинк|фото|изображени|иллюстрац"),
    "diagram": re.compile(r"схем"),
}
FOUND_NAMES = {
    "table": "таблица",
    "chart": "диаграмма",
    "picture": "картинки",
    "diagram": "схема",
}


def asked_slides(text: str, count: int) -> list[int]:
    """Номера слайдов из вопроса: «слайд 3», «на 3-м слайде», «на третьем», «последний»."""
    t = text.lower().replace("ё", "е")
    found: list[int] = []
    # «слайд 3», «на слайдах 3, 5 и 7», «слайды 3–5»
    for group in re.findall(
        r"слайд[а-я]*\s*(?:№\s*)?(\d{1,3}(?:\s*(?:,|и|-|–|—)\s*\d{1,3})*)(?!\d)", t
    ):
        for start, end in re.findall(r"(\d{1,3})(?:\s*[-–—]\s*(\d{1,3}))?", group):
            first, last = int(start), int(end or start)
            found.extend(range(first, min(last, first + 50) + 1) if last >= first else [first])
    found += [
        int(m)
        for m in re.findall(
            r"(?<!\d)(\d{1,3})\s*(?:-?\s*(?:й|я|е|м|ом|ем|ой|ий))?\s+слайд(?![а-я]*ов)", t
        )
    ]
    for word in re.findall(r"([а-я]+)\s+слайд", t):
        if word.startswith("последн") and count:
            found.append(count)
            continue
        index = next(
            (
                n
                for n, stem in enumerate(ORDINALS)
                if word.startswith(stem) and len(word) <= len(stem) + 4 and "надцат" not in word
            ),
            None,
        )
        if index is not None:
            found.append(index + 1)
    return sorted(set(found))


def deck_lines(state: ProjectState, text: str) -> list[str]:
    """Открытая презентация для модели: оглавление и содержимое слайдов в пределах объёма."""
    deck = state.deck
    if not deck:
        return []
    slides = deck.get("slides") or []
    named = set(asked_slides(text, len(slides)))
    out = [f"Открытая презентация, слайдов: {len(slides)}. Оглавление:", *outline_lines(deck)]
    out.append("Содержимое слайдов:")
    total = 0
    for slide in slides:
        limit = NAMED_SLIDE_CHARS if slide["index"] in named else OTHER_SLIDE_CHARS
        body = slide_lines(slide, limit)
        block = [f"Слайд {slide['index']}:", *(f"  {line}" for line in body or ["(пусто)"])]
        size = sum(len(line) for line in block)
        if total + size > DECK_CHARS and slide["index"] not in named:
            continue
        total += size
        out.extend(block)
    missing = sorted(n for n in named if n > len(slides))
    if missing:
        out.append(f"Слайдов с номерами {', '.join(map(str, missing))} в презентации нет.")
    return out


def answer_about_deck(state: ProjectState, text: str) -> JsonDict | None:
    """Вопрос о содержимом открытой презентации — правилами по снимку: сколько слайдов, что на
    слайде N, где таблица или диаграмма. Не вопрос или нет снимка — None."""
    deck = state.deck
    t = text.lower().replace("ё", "е").strip()
    if not deck or not QUESTION.search(t):
        return None
    slides = deck.get("slides") or []
    count = len(slides)
    reply: str | None = None
    asked = asked_slides(t, count)
    if asked:
        parts = []
        for n in asked[:3]:
            if n > count:
                parts.append(f"Слайда {n} нет: в презентации {count} {_slides_word(count)}.")
                continue
            parts.append(_slide_summary(slides[n - 1]))
        reply = " ".join(parts)
    elif re.search(r"сколько\s+(всего\s+)?слайд", t):
        reply = f"В презентации {count} {_slides_word(count)}."
    elif re.search(r"\bгде\b|на\s+как(ом|их)\s+слайд|есть\s+ли", t):
        for kind, pattern in FIND.items():
            if not pattern.search(t):
                continue
            where = [e["index"] for e in deck.get("outline") or [] if kind in e.get("kinds", [])]
            name = FOUND_NAMES[kind]
            reply = (
                f"{name.capitalize()}: {_slide_list(where)}."
                if where
                else f"В презентации нет: {name}."
            )
            break
    if reply is None:
        return None
    return {"reply": reply[:MAX_REPLY], "options": [], "source": "rules"}


def _slide_summary(slide: JsonDict) -> str:
    """Слайд одной фразой: заголовок, первые тексты, сколько картинок, таблиц и диаграмм."""
    texts: list[str] = []
    counts = {"image": 0, "icon": 0, "table": 0, "chart": 0}
    for obj in slide.get("objects") or []:
        if obj["fixed"] or obj["role"] in ("title", "decoration", "background"):
            continue
        if obj.get("text"):
            texts.append(" ".join(obj["text"]["plain"].split()))
        elif obj["role"] in counts:
            counts[obj["role"]] += 1
    extra = [
        f"{n} {plural(n, *forms)}"
        for role, forms in (
            ("table", ("таблица", "таблицы", "таблиц")),
            ("chart", ("диаграмма", "диаграммы", "диаграмм")),
            ("image", ("картинка", "картинки", "картинок")),
            ("icon", ("иконка", "иконки", "иконок")),
        )
        if (n := counts[role])
    ]
    body = "; ".join(t[:120] for t in texts[:3]) + ("…" if len(texts) > 3 else "")
    tail = ", ".join(extra)
    title = slide.get("title") or "без заголовка"
    detail = "; ".join(part for part in (body, tail) if part)
    end = "" if detail.endswith("…") else "."
    return f"Слайд {slide['index']} «{title}»" + (f": {detail}{end}" if detail else ".")


def _slides_word(n: int) -> str:
    return plural(n, "слайд", "слайда", "слайдов")


def _slide_list(numbers: list[int]) -> str:
    head = ", ".join(map(str, numbers[:12])) + ("…" if len(numbers) > 12 else "")
    return ("слайд " if len(numbers) == 1 else "слайды ") + head


def answer_without_model(state: ProjectState, text: str) -> JsonDict:
    """Ответ правилами: ведёт к недостающему шагу; о содержимом открытой презентации — по
    снимку. Остальные вопросы по существу правила не понимают."""
    about_deck = answer_about_deck(state, text)
    if about_deck is not None:
        return about_deck
    if state.job_status and state.job_status not in {"succeeded", "needs_review"}:
        return {
            "reply": (
                f"Статус генерации: {state.job_status}. Готовность презентации не подтверждена."
                " Подробности — в карточке задания; этот ответ не запускает новых действий."
            ),
            "options": [],
            "source": "rules",
        }
    if not state.template:
        return {
            "reply": (
                "Шаблон оформления пока не выбран — выберите его вверху справа"
                " или перетащите PPTX компании."
            ),
            "options": ["Выбрать шаблон", "Что дальше?"],
            "source": "rules",
        }
    if not state.materials and not (state.brief or {}).get("title"):
        return {
            "reply": (
                "Шаблон есть. Добавьте материалы — документы, таблицы, картинки —"
                " или опишите тему одной фразой."
            ),
            "options": ["Собрать по теме без файлов", "Что дальше?"],
            "source": "rules",
        }
    if not state.job_status:
        return {
            "reply": (
                "Шаблон и содержание есть, сборка запускается сама. Если она не началась,"
                " напишите «собери презентацию». Генерация ещё не запускалась."
            ),
            "options": ["Собрать презентацию", "Изменить задачу"],
            "source": "rules",
        }
    if state.issues:
        return {
            "reply": (
                f"Презентация собрана, аудит нашёл {state.issues} замечаний —"
                " отчёт относится к генерации, не к последующим офисным правкам."
            ),
            "options": ["Что проверяет аудит?"],
            "source": "rules",
        }
    return {
        "reply": (
            "Генерация завершена. Можно проверить результат и скачать его в шапке проекта"
            " или попросить изменить конкретный слайд."
        ),
        "options": ["Скачать", "Изменить слайд"],
        "source": "rules",
    }


def answer(
    state: ProjectState,
    text: str,
    history: list[JsonDict] | None = None,
    *,
    client: Any = None,
    skill: Any = None,
    deadline_s: float = 12.0,
) -> JsonDict:
    """Ответ модели с вариантами следующего шага; без модели — правила по состоянию проекта.

    О режиме оформления ассистент сам не спрашивает: сборка стартует со смешанным режимом, как
    только есть шаблон и содержание, а сменить режим можно фразой («строго по шаблону»)."""
    if client is None or skill is None:
        return answer_without_model(state, text)
    try:
        import asyncio

        from presentation_designer.llm.types import Deadline

        lines = "\n".join(state.lines())
        talk = "\n".join(
            f"{'Пользователь' if m.get('role') == 'user' else 'Ассистент'}: {m.get('text')}"
            for m in (history or [])[-6:]
            if m.get("text")
        )
        deck = "\n".join(deck_lines(state, text))
        request = skill.request(
            "chat.reply",
            f"Состояние проекта:\n{lines}\n\n"
            + (f"{deck}\n\n" if deck else "")
            + (f"Недавний разговор:\n{talk}\n\n" if talk else "")
            + f"Сообщение пользователя: {text}",
            schema=REPLY_SCHEMA,
            stage="brief",
        )
        request.schema_name = "chat_reply"
        request.deadline = Deadline.after(deadline_s)
        response = asyncio.run(client.complete(request))
        parsed = response.parsed if isinstance(response.parsed, dict) else {}
        reply = str(parsed.get("reply") or "").strip()
        if not reply:
            return answer_without_model(state, text)
        options = [str(o).strip() for o in (parsed.get("options") or []) if str(o).strip()]
        return {
            "reply": reply[:MAX_REPLY],
            "options": options[:MAX_OPTIONS],
            "source": "model",
        }
    except Exception:
        log.warning("ассистент не ответил моделью, отвечаю правилами", exc_info=True)
        return answer_without_model(state, text)


__all__ = [
    "REPLY_SCHEMA",
    "ProjectState",
    "answer",
    "answer_about_deck",
    "answer_without_model",
    "asked_slides",
    "deck_lines",
]
