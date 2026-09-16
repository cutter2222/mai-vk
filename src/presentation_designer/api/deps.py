"""Зависимости маршрутов: оркестратор приложения и его части."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from presentation_designer.pipeline.jobs import Orchestrator


def get_orch(request: Request) -> Orchestrator:
    return request.app.state.orchestrator  # type: ignore[no-any-return]


Orch = Annotated[Orchestrator, Depends(get_orch)]
