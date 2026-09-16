"""Оценка числа токенов до вызова: нужна лимитеру, чтобы зарезервировать TPM.

Токенизатор Qwen в приложение не тянется (docs/decisions.md): оценка нужна только для
резервирования и сверяется с usage провайдера после ответа. Оценка сознательно завышена:
лучше подождать лишнюю секунду, чем получить 429.
"""

from __future__ import annotations

import json

from presentation_designer.llm.types import JsonDict, Request

MESSAGE_OVERHEAD_TOKENS = 4


def estimate_text_tokens(text: str, chars_per_token: float = 3.0) -> int:
    if not text:
        return 0
    return int(len(text) / max(chars_per_token, 1.0)) + 1


def estimate_request_tokens(
    req: Request, *, chars_per_token: float = 3.0, image_tokens: int = 1280
) -> int:
    """Входные токены: текст сообщений, схема ответа, изображения по фиксированной оценке."""
    total = 0
    for message in req.messages:
        total += estimate_text_tokens(message.text, chars_per_token) + MESSAGE_OVERHEAD_TOKENS
        total += image_tokens * len(message.images)
    if req.schema is not None:
        total += estimate_text_tokens(json.dumps(req.schema, ensure_ascii=False), chars_per_token)
    return total


def estimate_output_tokens(req: Request, default: int = 1024) -> int:
    return req.max_output_tokens or default


def estimate_json_tokens(doc: JsonDict, chars_per_token: float = 3.0) -> int:
    return estimate_text_tokens(json.dumps(doc, ensure_ascii=False), chars_per_token)
