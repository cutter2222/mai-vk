"""Приложение FastAPI: маршруты /api, единый формат ошибок, фоновая сверка заданий.

Интерфейс отдаёт Caddy с того же origin, API не раздаёт статику. Тяжёлая работа
выполняется воркерами; здесь только приём файлов, состояние и выдача артефактов.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from presentation_designer import __version__
from presentation_designer.api.errors import ApiError
from presentation_designer.api.routes import (
    brief,
    content,
    generations,
    health,
    jobs,
    projects,
    templates,
)
from presentation_designer.pipeline.files import UploadError
from presentation_designer.pipeline.jobs import ConflictError, Orchestrator, build_orchestrator
from presentation_designer.pipeline.state import NotFound

log = logging.getLogger(__name__)


def create_app(orchestrator: Orchestrator | None = None, *, reconcile: bool = True) -> FastAPI:
    orch = orchestrator or build_orchestrator()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        stop = threading.Event()
        thread: threading.Thread | None = None
        if reconcile:
            interval = max(5, orch.settings.queue.reconcile_interval_s)

            def loop() -> None:
                # Периодическая сверка: задания без исполнителя и с истёкшим сроком получают ошибку.
                while not stop.wait(interval):
                    try:
                        touched = orch.reconcile()
                        if touched:
                            log.warning(
                                "сверка отметила задания без исполнителя: %s", ", ".join(touched)
                            )
                    except Exception:
                        log.exception("сверка заданий не удалась")

            thread = threading.Thread(target=loop, name="reconcile", daemon=True)
            thread.start()
        try:
            orch.files.cleanup_tmp()
        except Exception:
            log.exception("не удалось очистить временные загрузки")
        try:
            yield
        finally:
            stop.set()
            if thread:
                thread.join(timeout=2)

    app = FastAPI(
        title="Цифровой дизайнер презентаций",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.state.orchestrator = orch

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.body())

    @app.exception_handler(NotFound)
    async def _not_found(_: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "not_found", "message": f"Не найдено: {exc}"}},
        )

    @app.exception_handler(UploadError)
    async def _upload_error(_: Request, exc: UploadError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status, content={"error": {"code": exc.code, "message": str(exc)}}
        )

    @app.exception_handler(ConflictError)
    async def _conflict(_: Request, exc: ConflictError) -> JSONResponse:
        status = 409 if exc.code in {"revision_stale", "repair_in_progress"} else 422
        return JSONResponse(
            status_code=status,
            content={"error": {"code": exc.code, "message": str(exc), "details": exc.details}},
        )

    @app.exception_handler(RequestValidationError)
    async def _request_invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Запрос не соответствует контракту",
                    "details": {"errors": _errors(exc.errors())},
                }
            },
        )

    @app.exception_handler(ValidationError)
    async def _model_invalid(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_document",
                    "message": "Документ не соответствует контракту",
                    "details": {"errors": _errors(exc.errors())},
                }
            },
        )

    for router in (
        health.router,
        projects.router,
        templates.router,
        content.router,
        brief.router,
        generations.router,
        jobs.router,
    ):
        app.include_router(router, prefix="/api")
    return app


def _errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        {"loc": [str(x) for x in e.get("loc", [])], "message": e.get("msg", "")}
        for e in errors[:20]
    ]


app_factory = create_app
