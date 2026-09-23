"""Ядро конвейера: порядок этапов одного варианта и исправления без HTTP и очереди.

Ядро получает готовые входы (профиль шаблона, пакет, план истории, настройки) и объект
публикации артефактов, вызывает переданные реализации слоёв и отдаёт результат этапа
вместе с временем каждого шага. Хранение состояния, очередь и события подключаются
обёртками в pipeline/jobs.py; CLI и воркеры используют одно и то же ядро.
"""

from __future__ import annotations

import logging
import pathlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.generation.design_mode import profile_for_mode, validate_plan_mode
from presentation_designer.pipeline.artifacts import Staging
from presentation_designer.pipeline.state import now_iso

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]
Emit = Callable[[str, JsonDict], None]

VARIANT_AXIS = {
    "compact": ("density", "compact"),
    "balanced": ("density", "balanced"),
    "detailed": ("density", "detailed"),
    "original": ("custom", "original"),  # загруженная презентация как готовый результат
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
    # Изображения пакета: путь из assets[].path → байты; публикуются в каталог пакета.
    assets: dict[str, bytes] = field(default_factory=dict)
    report: JsonDict = field(default_factory=dict)


@dataclass
class StoryInput:
    package: JsonDict
    settings: JsonDict


@dataclass
class BriefInput:
    """Сообщение чата и текущий бриф/настройки проекта для извлечения брифа."""

    text: str
    brief: JsonDict | None = None
    settings: JsonDict | None = None


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
    # Каталог опубликованных ресурсов пакета (assets[].path относительно него); None — без картинок.
    package_dir: pathlib.Path | None = None
    # Файлы проекта для ручных правок редактора (source.kind == file): file_id → путь.
    extra_assets: dict[str, pathlib.Path] = field(default_factory=dict)
    # Полировка плана по фактам черновой сборки: для ревизии-патча выключена, слайды с
    # ручными правками (и перечисленные здесь) полировка не трогает.
    polish: bool = True
    locked_slide_ids: frozenset[str] = frozenset()
    settings: JsonDict = field(default_factory=dict)
    # Вариант original: charts.json предварительной ревизии (чтения диаграмм-картинок по
    # sha256 картинки) — сборка повторяет те же замены; None — прочитать самим.
    chart_report: JsonDict | None = None


@dataclass
class ChartImagesOutput:
    """Диаграммы-картинки готовой презентации: отчёт charts.json (чтения по sha256 картинки
    и замены) и фраза для чата; заглушки ничего не читают."""

    report: JsonDict = field(default_factory=dict)
    message: str | None = None
    replaced: int = 0


@dataclass
class ComposeOutput:
    slide_count: int
    slide_titles: list[str]
    composed_deck: JsonDict
    warnings: list[JsonDict] = field(default_factory=list)


@dataclass
class ExportInput:
    job_id: str
    variant_id: str
    revision: int
    staging: Staging
    slide_titles: list[str]
    deck_title: str
    slide_marks: dict[int, str] = field(default_factory=dict)
    # ComposedDeck ревизии: текст слайдов для автономного HTML; заглушка его не читает.
    composed_deck: JsonDict | None = None
    # Каталог с готовыми deck.pdf и thumbs/ того же по содержанию файла (предварительная
    # ревизия исходной презентации): рендер не повторяется.
    prerendered: pathlib.Path | None = None


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
    # Профиль шаблона: по нему проверяются шрифты, палитра, шкала кеглей, макеты и поля.
    template_profile: JsonDict = field(default_factory=dict)


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


@dataclass
class EditInput:
    """Правка одного слайда по инструкции: план базовой ревизии и входы сборки новой."""

    job_id: str
    variant_id: str
    revision: int
    base_revision: int
    plan: JsonDict
    slide_index: int
    instruction: str
    template_profile: JsonDict
    package: JsonDict
    story: JsonDict
    settings: JsonDict
    nonce: str | None = None
    # Ручные правки редактора (документ slide_patch): применяются без модели, инструкция
    # пуста; base_deck — ComposedDeck базовой ревизии для проверки адресов объектов.
    patch: JsonDict | None = None
    base_deck: JsonDict | None = None


@dataclass
class EditOutput:
    """Ответ слоя правки: новый план или отказ с причиной."""

    plan: JsonDict | None
    changed: bool
    slide_id: str
    change_note: str = ""
    reason: str = ""
    report: JsonDict = field(default_factory=dict)
    # Все изменённые слайды (у патча их может быть несколько), сводка для карточки.
    changed_slide_ids: list[str] = field(default_factory=list)
    summary: str = ""


class Layers:
    """Реализации слоёв. Заглушки и настоящие слои наследуют и заполняют `modes`."""

    modes: dict[str, str]

    def __init__(self) -> None:
        self.modes = {}

    def analyze(self, inp: AnalyzeInput) -> AnalyzeOutput:
        raise NotImplementedError

    def import_content(self, inp: ImportInput) -> ImportOutput:
        raise NotImplementedError

    def import_version(self) -> str:
        """Версия импортёра для идемпотентности пакета: смена парсеров даёт новый пакет."""
        return "stub"

    def story(self, inp: StoryInput) -> JsonDict:
        raise NotImplementedError

    def story_key(self, inp: StoryInput) -> str:
        """Смысловой ключ StoryPlan: по нему pipeline переиспользует готовый план."""
        raise NotImplementedError

    async def extract_brief(self, inp: BriefInput) -> JsonDict:
        """Документ BriefExtract без schema_version; API вызывает с тайм-аутом."""
        raise NotImplementedError

    def plan(self, inp: PlanInput) -> JsonDict:
        raise NotImplementedError

    def compose(self, inp: ComposeInput) -> ComposeOutput:
        raise NotImplementedError

    def chart_images(
        self, pptx_path: pathlib.Path, progress: Callable[[str, float], None] | None = None
    ) -> ChartImagesOutput:
        """Диаграммы-картинки файла → нативные диаграммы на месте (копия готовой презентации);
        без модели ничего не меняется. `progress(фраза, доля готовности 0–1)`."""
        return ChartImagesOutput()

    def export(self, inp: ExportInput) -> ExportOutput:
        raise NotImplementedError

    def audit(self, inp: AuditInput) -> JsonDict:
        raise NotImplementedError

    def repair(self, inp: RepairInput) -> RepairOutput:
        raise NotImplementedError

    def edit(self, inp: EditInput) -> EditOutput:
        raise NotImplementedError

    def take_llm_usage(self) -> JsonDict | None:
        """Вызовы модели с прошлого раза в формате `UsageRecorder.metrics()`; заглушки
        модель не зовут и отдают None."""
        return None

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
    package_dir: pathlib.Path | None = None
    prerendered: pathlib.Path | None = None
    chart_report: JsonDict | None = None


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
    # Предупреждения плана, которые должен увидеть пользователь в результате задания
    # (например, слайды исходной презентации, не перенесённые в вариант original).
    warnings: list[JsonDict] = field(default_factory=list)


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
        if ctx.variant_id != "original":
            ctx.template_profile = profile_for_mode(ctx.template_profile, ctx.settings)
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
        outcome.stages.append(
            timer.done(cache_hit=bool((plan.get("generation_meta") or {}).get("cache_hit")))
        )
        outcome.warnings = [
            {"code": w["code"], "message": w.get("message", "")}
            for w in plan.get("warnings") or []
            if w.get("code") == "original_slides_skipped"
        ]

        stage = "compose"
        _check_canceled(ctx, stage)
        if ctx.variant_id != "original":
            validate_plan_mode(plan, ctx.template_profile, ctx.settings)
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
                package_dir=ctx.package_dir,
                settings=ctx.settings,
                chart_report=ctx.chart_report,
            )
        )
        outcome.slide_count = composed.slide_count
        outcome.slide_titles = composed.slide_titles
        outcome.warnings.extend(composed.warnings)
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
                composed_deck=composed.composed_deck,
                prerendered=ctx.prerendered,
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
                ctx.template_profile,
            )
        )
        outcome.report = report
        outcome.audit = audit_summary(report)
        outcome.stages.append(timer.done())
        outcome.status = (
            "needs_review"
            if outcome.audit["issues_total"] > 0
            or not outcome.audit["coverage_complete"]
            or any(w.get("code") == "template_fit_review" for w in outcome.warnings)
            else "ready"
        )
        return outcome
    except StageError as e:
        outcome.status = "failed"
        outcome.error = e.as_dict(stage)
    except Exception as e:
        log.exception("вариант %s/%s: этап %s упал", ctx.job_id, ctx.variant_id, stage)
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


