"""Нехватка содержания на просьбу пользователя — одна фраза в задании, а не упавшие варианты."""

from __future__ import annotations

import json
from typing import Any

from presentation_designer.pipeline import jobs
from presentation_designer.pipeline.jobs import Orchestrator


def _variant(
    o: Orchestrator, job_id: str, variant_id: str, count: int, code: str | None
) -> dict[str, Any]:
    path = o.artifacts.revision_dir(job_id, variant_id, 1) / "plan.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    warnings = [{"code": code, "message": "…"}] if code else []
    path.write_text(json.dumps({"slides": [{}] * count, "warnings": warnings}), encoding="utf-8")
    return {"variant_id": variant_id, "revision": 1, "status": "ready", "slide_count": count}


def test_short_content_is_one_job_sentence(orchestrator: Orchestrator) -> None:
    o = orchestrator
    variants = [
        _variant(o, "job_a", "compact", 6, "slide_count_short"),
        _variant(o, "job_a", "balanced", 7, "slide_count_short"),
        _variant(o, "job_a", "detailed", 7, "slide_count_short"),
        {"variant_id": "extra", "revision": 1, "status": "failed"},
    ]
    exact = {"settings": {"slide_count": {"exact": 12}}}
    assert jobs._slide_count_short(o, "job_a", variants, exact) == [
        {
            "code": "slide_count_short",
            "message": "Просили 12 слайдов — содержания хватило на 6–7. Добавьте материалы или "
            "расскажите о теме подробнее, и я соберу больше.",
        }
    ]
    ranged = {"settings": {"slide_count": {"min": 15, "max": 18}}}
    message = jobs._slide_count_short(o, "job_a", variants[:1], ranged)[0]["message"]
    assert message.startswith("Просили 15–18 слайдов — содержания хватило на 6.")


def test_default_range_shortening_stays_silent(orchestrator: Orchestrator) -> None:
    o = orchestrator
    variants = [_variant(o, "job_b", "compact", 6, "slide_count_relaxed")]
    assert jobs._slide_count_short(o, "job_b", variants, {"settings": {}}) == []
