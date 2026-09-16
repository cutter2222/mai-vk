"""Готовность сервиса и его возможности."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from presentation_designer import __version__
from presentation_designer.api.deps import Orch
from presentation_designer.contracts import CONTRACTS_VERSION

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
        "execution_mode": orch.layers.execution_mode(),
        "checks": checks,
    }


@router.get("/capabilities")
def capabilities(orch: Orch) -> dict[str, Any]:
    limits = orch.settings.limits
    return {
        "contracts_version": CONTRACTS_VERSION,
        "execution_mode": orch.layers.execution_mode(),
        "features": {"generate_images": False, "contextual_audit": True, "html_export": True},
        "limits": {
            "max_upload_mb": limits.max_upload_mb,
            "max_content_files": limits.max_content_files,
            "slide_count_max": limits.slide_count_max,
            "max_project_files": limits.max_project_files,
            "max_project_mb": limits.max_project_mb,
        },
    }
