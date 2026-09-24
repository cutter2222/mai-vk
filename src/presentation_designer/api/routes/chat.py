"""Read server context and persist a conversational reply; never mutate presentation jobs."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from presentation_designer.api.deps import Orch
from presentation_designer.api.errors import ApiError
from presentation_designer.generation import assistant
from presentation_designer.generation.design_mode import DESCRIPTIONS, LABELS, parse_design_mode
from presentation_designer.pipeline.real import RealLayers
from presentation_designer.pipeline.results import result_or_none
from presentation_designer.pipeline.snapshots import project_snapshot
from presentation_designer.pipeline.state import NotFound

router = APIRouter(tags=["chat"])


class OpenDocument(BaseModel):
    """Офисная копия, открытая в редакторе проекта, и её ревизия."""

    document_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    revision: int = Field(ge=0)


class ChatRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=100)
    event_id: str = Field(min_length=1, max_length=100)
    # Ассистент отвечает о содержимом того, что открыто: копии (если она этого проекта) или
    # последней ревизии выбранного варианта.
    office: OpenDocument | None = None


class ChatResponse(BaseModel):
    reply: str
    options: list[str]
    source: Literal["model", "rules"]
    event: dict[str, Any]


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, orch: Orch) -> dict[str, Any]:
    # A synchronous route runs in FastAPI's thread pool: answer() owns its asyncio loop.
    try:
        project = orch.state.get_project(body.project_id)
    except NotFound as exc:
        raise ApiError(404, "project_not_found", "Проект не найден") from exc
    events = project["events"]
    index = next((i for i, e in enumerate(events) if e["event_id"] == body.event_id), None)
    if (
        index is None
        or events[index]["role"] != "user"
        or not events[index].get("text", "").strip()
    ):
        raise ApiError(422, "chat_message_invalid", "Нужно сохранённое сообщение пользователя")
    # Длинное сообщение (содержание целой презентации) ассистенту нужно началом: сам текст
    # уходит материалом, а отказ «длиннее 4000 символов» оставлял пользователя без ответа.
    text = events[index]["text"]
    if len(text) > 4000:
        text = text[:4000].rsplit(" ", 1)[0] + " …"
    state = assistant.ProjectState(
        brief=project["brief"],
        materials=[f["name"] for f in project["files"] if f["kind"] == "material"],
    )
    settings = project["settings"]
    selected_mode = parse_design_mode(text)
    if selected_mode:
        settings = {**settings, "design_mode": selected_mode}
        orch.state.update_project(body.project_id, {"settings": settings})
    state.design_mode = settings.get("design_mode")
    state.slide_count = (
        str(settings.get("exact"))
        if settings.get("mode") == "exact"
        else (f"{settings.get('min', 10)}–{settings.get('max', 15)}")
    )
    state.variants = settings.get("variants", [])
    if project.get("template_id"):
        try:
            template = orch.state.get_template(project["template_id"])
            state.template = template["name"]
            state.template_ready = template["status"] == "succeeded"
        except NotFound:
            pass
    chosen = None
    if project.get("job_id"):
        result = result_or_none(orch.state, project["job_id"])
        state.job_status = result["status"] if result else "missing"
        if result:
            variants = result["variants"]
            chosen = next(
                (v for v in variants if v["variant_id"] == project.get("chosen_variant")),
                None,
            )
            chosen = chosen or next((v for v in variants if v["artifacts"].get("pptx")), None)
            if chosen:
                state.slides = chosen.get("slide_count")
                audit = chosen.get("audit") or {}
                state.audit_status = audit.get("status")
                if audit.get("status") in {"done", "partial"}:
                    state.issues = audit.get("issues_total")
    if body.office is not None or chosen is not None:
        state.deck = project_snapshot(
            orch,
            project,
            body.office.model_dump() if body.office else None,
            chosen if chosen and chosen.get("artifacts", {}).get("pptx") else None,
        )
        if state.deck:
            state.slides = len(state.deck["slides"])
    client = skill = None
    if (
        not selected_mode
        and isinstance(orch.layers, RealLayers)
        and orch.layers.modes.get("brief") == "real"
    ):
        client = orch.layers.llm_client()
        skill = orch.layers.skill("project_assistant")
    answer = (
        {
            "reply": f"Режим «{LABELS[selected_mode]}» сохранён. {DESCRIPTIONS[selected_mode]}",
            "options": ["Собрать презентацию"],
            "source": "rules",
        }
        if selected_mode
        else assistant.answer(
            state,
            text,
            events[:index],
            client=client,
            skill=skill,
            deadline_s=orch.settings.timeouts.brief_s,
        )
    )
    event = orch.state.append_event(
        body.project_id,
        {
            "role": "assistant",
            "kind": "text",
            "text": answer["reply"],
        },
    )
    return {**answer, "event": event}
