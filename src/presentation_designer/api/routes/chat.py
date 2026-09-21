"""Read server context and persist a conversational reply; never mutate presentation jobs."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from presentation_designer.api.deps import Orch
from presentation_designer.api.errors import ApiError
from presentation_designer.generation import assistant
from presentation_designer.pipeline.real import RealLayers
from presentation_designer.pipeline.results import result_or_none
from presentation_designer.pipeline.state import NotFound

router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    project_id: str = Field(min_length=1, max_length=100)
    event_id: str = Field(min_length=1, max_length=100)


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
    text = events[index]["text"]
    if len(text) > 4000:
        raise ApiError(422, "chat_message_too_long", "Сообщение длиннее 4000 символов")
    state = assistant.ProjectState(
        brief=project["brief"],
        materials=[f["name"] for f in project["files"] if f["kind"] == "material"],
    )
    settings = project["settings"]
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
    client = skill = None
    if isinstance(orch.layers, RealLayers) and orch.layers.modes.get("brief") == "real":
        client = orch.layers.llm_client()
        skill = orch.layers.skill("project_assistant")
    answer = assistant.answer(
        state,
        text,
        events[:index],
        client=client,
        skill=skill,
        deadline_s=orch.settings.timeouts.brief_s,
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
