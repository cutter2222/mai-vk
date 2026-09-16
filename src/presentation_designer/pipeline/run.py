"""Ядро конвейера: порядок этапов одного варианта и исправления без HTTP и очереди.

Ядро получает готовые входы (профиль шаблона, пакет, план истории, настройки) и объект
публикации артефактов, вызывает переданные реализации слоёв и отдаёт результат этапа
вместе с временем каждого шага. Хранение состояния, очередь и события подключаются
обёртками в pipeline/jobs.py; CLI и воркеры используют одно и то же ядро.
"""

from __future__ import annotations

import pathlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.pipeline.artifacts import Staging
from presentation_designer.pipeline.state import now_iso

JsonDict = dict[str, Any]
Emit = Callable[[str, JsonDict], None]

VARIANT_AXIS = {
    "compact": ("density", "compact"),
    "balanced": ("density", "balanced"),
    "detailed": ("density", "detailed"),
}
DEFAULT_SLIDES = {"compact": 10, "balanced": 12, "detailed": 15}


class StageError(Exception):
    """Ошибка этапа с кодом для контракта error."""

    def __init__(
        self, code: str, message: str, *, retryable: bool = False, stage: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.stage = stage

    def as_dict(self, stage: str | None = None) -> JsonDict:
        out: JsonDict = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if stage or self.stage:
            out["stage"] = stage or self.stage
        return out


# ---------- входы и выходы слоёв ----------


@dataclass
class AnalyzeInput:
    template_id: str
    sha256: str
    name: str
    size_bytes: int
    path: pathlib.Path


@dataclass
class AnalyzeOutput:
    profile: JsonDict
    previews: dict[str, bytes]


@dataclass
class SourceFile:
    file_id: str
    name: str
    sha256: str
    size_bytes: int
    format: str
    path: pathlib.Path | None


@dataclass
class ImportInput:
    package_id: str
    mode: str
    sources: list[SourceFile]
    brief: JsonDict | None


@dataclass
class ImportOutput:
    package: JsonDict
    warnings: list[JsonDict]


@dataclass
class StoryInput:
    package: JsonDict
    settings: JsonDict


@dataclass
class PlanInput:
    job_id: str
    variant_id: str
    template_profile: JsonDict
    package: JsonDict
    story: JsonDict
    settings: JsonDict
    slide_count: int | None


@dataclass
class ComposeInput:
    job_id: str
    variant_id: str
    revision: int
    plan: JsonDict
    template_profile: JsonDict
    template_path: pathlib.Path | None
    package: JsonDict
    story: JsonDict
    staging: Staging


@dataclass
class ComposeOutput:
    slide_count: int
    slide_titles: list[str]
    composed_deck: JsonDict


@dataclass
class ExportInput:
    job_id: str
    variant_id: str
    revision: int
    staging: Staging
    slide_titles: list[str]
    deck_title: str
    slide_marks: dict[int, str] = field(default_factory=dict)


@dataclass
class ExportOutput:
    thumbnails: list[JsonDict]


@dataclass
class AuditInput:
    job_id: str
    variant_id: str
    revision: int
    staging: Staging
    slide_count: int
    contextual: bool
    composed_deck: JsonDict
    story: JsonDict
    package: JsonDict
    thumbnails: list[JsonDict]


@dataclass
class RepairInput:
    job_id: str
    variant_id: str
    revision: int
    base_revision: int
    base_report: JsonDict
    base_dir: pathlib.Path
    issue_ids: list[str]
    staging: Staging
    slide_titles: list[str]
    deck_title: str


@dataclass
class RepairOutput:
    report: JsonDict
    changed_slide_ids: list[str]
    thumbnails: list[JsonDict]


class Layers:
    """Реализации слоёв. Заглушки и настоящие слои наследуют и заполняют `modes`."""

    modes: dict[str, str]

    def __init__(self) -> None:
        self.modes = {}

    def analyze(self, inp: AnalyzeInput) -> AnalyzeOutput:
        raise NotImplementedError

    def import_content(self, inp: ImportInput) -> ImportOutput:
        raise NotImplementedError

    def story(self, inp: StoryInput) -> JsonDict:
        raise NotImplementedError

    def plan(self, inp: PlanInput) -> JsonDict:
        raise NotImplementedError

    def compose(self, inp: ComposeInput) -> ComposeOutput:
        raise NotImplementedError

    def export(self, inp: ExportInput) -> ExportOutput:
        raise NotImplementedError

    def audit(self, inp: AuditInput) -> JsonDict:
        raise NotImplementedError

    def repair(self, inp: RepairInput) -> RepairOutput:
        raise NotImplementedError

    def execution_mode(self) -> JsonDict:
        values = set(self.modes.values())
        mode = (
            "real" if values == {"real"} else "stub" if values <= {"stub", "skipped"} else "mixed"
        )
        return {"mode": mode, "layers": dict(self.modes)}


# ---------- контекст и результат варианта ----------


@dataclass
class VariantContext:
    job_id: str
    variant_id: str
    template_profile: JsonDict
    template_path: pathlib.Path | None
    package: JsonDict
    story: JsonDict
    settings: JsonDict
    staging: Staging
    revision: int = 1
    is_canceled: Callable[[], bool] = lambda: False


@dataclass
class VariantOutcome:
    status: str
    slide_count: int | None = None
    slide_titles: list[str] = field(default_factory=list)
    audit: JsonDict | None = None
    report: JsonDict | None = None
    error: JsonDict | None = None
    stages: list[JsonDict] = field(default_factory=list)
    thumbnails: list[JsonDict] = field(default_factory=list)
    deck_title: str = ""


def resolve_slide_count(variant_id: str, settings: JsonDict) -> int:
    """Точное число из настроек или умолчание варианта, зажатое в диапазон."""
    spec = (settings or {}).get("slide_count") or {}
    if spec.get("exact"):
        return int(spec["exact"])
    count = DEFAULT_SLIDES.get(variant_id, 12)
    lo, hi = spec.get("min"), spec.get("max")
    if lo is not None:
        count = max(count, int(lo))
    if hi is not None:
        count = min(count, int(hi))
    return count


def audit_summary(report: JsonDict) -> JsonDict:
    """Сводка отчёта для GenerationResult.variants[].audit."""
    coverage = report.get("coverage", {})
    summary = report.get("summary", {})
    complete = bool(coverage.get("complete"))
    return {
        "status": "complete" if complete else "partial",
        "coverage_complete": complete,
        "issues_total": int(summary.get("issues_total", 0)),
        "blocking": int(summary.get("by_severity", {}).get("blocking", 0)),
    }


class _Timer:
    def __init__(self, emit: Emit, stage: str, variant_id: str | None) -> None:
        self.emit = emit
        self.stage = stage
        self.variant_id = variant_id
        self.started = time.monotonic()
        self.started_at = now_iso()
        self.timing: JsonDict = {"stage": stage, "status": "running", "started_at": self.started_at}
        if variant_id:
            self.timing["variant_id"] = variant_id
        emit("stage", dict(self.timing))

    def done(self, status: str = "done", **extra: Any) -> JsonDict:
        self.timing = {
            **self.timing,
            "status": status,
            "duration_ms": int((time.monotonic() - self.started) * 1000),
            **extra,
        }
        self.emit("stage", dict(self.timing))
        return self.timing


def _check_canceled(ctx: VariantContext, stage: str) -> None:
    if ctx.is_canceled():
        raise StageError("canceled", "Задание отменено пользователем", stage=stage)


def run_variant(layers: Layers, ctx: VariantContext, emit: Emit) -> VariantOutcome:
    """План → сборка → экспорт → аудит одного варианта.

    Ошибка этапа завершает вариант, не задание."""
    outcome = VariantOutcome(status="running")
    stage = "plan"
    try:
        _check_canceled(ctx, stage)
        timer = _Timer(emit, "plan", ctx.variant_id)
        plan = layers.plan(
            PlanInput(
                ctx.job_id,
                ctx.variant_id,
                ctx.template_profile,
                ctx.package,
                ctx.story,
                ctx.settings,
                resolve_slide_count(ctx.variant_id, ctx.settings),
            )
        )
        outcome.stages.append(timer.done())

        stage = "compose"
        _check_canceled(ctx, stage)
        timer = _Timer(emit, "compose", ctx.variant_id)
        composed = layers.compose(
            ComposeInput(
                ctx.job_id,
                ctx.variant_id,
                ctx.revision,
                plan,
                ctx.template_profile,
                ctx.template_path,
                ctx.package,
                ctx.story,
                ctx.staging,
            )
        )
        outcome.slide_count = composed.slide_count
        outcome.slide_titles = composed.slide_titles
        outcome.deck_title = ctx.package.get("brief", {}).get("title") or "Презентация"
        outcome.stages.append(timer.done())

        stage = "export"
        _check_canceled(ctx, stage)
        timer = _Timer(emit, "export", ctx.variant_id)
        exported = layers.export(
            ExportInput(
                ctx.job_id,
                ctx.variant_id,
                ctx.revision,
                ctx.staging,
                composed.slide_titles,
                outcome.deck_title,
            )
        )
        outcome.thumbnails = exported.thumbnails
        outcome.stages.append(timer.done())
        emit(
            "files_ready",
            {
                "variant_id": ctx.variant_id,
                "slide_count": composed.slide_count,
                "thumbnails": exported.thumbnails,
            },
        )

        stage = "audit"
        _check_canceled(ctx, stage)
        timer = _Timer(emit, "audit", ctx.variant_id)
        contextual = bool((ctx.settings or {}).get("run_contextual_audit", True))
        report = layers.audit(
            AuditInput(
                ctx.job_id,
                ctx.variant_id,
                ctx.revision,
                ctx.staging,
                composed.slide_count,
                contextual,
                composed.composed_deck,
                ctx.story,
                ctx.package,
                exported.thumbnails,
            )
        )
        outcome.report = report
        outcome.audit = audit_summary(report)
        outcome.stages.append(timer.done())
        outcome.status = (
            "needs_review"
            if outcome.audit["issues_total"] > 0 or not outcome.audit["coverage_complete"]
            else "ready"
        )
        return outcome
    except StageError as e:
        outcome.status = "failed"
        outcome.error = e.as_dict(stage)
    except Exception as e:
        outcome.status = "failed"
        outcome.error = {
            "code": f"{stage}_error",
            "message": str(e) or e.__class__.__name__,
            "stage": stage,
            "retryable": True,
        }
    outcome.stages.append({"stage": stage, "variant_id": ctx.variant_id, "status": "failed"})
    for later in _stages_after(stage):
        outcome.stages.append({"stage": later, "variant_id": ctx.variant_id, "status": "skipped"})
    emit("stage", {"stage": stage, "variant_id": ctx.variant_id, "status": "failed"})
    return outcome


def _stages_after(stage: str) -> list[str]:
    order = ["plan", "compose", "export", "audit"]
    return order[order.index(stage) + 1 :] if stage in order else []


def run_repair(layers: Layers, inp: RepairInput, emit: Emit) -> RepairOutput:
    timer = _Timer(emit, "repair", inp.variant_id)
    try:
        out = layers.repair(inp)
    except Exception:
        timer.done("failed")
        raise
    timer.done()
    return out