@dataclass
class EditContext:
    """Входы правки слайда: план базовой ревизии и всё для сборки новой ревизии."""

    job_id: str
    variant_id: str
    revision: int
    base_revision: int
    plan: JsonDict
    slide_index: int
    instruction: str
    template_profile: JsonDict
    template_path: pathlib.Path | None
    package: JsonDict
    story: JsonDict
    settings: JsonDict
    staging: Staging
    package_dir: pathlib.Path | None = None
    nonce: str | None = None
    patch: JsonDict | None = None
    base_deck: JsonDict | None = None
    extra_assets: dict[str, pathlib.Path] = field(default_factory=dict)


@dataclass
class EditOutcome:
    """Итог правки: applied — новая ревизия собрана и проверена; unchanged — просьба
    отклонена, ревизии нет; failed — ошибка этапа."""

    status: str
    slide_id: str = ""
    change_note: str = ""
    reason: str = ""
    slide_count: int | None = None
    slide_titles: list[str] = field(default_factory=list)
    audit: JsonDict | None = None
    report: JsonDict | None = None
    error: JsonDict | None = None
    stages: list[JsonDict] = field(default_factory=list)
    thumbnails: list[JsonDict] = field(default_factory=list)
    edit_report: JsonDict = field(default_factory=dict)
    changed_slide_ids: list[str] = field(default_factory=list)
    summary: str = ""


