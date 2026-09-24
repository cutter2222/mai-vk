"""Бриф из свободного сообщения чата: короткий вызов слоя `brief` через ядро.

Слой отвечает моделью (скилл `brief_extractor`, `source: model`) или детерминированной
эвристикой (`source: heuristic`) — при заглушечных слоях, ненастроенном провайдере, ошибке
или тайм-ауте `timeouts.brief_s`. Ответ — предложение для подтверждения пользователем.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from presentation_designer.api.deps import Orch
from presentation_designer.contracts import models as m

router = APIRouter(tags=["brief"])


class BriefRequest(BaseModel):
    # Длинное сообщение с содержанием разрешено: модели уходит выдержка (brief.model_text),
    # а сам текст — материалом «Текст из чата».
    text: str = Field(..., max_length=100_000)
    brief: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None


@router.post("/brief")
async def brief(body: BriefRequest, orch: Orch) -> dict[str, Any]:
    result = await orch.extract_brief(body.text, body.brief, body.settings)
    doc = {"schema_version": "1.2", **result}
    return m.BriefExtract.model_validate(doc).model_dump(mode="json", exclude_none=True)
