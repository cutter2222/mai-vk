"""Настоящие реализации слоёв поверх заглушек: подключаются по мере готовности этапов.

`execution.mode: real` в конфиге включает все реализованные слои; не реализованные остаются
заглушками с отметкой `stub` в `execution_mode` (режим `mixed`), чтобы результат честно
показывал, какие части настоящие. Этап 5 подключил анализ шаблона (`parsing.template`),
этап 6 — импорт содержания (`parsing.content`), извлечение брифа (`brief`) и общий смысловой
план (`generation.story`), этап 7 — планы трёх вариантов (`generation.plan`: три задания
вариантов строят каждый свой план из общего StoryPlan, готовые планы переиспользуются из
кэша по ключу), этап 8 — вёрстку PPTX и ComposedDeck (`layout`: композер по плану, профилю,
исходному шаблону и ресурсам пакета; артефакты ревизии пишутся через staging), экспорт
(`export`: PDF собранного PPTX через LibreOffice под слотом рендера, миниатюры страниц через
PDFium, промежуточный автономный HTML — картинки страниц с текстом ComposedDeck; полный
рендер HTML и кэш — этап 9). Клиент моделей и слоты рендера создаются лениво в процессе.
"""

from __future__ import annotations

import logging
import pathlib
from typing import Any

from presentation_designer.generation.edit import EditError, edit_slide
from presentation_designer.generation.story import (
    StoryError,
    build_story,
    llm_model_ref,
    story_key,
)
from presentation_designer.generation.variants import PlanCache, PlanError, build_variant_plan
from presentation_designer.layout.compose import ComposeError, compose_deck
from presentation_designer.parsing.content.brief import extract_brief as heuristic_brief
from presentation_designer.parsing.content.brief import extract_brief_with_model
from presentation_designer.parsing.content.importer import (
    MaterialFile,
    import_content,
    import_version,
)
from presentation_designer.parsing.template.analyzer import PackageError, analyze_template
from presentation_designer.pipeline.run import (
    AnalyzeInput,
    AnalyzeOutput,
    BriefInput,
    ComposeInput,
    ComposeOutput,
    EditInput,
    EditOutput,
    ExportInput,
    ExportOutput,
    ImportInput,
    ImportOutput,
    PlanInput,
    RepairInput,
    RepairOutput,
    StageError,
    StoryInput,
)
from presentation_designer.pipeline.stubs import StubLayers
from presentation_designer.shared.settings import Settings

log = logging.getLogger(__name__)