def run_edit(layers: Layers, ctx: EditContext, emit: Emit) -> EditOutcome:
    """Правка слайда → сборка → экспорт → аудит новой ревизии; отказ модели останавливает
    цепочку после первого этапа без ревизии. Для ручных правок редактора (`ctx.patch`) шаг
    plan детерминирован: патч применяется к плану без модели, полировка по фактам сборки
    выключена, чтобы не переписать то, что пользователь поправил руками."""
    outcome = EditOutcome(status="running")
    stage = "plan"
    is_patch = ctx.patch is not None
    try:
        if ctx.variant_id != "original":
            ctx.template_profile = profile_for_mode(ctx.template_profile, ctx.settings)
        timer = _Timer(emit, "plan", ctx.variant_id)
        edited = layers.edit(
            EditInput(
                ctx.job_id,
                ctx.variant_id,
                ctx.revision,
                ctx.base_revision,
                ctx.plan,
                ctx.slide_index,
                ctx.instruction,
                ctx.template_profile,
                ctx.package,
                ctx.story,
                ctx.settings,
                nonce=ctx.nonce,
                patch=ctx.patch,
                base_deck=ctx.base_deck,
            )
        )
        outcome.slide_id = edited.slide_id
        outcome.edit_report = edited.report
        outcome.changed_slide_ids = list(edited.changed_slide_ids) or (
            [edited.slide_id] if edited.slide_id else []
        )
        outcome.summary = edited.summary
        outcome.stages.append(timer.done())
        if not edited.changed or edited.plan is None:
            outcome.status = "unchanged"
            outcome.reason = edited.reason
            return outcome
        outcome.change_note = edited.change_note
        if ctx.variant_id != "original":
            validate_plan_mode(edited.plan, ctx.template_profile, ctx.settings)

        stage = "compose"
        timer = _Timer(emit, "compose", ctx.variant_id)
        composed = layers.compose(
            ComposeInput(
                ctx.job_id,
                ctx.variant_id,
                ctx.revision,
                edited.plan,
                ctx.template_profile,
                ctx.template_path,
                ctx.package,
                ctx.story,
                ctx.staging,
                package_dir=ctx.package_dir,
                extra_assets=dict(ctx.extra_assets),
                polish=not is_patch,
                settings=ctx.settings,
            )
        )
        outcome.slide_count = composed.slide_count
        outcome.slide_titles = composed.slide_titles
        deck_title = ctx.package.get("brief", {}).get("title") or "Презентация"
        outcome.stages.append(timer.done())

        stage = "export"
        timer = _Timer(emit, "export", ctx.variant_id)
        exported = layers.export(
            ExportInput(
                ctx.job_id,
                ctx.variant_id,
                ctx.revision,
                ctx.staging,
                composed.slide_titles,
                deck_title,
                composed_deck=composed.composed_deck,
            )
        )
        outcome.thumbnails = exported.thumbnails
        outcome.stages.append(timer.done())

        stage = "audit"
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
                ctx.template_profile,
            )
        )
        # Отчёт уже записан слоем аудита; область повторной проверки дописывается и файл
        # ревизии перезаписывается, чтобы интерфейс видел изменённый слайд.
        rechecked = report.get("rechecked_after_repair") or {}
        report["rechecked_after_repair"] = {
            "base_revision": ctx.base_revision,
            "changed_slide_ids": list(outcome.changed_slide_ids),
            "dependent_slide_ids": list(rechecked.get("dependent_slide_ids") or []),
            "deck_checks_rerun": list(rechecked.get("deck_checks_rerun") or []),
        }
        ctx.staging.write_json("audit.json", report)
        outcome.report = report
        outcome.audit = audit_summary(report)
        outcome.stages.append(timer.done())
        outcome.status = "applied"
        return outcome
    except StageError as e:
        outcome.status = "failed"
        outcome.error = e.as_dict(stage)
    except Exception as e:
        log.exception("правка %s/%s: этап %s упал", ctx.job_id, ctx.variant_id, stage)
        code = getattr(e, "code", f"{stage}_error")
        outcome.status = "failed"
        outcome.error = {
            "code": code,
            "message": str(e) or e.__class__.__name__,
            "stage": stage,
            "retryable": bool(getattr(e, "retryable", True)),
        }
    outcome.stages.append({"stage": stage, "variant_id": ctx.variant_id, "status": "failed"})
    emit("stage", {"stage": stage, "variant_id": ctx.variant_id, "status": "failed"})
    return outcome


def run_repair(layers: Layers, inp: RepairInput, emit: Emit) -> RepairOutput:
    timer = _Timer(emit, "repair", inp.variant_id)
    try:
        out = layers.repair(inp)
    except Exception:
        timer.done("failed")
        raise
    timer.done()
    return out
