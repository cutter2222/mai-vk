"""Бриф из свободного сообщения чата: короткий вызов слоя `brief` через ядро."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from presentation_designer.api.deps import Orch
from presentation_designer.contracts import models as m
from presentation_designer.parsing.content.brief import extract_brief

router = APIRouter(tags=["brief"])


class BriefRequest(BaseModel):
    text: str = Field(..., max_length=4000)
    brief: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None


@router.post("/brief")
def brief(body: BriefRequest, orch: Orch) -> dict[str, Any]:
    """Пока детерминированная эвристика (`source: heuristic`);
    модель подключается на этапе импорта содержания."""
    doc = {"schema_version": "1.2", **extract_brief(body.text, body.brief)}
    return m.BriefExtract.model_validate(doc).model_dump(mode="json", exclude_none=True)
