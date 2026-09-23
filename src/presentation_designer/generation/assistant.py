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
from dataclasses import dataclass, field
from typing import Any

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


def answer_without_model(state: ProjectState, text: str) -> JsonDict:
    """Ответ правилами: ведёт к недостающему шагу. Вопрос по существу правила не понимают."""
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
                "Шаблон оформления пока не выбран — перетащите PPTX компании"
                " или выберите его в первом сообщении чата."
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
                "Тема или материалы есть. Проверьте назначение в брифе и готовность шаблона;"
                " затем можно запустить сборку. Генерация ещё не запускалась."
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
    """Ответ модели с вариантами следующего шага; без модели — правила по состоянию проекта."""
    if (
        state.template
        and (state.materials or state.brief.get("title"))
        and not state.design_mode
        and not state.job_status
    ):
        from presentation_designer.generation.design_mode import LABELS

        return {
            "reply": (
                "Как оформить презентацию? По шаблону — только готовые макеты; "
                "смешанный — макеты и новые композиции; все слайды новые — новые композиции "
                "в стиле исходника, включая титул и финал. "
                "Это не влияет на подробность содержания."
            ),
            "options": list(LABELS.values()),
            "source": "rules",
        }
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
        request = skill.request(
            "chat.reply",
            f"Состояние проекта:\n{lines}\n\n"
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


__all__ = ["REPLY_SCHEMA", "ProjectState", "answer", "answer_without_model"]
