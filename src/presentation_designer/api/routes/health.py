"""Готовность сервиса и его возможности."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter

from presentation_designer import __version__
from presentation_designer.api.deps import Orch
from presentation_designer.contracts import CONTRACTS_VERSION
from presentation_designer.llm import describe_provider
from presentation_designer.pipeline.gc import read_report

router = APIRouter(tags=["health"])


@router.get("/health")
def health(orch: Orch) -> dict[str, Any]:
    """Liveness и readiness различаются: ранние неполные слои показываются как возможности,
    а не как здоровая генерация."""
    checks: dict[str, Any] = {}
    try:
        orch.state.list_active_jobs()
        checks["db"] = True
    except Exception:
        checks["db"] = False
    try:
        checks["storage"] = orch.files.root.is_dir() and orch.artifacts.root.is_dir()
    except Exception:
        checks["storage"] = False
    checks["valkey"] = orch.executor.ping()
    workers = orch.executor.workers() if checks["valkey"] else {"analysis": 0, "generation": 0}
    checks["workers"] = workers["analysis"] > 0 and workers["generation"] > 0
    renderer = orch.executor.renderer_ok()
    checks["renderer"] = renderer
    if not (checks["db"] and checks["storage"]):
        status = "down"
    elif not checks["valkey"] or not checks["workers"] or renderer is False:
        status = "degraded"
    else:
        status = "ok"
    return {
        "status": status,
        "workers": workers,
        "valkey_ok": checks["valkey"],
        "renderer_ok": bool(renderer),
        "version": __version__,
        # Выложенная версия: тег образа и commit, которые deploy.sh передаёт через окружение.
        "release": {
            "image_tag": os.environ.get("PD_IMAGE_TAG") or None,
            "commit": os.environ.get("PD_BUILD_COMMIT") or None,
            "schema": orch.state.schema_version() if checks["db"] else None,
        },
        "execution_mode": orch.layers.execution_mode(),
        "checks": checks,
        "gc": _gc_summary(orch),
        # Провайдер моделей: имя, хост, модели по ролям и подтверждён ли зондом. Без секретов;
        # на готовность не влияет — слои пока работают в режиме заглушек.
        "provider": _provider_summary(),
    }


def _gc_summary(orch: Any) -> dict[str, Any] | None:
    """Итог последней сборки мусора из runs/gc/last.json, без списка объектов."""
    report = read_report(orch.gc_report_path)
    if report is None:
        return None
    return {k: v for k, v in report.items() if k != "items"}


def _provider_summary() -> dict[str, Any] | None:
    try:
        return describe_provider()
    except Exception:
        return None


@router.get("/capabilities")
def capabilities(orch: Orch) -> dict[str, Any]:
    limits = orch.settings.limits
    return {
        "contracts_version": CONTRACTS_VERSION,
        "execution_mode": orch.layers.execution_mode(),
        "provider": _provider_summary(),
        "features": {"generate_images": False, "contextual_audit": True, "html_export": True},
        "limits": {
            "max_upload_mb": limits.max_upload_mb,
            "max_content_files": limits.max_content_files,
            "slide_count_max": limits.slide_count_max,
            "max_project_files": limits.max_project_files,
            "max_project_mb": limits.max_project_mb,
        },
    }
