"""Сборка документов GenerationResult и JobStatus из состояния SQLite и манифестов ревизий.

Документы собираются при чтении: состояние хранится по частям (задание, генерация,
варианты, ревизии, исправления), а контракт отдаёт единый снимок на любом этапе.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from presentation_designer.pipeline.state import TERMINAL, JsonDict, State

VARIANT_STAGES = ["plan", "compose", "export", "audit"]


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if ts else None


def _ms_between(a: str | None, b: str | None) -> int | None:
    da, db = _parse(a), _parse(b)
    if da is None or db is None:
        return None
    return max(0, int((db - da).total_seconds() * 1000))


def _clean(doc: JsonDict) -> JsonDict:
    return {k: v for k, v in doc.items() if v is not None}


def build_generation_result(state: State, job_id: str) -> JsonDict:
    job = state.get_job(job_id)
    gen = state.get_generation(job_id)
    variants_rows = state.get_variants(job_id)
    revisions = state.list_revisions(job_id)
    repairs = state.list_repairs(job_id)
    revisions_by_variant: dict[str, list[JsonDict]] = {}
    for rev in revisions:
        revisions_by_variant.setdefault(rev["variant_id"], []).append(rev)

    manifest: JsonDict = {}
    for rev in revisions:
        manifest.update(rev["manifest"])

    variants: list[JsonDict] = []
    for row in variants_rows:
        revs = revisions_by_variant.get(row["variant_id"], [])
        current = next(
            (r for r in revs if r["revision"] == row["revision"]), revs[-1] if revs else None
        )
        artifacts: JsonDict = {"thumbnails": []}
        if current:
            prefix = current["artifacts_prefix"]
            names = current["manifest"]
            for key, art in (("pptx", "deck.pptx"), ("pdf", "deck.pdf"), ("html", "deck.html")):
                if f"{prefix}{art}" in names:
                    artifacts[key] = f"{prefix}{art}"
            thumbs = sorted(n for n in names if n.startswith(f"{prefix}thumbs/"))
            artifacts["thumbnails"] = [
                {"slide_index": i, "name": n, "width_px": 1280, "height_px": 720}
                for i, n in enumerate(thumbs)
            ]
        audit = dict(row["audit"]) if row.get("audit") else None
        variant: JsonDict = {
            "variant_id": row["variant_id"],
            "axis": row["axis"],
            "value": row["value"],
            "rationale": row["rationale"],
            "status": row["status"],
            "revision": row["revision"],
            "revisions": [
                _clean(
                    {
                        "revision": r["revision"],
                        "created_at": r["created_at"],
                        "repair_job_id": r["repair_job_id"],
                        "changed_slide_ids": r["changed_slide_ids"] or None,
                        "pptx_hash": r["pptx_hash"],
                        "artifacts_prefix": r["artifacts_prefix"],
                    }
                )
                for r in revs
            ],
            "artifacts": artifacts,
            "stages": _variant_stages(row),
        }
        if row.get("slide_count") is not None:
            variant["slide_count"] = row["slide_count"]
        if current:
            prefix = current["artifacts_prefix"]
            if f"{prefix}plan.json" in current["manifest"]:
                variant["plan_artifact"] = f"{prefix}plan.json"
            if f"{prefix}composed.json" in current["manifest"]:
                variant["composed_deck_artifact"] = f"{prefix}composed.json"
        if row.get("ready_at"):
            variant["ready_at"] = row["ready_at"]
        if row.get("audited_at"):
            variant["audited_at"] = row["audited_at"]
        if audit:
            variant["audit"] = audit
            if audit.get("report_artifact"):
                variant["audit_artifact"] = audit["report_artifact"]
        elif row["status"] in {"pending", "running"}:
            variant["audit"] = {
                "status": "pending",
                "coverage_complete": False,
                "issues_total": 0,
                "blocking": 0,
            }
        if row.get("error"):
            variant["error"] = row["error"]
        variants.append(variant)

    status = job["status"]
    stage = job["stage"]
    created = job["created_at"]
    finished = job["finished_at"]
    ready = [v["ready_at"] for v in variants_rows if v.get("ready_at")]
    audited = [v["audited_at"] for v in variants_rows if v.get("audited_at")]
    failed = [v for v in variants_rows if v["status"] == "failed"]
    all_ready = len(ready) + len(failed) == len(variants_rows) and bool(variants_rows)
    percent = _percent(job, variants_rows)
    timeline: JsonDict = {"accepted_at": created}
    if ready:
        timeline["first_file_ready_ms"] = _ms_between(created, min(ready))
    if audited:
        timeline["first_variant_audited_ms"] = _ms_between(created, min(audited))
    if all_ready and ready:
        timeline["all_variants_ready_ms"] = _ms_between(created, max(ready))
    if status in TERMINAL and audited:
        timeline["all_variants_audited_ms"] = _ms_between(created, max(audited))
    end = finished or _now_iso()
    metrics: JsonDict = {
        "stages": job["stages"],
        "llm_calls": [],
        "totals": {
            "duration_ms": _ms_between(created, end) or 0,
            "llm_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        },
        "queue_wait_ms": job.get("queue_wait_ms") or 0,
        "quota_wait_ms": 0,
        "retries": 0,
        "cache": {
            "llm_hits": 0,
            "llm_misses": 0,
            "profile_hit": any(
                s.get("stage") == "analyze" and s.get("cache_hit") for s in job["stages"]
            ),
            "story_hit": bool(gen["story_hit"]),
            "render_hits": 0,
        },
        "timeline": timeline,
    }
    result: JsonDict = {
        "schema_version": "1.1",
        "job_id": job_id,
        "status": status,
        "stage": stage,
        "progress": {
            "percent": percent,
            "message": (job.get("progress") or {}).get(
                "message", _progress_message(stage, variants_rows)
            ),
        },
        "template_id": gen["template_id"],
        "package_id": gen["package_id"],
        "request": gen["request"],
        "created_at": created,
        "variants": variants,
        "artifacts_manifest": manifest,
        "metrics": metrics,
        "versions": gen["versions"],
        "execution_mode": gen["execution_mode"],
        "partial": bool(failed) and len(failed) < len(variants_rows),
        "repairs": [
            {
                "repair_job_id": r["repair_job_id"],
                "variant_id": r["variant_id"],
                "issue_ids": r["issue_ids"],
                "result": r["result"] or "skipped",
                "base_revision": r["base_revision"],
                **({"new_revision": r["new_revision"]} if r["new_revision"] else {}),
                **({"message": r["message"]} if r["message"] else {}),
                **({"changed_slide_ids": r["changed_slide_ids"]} if r["changed_slide_ids"] else {}),
            }
            for r in repairs
            if r["result"]
        ],
        "warnings": gen["warnings"],
    }
    if finished:
        result["finished_at"] = finished
    if gen.get("story"):
        result["story_id"] = gen["story"].get("story_id")
    if job.get("error"):
        result["error"] = job["error"]
    return result


def _variant_stages(row: JsonDict) -> list[JsonDict]:
    known = {s["stage"]: s for s in row.get("stages", [])}
    out: list[JsonDict] = []
    for name in VARIANT_STAGES:
        if name in known:
            out.append(_clean({**known[name], "variant_id": row["variant_id"]}))
        else:
            status = "skipped" if row["status"] == "failed" else "pending"
            out.append({"stage": name, "variant_id": row["variant_id"], "status": status})
    return out


def _percent(job: JsonDict, variants: list[JsonDict]) -> int:
    if job["status"] in TERMINAL:
        return 100
    stages_done = {s["stage"] for s in job["stages"] if s.get("status") == "done"}
    base = 5 if job["started_at"] else 0
    if "story" in stages_done:
        base = 20
    if not variants:
        return base
    per_variant = 80 / len(variants)
    total = float(base)
    for v in variants:
        done = sum(1 for s in v.get("stages", []) if s.get("status") == "done")
        if v["status"] == "failed":
            done = len(VARIANT_STAGES)
        total += per_variant * min(done, len(VARIANT_STAGES)) / len(VARIANT_STAGES)
    return max(0, min(99, int(total)))


def _progress_message(stage: str, variants: list[JsonDict]) -> str:
    ready = sum(1 for v in variants if v.get("ready_at"))
    audited = sum(1 for v in variants if v.get("audited_at"))
    if stage == "done":
        return f"Готово: {len(variants)} вариантов, аудит завершён у {audited}"
    if stage == "queued":
        return "Задание в очереди"
    if stage == "story":
        return "Строится общий смысловой план"
    return f"Вариантов с файлами: {ready} из {len(variants)}; аудит завершён у {audited}"


def build_job_status(state: State, job_id: str) -> JsonDict:
    job = state.get_job(job_id)
    status: JsonDict = {
        "schema_version": "1.1",
        "job_id": job_id,
        "kind": job["kind"],
        "status": job["status"],
        "stage": job["stage"],
        "stages": job["stages"],
        "created_at": job["created_at"],
        "result": job["result"],
    }
    if job["kind"] == "generation":
        gen = state.get_generation(job_id)
        variants = state.get_variants(job_id)
        status["progress"] = {
            "percent": _percent(job, variants),
            "message": (job.get("progress") or {}).get(
                "message", _progress_message(job["stage"], variants)
            ),
        }
        status["depends_on"] = job["depends_on"]
        status["result"] = {
            "template_id": gen["template_id"],
            "package_id": gen["package_id"],
            "generation_result_url": f"/api/generations/{job_id}",
        }
    elif job.get("progress"):
        status["progress"] = job["progress"]
    for key in ("started_at", "finished_at", "queue_wait_ms", "parent_job_id", "error"):
        if job.get(key) is not None:
            status[key] = job[key]
    return status


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def result_or_none(state: State, job_id: str) -> JsonDict | None:
    try:
        return build_generation_result(state, job_id)
    except Exception:
        return None


def template_detail(state: State, template: JsonDict) -> JsonDict:
    status = template["status"]
    detail: JsonDict = {
        "status": status,
        "job_id": template["job_id"],
        "name": template["name"],
        "previews": template["previews"] if status == "succeeded" else [],
    }
    if status == "succeeded" and template.get("profile"):
        detail["profile"] = template["profile"]
    if template.get("error"):
        detail["error"] = template["error"]
    return detail


def package_detail(package: JsonDict) -> JsonDict:
    detail: JsonDict = {"status": package["status"], "job_id": package["job_id"]}
    if package["status"] == "succeeded" and package.get("package"):
        detail["package"] = package["package"]
    if package.get("error"):
        detail["error"] = package["error"]
    return detail


def any_value(values: list[Any]) -> Any:
    return next((v for v in values if v), None)
