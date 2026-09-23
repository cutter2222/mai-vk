"""Устройство диаграммы от модели (роль vlm, скилл chart_reader): один запрос на картинку."""

from __future__ import annotations

import asyncio
import copy
from typing import Any

from PIL import Image

from presentation_designer.llm.skills import get_skill
from presentation_designer.llm.types import Deadline, Message
from presentation_designer.llm.types import Image as LlmImage
from presentation_designer.parsing.raster_charts.model import ChartStructure
from presentation_designer.parsing.raster_charts.pixels import to_png

PROMPT_ID = "rebuild.chart_image"
# Мелкие подписи осей в картинках по 600 px модель читает увереннее после увеличения;
# мерит код всё равно оригинал.
UPSCALE_BELOW = 900


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Схема для строгого json_schema: все поля обязательны, значения по умолчанию убраны."""
    out = copy.deepcopy(schema)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                node["required"] = list(props)
                node["additionalProperties"] = False
                for p in props.values():
                    if isinstance(p, dict):
                        p.pop("default", None)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(out)
    return out


STRUCTURE_SCHEMA = strict_schema(ChartStructure.model_json_schema())


def model_image(image: Image.Image) -> bytes:
    if image.width < UPSCALE_BELOW:
        k = 2
        image = image.resize((image.width * k, image.height * k), Image.Resampling.LANCZOS)
    return to_png(image)


async def read_structure(client: Any, image: Image.Image, *, deadline: Deadline) -> ChartStructure:
    skill = get_skill("chart_reader")
    req = skill.request(
        PROMPT_ID,
        "Опиши диаграмму на изображении.",
        schema=STRUCTURE_SCHEMA,
        deadline=deadline,
        schema_name="chart_structure",
    )
    req.messages[-1] = Message(
        "user", req.messages[-1].text, (LlmImage(model_image(image), detail="high"),)
    )
    response = await asyncio.wait_for(client.complete(req), timeout=deadline.remaining())
    return ChartStructure.model_validate(response.parsed)
