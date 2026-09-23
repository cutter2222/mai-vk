from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from presentation_designer.generation import assistant
from presentation_designer.generation.design_mode import LABELS
from presentation_designer.llm.skills import get_skill
from presentation_designer.pipeline.jobs import Orchestrator


def message(client: TestClient, project: str, text: str = "Что дальше?") -> str:
    return str(
        client.post(
            f"/api/projects/{project}/events",
            json={
                "role": "user",
                "kind": "message",
                "text": text,
                "file_ids": [],
            },
        ).json()["event_id"]
    )


def test_chat_persists_reply_without_starting_generation(client: TestClient) -> None:
    project = client.post("/api/projects", json={}).json()["project_id"]
    event = message(client, project)
    response = client.post("/api/chat", json={"project_id": project, "event_id": event})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["source"] == "rules"
    assert "не выбран" in result["reply"]
    saved = client.get(f"/api/projects/{project}").json()
    assert len(saved["events"]) == 2
    assert saved["events"][-1]["text"] == result["reply"]
    assert not saved.get("job_id")
    assert (
        client.post(
            "/api/chat",
            json={
                "project_id": project,
                "event_id": result["event"]["event_id"],
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/chat",
            json={
                "project_id": "missing",
                "event_id": event,
            },
        ).status_code
        == 404
    )


@pytest.mark.parametrize("mode,label", LABELS.items())
def test_chat_design_mode_persists_without_changing_density(
    client: TestClient, mode: str, label: str
) -> None:
    project = client.post("/api/projects", json={}).json()["project_id"]
    before = client.get(f"/api/projects/{project}").json()["settings"]
    event = message(client, project, label)
    result = client.post("/api/chat", json={"project_id": project, "event_id": event})
    assert result.status_code == 200, result.text
    saved = client.get(f"/api/projects/{project}").json()
    assert saved["settings"] == {**before, "design_mode": mode}
    assert not saved.get("job_id")
    assert label in result.json()["reply"]


def test_assistant_requests_design_mode_only_before_first_generation() -> None:
    state = assistant.ProjectState(template="Template", materials=["Report"])
    assert assistant.answer(state, "Что дальше?")["options"] == list(LABELS.values())
    state.design_mode = "mixed"
    assert assistant.answer(state, "Что дальше?")["options"] != list(LABELS.values())
    state.design_mode = None
    state.job_status = "succeeded"
    assert assistant.answer(state, "Что дальше?")["options"] != list(LABELS.values())


def test_chat_uses_server_context_and_history(
    client: TestClient,
    orchestrator: Orchestrator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = client.post("/api/projects", json={}).json()["project_id"]
    saved = client.get(f"/api/projects/{project}").json()
    patched = client.patch(
        f"/api/projects/{project}",
        json={
            "brief": {
                **saved["brief"],
                "title": "Пилот",
                "audience": "Директора",
                "purpose": "product",
            },
            "settings": {**saved["settings"], "mode": "exact", "exact": 8, "variants": ["compact"]},
        },
    )
    assert patched.status_code == 200, patched.text
    message(client, project, "Для директоров")
    event = message(client, project, "Сколько слайдов?")

    def answer(state: assistant.ProjectState, text: str, history: list[Any], **_: Any) -> Any:
        assert state.brief["audience"] == "Директора"
        assert state.slide_count == "8"
        assert state.variants == ["compact"]
        assert state.job_status is None
        assert text == "Сколько слайдов?"
        assert [e["text"] for e in history] == ["Для директоров"]
        return {
            "reply": "Запрошено 8 слайдов; генерация не запускалась.",
            "options": ["Какие материалы нужны?"],
            "source": "model",
        }

    monkeypatch.setattr(assistant, "answer", answer)
    result = client.post("/api/chat", json={"project_id": project, "event_id": event})
    assert result.status_code == 200, result.text
    assert result.json()["source"] == "model"
    assert not orchestrator.state.get_project(project).get("job_id")
    other = client.post("/api/projects", json={}).json()["project_id"]
    assert (
        client.post(
            "/api/chat",
            json={
                "project_id": other,
                "event_id": event,
            },
        ).status_code
        == 422
    )


@pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled", "missing"])
def test_fallback_never_claims_unfinished_deck_is_ready(status: str) -> None:
    result = assistant.answer_without_model(assistant.ProjectState(job_status=status), "Готово?")
    assert status in result["reply"]
    assert "не подтверждена" in result["reply"]
    assert "проверена" not in result["reply"]


def test_assistant_model_and_failure_paths() -> None:
    class Client:
        async def complete(self, request: Any) -> Any:
            assert request.schema_name == "chat_reply"
            assert "не запускалась" in request.messages[-1].text
            return type(
                "Reply",
                (),
                {
                    "parsed": {"reply": "Какая аудитория?", "options": ["Директора"]},
                },
            )()

    skill = get_skill("project_assistant")
    result = assistant.answer(assistant.ProjectState(), "Помоги", client=Client(), skill=skill)
    assert result["source"] == "model"

    class Broken:
        async def complete(self, _: Any) -> Any:
            raise TimeoutError("provider unavailable")

    fallback = assistant.answer(assistant.ProjectState(), "Помоги", client=Broken(), skill=skill)
    assert fallback["source"] == "rules"


def test_partial_audit_is_reported_not_hidden(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from presentation_designer.api.routes import chat

    project = client.post("/api/projects", json={}).json()["project_id"]
    patched = client.patch(f"/api/projects/{project}", json={"job_id": "job_partial"})
    assert patched.status_code == 200
    event = message(client, project)
    monkeypatch.setattr(
        chat,
        "result_or_none",
        lambda *_: {
            "status": "needs_review",
            "variants": [
                {
                    "variant_id": "compact",
                    "slide_count": 10,
                    "artifacts": {"pptx": "deck.pptx"},
                    "audit": {"status": "partial", "issues_total": 7},
                }
            ],
        },
    )

    def answer(state: assistant.ProjectState, *_: Any, **__: Any) -> Any:
        assert state.issues == 7 and state.audit_status == "partial"
        assert "Статус аудита: partial" in state.lines()
        return {"reply": "Аудит неполный, найдено 7 замечаний.", "options": [], "source": "rules"}

    monkeypatch.setattr(assistant, "answer", answer)
    assert (
        client.post(
            "/api/chat",
            json={
                "project_id": project,
                "event_id": event,
            },
        ).status_code
        == 200
    )
