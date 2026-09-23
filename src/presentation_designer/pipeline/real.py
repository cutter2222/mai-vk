"""Настоящие реализации слоёв поверх заглушек: подключаются по мере готовности этапов.

`execution.mode: real` в конфиге включает все реализованные слои; не реализованные остаются
заглушками с отметкой `stub` в `execution_mode` (режим `mixed`), чтобы результат честно
показывал, какие части настоящие. Этап 5 подключил анализ шаблона (`parsing.template`),
этап 6 — импорт содержания (`parsing.content`), извлечение брифа (`brief`) и общий смысловой
план (`generation.story`), этап 7 — планы трёх вариантов (`generation.plan`: три задания
вариантов строят каждый свой план из общего StoryPlan, готовые планы переиспользуются из
кэша по ключу), этап 8 — вёрстку PPTX и ComposedDeck (`layout`: композер по плану, профилю,
исходному шаблону и ресурсам пакета; артефакты ревизии пишутся через staging), экспорт
(`export`: PDF собранного PPTX через ONLYOFFICE под слотом рендера, миниатюры страниц через
PDFium, промежуточный автономный HTML — картинки страниц с текстом ComposedDeck; полный
рендер HTML и кэш — этап 9). Клиент моделей и слоты рендера создаются лениво в процессе.
"""

from __future__ import annotations

import logging
import pathlib
import re
import time
from typing import Any

from presentation_designer import design
from presentation_designer.audit.contextual import ContextualResult, run_contextual_checks
from presentation_designer.audit.report import build_report as build_audit_report
from presentation_designer.generation.edit import EditError, edit_slide
from presentation_designer.generation.original import VARIANT_ID as ORIGINAL_VARIANT
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
    AuditInput,
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

JsonDict = dict[str, Any]

log = logging.getLogger(__name__)


