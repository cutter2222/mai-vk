"""Задания любого вида: состояние, отмена, повтор."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response

from presentation_designer.api.deps import Orch
from presentation_designer.api.errors import ApiError
from presentation_designer.pipeline.results import build_job_status
from presentation_designer.pipeline.state import NotFound

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}")
def get_job(job_id: str, orch: Orch) -> dict[str, Any]:
    try:
        return build_job_status(orch.state, job_id)
    except NotFound as e:
        raise ApiError(404, "job_not_found", "Задание не найдено") from e


@router.post("/jobs/{job_id}/cancel", status_code=202)
def cancel_job(job_id: str, orch: Orch) -> Response:
    try:
        orch.cancel_job(job_id)
    except NotFound as e:
        raise ApiError(404, "job_not_found", "Задание не найдено") from e
    return Response(status_code=202)


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: str, orch: Orch) -> dict[str, Any]:
    try:
        job = orch.retry_job(job_id)
    except NotFound as e:
        raise ApiError(404, "job_not_found", "Задание не найдено") from e
    return {"job_id": job["job_id"]}