class RealLayers(StubLayers):
    """Слои, реализованные к текущему этапу; остальное — заглушки."""

    def __init__(self, settings: Settings, *, stage_delay_ms: int = 0) -> None:
        super().__init__(stage_delay_ms=stage_delay_ms)
        self.settings = settings
        self.modes["parsing.template"] = "real"
        self.modes["parsing.content"] = "real"
        self.modes["brief"] = "real"
        self.modes["generation.story"] = "real"
        self.modes["generation.plan"] = "real"
        self.modes["layout"] = "real"
        self.modes["generation.edit"] = "real"
        # Экспорт настоящий там, где его выполняет воркер с LibreOffice; на машине разработчика
        # со встроенной очередью без рендерера остаётся заглушка, и execution_mode это показывает.
        self.renderer_available = _renderer_available()
        self.modes["export"] = "real" if self.renderer_available else "stub"
        self._llm: Any = None
        self._plan_cache: PlanCache | None = None
        self._llm_failed = False
        self._render_slots: Any = None
        self.last_analysis_report: dict[str, Any] | None = None
        self.last_import_report: dict[str, Any] | None = None
        self.last_story_report: dict[str, Any] | None = None
        self.last_plan_report: dict[str, Any] | None = None
        self.last_compose_report: dict[str, Any] | None = None
        self.last_export_report: dict[str, Any] | None = None

    # ----- ленивые зависимости -----

    def llm_client(self) -> Any:
        """Клиент моделей, если провайдер настроен; иначе None (слои работают без модели
        там, где это допустимо, и дают понятную ошибку там, где модель обязательна)."""
        if self._llm is None and not self._llm_failed:
            try:
                from presentation_designer.llm import build_client

                client = build_client(self.settings)
                target = client.target("llm")
                if not target.provider.configured():
                    log.warning("провайдер моделей не настроен: слои работают без модели")
                    self._llm_failed = True
                    return None
                self._llm = client
            except Exception:
                log.exception("клиент моделей не создан: слои работают без модели")
                self._llm_failed = True
        return self._llm

    def skill(self, name: str) -> Any:
        try:
            from presentation_designer.llm.skills import get_skill

            return get_skill(name)
        except Exception:
            log.exception("скилл %s не загружен", name)
            return None

    def plan_cache(self) -> PlanCache:
        if self._plan_cache is None:
            self._plan_cache = PlanCache(self.settings.plan_cache_dir)
        return self._plan_cache

    def render_slots(self) -> Any:
        if self._render_slots is None:
            from presentation_designer.export.render_slots import render_slots_from_env

            self._render_slots = render_slots_from_env(self.settings.render.slots)
        return self._render_slots

    # ----- анализ шаблона -----

    def analyze(self, inp: AnalyzeInput) -> AnalyzeOutput:
        try:
            result = analyze_template(
                pathlib.Path(inp.path),
                template_id=inp.template_id,
                name=inp.name,
                size_bytes=inp.size_bytes,
                sha256=inp.sha256,
                settings=self.settings,
                llm_client=self.llm_client(),
                render_slots=self.render_slots(),
                workdir=self.settings.runs_dir / "tmp",
            )
        except PackageError as e:
            raise StageError(
                f"template_{e.code}", f"Шаблон не поддерживается: {e}", stage="analyze"
            ) from e
        self.last_analysis_report = result.report.as_dict()
        log.info(
            "анализ %s: %s паттернов, %s ресурсов, %d мс, превью %d, vlm=%s",
            inp.name,
            result.report.counts.get("patterns"),
            result.report.counts.get("assets"),
            result.report.as_dict()["total_ms"],
            result.report.previews_rendered,
            result.report.vlm.get("asked", 0) if result.report.vlm else 0,
        )
        return AnalyzeOutput(profile=result.profile, previews=result.previews)

    # ----- импорт содержания -----

    def import_version(self) -> str:
        return import_version()

    def import_content(self, inp: ImportInput) -> ImportOutput:
        files = [
            MaterialFile(s.file_id, s.name, s.sha256, s.size_bytes, s.format, s.path)
            for s in inp.sources
        ]
        try:
            result = import_content(
                files,
                inp.brief,
                package_id=inp.package_id,
                mode=inp.mode,
                settings=self.settings,
                llm_client=self.llm_client(),
                skill=self.skill("content_importer"),
            )
        except ValueError as e:
            raise StageError("import_invalid", str(e), stage="import") from e
        self.last_import_report = result.report
        counts = result.report["counts"]
        log.info(
            "импорт %s: %d блоков, %d фактов, %d наборов, %d изображений, кэш %s, %d мс",
            inp.package_id,
            counts["blocks"],
            counts["facts"],
            counts["datasets"],
            counts["assets"],
            result.report["cache"],
            result.report["total_ms"],
        )
        return ImportOutput(
            package=result.package,
            warnings=result.warnings,
            assets=result.assets,
            report=result.report,
        )

    # ----- бриф -----

    async def extract_brief(self, inp: BriefInput) -> dict[str, Any]:
        client = self.llm_client()
        skill = self.skill("brief_extractor")
        if client is None or skill is None:
            return heuristic_brief(inp.text, inp.brief)
        return await extract_brief_with_model(
            inp.text,
            inp.brief,
            client=client,
            skill=skill,
            deadline_s=float(self.settings.timeouts.brief_s),
        )

    # ----- смысловой план -----

    def story_key(self, inp: StoryInput) -> str:
        client = self.llm_client()
        skill = self.skill("story_planner")
        model = llm_model_ref(client, skill) if client is not None else None
        return story_key(inp.package, inp.settings, skill=skill, model=model)

    def story(self, inp: StoryInput) -> dict[str, Any]:
        client = self.llm_client()
        skill = self.skill("story_planner")
        if client is None or skill is None:
            raise StageError(
                "story_llm_not_configured",
                "Провайдер моделей не настроен: смысловой план не построен",
                stage="story",
            )
        nonce = None
        if inp.settings.get("force_regenerate"):
            import uuid

            nonce = uuid.uuid4().hex
        try:
            result = build_story(
                inp.package,
                inp.settings,
                client=client,
                skill=skill,
                app_settings=self.settings,
                deadline_s=min(
                    float((skill.manifest.params or {}).get("time_budget_s", 90)),
                    float(self.settings.timeouts.stage_story_s) - 10,
                ),
                nonce=nonce,
            )
        except StoryError as e:
            raise StageError(e.code, str(e), retryable=e.retryable, stage="story") from e
        self.last_story_report = result.report
        log.info(
            "смысловой план %s: %d тезисов, покрытие фактов %s, %d мс",
            inp.package.get("package_id"),
            result.report["counts"]["theses"],
            result.report["coverage"]["must_keep_facts"],
            result.report["total_ms"],
        )
        return result.story

    # ----- план варианта -----

    def plan(self, inp: PlanInput) -> dict[str, Any]:
        client = self.llm_client()
        skill = self.skill("variant_planner")
        if client is None or skill is None:
            raise StageError(
                "plan_llm_not_configured",
                "Провайдер моделей не настроен: план варианта не построен",
                stage="plan",
            )
        nonce = None
        if inp.settings.get("force_regenerate"):
            import uuid

            nonce = uuid.uuid4().hex
        try:
            result = build_variant_plan(
                inp.story,
                inp.template_profile,
                inp.package,
                inp.variant_id,
                inp.settings,
                slide_count=inp.slide_count,
                client=client,
                skill=skill,
                app_settings=self.settings,
                deadline_s=min(
                    float((skill.manifest.params or {}).get("time_budget_s", 100)),
                    float(self.settings.timeouts.stage_plan_s) - 10,
                ),
                cache=self.plan_cache(),
                nonce=nonce,
                plan_id=f"plan_{inp.job_id}_{inp.variant_id}",
            )
        except PlanError as e:
            raise StageError(e.code, str(e), retryable=e.retryable, stage="plan") from e
        self.last_plan_report = result.report
        counts = result.report.get("counts") or {}
        log.info(
            "план %s/%s: %s слайдов, покрытие %s/%s, переполнений %s, кэш %s, %d мс",
            inp.job_id,
            inp.variant_id,
            counts.get("slides", len(result.plan["slides"])),
            counts.get("covered"),
            counts.get("required_theses"),
            counts.get("overflow"),
            result.report.get("cache_hit"),
            result.report.get("total_ms", 0),
        )
        return result.plan

    # ----- вёрстка -----

    def compose(self, inp: ComposeInput) -> ComposeOutput:
        if inp.template_path is None or not pathlib.Path(inp.template_path).is_file():
            raise StageError(
                "compose_template_missing",
                "Файл шаблона недоступен: сборка невозможна",
                retryable=True,
                stage="compose",
            )
        if inp.variant_id == "detailed" and is_failure_fixture(pathlib.Path(inp.template_path)):
            # Сценарий сквозных тестов «частичный отказ» (фикстура mini_template_fail.pptx):
            # опознаётся по комментарию ZIP, а не по имени файла пользователя.
            raise StageError(
                "compose_failed",
                "Не удалось клонировать образец: тестовая фикстура частичного отказа",
                retryable=True,
                stage="compose",
            )
        pptx_artifact = f"{inp.staging.prefix}deck.pptx"
        try:
            result = compose_deck(
                inp.plan,
                inp.template_profile,
                pathlib.Path(inp.template_path),
                inp.package,
                out_pptx=inp.staging.path("deck.pptx"),
                package_dir=inp.package_dir,
                job_id=inp.job_id,
                variant_id=inp.variant_id,
                revision=inp.revision,
                pptx_artifact=pptx_artifact,
                prune_layouts=bool(self.settings.layout.prune_unused_layouts),
                fit_min_ratio=float(self.settings.plan.min_font_ratio),
                fit_min_body_pt=float(self.settings.plan.min_body_pt),
                fit_min_title_pt=float(self.settings.plan.min_title_pt),
            )
        except ComposeError as e:
            raise StageError(e.code, str(e), retryable=e.retryable, stage="compose") from e
        inp.staging.write_json("composed.json", result.deck)
        inp.staging.write_json("plan.json", inp.plan)
        self.last_compose_report = result.report
        log.info(
            "сборка %s/%s: %d слайдов, объектов %s, удалено %s, %d КБ, %d мс, предупреждений %d",
            inp.job_id,
            inp.variant_id,
            len(result.slide_titles),
            result.deck["stats"].get("objects"),
            result.deck["stats"].get("removed_objects"),
            result.report["file_size_bytes"] // 1024,
            result.report["timings_ms"]["total"],
            len(result.warnings),
        )
        return ComposeOutput(
            slide_count=len(result.slide_titles),
            slide_titles=result.slide_titles,
            composed_deck=result.deck,
        )

    # ----- экспорт -----

    def export(self, inp: ExportInput) -> ExportOutput:
        from presentation_designer.export.deck import (
            ConversionError,
            RendererUnavailableError,
            export_revision,
        )

        if not self.renderer_available:
            log.warning(
                "LibreOffice недоступен: экспорт %s/%s заглушкой", inp.job_id, inp.variant_id
            )
            return super().export(inp)
        pptx = inp.staging.path("deck.pptx")
        if not pptx.is_file():
            raise StageError(
                "export_pptx_missing", "Файл PPTX ревизии не найден", retryable=True, stage="export"
            )
        try:
            result = export_revision(
                pptx,
                inp.staging.dir,
                prefix=inp.staging.prefix,
                composed_deck=inp.composed_deck,
                deck_title=inp.deck_title,
                settings=self.settings,
                render_slots=self.render_slots(),
                slide_titles=inp.slide_titles,
            )
        except RendererUnavailableError as e:
            raise StageError(
                "export_renderer_unavailable", str(e), retryable=True, stage="export"
            ) from e
        except ConversionError as e:
            raise StageError("export_failed", str(e), retryable=True, stage="export") from e
        self.last_export_report = result.report
        log.info(
            "экспорт %s/%s r%d: %d страниц, pdf %d мс, миниатюры %d мс, ожидание слота %d мс",
            inp.job_id,
            inp.variant_id,
            inp.revision,
            result.report.get("pages", 0),
            result.report["timings_ms"].get("pdf", 0),
            result.report["timings_ms"].get("thumbnails", 0),
            result.report["timings_ms"].get("render_slot_wait", 0),
        )
        return ExportOutput(thumbnails=result.thumbnails)

    # ----- правка слайда по запросу (этап 20) -----

    def edit(self, inp: EditInput) -> EditOutput:
        client = self.llm_client()
        skill = self.skill("slide_editor")
        if client is None or skill is None:
            raise StageError(
                "edit_llm_not_configured",
                "Провайдер моделей не настроен: правка слайда невозможна",
                stage="plan",
            )
        try:
            result = edit_slide(
                inp.plan,
                inp.story,
                inp.template_profile,
                inp.package,
                slide_index=inp.slide_index,
                instruction=inp.instruction,
                client=client,
                skill=skill,
                settings=inp.settings,
                app_settings=self.settings,
                deadline_s=min(
                    float((skill.manifest.params or {}).get("time_budget_s", 60)),
                    float(self.settings.timeouts.stage_plan_s) - 10,
                ),
                nonce=inp.nonce,
            )
        except EditError as e:
            raise StageError(e.code, str(e), retryable=e.retryable, stage="plan") from e
        log.info(
            "правка %s/%s слайд %d: %s, %d мс",
            inp.job_id,
            inp.variant_id,
            inp.slide_index + 1,
            "применена" if result.changed else f"отклонена ({result.reason})",
            result.report.get("total_ms", 0),
        )
        return EditOutput(
            plan=result.plan,
            changed=result.changed,
            slide_id=result.slide_id,
            change_note=result.change_note,
            reason=result.reason,
            report=result.report,
        )

    # ----- исправления (этап 10) -----

    def repair(self, inp: RepairInput) -> RepairOutput:
        """Исправления пока заглушка: отчёт пересчитывается, файлы новой ревизии — копия
        предыдущей. Миниатюры, PDF и HTML берутся настоящие из базовой ревизии, а не
        рисунок заглушки."""
        out = super().repair(inp)
        base = inp.base_dir
        if not self.renderer_available or not (base / "deck.pdf").is_file():
            return out
        for name in ("deck.pdf", "deck.html"):
            inp.staging.write_bytes(name, (base / name).read_bytes())
        thumbnails: list[dict[str, Any]] = []
        for thumb in out.thumbnails:
            rel = str(thumb["name"]).removeprefix(inp.staging.prefix)
            source = base / rel
            if not source.is_file():
                return out
            inp.staging.write_bytes(rel, source.read_bytes())
            from PIL import Image

            with Image.open(source) as image:
                thumbnails.append(
                    {**thumb, "width_px": int(image.width), "height_px": int(image.height)}
                )
        return RepairOutput(
            report=out.report, changed_slide_ids=out.changed_slide_ids, thumbnails=thumbnails
        )


