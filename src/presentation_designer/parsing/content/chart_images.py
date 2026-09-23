"""Fail-closed VLM extraction; deterministic parsers and their cache stay untouched."""

from __future__ import annotations

import asyncio
import hashlib
import io
import math
import re
from typing import Any, Literal

from PIL import Image as PilImage
from pydantic import BaseModel, ConfigDict, Field

from presentation_designer.llm.skills import get_skill
from presentation_designer.llm.types import Deadline, Image, Message
from presentation_designer.parsing.content.datasets import Column, Dataset, _cell_number
from presentation_designer.parsing.content.parsers.base import ParsedImage


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Point(_Strict):
    value: float | None
    label: str | None
    basis: Literal["label", "estimated", "unreadable"]


class Series(_Strict):
    name: str = Field(min_length=1, max_length=120)
    points: list[Point] = Field(max_length=40)


class Extraction(_Strict):
    status: Literal["readable", "uncertain", "unsupported", "not_chart"]
    reason: str = Field(max_length=600)
    type: Literal["column", "bar", "line"] | None
    title: str | None
    unit: str | None
    categories: list[str] = Field(max_length=40)
    series: list[Series] = Field(max_length=5)
    axis_minimum: float | None
    axis_maximum: float | None

    def validate_readable(self) -> None:
        """A model's 'readable' claim is not sufficient to accept its numbers."""
        if self.status != "readable" or not self.type or not self.categories or not self.series:
            raise ValueError(self.reason or "не все данные графика читаются")
        if any(not c.strip() for c in self.categories):
            raise ValueError("пустая категория")
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("неоднозначные категории")
        names = [s.name.strip() for s in self.series]
        if len(set(names)) != len(names) or not all(names) or "Категория" in names:
            raise ValueError("неоднозначные имена рядов")
        values = []
        for series in self.series:
            if len(series.points) != len(self.categories):
                raise ValueError("число значений не совпадает с категориями")
            for point in series.points:
                if point.basis != "label" or point.value is None or not point.label:
                    raise ValueError("нужны явно подписанные значения всех точек")
                # Preserve displayed scale: the generic table parser expands «млн».
                # Strip only the explicitly declared suffix before numeric comparison.
                label = point.label.strip()
                if self.unit and label.endswith(self.unit):
                    label = label[: -len(self.unit)].strip()
                if not re.fullmatch(r"[+−-]?\d[\d\s\u00a0]*(?:[.,]\d+)?", label):
                    raise ValueError("подпись содержит неоднозначную единицу или формат числа")
                parsed = _cell_number(label)
                if (
                    not math.isfinite(point.value)
                    or parsed is None
                    or not math.isclose(parsed[0], point.value, rel_tol=1e-10, abs_tol=1e-10)
                ):
                    raise ValueError("значение не соответствует числовой подписи")
                if parsed[1] and parsed[1] != self.unit:
                    raise ValueError("единица подписи не совпадает с единицей графика")
                values.append(point.value)
        lo, hi = self.axis_minimum, self.axis_maximum
        if any(v is not None and not math.isfinite(v) for v in (lo, hi)):
            raise ValueError("нечисловая граница оси")
        if lo is not None and hi is not None and lo >= hi:
            raise ValueError("неверные границы оси")
        if (lo is not None and min(values) < lo) or (hi is not None and max(values) > hi):
            raise ValueError("значения вне шкалы")
        if self.type in ("bar", "column") and (
            (lo is not None and lo > 0) or (hi is not None and hi < 0)
        ):
            raise ValueError("обрезанная шкала столбцов не поддерживается")

    def dataset(
        self, *, dataset_id: str, source_id: str, block_id: str, asset_id: str, image: ParsedImage
    ) -> Dataset:
        self.validate_readable()
        # Do not re-infer categories (e.g. years or numeric labels) as measures.
        return Dataset(
            dataset_id=dataset_id,
            title=self.title,
            columns=[
                Column("Категория", "string"),
                *[
                    Column(s.name, "percent" if self.unit == "%" else "number", self.unit)
                    for s in self.series
                ],
            ],
            rows=[
                [c, *[s.points[i].value for s in self.series]]
                for i, c in enumerate(self.categories)
            ],
            source_id=source_id,
            block_id=block_id,
            location=image.location,
            total_rows=len(self.categories),
            truncated=False,
            numeric_columns={
                i + 1: [(j, p.value) for j, p in enumerate(s.points) if p.value is not None]
                for i, s in enumerate(self.series)
            },
            source_chart={
                "type": self.type,
                "asset_id": asset_id,
                **({"axis_minimum": self.axis_minimum} if self.axis_minimum is not None else {}),
                **({"axis_maximum": self.axis_maximum} if self.axis_maximum is not None else {}),
            },
        )


def image_key(image: ParsedImage) -> str:
    return hashlib.sha256(image.data).hexdigest()


def extract_charts(
    images: list[ParsedImage], client: Any, *, budget_s: float, max_images: int
) -> tuple[dict[str, Extraction], dict[str, Any]]:
    """One request per unique raster, bounded total time, no network outside LlmClient."""
    skill = get_skill("chart_extractor")
    unique = {image_key(i): i for i in images}
    accepted: dict[str, Extraction] = {}
    summary: dict[str, Any] = {"calls": 0, "items": {}}
    deadline = Deadline.after(budget_s)

    async def run() -> None:
        for sha, image in unique.items():
            if summary["calls"] >= max_images or deadline.expired():
                summary["items"][sha] = {"status": "skipped", "reason": "лимит VLM-извлечения"}
                continue
            try:
                # Normalize raster formats; no 768px template thumbnail/downscaling for OCR.
                with PilImage.open(io.BytesIO(image.data)) as raster:
                    if raster.width * raster.height > 16_000_000:
                        raise ValueError("слишком большое изображение для извлечения")
                    out = io.BytesIO()
                    raster.convert("RGB").save(out, format="PNG")
                req = skill.request(
                    "import.chart_image",
                    "Прочитай график на изображении.",
                    schema=Extraction.model_json_schema(),
                    stage="import",
                    deadline=deadline,
                    schema_name="chart_extraction",
                )
                req.messages[-1] = Message(
                    "user", req.messages[-1].text, (Image(out.getvalue(), detail="high"),)
                )
                summary["calls"] += 1
                response = await asyncio.wait_for(
                    client.complete(req), timeout=deadline.remaining()
                )
                extraction = Extraction.model_validate(response.parsed)
                summary["items"][sha] = extraction.model_dump()
                if extraction.status != "not_chart":
                    extraction.validate_readable()
                    accepted[sha] = extraction
            except Exception as exc:
                # Bad OCR/invalid response/provider failures never destroy the source image.
                summary["items"].setdefault(sha, {})
                summary["items"][sha].update(
                    status="rejected", reason=(str(exc) or type(exc).__name__)[:600]
                )

    asyncio.run(run())
    return accepted, summary
