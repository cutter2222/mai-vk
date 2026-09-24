"""Сборка документов GenerationResult и JobStatus из состояния SQLite и манифестов ревизий.

Документы собираются при чтении: состояние хранится по частям (задание, генерация,
варианты, ревизии, исправления и правки слайдов), а контракт отдаёт единый снимок на
любом этапе.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from presentation_designer.pipeline.state import TERMINAL, JsonDict, NotFound, State
from presentation_designer.shared.text import plural

VARIANT_STAGES = ["plan", "compose", "export", "audit"]

# Разбор готовой презентации в фоне («Открыть как презентацию»): сколько ждать плана варианта
# original, после которого доступны правки из чата. Оценки по замерам на izbox.ru 23–24.09:
# анализ шаблона — около 1,5 с на слайд (VK Education: 82 с на 54 слайда), импорт содержания —
# около 0,8 с на слайд (42 с), фон копии (подписи, диаграммы и рендер) — 27–35 с на 55 слайдов,
# полная сборка варианта без контекстного аудита — 7–8 с, правка слайда моделью — 23–29 с.
ANALYZE_S_PER_SLIDE = 1.5
IMPORT_S_PER_SLIDE = 0.8
PREVIEW_S = 10.0
PREVIEW_S_PER_SLIDE = 0.45
ORIGINAL_BUILD_S = 5.0
ORIGINAL_BUILD_S_PER_SLIDE = 0.06
EDIT_S = 25.0
# Задача разбора, которой ничего не мешает, начинается за секунду. Дольше — все воркеры
# генерации заняты чужими сборками, и срок не оценить.
QUEUE_GRACE_S = 8.0


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
    repairs = state.list_repairs(job_id, kind="repair")
    edits = state.list_repairs(job_id, kind=("edit", "patch"))
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
    progress = _progress(state, job, variants_rows)
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
    # Вызовы модели копятся в строке задания по мере завершения этапов (State.add_llm_usage).
    usage = job.get("metrics") or {}
    usage_totals = usage.get("totals") or {}
    usage_cache = usage.get("cache") or {}
    totals: JsonDict = {
        "duration_ms": _ms_between(created, end) or 0,
        "llm_calls": int(usage_totals.get("llm_calls") or 0),
        "prompt_tokens": int(usage_totals.get("prompt_tokens") or 0),
        "completion_tokens": int(usage_totals.get("completion_tokens") or 0),
    }
    if usage_totals.get("usage_estimated"):
        totals["usage_estimated"] = True
    metrics: JsonDict = {
        "stages": job["stages"],
        "llm_calls": list(usage.get("llm_calls") or []),
        "totals": totals,
        "queue_wait_ms": job.get("queue_wait_ms") or 0,
        "quota_wait_ms": int(usage.get("quota_wait_ms") or 0),
        "retries": int(usage.get("retries") or 0),
        "cache": {
            "llm_hits": int(usage_cache.get("llm_hits") or 0),
            "llm_misses": int(usage_cache.get("llm_misses") or 0),
            "profile_hit": any(
                s.get("stage") == "analyze" and s.get("cache_hit") for s in job["stages"]
            ),
            "story_hit": bool(gen["story_hit"]),
            "render_hits": 0,
        },
        "timeline": timeline,
    }
    result: JsonDict = {
        "schema_version": "1.3",
        "job_id": job_id,
        "status": status,
        "stage": stage,
        "progress": progress,
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
        "edits": [
            _clean(
                {
                    "edit_job_id": e["repair_job_id"],
                    "variant_id": e["variant_id"],
                    "base_revision": e["base_revision"],
                    "slide_index": int(e["slide_index"] or 0),
                    "slide_id": e["slide_id"],
                    # У ручных правок инструкции нет: в поле идёт сводка, как и в summary.
                    "instruction": e["instruction"] or e["change_note"] or "",
                    "origin": "editor" if e["kind"] == "patch" else "chat",
                    "summary": e["change_note"] if e["kind"] == "patch" else None,
                    "result": e["result"],
                    "change_note": e["change_note"],
                    "message": e["message"],
                    "new_revision": e["new_revision"],
                    "changed_slide_ids": e["changed_slide_ids"] or None,
                }
            )
            for e in edits
            if e["result"]
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


def _progress(state: State, job: JsonDict, variants: list[JsonDict]) -> JsonDict:
    """Ход задания: процент по этапам и последняя фраза задачи. Готовая презентация после
    показа говорит о разборе в фоне — когда станут доступны правки из чата."""
    deck = _deck_progress(state, job, variants)
    if deck is not None:
        return deck
    return {
        "percent": _percent(job, variants),
        "message": (job.get("progress") or {}).get(
            "message", _progress_message(job["stage"], variants)
        ),
    }


def _deck_progress(state: State, job: JsonDict, variants: list[JsonDict]) -> JsonDict | None:
    if job["status"] in TERMINAL or len(variants) != 1:
        return None
    variant = variants[0]
    if variant["variant_id"] != "original" or not variant.get("ready_at"):
        return None
    eta = original_eta_s(state, job, variants)
    if eta is None:
        return {
            "percent": max(_percent(job, variants), 90),
            "message": "Презентация разобрана: правки из чата доступны",
        }
    if deck_queued(state, job, variants):
        return {
            "percent": max(5, _percent(job, variants)),
            "message": "Разбираю презентацию в фоне: очередь занята другими сборками, "
            "правки из чата — сразу после разбора",
        }
    elapsed = _since_s(job["created_at"], datetime.now(UTC))
    percent = int(100 * elapsed / max(1.0, elapsed + eta))
    return {
        "percent": max(5, min(95, percent)),
        "message": f"Разбираю презентацию в фоне: правки из чата — через {eta_phrase(eta)}",
    }


def deck_queued(state: State, job: JsonDict, variants: list[JsonDict]) -> bool:
    """Задачи разбора готовой презентации стоят в очереди generation за чужими сборками: всё,
    чего они ждали, готово, а начаться они не могут дольше `QUEUE_GRACE_S`."""
    if job["status"] in TERMINAL or len(variants) != 1:
        return False
    variant = variants[0]
    if variant["status"] not in {"pending", "running"} or variant.get("stages"):
        return False
    now = datetime.now(UTC)
    done = {s["stage"]: s for s in job.get("stages") or [] if s.get("status") == "done"}
    if "story" not in done:
        if job["stage"] == "story":  # смысловой план строится
            return False
        try:
            gen = state.get_generation(job["job_id"])
            owners = [
                state.get_template(gen["template_id"]),
                state.get_package(gen["package_id"]),
            ]
            finished = [state.get_job(o["job_id"]).get("finished_at") for o in owners]
        except NotFound:
            return False
        if any(o["status"] not in TERMINAL for o in owners):
            return False  # ещё идут разбор шаблона или импорт: это срок, а не очередь
        since = max([job["created_at"], *[f for f in finished if f]])
        return _since_s(since, now) > QUEUE_GRACE_S
    # План построен, вариант не начат: ждёт ли он ещё фон копии (рендер последней ревизии)?
    revisions = state.list_revisions(job["job_id"], variant["variant_id"])
    latest = revisions[-1] if revisions else None
    if latest is None or f"{latest['artifacts_prefix']}deck.pdf" not in latest["manifest"]:
        return False
    story = done["story"]
    story_end = _parse(story.get("started_at"))
    if story_end is None:
        return False
    ended = story_end.timestamp() + (story.get("duration_ms") or 0) / 1000
    return now.timestamp() - ended > QUEUE_GRACE_S


def original_eta_s(state: State, job: JsonDict, variants: list[JsonDict]) -> float | None:
    """Сколько секунд ещё ждать плана готовой презентации (правки из чата работают по плану);
    None — ждать нечего: план есть, задание закончилось или это не готовая презентация."""
    if job["status"] in TERMINAL or len(variants) != 1:
        return None
    variant = variants[0]
    if variant["variant_id"] != "original" or variant["status"] not in {"pending", "running"}:
        return None
    now = datetime.now(UTC)
    slides = int(variant.get("slide_count") or 0) or 20
    build = ORIGINAL_BUILD_S + ORIGINAL_BUILD_S_PER_SLIDE * slides
    started = [s.get("started_at") for s in variant.get("stages") or [] if s.get("started_at")]
    if started:  # сборка варианта уже идёт
        return max(3.0, build - _since_s(min(started), now))
    # Фон копии (подписи, диаграммы, рендер) идёт с публикации r1; сборка варианта ждёт его.
    wait = max(0.0, PREVIEW_S + PREVIEW_S_PER_SLIDE * slides - _since_s(job["created_at"], now))
    try:
        gen = state.get_generation(job["job_id"])
        template = state.get_template(gen["template_id"])
        package = state.get_package(gen["package_id"])
    except NotFound:
        return build
    for status, owner, per_slide in (
        (template["status"], template["job_id"], ANALYZE_S_PER_SLIDE),
        (package["status"], package["job_id"], IMPORT_S_PER_SLIDE),
    ):
        if status in TERMINAL:
            continue
        expected = 10.0 + per_slide * slides
        try:
            begun = state.get_job(owner).get("started_at")
        except NotFound:
            begun = None
        left = expected - _since_s(begun, now) if begun else expected + 10.0
        wait = max(wait, left, 5.0)
    return wait + build


def deferred_edit_message(state: State, job_id: str) -> str:
    """Правка из чата ждёт разбора готовой презентации: когда её применят. Если разбор стоит в
    очереди за чужими сборками, срока нет — так и говорится."""
    try:
        job = state.get_job(job_id)
        variants = state.get_variants(job_id)
        eta = original_eta_s(state, job, variants) or 0.0
        queued = deck_queued(state, job, variants)
    except NotFound:
        eta, queued = 0.0, False
    if queued:
        return (
            "Разбираю презентацию: очередь занята другими сборками, правку применю сразу после "
            "разбора"
        )
    return f"Разбираю презентацию, правку применю через {eta_phrase(eta + EDIT_S)}"


def eta_phrase(seconds: float) -> str:
    """43 → «~45 с», 100 → «~1,5 мин», 150 → «~2,5 мин»: оценка, а не обещание."""
    if seconds < 60:
        return f"~{max(5, int(-(-seconds // 5) * 5))} с"
    halves = max(2, round(seconds / 30))
    whole, half = divmod(halves, 2)
    return f"~{whole}{',5' if half else ''} мин"


def _since_s(ts: str | None, now: datetime) -> float:
    started = _parse(ts)
    return max(0.0, (now - started).total_seconds()) if started else 0.0


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
        word = plural(len(variants), "вариант", "варианта", "вариантов")
        return f"Готово: {len(variants)} {word}, аудит завершён у {audited}"
    if stage == "queued":
        return "Задание в очереди"
    if stage == "story":
        return "Строится общий смысловой план"
    # Первый вариант собирается один, остальные — за ним в фоне: строка говорит о том же.
    if ready == 0:
        return "Собираю первый вариант" if len(variants) > 1 else "Собираю презентацию"
    if ready < len(variants):
        rest = len(variants) - ready
        word = plural(rest, "вариант собирается", "варианта собираются", "вариантов собираются")
        return f"Готово {ready} из {len(variants)}, ещё {rest} {word} в фоне"
    return f"Все варианты готовы, аудит завершён у {audited} из {len(variants)}"


def build_job_status(state: State, job_id: str) -> JsonDict:
    job = state.get_job(job_id)
    status: JsonDict = {
        "schema_version": "1.3",
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
        status["progress"] = _progress(state, job, variants)
        status["depends_on"] = job["depends_on"]
        status["result"] = {
            "template_id": gen["template_id"],
            "package_id": gen["package_id"],
            "generation_result_url": f"/api/generations/{job_id}",
        }
    elif job.get("progress"):
        status["progress"] = job["progress"]
        if job["kind"] == "slide_edit" and job["status"] == "queued" and job.get("depends_on"):
            # Правка ждёт разбора готовой презентации: срок пересчитывается при каждом опросе.
            status["progress"] = {
                "percent": 0,
                "message": deferred_edit_message(state, str(job["parent_job_id"])),
            }
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
    # Время анализа — из задания: карточка шаблона показывает, сколько заняло чтение образцов.
    try:
        job = state.get_job(template["job_id"])
    except NotFound:
        job = None
    if job is not None:
        timing = {k: job.get(k) for k in ("created_at", "started_at", "finished_at") if job.get(k)}
        stage = next((s for s in job.get("stages") or [] if s.get("stage") == "analyze"), None)
        if stage and stage.get("duration_ms") is not None:
            timing["duration_ms"] = stage["duration_ms"]
        if timing:
            detail["timing"] = timing
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