def attach_deck_fonts(deck: JsonDict, staging: Any) -> None:
    """Кладёт файлы гарнитур колоды в ревизию и записывает их в описание.

    Холст редактора рисует текст в браузере, а шрифта шаблона в системе пользователя обычно
    нет: браузер подставляет свой, и «Редактировать» меняло вид слайда. Файлы берутся тем же
    резолвером, которым мерилась вместимость слотов, поэтому на холсте стоит ровно тот шрифт,
    по которому считалась вёрстка. Подменённые гарнитуры не прикладываются: подмена — это уже
    не шрифт шаблона, и показывать её как настоящую нечестно.
    """
    from presentation_designer.shared import text_metrics

    seen: dict[str, str] = {}
    for entry in deck.get("fonts") or []:
        family = str(entry.get("family") or "").strip()
        if not family:
            continue
        files: list[JsonDict] = []
        for weight, bold in ((400, False), (700, True)):
            resolved = text_metrics.resolve_font(family, bold=bold)
            if resolved.substituted or not resolved.file:
                continue
            source = pathlib.Path(resolved.file)
            if not source.is_file():
                continue
            name = seen.get(str(source))
            if name is None:
                suffix = source.suffix.lower() if source.suffix else ".ttf"
                slug = re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-") or "font"
                name = f"fonts/{slug}-{weight}{suffix}"
                try:
                    staging.write_bytes(name, source.read_bytes())
                except OSError:
                    log.warning("шрифт %s не приложен к ревизии", source)
                    continue
                seen[str(source)] = name
            files.append(
                {
                    "weight": weight,
                    "artifact": f"{staging.prefix}{name}",
                    "format": "opentype" if name.endswith(".otf") else "truetype",
                }
            )
        if files:
            entry["files"] = files


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
        # Детерминированные проверки считаются по ComposedDeck и профилю — модель им не нужна.
        self.modes["audit.deterministic"] = "real"
        # Контекстные требуют VLM и картинок слайдов. Слой настоящий; когда провайдер не
        # настроен или проверки выключены, это видно в coverage отчёта, а не в режиме.
        self.modes["audit.contextual"] = "real"
        # Real execution must never silently return placeholder PDF/previews.
        self.renderer_available = True
        self.modes["export"] = "real"
        self._llm: Any = None
        self._plan_cache: PlanCache | None = None
        self._llm_failed = False
        self._render_slots: Any = None
        self.last_analysis_report: dict[str, Any] | None = None
        self.last_import_report: dict[str, Any] | None = None
        self.last_story_report: dict[str, Any] | None = None
        self.last_plan_report: dict[str, Any] | None = None
        self.last_compose_report: dict[str, Any] | None = None
        self.last_feedback_report: dict[str, Any] | None = None
        self.last_export_report: dict[str, Any] | None = None
        self.last_audit_report: dict[str, Any] | None = None

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

    def take_llm_usage(self) -> dict[str, Any] | None:
        """Вызовы модели, сделанные с прошлого раза: их записывают в метрики задания."""
        client = self._llm
        recorder = getattr(client, "recorder", None) if client is not None else None
        if recorder is None:
            return None
        drained = recorder.drain()
        return dict(drained) if drained else None

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
        import hashlib
        import json

        # Package idempotency must not reuse pre-vision/disabled-vision results.
        ci = self.settings.content_import
        chart_skill = self.skill("chart_extractor")
        payload = {
            "enabled": ci.chart_image_model,
            "limit": ci.chart_image_max_images,
            "budget_s": ci.chart_image_budget_s,
            "skill": chart_skill.ref if chart_skill else None,
        }
        client = self.llm_client()
        if client is not None:
            from presentation_designer.llm import skill_model_ref

            payload["model"] = skill_model_ref(client, chart_skill, role="vlm")
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]
        return f"{import_version()}/{digest}"

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
        if inp.variant_id == ORIGINAL_VARIANT:
            return self.original_plan(inp)
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
        # Слой design: композиция приводится к объёму содержания. Модель знает
        # вместимость слотов и всё равно оставляет их пустыми — под один
        # показатель выбирался паттерн на двадцать шесть чисел. Проверка
        # детерминированная и идёт после модели, а не вместо неё.
        # Дозапрос на пустые слоты передаётся слою: порядок проходов задан
        # внутри `design.apply`, потому что дозапрос обязан идти после заставы.
        filler = self.skill("slot_filler")
        ask = None
        if filler is not None:

            def ask(_system: str, user: str) -> str:
                request = filler.request("fill.slots", user, stage="plan")
                return str(client.complete_sync(request).text)

        plan_doc, design_report = design.apply(
            result.plan,
            inp.template_profile,
            inp.story,
            variant=inp.variant_id,
            config=self.settings.model_dump() if hasattr(self.settings, "model_dump") else None,
            ask=ask,
            refill_limit=int((filler.manifest.params or {}).get("max_slots", 24)) if filler else 24,
            package=inp.package,
        )
        result.plan = plan_doc
        result.report["design"] = design_report.to_dict()

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
        log.info("слой design %s/%s: %s", inp.job_id, inp.variant_id, design_report.summary())
        return result.plan

    def original_plan(self, inp: PlanInput) -> dict[str, Any]:
        """Вариант original: план из профиля и пакета без модели, проверка связей контракта."""
        from presentation_designer.contracts import (
            ContentPackage,
            SlidePlan,
            StoryPlan,
            TemplateProfile,
        )
        from presentation_designer.contracts.validators import check_slide_plan
        from presentation_designer.generation.original import original_plan, original_profile

        plan = original_plan(
            inp.story,
            inp.template_profile,
            inp.package,
            plan_id=f"plan_{inp.job_id}_{inp.variant_id}",
        )
        # Обязательные слоты без блока (таблицы, диаграммы, картинки) заполнены самим
        # образцом: композер в режиме сохранения их не трогает.
        violations = [
            v
            for v in check_slide_plan(
                SlidePlan.model_validate(plan),
                TemplateProfile.model_validate(original_profile(inp.template_profile)),
                ContentPackage.model_validate(inp.package),
                StoryPlan.model_validate(inp.story),
            )
            if v.code != "slot_required"
        ]
        if violations:
            raise StageError(
                "plan_invalid",
                "План исходной презентации нарушает связи контракта: "
                + "; ".join(str(v) for v in violations[:6]),
                stage="plan",
            )
        log.info(
            "план original %s: %d слайдов, предупреждений %d",
            inp.job_id,
            len(plan["slides"]),
            len(plan.get("warnings") or []),
        )
        return plan

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
        plan = self.polish_plan(inp)
        try:
            result = compose_deck(
                plan,
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
                extra_assets=dict(inp.extra_assets),
                media_dir=inp.staging.path("media"),
                media_prefix=inp.staging.prefix,
            )
        except ComposeError as e:
            raise StageError(e.code, str(e), retryable=e.retryable, stage="compose") from e
        attach_deck_fonts(result.deck, inp.staging)
        inp.staging.write_json("composed.json", result.deck)
        inp.staging.write_json("plan.json", plan)
        inp.staging.write_json("story.json", inp.story)
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

    def polish_plan(self, inp: ComposeInput) -> JsonDict:
        """План, исправленный по фактам черновой сборки.

        Вёрстка — единственное место, где видно, что получилось на самом деле:
        какие слоты остались пустыми и какой текст не поместился. Поэтому
        колода собирается начерно, факты читаются из результата, план правится
        и собирается заново. Черновые сборки идут в отдельный файл и удаляются.

        Проход необязателен по построению: любая ошибка внутри него оставляет
        план как есть. Улучшение вёрстки не имеет права ронять генерацию.
        """
        settings = self.settings.design.feedback
        if not settings.enabled or inp.template_path is None or inp.variant_id == ORIGINAL_VARIANT:
            return inp.plan  # исходная презентация сохраняется как есть: править нечего
        if not inp.polish:
            return inp.plan  # ревизия-патч: пользователь правил руками, план не трогаем
        locked = {
            str(s.get("slide_id")) for s in inp.plan.get("slides") or [] if s.get("overrides")
        } | set(inp.locked_slide_ids)

        draft = inp.staging.path("deck.draft.pptx")
        template = pathlib.Path(inp.template_path)

        def compose_draft(plan: JsonDict) -> JsonDict:
            return compose_deck(
                plan,
                inp.template_profile,
                template,
                inp.package,
                out_pptx=draft,
                package_dir=inp.package_dir,
                job_id=inp.job_id,
                variant_id=inp.variant_id,
                revision=inp.revision,
                prune_layouts=bool(self.settings.layout.prune_unused_layouts),
                fit_min_ratio=float(self.settings.plan.min_font_ratio),
                fit_min_body_pt=float(self.settings.plan.min_body_pt),
                fit_min_title_pt=float(self.settings.plan.min_title_pt),
            ).deck

        ask = None
        client = self.llm_client()
        filler = self.skill("slot_filler")
        if settings.refill and client is not None and filler is not None:

            def ask(_system: str, user: str) -> str:
                request = filler.request("fill.slots", user, stage="plan")
                return str(client.complete_sync(request).text)

        try:
            plan, report = design.polish(
                inp.plan,
                inp.template_profile,
                inp.story,
                compose=compose_draft,
                variant=inp.variant_id,
                config=self.settings.model_dump(),
                ask=ask,
                package=inp.package,
                locked_slide_ids=locked,
            )
        except Exception:  # правка не имеет права ронять сборку
            log.warning(
                "правка по фактам вёрстки не удалась %s/%s",
                inp.job_id,
                inp.variant_id,
                exc_info=True,
            )
            return inp.plan
        finally:
            draft.unlink(missing_ok=True)

        self.last_feedback_report = report
        log.info(
            "правка по фактам %s/%s: %s",
            inp.job_id,
            inp.variant_id,
            {k: v for k, v in report.items() if k not in ("decisions", "refilled")},
        )
        return plan

    # ----- экспорт -----

    def export(self, inp: ExportInput) -> ExportOutput:
        from presentation_designer.export.deck import (
            ConversionError,
            RendererUnavailableError,
            export_revision,
        )

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
                prerendered=inp.prerendered,
            )
        except RendererUnavailableError as e:
            raise StageError(
                "export_renderer_unavailable", str(e), retryable=True, stage="export"
            ) from e
        except ConversionError as e:
            raise StageError("export_failed", str(e), retryable=True, stage="export") from e
        self.last_export_report = result.report
        log.info(
            "экспорт %s/%s r%d: %d страниц, pdf %d мс, миниатюры %d мс, ожидание слота %d мс%s",
            inp.job_id,
            inp.variant_id,
            inp.revision,
            result.report.get("pages", 0),
            result.report["timings_ms"].get("pdf", 0),
            result.report["timings_ms"].get("thumbnails", 0),
            result.report["timings_ms"].get("render_slot_wait", 0),
            " (рендер предварительной ревизии)"
            if result.report.get("renderer") == "prerendered"
            else "",
        )
        return ExportOutput(thumbnails=result.thumbnails)

    # ----- аудит (этап 10) -----

    def audit(self, inp: AuditInput) -> dict[str, Any]:
        """Проверки по собранной колоде: реестр в `audit/registry.py`.

        Детерминированная часть считается по ComposedDeck, файлу и отрендеренной странице
        (контраст к тому, что под буквами, и подмена гарнитуры). Контекстная — 11 вопросов
        Приложения 1 моделью по картинке слайда — выполняется, когда она включена в настройках
        и провайдер моделей настроен; иначе такие проверки помечены `not_checked`, а причина
        записана в `coverage.missing_inputs`, и отчёт не выдаёт пропуск за успех.
        """
        started = time.perf_counter()
        ctx = self._contextual_audit(inp)
        try:
            report = build_audit_report(
                job_id=inp.job_id,
                variant_id=inp.variant_id,
                revision=inp.revision,
                deck=inp.composed_deck,
                profile=inp.template_profile or {},
                staging_prefix=inp.staging.prefix,
                contextual=ctx.ran,
                contextual_answers=ctx.answers,
                contextual_issues=ctx.issues,
                contextual_outcomes=ctx.outcomes,
                missing_inputs=ctx.missing_inputs,
                llm_metrics=ctx.metrics,
                started=started,
                pptx_path=inp.staging.path("deck.pptx"),
                # Рендер ревизии уже лежит рядом: по нему меряются контраст к настоящей
                # подложке и подмена гарнитуры — по файлу это не узнать.
                pdf_path=inp.staging.path("deck.pdf"),
            )
        except Exception:
            # Сбой проверки не должен ронять готовую колоду: она уже собрана и выгружена.
            log.exception("аудит не выполнен, остаётся заглушка")
            return super().audit(inp)
        # Отчёт кладётся рядом с колодой: интерфейс и исправления читают его артефактом,
        # как и у заглушки, — без этого сводка есть, а самого отчёта не найти.
        inp.staging.write_json("audit.json", report)
        self.last_audit_report = {
            "issues": report["summary"]["issues_total"],
            "score": report["summary"]["score"],
            "duration_ms": report["metrics"]["duration_ms"],
            "contextual_answers": len(report.get("contextual_answers") or []),
        }
        log.info(
            "аудит %s/%s r%s: находок %s (контекстных %s), оценка %s, покрытие %s из %s",
            inp.job_id,
            inp.variant_id,
            inp.revision,
            report["summary"]["issues_total"],
            report["summary"]["by_kind"].get("contextual", 0),
            report["summary"]["score"],
            report["coverage"]["checked"],
            len(report["results"]),
        )
        return report

    def _contextual_audit(self, inp: AuditInput) -> ContextualResult:
        """Контекстная часть аудита. Её сбой не должен ронять готовую колоду: всё, что не
        удалось спросить, возвращается как непроверенное с причиной."""
        if not (inp.contextual and self.settings.audit.contextual_enabled):
            return ContextualResult(missing_inputs=["contextual_disabled"])
        client = self.llm_client()
        skill = self.skill("auditor")
        if client is None or skill is None:
            return ContextualResult(missing_inputs=["vlm_unavailable"])
        params = (skill.manifest.params or {}) if hasattr(skill, "manifest") else {}
        budget = min(
            float(params.get("time_budget_s", 120)),
            max(30.0, float(self.settings.timeouts.stage_audit_s) - 10),
        )
        try:
            return run_contextual_checks(
                inp.composed_deck,
                inp.package,
                inp.story,
                images=self._slide_images(inp),
                client=client,
                skill=skill,
                concurrency=int(
                    params.get("concurrency", self.settings.audit.contextual_concurrency)
                ),
                deadline_s=budget,
            )
        except Exception as e:
            log.exception("контекстный аудит не выполнен")
            return ContextualResult(missing_inputs=["vlm_error"], errors=[str(e)[:200]])

    def _slide_images(self, inp: AuditInput) -> dict[int, bytes]:
        """Миниатюры страниц, отрендеренные на экспорте: по ним модель и смотрит слайд."""
        images: dict[int, bytes] = {}
        for thumb in inp.thumbnails:
            name = str(thumb.get("name") or "").removeprefix(inp.staging.prefix)
            if not name:
                continue
            path = inp.staging.dir / name
            try:
                images[int(thumb.get("slide_index", 0))] = path.read_bytes()
            except OSError:
                log.warning("миниатюра %s недоступна для аудита", name)
        return images

    # ----- правка слайда по запросу (этап 20) -----

    def edit(self, inp: EditInput) -> EditOutput:
        if inp.patch is not None:
            # Ручные правки редактора применяются без модели — той же детерминированной
            # веткой, что и в заглушках.
            return super().edit(inp)
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
        # Описание колоды и план тоже переносятся: без них ревизия неполная — на ней не
        # работают ни холст редактора, ни следующее исправление.
        for name in ("deck.pdf", "deck.html", "deck.pptx", "composed.json", "plan.json"):
            source = base / name
            if source.is_file():
                inp.staging.write_bytes(name, source.read_bytes())
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
