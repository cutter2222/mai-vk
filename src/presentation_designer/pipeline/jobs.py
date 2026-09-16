"""Задания и очередь: постановка, зависимости, задачи воркеров, отмена, сверка.

Граф генерации: анализ шаблона идёт параллельно импорту содержания; смысловой план ждёт
импорт; варианты ждут план и анализ; финализатор ждёт все варианты и не занимает воркер
в ожидании — зависимости планирует очередь. Отказ варианта не отменяет остальные
(`Dependency(allow_failure=True)`), состояние всегда читается из SQLite, а не из RQ.

Исполнитель RQ работает поверх Valkey; встроенный исполнитель выполняет задачи сразу
и служит тестам и CLI. Функции задач принимают только идентификаторы и достают
оркестратор процесса через `get_orchestrator()`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import socket
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from presentation_designer import __version__
from presentation_designer.contracts import CONTRACTS_VERSION
from presentation_designer.pipeline.artifacts import ArtifactStore
from presentation_designer.pipeline.files import FileStore, format_for
from presentation_designer.pipeline.run import (
    VARIANT_AXIS,
    AnalyzeInput,
    ImportInput,
    Layers,
    RepairInput,
    SourceFile,
    StoryInput,
    VariantContext,
    audit_summary,
    run_repair,
    run_variant,
)
from presentation_designer.pipeline.state import (
    TERMINAL,
    JsonDict,
    NotFound,
    State,
    new_id,
    now_iso,
)
from presentation_designer.shared.settings import Settings, get_settings

log = logging.getLogger(__name__)

STAGE_ORDER = [
    "queued",
    "analyze",
    "import",
    "story",
    "plan",
    "compose",
    "export",
    "audit",
    "repair",
    "finalize",
    "done",
]


class ConflictError(ValueError):
    def __init__(self, code: str, message: str, details: JsonDict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


# ---------- исполнители ----------


class Executor(Protocol):
    name: str

    def enqueue(
        self,
        queue: str,
        func: Callable[..., Any],
        args: tuple[Any, ...],
        *,
        depends_on: list[str],
        timeout: int,
        description: str,
    ) -> str: ...

    def status(self, rq_id: str) -> str | None: ...

    def workers(self) -> dict[str, int]: ...

    def renderer_ok(self) -> bool | None: ...

    def ping(self) -> bool: ...


class InlineExecutor:
    """Выполняет задачу сразу при постановке. Порядок постановки повторяет порядок зависимостей."""

    name = "inline"

    def __init__(self) -> None:
        self.done: dict[str, str] = {}

    def enqueue(
        self,
        queue: str,
        func: Callable[..., Any],
        args: tuple[Any, ...],
        *,
        depends_on: list[str],
        timeout: int,
        description: str,
    ) -> str:
        rq_id = new_id("inl")
        try:
            func(*args)
            self.done[rq_id] = "finished"
        except Exception:
            log.exception("задача %s завершилась ошибкой", description)
            self.done[rq_id] = "failed"
        return rq_id

    def status(self, rq_id: str) -> str | None:
        return self.done.get(rq_id)

    def workers(self) -> dict[str, int]:
        return {"analysis": 1, "generation": 1}

    def renderer_ok(self) -> bool | None:
        return None

    def ping(self) -> bool:
        return True


class RQExecutor:
    name = "rq"

    def __init__(self, url: str, settings: Settings) -> None:
        import redis
        from rq import Queue

        self.settings = settings
        self.redis = redis.Redis.from_url(url)
        self._queues: dict[str, Queue] = {}
        self._queue_cls = Queue

    def queue(self, name: str) -> Any:
        if name not in self._queues:
            self._queues[name] = self._queue_cls(name, connection=self.redis)
        return self._queues[name]

    def enqueue(
        self,
        queue: str,
        func: Callable[..., Any],
        args: tuple[Any, ...],
        *,
        depends_on: list[str],
        timeout: int,
        description: str,
    ) -> str:
        from rq.job import Dependency

        q = self.settings.queue
        kwargs: dict[str, Any] = {
            "args": args,
            "job_timeout": timeout,
            "result_ttl": q.result_ttl_s,
            "failure_ttl": q.failure_ttl_s,
            "description": description,
        }
        if depends_on:
            kwargs["depends_on"] = Dependency(jobs=list(depends_on), allow_failure=True)
        job = self.queue(queue).enqueue(func, **kwargs)
        return str(job.id)

    def status(self, rq_id: str) -> str | None:
        from rq.exceptions import NoSuchJobError
        from rq.job import Job

        try:
            job = Job.fetch(rq_id, connection=self.redis)
        except NoSuchJobError:
            return None
        status = job.get_status(refresh=False)
        return str(getattr(status, "value", status))

    def workers(self) -> dict[str, int]:
        from rq import Worker

        counts = {"analysis": 0, "generation": 0}
        try:
            for worker in Worker.all(connection=self.redis):
                names = set(worker.queue_names())
                if self.settings.queue.generation_queue in names:
                    counts["generation"] += 1
                if self.settings.queue.analysis_queue in names:
                    counts["analysis"] += 1
        except Exception:
            log.exception("не удалось получить список воркеров")
        return counts

    def renderer_ok(self) -> bool | None:
        """Все живые воркеры генерации прошли проверку рендерера при старте."""
        from rq import Worker

        try:
            statuses: list[bytes | None] = []
            for worker in Worker.all(connection=self.redis):
                if self.settings.queue.generation_queue in set(worker.queue_names()):
                    statuses.append(self.redis.get(f"pd:renderer:{worker.name}"))
        except Exception:
            return None
        if not statuses:
            return None
        return all(s == b"ok" for s in statuses)

    def ping(self) -> bool:
        try:
            return bool(self.redis.ping())
        except Exception:
            return False


# ---------- оркестратор ----------


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        state: State,
        files: FileStore,
        artifacts: ArtifactStore,
        layers: Layers,
        executor: Executor,
    ) -> None:
        self.settings = settings
        self.state = state
        self.files = files
        self.artifacts = artifacts
        self.layers = layers
        self.executor = executor
        set_current(self)

    # ----- шаблоны -----

    def submit_template(self, *, sha256: str, name: str, size_bytes: int) -> tuple[JsonDict, bool]:
        job_id = new_id("job")
        template, cached = self.state.get_or_create_template(
            sha256=sha256, name=name, size_bytes=size_bytes, job_id=job_id
        )
        if cached:
            if template["status"] == "failed":
                self.state.update_template(
                    template["template_id"], status="queued", error=None, job_id=job_id
                )
                template["job_id"] = job_id
                cached = False
            else:
                return template, True
        job = self.state.create_job(
            kind="template_analysis",
            job_id=job_id,
            stage="analyze",
            result={
                "template_id": template["template_id"],
                "template_profile_url": f"/api/templates/{template['template_id']}",
            },
            progress={"percent": 0, "message": "Анализ образцов шаблона"},
        )
        rq_id = self.executor.enqueue(
            self.settings.queue.analysis_queue,
            task_analyze,
            (template["template_id"],),
            depends_on=[],
            timeout=self.settings.timeouts.stage_analyze_s,
            description=f"analyze {template['template_id']}",
        )
        self.state.update_job(job["job_id"], rq_ids=[rq_id])
        return template, cached

    # ----- содержание -----

    def submit_package(
        self, *, sources: list[JsonDict], brief: JsonDict | None
    ) -> tuple[JsonDict, bool]:
        """sources: записи файлов проекта (file_id, name, sha256, size_bytes, check)."""
        file_ids = [s["file_id"] for s in sources]
        mode = "mixed" if sources and brief else "brief" if brief else "package"
        key_src = json.dumps(
            {"files": sorted(s["sha256"] for s in sources), "brief": brief or {}},
            sort_keys=True,
            ensure_ascii=False,
        )
        idem_key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()
        job_id = new_id("job")
        package, cached = self.state.get_or_create_package(
            idem_key=idem_key, mode=mode, file_ids=file_ids, brief=brief, job_id=job_id
        )
        if cached:
            return package, True
        self.state.create_job(
            kind="content_import",
            job_id=job_id,
            stage="import",
            result={
                "package_id": package["package_id"],
                "content_package_url": f"/api/content/{package['package_id']}",
            },
            progress={"percent": 0, "message": "Извлечение блоков и фактов"},
        )
        rq_id = self.executor.enqueue(
            self.settings.queue.generation_queue,
            task_import,
            (package["package_id"],),
            depends_on=[],
            timeout=self.settings.timeouts.stage_story_s,
            description=f"import {package['package_id']}",
        )
        self.state.update_job(job_id, rq_ids=[rq_id])
        return package, False

    # ----- генерация -----

    def submit_generation(self, request: JsonDict) -> JsonDict:
        key = request.get("idempotency_key")
        if key:
            same = self.state.find_generation_by_key(key)
            if same:
                return self.state.get_job(same["job_id"])
        template = self.state.get_template(request["template_id"])
        package = self.state.get_package(request["package_id"])
        settings = request.get("settings") or {}
        variant_ids = list(settings.get("variants") or ["compact", "balanced", "detailed"])
        job_id = new_id("job")
        deadline = (
            datetime.now(UTC) + timedelta(seconds=self.settings.timeouts.job_total_s)
        ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        depends_on = [template["job_id"], package["job_id"]]
        self.state.create_job(
            kind="generation",
            job_id=job_id,
            stage="queued",
            deadline_at=deadline,
            depends_on=depends_on,
            result={
                "template_id": template["template_id"],
                "package_id": package["package_id"],
                "generation_result_url": f"/api/generations/{job_id}",
            },
            progress={"percent": 0, "message": "Задание в очереди"},
        )
        self.state.create_generation(
            job_id=job_id,
            template_id=template["template_id"],
            package_id=package["package_id"],
            request=request,
            idempotency_key=key,
            execution_mode=self.layers.execution_mode(),
            versions=self.versions(),
            variants=[
                {
                    "variant_id": v,
                    "axis": VARIANT_AXIS.get(v, ("density", v))[0],
                    "value": VARIANT_AXIS.get(v, ("density", v))[1],
                }
                for v in variant_ids
            ],
        )
        self.enqueue_generation(job_id, template, package, variant_ids)
        return self.state.get_job(job_id)

    def enqueue_generation(
        self, job_id: str, template: JsonDict, package: JsonDict, variant_ids: list[str]
    ) -> None:
        q = self.settings.queue
        t = self.settings.timeouts
        rq_ids: list[str] = []
        deps_story = self._pending_rq_ids(package["job_id"])
        story_id = self.executor.enqueue(
            q.generation_queue,
            task_story,
            (job_id,),
            depends_on=deps_story,
            timeout=t.stage_story_s,
            description=f"story {job_id}",
        )
        rq_ids.append(story_id)
        deps_variant = [story_id, *self._pending_rq_ids(template["job_id"])]
        variant_rq: list[str] = []
        for variant_id in variant_ids:
            vid = self.executor.enqueue(
                q.generation_queue,
                task_variant,
                (job_id, variant_id),
                depends_on=deps_variant,
                timeout=t.stage_plan_s + t.stage_compose_s + t.stage_export_s + t.stage_audit_s,
                description=f"variant {job_id} {variant_id}",
            )
            variant_rq.append(vid)
        rq_ids.extend(variant_rq)
        fin = self.executor.enqueue(
            q.generation_queue,
            task_finalize,
            (job_id,),
            depends_on=variant_rq,
            timeout=60,
            description=f"finalize {job_id}",
        )
        rq_ids.append(fin)
        self.state.update_job(job_id, rq_ids=rq_ids)

    def _pending_rq_ids(self, job_id: str) -> list[str]:
        """RQ-идентификаторы незавершённого задания: завершённые не нужны как зависимости."""
        try:
            job = self.state.get_job(job_id)
        except NotFound:
            return []
        if job["status"] in TERMINAL:
            return []
        return [rid for rid in job["rq_ids"] if self.executor.status(rid) is not None]

    def cancel_job(self, job_id: str) -> JsonDict:
        job = self.state.get_job(job_id)
        if job["status"] in TERMINAL:
            return job
        ts = now_iso()
        if job["kind"] == "generation":
            self.state.update_generation(job_id, canceled=True)
            for variant in self.state.get_variants(job_id):
                if variant["status"] in {"pending", "running"}:
                    self.state.update_variant(
                        job_id,
                        variant["variant_id"],
                        status="failed",
                        error={"code": "canceled", "message": "Задание отменено пользователем"},
                    )
        self.state.update_job(
            job_id,
            status="canceled",
            stage="done",
            finished_at=ts,
            error={"code": "canceled", "message": "Задание отменено пользователем"},
        )
        return self.state.get_job(job_id)

    def retry_job(self, job_id: str) -> JsonDict:
        job = self.state.get_job(job_id)
        if job["kind"] != "generation":
            raise ConflictError("retry_unsupported", "Повтор доступен только для заданий генерации")
        gen = self.state.get_generation(job_id)
        request = {k: v for k, v in gen["request"].items() if k != "idempotency_key"}
        return self.submit_generation(request)

    # ----- исправления -----

    def submit_repair(
        self, job_id: str, variant_id: str, base_revision: int, issue_ids: list[str]
    ) -> JsonDict:
        variant = self.state.get_variant(job_id, variant_id)
        if variant["revision"] != base_revision:
            raise ConflictError(
                "revision_stale",
                f"Ревизия {base_revision} устарела: текущая ревизия {variant['revision']}. Обновите отчёт и выберите находки заново.",  # noqa: E501
                {"current_revision": variant["revision"]},
            )
        if variant["status"] == "failed":
            raise ConflictError("variant_failed", "Вариант не собран, исправлять нечего")
        active = self.state.active_repair(job_id, variant_id)
        if active:
            raise ConflictError(
                "repair_in_progress",
                "Исправление этой ревизии уже выполняется",
                {"repair_job_id": active["repair_job_id"]},
            )
        repair_id = new_id("rep")
        self.state.create_job(
            kind="repair",
            job_id=repair_id,
            stage="repair",
            parent_job_id=job_id,
            result={"generation_result_url": f"/api/generations/{job_id}"},
            progress={"percent": 0, "message": "Исправление выбранных находок"},
        )
        self.state.create_repair(
            repair_job_id=repair_id,
            job_id=job_id,
            variant_id=variant_id,
            base_revision=base_revision,
            issue_ids=issue_ids,
        )
        rq_id = self.executor.enqueue(
            self.settings.queue.repair_queue,
            task_repair,
            (repair_id,),
            depends_on=[],
            timeout=self.settings.timeouts.stage_compose_s
            + self.settings.timeouts.stage_export_s
            + self.settings.timeouts.stage_audit_s,
            description=f"repair {repair_id}",
        )
        self.state.update_job(repair_id, rq_ids=[rq_id])
        return self.state.get_job(repair_id)

    # ----- служебное -----

    def versions(self) -> JsonDict:
        return {
            "app": __version__,
            "contracts": CONTRACTS_VERSION,
            "skills": [],
            "prompts": [],
            "models": [],
            "renderer": {"name": "stub", "version": __version__},
        }

    def reconcile(self) -> list[str]:
        """Задания без живого исполнителя и с истёкшим сроком помечаются ошибкой;
        возвращает их идентификаторы."""
        touched: list[str] = []
        now = datetime.now(UTC)
        for job in self.state.list_active_jobs():
            reason: JsonDict | None = None
            if (
                job["deadline_at"]
                and datetime.fromisoformat(job["deadline_at"].replace("Z", "+00:00")) < now
            ):
                reason = {
                    "code": "deadline_exceeded",
                    "message": "Общий срок задания истёк",
                    "retryable": True,
                }
            elif job["rq_ids"]:
                statuses = [self.executor.status(rid) for rid in job["rq_ids"]]
                if all(s in (None, "failed", "stopped", "canceled") for s in statuses):
                    reason = {
                        "code": "worker_lost",
                        "message": "Исполнитель задания потерян; повторите запуск",
                        "retryable": True,
                    }
            if reason is None:
                continue
            ts = now_iso()
            if job["kind"] == "generation":
                for variant in self.state.get_variants(job["job_id"]):
                    if variant["status"] in {"pending", "running"}:
                        self.state.update_variant(
                            job["job_id"], variant["variant_id"], status="failed", error=reason
                        )
            self.state.update_job(
                job["job_id"], status="failed", stage="done", finished_at=ts, error=reason
            )
            touched.append(job["job_id"])
        return touched

    def is_canceled(self, job_id: str) -> bool:
        try:
            return bool(self.state.get_generation(job_id)["canceled"])
        except NotFound:
            return True


_current: Orchestrator | None = None
_lock = threading.Lock()


def set_current(orchestrator: Orchestrator) -> None:
    global _current
    _current = orchestrator


def build_orchestrator(
    settings: Settings | None = None, executor: Executor | None = None
) -> Orchestrator:
    """Собирает оркестратор из настроек: SQLite, хранилища и заглушечные слои."""
    from presentation_designer.pipeline.stubs import StubLayers

    settings = settings or get_settings()
    state = State(settings.db_path)
    files = FileStore(
        settings.uploads_dir,
        max_upload_mb=settings.limits.max_upload_mb,
        max_unzipped_mb=settings.limits.max_unzipped_mb,
    )
    artifacts = ArtifactStore(settings.artifacts_dir)
    layers = StubLayers(stage_delay_ms=settings.execution.stub_stage_delay_ms)
    if executor is None:
        import os

        mode = os.environ.get("PD_QUEUE_MODE", "rq")
        executor = (
            InlineExecutor()
            if mode == "inline"
            else RQExecutor(os.environ.get("PD_VALKEY_URL", "redis://localhost:6379/0"), settings)
        )
    return Orchestrator(settings, state, files, artifacts, layers, executor)


def get_orchestrator() -> Orchestrator:
    global _current
    with _lock:
        if _current is None:
            _current = build_orchestrator()
        return _current


# ---------- задачи воркеров ----------


def task_analyze(template_id: str) -> None:
    o = get_orchestrator()
    template = o.state.get_template(template_id)
    job_id = template["job_id"]
    o.state.job_started(job_id)
    o.state.update_template(template_id, status="running")
    started = now_iso()
    try:
        path = o.files.open(template["sha256"])
        out = o.layers.analyze(
            AnalyzeInput(
                template_id, template["sha256"], template["name"], template["size_bytes"], path
            )
        )
        with o.artifacts.stage_dir(o.artifacts.template_dir(template_id)) as tmp:
            for name, data in out.previews.items():
                target = tmp / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        previews = sorted(out.previews)
        o.state.update_template(
            template_id, status="succeeded", profile=out.profile, previews=previews
        )
        o.state.add_stage(
            job_id,
            {
                "stage": "analyze",
                "status": "done",
                "started_at": started,
                "duration_ms": _ms_since(started),
            },
        )
        o.state.update_job(
            job_id,
            status="succeeded",
            stage="done",
            finished_at=now_iso(),
            progress={
                "percent": 100,
                "message": f"Профиль готов: {len(out.profile.get('patterns', []))} паттернов",
            },
        )
    except Exception as e:
        log.exception("анализ шаблона %s не удался", template_id)
        error = {
            "code": "analyze_failed",
            "message": str(e) or e.__class__.__name__,
            "stage": "analyze",
            "retryable": True,
        }
        o.state.update_template(template_id, status="failed", error=error)
        o.state.add_stage(
            job_id,
            {
                "stage": "analyze",
                "status": "failed",
                "started_at": started,
                "duration_ms": _ms_since(started),
            },
        )
        o.state.update_job(
            job_id, status="failed", stage="done", finished_at=now_iso(), error=error
        )


def task_import(package_id: str) -> None:
    o = get_orchestrator()
    package = o.state.get_package(package_id)
    job_id = package["job_id"]
    o.state.job_started(job_id)
    o.state.update_package(package_id, status="running")
    started = now_iso()
    try:
        sources: list[SourceFile] = []
        for file_id in package["file_ids"]:
            row = o.state.get_file(file_id)
            fmt = row.get("check", {}).get("format") or format_for(row["name"])
            path = o.files.path_for(row["sha256"])
            sources.append(
                SourceFile(
                    file_id,
                    row["name"],
                    row["sha256"],
                    row["size_bytes"],
                    fmt,
                    path if path.is_file() else None,
                )
            )
        out = o.layers.import_content(
            ImportInput(package_id, package["mode"], sources, package["brief"])
        )
        o.state.update_package(package_id, status="succeeded", package=out.package)
        pk = out.package
        o.state.add_stage(
            job_id,
            {
                "stage": "import",
                "status": "done",
                "started_at": started,
                "duration_ms": _ms_since(started),
            },
        )
        message = f"Импорт готов: {len(pk.get('blocks', []))} блоков, {len(pk.get('facts', []))} фактов, {len(pk.get('datasets', []))} таблиц, {len(pk.get('assets', []))} изображений"  # noqa: E501
        o.state.update_job(
            job_id,
            status="succeeded",
            stage="done",
            finished_at=now_iso(),
            progress={"percent": 100, "message": message},
        )
    except Exception as e:
        log.exception("импорт пакета %s не удался", package_id)
        error = {
            "code": "import_failed",
            "message": str(e) or e.__class__.__name__,
            "stage": "import",
            "retryable": True,
        }
        o.state.update_package(package_id, status="failed", error=error)
        o.state.update_job(
            job_id, status="failed", stage="done", finished_at=now_iso(), error=error
        )


def _fail_generation(o: Orchestrator, job_id: str, error: JsonDict) -> None:
    for variant in o.state.get_variants(job_id):
        if variant["status"] in {"pending", "running"}:
            o.state.update_variant(job_id, variant["variant_id"], status="failed", error=error)
    o.state.update_job(job_id, status="failed", stage="done", finished_at=now_iso(), error=error)


def task_story(job_id: str) -> None:
    o = get_orchestrator()
    job = o.state.get_job(job_id)
    gen = o.state.get_generation(job_id)
    if gen["canceled"] or job["status"] in TERMINAL:
        return
    o.state.job_started(job_id)
    package = o.state.get_package(gen["package_id"])
    template = o.state.get_template(gen["template_id"])
    if package["status"] == "failed":
        _fail_generation(
            o,
            job_id,
            {
                "code": "import_failed",
                "message": "Импорт содержания завершился ошибкой",
                "stage": "import",
                "retryable": True,
            },
        )
        return
    if package["status"] != "succeeded" or package["package"] is None:
        _fail_generation(
            o,
            job_id,
            {
                "code": "package_not_ready",
                "message": "Контент-пакет ещё не импортирован",
                "stage": "import",
                "retryable": True,
            },
        )
        return
    import_job = o.state.get_job(package["job_id"])
    o.state.add_stage(
        job_id,
        {
            "stage": "import",
            "status": "done",
            "duration_ms": _duration(import_job),
            "cache_hit": import_job["created_at"] < job["created_at"],
        },
    )
    if template["status"] in TERMINAL:
        template_job = o.state.get_job(template["job_id"])
        o.state.add_stage(
            job_id,
            {
                "stage": "analyze",
                "status": "done" if template["status"] == "succeeded" else "failed",
                "duration_ms": _duration(template_job),
                "cache_hit": template_job["created_at"] < job["created_at"],
            },
        )
    o.state.update_job(
        job_id, stage="story", progress={"percent": 5, "message": "Строится общий смысловой план"}
    )
    started = now_iso()
    story = o.state.find_story(gen["package_id"])
    hit = story is not None
    try:
        if story is None:
            story = o.layers.story(
                StoryInput(package["package"], gen["request"].get("settings") or {})
            )
    except Exception as e:
        log.exception("смысловой план %s не удался", job_id)
        _fail_generation(
            o,
            job_id,
            {
                "code": "story_failed",
                "message": str(e) or e.__class__.__name__,
                "stage": "story",
                "retryable": True,
            },
        )
        return
    o.state.update_generation(job_id, story=story, story_hit=1 if hit else 0)
    o.state.add_stage(
        job_id,
        {
            "stage": "story",
            "status": "done",
            "started_at": started,
            "duration_ms": _ms_since(started),
            "cache_hit": hit,
        },
    )
    o.state.update_job(job_id, stage="plan", progress={"percent": 20, "message": "Планы вариантов"})


def task_variant(job_id: str, variant_id: str) -> None:
    o = get_orchestrator()
    job = o.state.get_job(job_id)
    gen = o.state.get_generation(job_id)
    if gen["canceled"] or job["status"] in TERMINAL:
        return
    template = o.state.get_template(gen["template_id"])
    if template["status"] != "succeeded" or template["profile"] is None:
        error = {
            "code": "template_failed",
            "message": "Анализ шаблона не завершился успешно",
            "stage": "analyze",
            "retryable": True,
        }
        o.state.update_variant(job_id, variant_id, status="failed", error=error)
        return
    if gen["story"] is None:
        o.state.update_variant(
            job_id,
            variant_id,
            status="failed",
            error={
                "code": "story_missing",
                "message": "Смысловой план не построен",
                "stage": "story",
                "retryable": True,
            },
        )
        return
    package = o.state.get_package(gen["package_id"])
    o.state.update_variant(job_id, variant_id, status="running")
    o.state.add_stage(job_id, {"stage": "analyze", "status": "done", "cache_hit": True})
    template_path = o.files.path_for(template["sha256"])
    revision = 1
    prefix = o.artifacts.prefix(variant_id, revision)

    def emit(event: str, data: JsonDict) -> None:
        if event == "stage":
            o.state.add_variant_stage(job_id, variant_id, data)
            if data.get("status") == "running":
                o.state.update_job(job_id, stage=data["stage"])
        elif event == "files_ready":
            manifest = o.artifacts.publish(staging)
            o.state.add_revision(
                job_id=job_id,
                variant_id=variant_id,
                revision=revision,
                artifacts_prefix=prefix,
                manifest=manifest,
                pptx_hash=_pptx_hash(manifest, prefix),
            )
            o.state.update_variant(
                job_id,
                variant_id,
                status="ready",
                slide_count=data.get("slide_count"),
                ready_at=now_iso(),
                audit={
                    "status": "running",
                    "coverage_complete": False,
                    "issues_total": 0,
                    "blocking": 0,
                },
            )

    with o.artifacts.stage_revision(job_id, variant_id, revision) as staging:
        ctx = VariantContext(
            job_id=job_id,
            variant_id=variant_id,
            template_profile=template["profile"],
            template_path=template_path if template_path.is_file() else None,
            package=package["package"] or {},
            story=gen["story"],
            settings=gen["request"].get("settings") or {},
            staging=staging,
            revision=revision,
            is_canceled=lambda: o.is_canceled(job_id),
        )
        outcome = run_variant(o.layers, ctx, emit)
        if outcome.status == "failed":
            o.state.update_variant(
                job_id, variant_id, status="failed", error=outcome.error, stages=outcome.stages
            )
            return
    manifest = o.artifacts.read_manifest(job_id, variant_id, revision)
    o.state.update_revision(job_id, variant_id, revision, manifest=manifest)
    audit = dict(outcome.audit or {})
    audit["report_artifact"] = f"{prefix}audit.json"
    o.state.update_variant(
        job_id,
        variant_id,
        status=outcome.status,
        slide_count=outcome.slide_count,
        audited_at=now_iso(),
        audit=audit,
        stages=outcome.stages,
    )


def task_finalize(job_id: str) -> None:
    o = get_orchestrator()
    job = o.state.get_job(job_id)
    if job["status"] in TERMINAL:
        return
    gen = o.state.get_generation(job_id)
    variants = o.state.get_variants(job_id)
    unfinished = [v for v in variants if v["status"] in {"pending", "running"}]
    if unfinished and not gen["canceled"]:
        for v in unfinished:
            o.state.update_variant(
                job_id,
                v["variant_id"],
                status="failed",
                error={
                    "code": "variant_lost",
                    "message": "Вариант не завершился",
                    "retryable": True,
                },
            )
        variants = o.state.get_variants(job_id)
    failed = [v for v in variants if v["status"] == "failed"]
    needs_review = any(
        v["status"] == "needs_review"
        or (v.get("audit") and not v["audit"].get("coverage_complete"))
        for v in variants
    )
    ts = now_iso()
    status: str
    error: JsonDict | None
    warnings = list(gen["warnings"])
    if failed and len(failed) < len(variants):
        warnings.append(
            {
                "code": "variant_failed",
                "message": f"Вариант {', '.join(v['variant_id'] for v in failed)} не собран; доступны остальные варианты",  # noqa: E501
            }
        )
    if gen["canceled"]:
        status, error = (
            "canceled",
            {"code": "canceled", "message": "Задание отменено пользователем"},
        )
    elif len(failed) == len(variants):
        status, error = (
            "failed",
            {
                "code": "all_variants_failed",
                "message": "Ни один вариант не собран",
                "retryable": True,
            },
        )
    elif failed or needs_review:
        status, error = "needs_review", None
    else:
        status, error = "succeeded", None
    o.state.update_generation(job_id, warnings=warnings)
    o.state.add_stage(job_id, {"stage": "finalize", "status": "done", "duration_ms": 0})
    o.state.update_job(
        job_id,
        status=status,
        stage="done",
        finished_at=ts,
        error=error,
        progress={"percent": 100, "message": _final_message(variants)},
    )


def task_repair(repair_job_id: str) -> None:
    o = get_orchestrator()
    repair = o.state.get_repair(repair_job_id)
    job_id, variant_id = repair["job_id"], repair["variant_id"]
    o.state.job_started(repair_job_id)
    o.state.update_job(
        repair_job_id,
        stage="repair",
        progress={"percent": 10, "message": "Исправление выбранных находок"},
    )
    try:
        variant = o.state.get_variant(job_id, variant_id)
        base = repair["base_revision"]
        if variant["revision"] != base:
            raise ConflictError("revision_stale", "Ревизия устарела до начала исправления")
        base_dir = o.artifacts.revision_dir(job_id, variant_id, base)
        base_report = json.loads((base_dir / "audit.json").read_text(encoding="utf-8"))
        plan = json.loads((base_dir / "plan.json").read_text(encoding="utf-8"))
        gen = o.state.get_generation(job_id)
        package = o.state.get_package(gen["package_id"])
        deck_title = (package["package"] or {}).get("brief", {}).get("title") or "Презентация"
        count = int(plan.get("slide_count") or variant.get("slide_count") or 10)
        from presentation_designer.pipeline.stubs import SLIDE_TITLES

        titles = (SLIDE_TITLES * 4)[:count]
        new_rev = base + 1
        prefix = o.artifacts.prefix(variant_id, new_rev)
        with o.artifacts.stage_revision(job_id, variant_id, new_rev) as staging:
            out = run_repair(
                o.layers,
                RepairInput(
                    job_id,
                    variant_id,
                    new_rev,
                    base,
                    base_report,
                    base_dir,
                    repair["issue_ids"],
                    staging,
                    titles,
                    deck_title,
                ),
                lambda e, d: None,
            )
        manifest = o.artifacts.read_manifest(job_id, variant_id, new_rev)
        o.state.add_revision(
            job_id=job_id,
            variant_id=variant_id,
            revision=new_rev,
            artifacts_prefix=prefix,
            manifest=manifest,
            repair_job_id=repair_job_id,
            changed_slide_ids=out.changed_slide_ids,
            pptx_hash=_pptx_hash(manifest, prefix),
        )
        summary = audit_summary(out.report)
        summary["report_artifact"] = f"{prefix}audit.json"
        status = (
            "needs_review"
            if summary["issues_total"] > 0 or not summary["coverage_complete"]
            else "ready"
        )
        o.state.update_variant(
            job_id, variant_id, status=status, audited_at=now_iso(), audit=summary
        )
        o.state.update_repair(
            repair_job_id,
            result="applied",
            new_revision=new_rev,
            changed_slide_ids=out.changed_slide_ids,
        )
        o.state.update_job(
            repair_job_id,
            status="succeeded",
            stage="done",
            finished_at=now_iso(),
            result={"revision": new_rev, "generation_result_url": f"/api/generations/{job_id}"},
            progress={
                "percent": 100,
                "message": "Исправления применены, затронутые слайды перепроверены",
            },
        )
    except Exception as e:
        log.exception("исправление %s не удалось", repair_job_id)
        message = str(e) or e.__class__.__name__
        code = getattr(e, "code", "repair_failed")
        o.state.update_repair(repair_job_id, result="failed", message=message)
        o.state.update_job(
            repair_job_id,
            status="failed",
            stage="done",
            finished_at=now_iso(),
            error={"code": code, "message": message, "stage": "repair", "retryable": True},
        )


# ---------- вспомогательное ----------


def _ms_since(started_iso: str) -> int:
    started = datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
    return int((datetime.now(UTC) - started).total_seconds() * 1000)


def _duration(job: JsonDict) -> int | None:
    if not job.get("started_at") or not job.get("finished_at"):
        return None
    a = datetime.fromisoformat(job["started_at"].replace("Z", "+00:00"))
    b = datetime.fromisoformat(job["finished_at"].replace("Z", "+00:00"))
    return int((b - a).total_seconds() * 1000)


def _pptx_hash(manifest: JsonDict, prefix: str) -> str | None:
    entry = manifest.get(f"{prefix}deck.pptx")
    return f"sha256:{entry['sha256']}" if entry and entry.get("sha256") else None


def _final_message(variants: list[JsonDict]) -> str:
    audited = sum(1 for v in variants if v.get("audited_at"))
    return f"Готово: {len(variants)} вариантов, аудит завершён у {audited}"


def renderer_check(
    redis_url: str, worker_name: str, fixture: pathlib.Path | None = None, timeout_s: int = 90
) -> bool:
    """Пробный рендер собственной фикстуры при старте воркера; результат читает /api/health."""
    import shutil
    import subprocess
    import tempfile

    import redis

    ok = False
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    fixture = (
        fixture
        or pathlib.Path(__file__).resolve().parents[3]
        / "tests"
        / "fixtures"
        / "pptx"
        / "mini_template.pptx"
    )
    if soffice and fixture.is_file():
        with tempfile.TemporaryDirectory(prefix="render-check-") as tmp:
            profile = pathlib.Path(tmp) / "profile"
            try:
                subprocess.run(
                    [
                        soffice,
                        f"-env:UserInstallation=file://{profile}",
                        "--headless",
                        "--norestore",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        tmp,
                        str(fixture),
                    ],
                    check=True,
                    timeout=timeout_s,
                    capture_output=True,
                )
                ok = (pathlib.Path(tmp) / f"{fixture.stem}.pdf").is_file()
            except Exception:
                log.exception("проверка рендерера не прошла")
    try:
        redis.Redis.from_url(redis_url).set(f"pd:renderer:{worker_name}", "ok" if ok else "fail")
    except Exception:
        log.exception("не удалось записать результат проверки рендерера")
    return ok


def worker_name(role: str) -> str:
    return f"{role}-{socket.gethostname()}"