def _renderer_available() -> bool:
    """Экспорт выполняет воркер генерации, а он проверяет LibreOffice при старте
    (`renderer_check`), поэтому при очереди RQ слой настоящий даже в процессе API без
    рендерера. При встроенной очереди (`PD_QUEUE_MODE=inline`: локальный API, тесты) экспорт
    идёт в этом же процессе — только если рендерер есть здесь."""
    import os

    from presentation_designer.export.pdf import find_soffice

    if os.environ.get("PD_QUEUE_MODE", "rq") != "inline":
        return True
    soffice = find_soffice()
    return soffice is not None and soffice.exists()


FAILURE_FIXTURE_MARK = b"fixture: template whose detailed variant fails"


def is_failure_fixture(path: pathlib.Path) -> bool:
    """Собственная фикстура сквозных тестов: PPTX с отметкой в комментарии ZIP."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            return zf.comment.startswith(FAILURE_FIXTURE_MARK)
    except (OSError, zipfile.BadZipFile):
        return False


def build_layers(settings: Settings) -> StubLayers:
    if settings.execution.mode == "real":
        return RealLayers(settings, stage_delay_ms=settings.execution.stub_stage_delay_ms)
    return StubLayers(stage_delay_ms=settings.execution.stub_stage_delay_ms)
