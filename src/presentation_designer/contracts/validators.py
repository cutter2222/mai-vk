"""Проверки связей между документами, которые JSON Schema выразить не может.

Валидный JSON сам по себе не означает корректный план: здесь проверяются
существование слотов, паттернов и фактов, соответствие содержимого блока
его виду, число слайдов, покрытие обязательных тезисов и границы запроса.
Каждая функция возвращает список нарушений; пустой список означает успех.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from presentation_designer.contracts import models as m

FACT_REF = re.compile(r"\{fact:([A-Za-z0-9_.:-]+)\}")

# Поле содержимого, обязательное для каждого вида блока.
KIND_CONTENT: dict[str, str] = {
    "title": "text",
    "subtitle": "text",
    "body": "text",
    "label": "text",
    "caption": "text",
    "date": "text",
    "name": "text",
    "position": "text",
    "code": "text",
    "bullets": "items",
    "number": "number",
    "table": "table",
    "chart": "chart",
    "image": "image",
    "icon": "icon",
    "diagram": "diagram",
}


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    path: str = ""

    def __str__(self) -> str:
        return f"{self.code}: {self.message}" + (f" [{self.path}]" if self.path else "")


class ContractError(ValueError):
    """Поднимается, когда документ прошёл схему, но нарушил связи."""

    def __init__(self, violations: list[Violation]) -> None:
        self.violations = violations
        super().__init__("; ".join(str(v) for v in violations))


def _id(value: object) -> str:
    """Возвращает строку из Id (RootModel) или уже готовой строки."""
    root = getattr(value, "root", None)
    return str(root if root is not None else value)


def _ids(values: Sequence[object] | None) -> list[str]:
    return [_id(v) for v in values or []]


def _fact_refs_in_text(text: str | None) -> set[str]:
    return set(FACT_REF.findall(text or ""))


def _chart_in_image(pattern: m.Pattern, slot: m.Slot, block: m.BlockModel) -> bool:
    """Картинка диаграммы в образце паттерна роли chart заменяется нативной диаграммой:
    блок chart в слоте image допустим только там (slide_plan 1.2)."""
    return pattern.role == "chart" and slot.kind == "image" and block.kind == "chart"


# ----------------------------------------------------------------------------
# GenerationRequest
# ----------------------------------------------------------------------------


def check_generation_request(req: m.GenerationRequest) -> list[Violation]:
    out: list[Violation] = []
    settings = req.settings
    if settings is None:
        return out
    sc = settings.slide_count
    if sc is not None:
        if sc.min is not None and sc.max is not None and sc.min > sc.max:
            out.append(
                Violation(
                    "slide_count_range", f"min {sc.min} больше max {sc.max}", "settings.slide_count"
                )
            )
        if sc.exact is not None and (sc.min is not None or sc.max is not None):
            out.append(
                Violation(
                    "slide_count_exact_and_range",
                    "задано и точное число, и диапазон: диапазон игнорируется",
                    "settings.slide_count",
                )
            )
    if settings.variants is not None and len(set(settings.variants)) != len(settings.variants):
        out.append(Violation("variants_duplicate", "варианты повторяются", "settings.variants"))
    return out


# ----------------------------------------------------------------------------
# SlidePlan против TemplateProfile, ContentPackage и StoryPlan
# ----------------------------------------------------------------------------


def check_slide_plan(
    plan: m.SlidePlan,
    profile: m.TemplateProfile | None = None,
    package: m.ContentPackage | None = None,
    story: m.StoryPlan | None = None,
) -> list[Violation]:
    out: list[Violation] = []
    patterns: dict[str, m.Pattern] = {}
    if profile is not None:
        patterns = {_id(p.pattern_id): p for p in profile.patterns}
    facts: set[str] = set()
    datasets: set[str] = set()
    assets: set[str] = set()
    blocks: set[str] = set()
    if package is not None:
        facts = {_id(f.fact_id) for f in package.facts}
        datasets = {_id(d.dataset_id) for d in package.datasets}
        assets = {_id(a.asset_id) for a in package.assets}
        blocks = {_id(b.block_id) for b in package.blocks}
    if profile is not None:
        assets |= {_id(a.asset_id) for a in profile.assets}
    theses: dict[str, m.Thesis] = {}
    if story is not None:
        theses = {_id(t.thesis_id): t for t in story.theses}
        if _id(plan.story_id) != _id(story.story_id):
            out.append(
                Violation(
                    "story_mismatch",
                    f"план ссылается на {_id(plan.story_id)}, передан {_id(story.story_id)}",
                    "story_id",
                )
            )
    if profile is not None and _id(plan.template_id) != _id(profile.template_id):
        out.append(
            Violation(
                "template_mismatch",
                f"план для {_id(plan.template_id)}, профиль {_id(profile.template_id)}",
                "template_id",
            )
        )
    if package is not None and _id(plan.package_id) != _id(package.package_id):
        out.append(
            Violation(
                "package_mismatch",
                f"план для {_id(plan.package_id)}, пакет {_id(package.package_id)}",
                "package_id",
            )
        )

    # Число слайдов и порядок.
    n = len(plan.slides)
    sc = plan.slide_count
    if sc.exact is not None and n != sc.exact:
        out.append(
            Violation("slide_count", f"в плане {n} слайдов, требуется ровно {sc.exact}", "slides")
        )
    else:
        if sc.min is not None and n < sc.min:
            out.append(Violation("slide_count", f"в плане {n} слайдов, минимум {sc.min}", "slides"))
        if sc.max is not None and n > sc.max:
            out.append(
                Violation("slide_count", f"в плане {n} слайдов, максимум {sc.max}", "slides")
            )
    orders = [s.order for s in plan.slides]
    if sorted(orders) != list(range(1, n + 1)):
        out.append(
            Violation(
                "slide_order", "поле order должно быть перестановкой 1..N без пропусков", "slides"
            )
        )
    slide_ids = [_id(s.slide_id) for s in plan.slides]
    if len(set(slide_ids)) != len(slide_ids):
        out.append(Violation("slide_id_duplicate", "идентификаторы слайдов повторяются", "slides"))

    # Слайды и блоки.
    for s in plan.slides:
        spath = f"slides[{_id(s.slide_id)}]"
        pattern = patterns.get(_id(s.pattern_id)) if profile is not None else None
        if profile is not None and pattern is None:
            out.append(
                Violation(
                    "pattern_missing", f"паттерн {_id(s.pattern_id)} отсутствует в профиле", spath
                )
            )
        slots: dict[str, m.Slot] = (
            {_id(sl.slot_id): sl for sl in pattern.slots} if pattern is not None else {}
        )
        required_slots = {sid for sid, sl in slots.items() if sl.required}
        used_slots: set[str] = set()
        for b in s.blocks:
            bpath = f"{spath}.blocks[{_id(b.slot_id)}]"
            sid = _id(b.slot_id)
            if sid in used_slots:
                out.append(Violation("slot_duplicate", f"слот {sid} заполнен дважды", bpath))
            used_slots.add(sid)
            if pattern is not None and sid not in slots:
                out.append(
                    Violation(
                        "slot_missing",
                        f"слот {sid} отсутствует в паттерне {_id(s.pattern_id)}",
                        bpath,
                    )
                )
            elif (
                pattern is not None
                and slots[sid].kind != b.kind
                and not _chart_in_image(pattern, slots[sid], b)
            ):
                out.append(
                    Violation(
                        "slot_kind_mismatch",
                        f"слот {sid} имеет вид {slots[sid].kind}, блок {b.kind}",
                        bpath,
                    )
                )
            field = KIND_CONTENT.get(b.kind)
            if field is not None and getattr(b, field, None) in (None, [], ""):
                out.append(
                    Violation(
                        "kind_content", f"блок вида {b.kind} должен содержать поле {field}", bpath
                    )
                )
            # Ссылки на факты, наборы данных и ресурсы.
            refs = set(_ids(b.fact_refs)) | _fact_refs_in_text(b.text)
            if b.number is not None:
                refs.add(_id(b.number.fact_id))
            for item in b.items or []:
                refs |= _fact_refs_in_text(item.text) | set(_ids(item.fact_refs))
            if package is not None:
                for r in sorted(refs - facts):
                    out.append(Violation("fact_missing", f"факт {r} отсутствует в пакете", bpath))
                for ds in (
                    b.table.dataset_id if b.table else None,
                    b.chart.dataset_id if b.chart else None,
                ):
                    if ds is not None and _id(ds) not in datasets:
                        out.append(
                            Violation(
                                "dataset_missing",
                                f"набор данных {_id(ds)} отсутствует в пакете",
                                bpath,
                            )
                        )
                for br in _ids(b.source_refs):
                    if br not in blocks:
                        out.append(
                            Violation(
                                "block_missing", f"блок источника {br} отсутствует в пакете", bpath
                            )
                        )
            if (
                b.image is not None
                and b.image.asset_id is not None
                and (package is not None or profile is not None)
            ):
                if _id(b.image.asset_id) not in assets:
                    out.append(
                        Violation(
                            "asset_missing", f"ресурс {_id(b.image.asset_id)} не найден", bpath
                        )
                    )
        for sid in sorted(required_slots - used_slots):
            out.append(Violation("slot_required", f"обязательный слот {sid} не заполнен", spath))
        if story is not None:
            for t in _ids(s.thesis_refs):
                if t not in theses:
                    out.append(
                        Violation("thesis_missing", f"тезис {t} отсутствует в StoryPlan", spath)
                    )

    # Покрытие обязательных тезисов.
    if story is not None:
        required = {tid for tid, t in theses.items() if t.required}
        declared_required = set(_ids(plan.coverage.required_thesis_ids))
        if declared_required != required:
            out.append(
                Violation(
                    "coverage_required_mismatch",
                    "список обязательных тезисов в плане не совпадает со StoryPlan",
                    "coverage.required_thesis_ids",
                )
            )
        covered: dict[str, set[str]] = {}
        for c in plan.coverage.covered:
            covered.setdefault(_id(c.thesis_id), set()).update(_ids(c.slide_ids))
        slide_set = set(slide_ids)
        for tid, sids in covered.items():
            for sid in sorted(sids - slide_set):
                out.append(
                    Violation(
                        "coverage_slide_missing",
                        f"тезис {tid} ссылается на несуществующий слайд {sid}",
                        "coverage.covered",
                    )
                )
        for tid in sorted(required - set(covered)):
            out.append(
                Violation(
                    "coverage_missing",
                    f"обязательный тезис {tid} не покрыт ни одним слайдом",
                    "coverage",
                )
            )
        # Тезис считается покрытым, если хотя бы один из его слайдов ссылается на него.
        by_slide: dict[str, set[str]] = {
            _id(s.slide_id): set(_ids(s.thesis_refs)) for s in plan.slides
        }
        for tid, sids in covered.items():
            if tid in required and not any(tid in by_slide.get(sid, set()) for sid in sids):
                out.append(
                    Violation(
                        "coverage_unconfirmed",
                        f"тезис {tid} заявлен покрытым, но слайды на него не ссылаются",
                        "coverage.covered",
                    )
                )
    return out


# ----------------------------------------------------------------------------
# StoryPlan против ContentPackage
# ----------------------------------------------------------------------------


def check_story_plan(
    story: m.StoryPlan, package: m.ContentPackage | None = None
) -> list[Violation]:
    out: list[Violation] = []
    ids = [_id(t.thesis_id) for t in story.theses]
    if len(set(ids)) != len(ids):
        out.append(Violation("thesis_id_duplicate", "идентификаторы тезисов повторяются", "theses"))
    if sorted(t.order for t in story.theses) != list(range(1, len(ids) + 1)):
        out.append(Violation("thesis_order", "поле order должно быть перестановкой 1..N", "theses"))
    id_set = set(ids)
    for t in story.theses:
        if t.parent_id is not None and _id(t.parent_id) not in id_set:
            out.append(
                Violation(
                    "thesis_parent_missing",
                    f"родитель {_id(t.parent_id)} не найден",
                    f"theses[{_id(t.thesis_id)}]",
                )
            )
    for red in story.allowed_reductions or []:
        if _id(red.thesis_id) not in id_set:
            out.append(
                Violation(
                    "reduction_thesis_missing",
                    f"тезис {_id(red.thesis_id)} не найден",
                    "allowed_reductions",
                )
            )
        if red.reduction == "merge_with" and (
            red.target_thesis_id is None or _id(red.target_thesis_id) not in id_set
        ):
            out.append(
                Violation(
                    "reduction_target_missing",
                    "merge_with требует существующий target_thesis_id",
                    "allowed_reductions",
                )
            )
    if package is not None:
        if _id(story.package_id) != _id(package.package_id):
            out.append(
                Violation("package_mismatch", "StoryPlan ссылается на другой пакет", "package_id")
            )
        facts = {_id(f.fact_id) for f in package.facts}
        datasets = {_id(d.dataset_id) for d in package.datasets}
        blocks = {_id(b.block_id) for b in package.blocks}
        assets = {_id(a.asset_id) for a in package.assets}
        for t in story.theses:
            tpath = f"theses[{_id(t.thesis_id)}]"
            for r in sorted(
                (
                    set(_ids(t.fact_refs))
                    | _fact_refs_in_text(t.statement)
                    | _fact_refs_in_text(t.explanation)
                )
                - facts
            ):
                out.append(Violation("fact_missing", f"факт {r} отсутствует в пакете", tpath))
            for r in sorted(set(_ids(t.dataset_refs)) - datasets):
                out.append(Violation("dataset_missing", f"набор данных {r} отсутствует", tpath))
            for r in sorted(set(_ids(t.source_refs)) - blocks):
                out.append(Violation("block_missing", f"блок {r} отсутствует", tpath))
            for r in sorted(set(_ids(t.asset_refs)) - assets):
                out.append(Violation("asset_missing", f"ресурс {r} отсутствует", tpath))
        must_keep = {_id(f.fact_id) for f in package.facts if f.must_keep is not False}
        used: set[str] = set()
        for t in story.theses:
            used |= (
                set(_ids(t.fact_refs))
                | _fact_refs_in_text(t.statement)
                | _fact_refs_in_text(t.explanation)
            )
        for r in sorted(must_keep - used):
            out.append(
                Violation(
                    "must_keep_fact_unused",
                    f"обязательный факт {r} не использован ни в одном тезисе",
                    "theses",
                )
            )
    return out


# ----------------------------------------------------------------------------
# ContentPackage
# ----------------------------------------------------------------------------


def check_content_package(package: m.ContentPackage) -> list[Violation]:
    out: list[Violation] = []
    sources = {_id(s.source_id) for s in package.sources}
    facts = {_id(f.fact_id) for f in package.facts}
    blocks = {_id(b.block_id) for b in package.blocks}
    datasets = {_id(d.dataset_id) for d in package.datasets}
    assets = {_id(a.asset_id) for a in package.assets}
    for name, ids in (
        ("facts", facts),
        ("blocks", blocks),
        ("datasets", datasets),
        ("assets", assets),
    ):
        seq = {
            "facts": [_id(f.fact_id) for f in package.facts],
            "blocks": [_id(b.block_id) for b in package.blocks],
            "datasets": [_id(d.dataset_id) for d in package.datasets],
            "assets": [_id(a.asset_id) for a in package.assets],
        }[name]
        if len(seq) != len(ids):
            out.append(Violation("id_duplicate", f"в {name} повторяются идентификаторы", name))
    for b in package.blocks:
        bpath = f"blocks[{_id(b.block_id)}]"
        if _id(b.source_id) not in sources:
            out.append(Violation("source_missing", f"источник {_id(b.source_id)} не описан", bpath))
        if b.dataset_id is not None and _id(b.dataset_id) not in datasets:
            out.append(Violation("dataset_missing", f"набор {_id(b.dataset_id)} не найден", bpath))
        if b.asset_id is not None and _id(b.asset_id) not in assets:
            out.append(Violation("asset_missing", f"ресурс {_id(b.asset_id)} не найден", bpath))
    for f in package.facts:
        fpath = f"facts[{_id(f.fact_id)}]"
        if _id(f.source_id) not in sources:
            out.append(Violation("source_missing", f"источник {_id(f.source_id)} не описан", fpath))
        if f.block_id is not None and _id(f.block_id) not in blocks:
            out.append(Violation("block_missing", f"блок {_id(f.block_id)} не найден", fpath))
        if f.derived is not None:
            for r in _ids(f.derived.inputs):
                if r not in facts:
                    out.append(
                        Violation("derived_input_missing", f"входной факт {r} не найден", fpath)
                    )
                if r == _id(f.fact_id):
                    out.append(
                        Violation("derived_self_reference", "факт ссылается сам на себя", fpath)
                    )
    for d in package.datasets:
        width = len(d.columns)
        for i, row in enumerate(d.rows):
            if len(row) != width:
                out.append(
                    Violation(
                        "dataset_row_width",
                        f"строка {i} имеет {len(row)} ячеек при {width} колонках",
                        f"datasets[{_id(d.dataset_id)}]",
                    )
                )
                break
    return out


# ----------------------------------------------------------------------------
# TemplateProfile
# ----------------------------------------------------------------------------


def check_template_profile(profile: m.TemplateProfile) -> list[Violation]:
    out: list[Violation] = []
    layouts = {_id(layout.layout_id) for layout in profile.layouts}
    assets = {_id(a.asset_id) for a in profile.assets}
    pids = [_id(p.pattern_id) for p in profile.patterns]
    if len(set(pids)) != len(pids):
        out.append(
            Violation("pattern_id_duplicate", "идентификаторы паттернов повторяются", "patterns")
        )
    for p in profile.patterns:
        ppath = f"patterns[{_id(p.pattern_id)}]"
        if _id(p.source.layout_id) not in layouts:
            out.append(
                Violation("layout_missing", f"макет {_id(p.source.layout_id)} не описан", ppath)
            )
        if p.source.kind == "sample_slide" and p.source.slide_index is None:
            out.append(
                Violation(
                    "sample_index_missing", "паттерн из образца должен указывать slide_index", ppath
                )
            )
        sids = [_id(s.slot_id) for s in p.slots]
        if len(set(sids)) != len(sids):
            out.append(Violation("slot_id_duplicate", "идентификаторы слотов повторяются", ppath))
        if not any(s.kind == "title" for s in p.slots) and p.role not in (
            "image_full",
            "freeform",
            "qr",
        ):
            out.append(
                Violation("slot_title_missing", f"паттерн роли {p.role} без слота title", ppath)
            )
    for fe in profile.fixed_elements:
        if fe.asset_id is not None and _id(fe.asset_id) not in assets:
            out.append(
                Violation(
                    "asset_missing",
                    f"ресурс {_id(fe.asset_id)} не найден",
                    f"fixed_elements[{_id(fe.element_id)}]",
                )
            )
    return out


# ----------------------------------------------------------------------------
# AuditReport против ComposedDeck
# ----------------------------------------------------------------------------


def check_audit_report(
    report: m.AuditReport, deck: m.ComposedDeck | None = None
) -> list[Violation]:
    out: list[Violation] = []
    checks = {c.check_id for c in report.checks}
    issue_ids = {_id(i.issue_id) for i in report.issues}
    for r in report.results:
        if r.check_id not in checks:
            out.append(
                Violation(
                    "check_unknown",
                    f"результат ссылается на неизвестную проверку {r.check_id}",
                    "results",
                )
            )
        if r.outcome in ("not_applicable", "not_checked") and not r.reason:
            out.append(
                Violation(
                    "reason_required", f"{r.check_id}: для {r.outcome} нужна причина", "results"
                )
            )
        if r.scope == "slide" and r.slide_index is None:
            out.append(
                Violation(
                    "slide_index_required",
                    f"{r.check_id}: результат по слайду без slide_index",
                    "results",
                )
            )
        for iid in _ids(r.issue_ids):
            if iid not in issue_ids:
                out.append(
                    Violation("issue_unknown", f"результат ссылается на находку {iid}", "results")
                )
    for i in report.issues:
        if i.check_id not in checks:
            out.append(
                Violation(
                    "check_unknown",
                    f"находка {_id(i.issue_id)} ссылается на неизвестную проверку {i.check_id}",
                    "issues",
                )
            )
        if i.revision is not None and i.revision != report.revision:
            out.append(
                Violation(
                    "revision_mismatch",
                    f"находка {_id(i.issue_id)}: ревизия {i.revision}, отчёт: {report.revision}",
                    "issues",
                )
            )
    if deck is not None:
        if deck.revision != report.revision or _id(deck.variant_id) != _id(report.variant_id):
            out.append(
                Violation(
                    "deck_mismatch",
                    "отчёт и ComposedDeck относятся к разным ревизиям или вариантам",
                    "deck",
                )
            )
        deck_slides = {_id(s.slide_id): s.index for s in deck.slides}
        for i in report.issues:
            if i.slide_id is not None and _id(i.slide_id) not in deck_slides:
                out.append(
                    Violation(
                        "slide_missing",
                        f"находка {_id(i.issue_id)} ссылается на слайд {_id(i.slide_id)}",
                        "issues",
                    )
                )
    if report.coverage.complete and any(r.outcome == "not_checked" for r in report.results):
        out.append(
            Violation(
                "coverage_inconsistent",
                "coverage.complete=true при наличии not_checked",
                "coverage",
            )
        )
    return out


# ----------------------------------------------------------------------------
# GenerationResult
# ----------------------------------------------------------------------------


def check_generation_result(result: m.GenerationResult) -> list[Violation]:
    out: list[Violation] = []
    manifest = set((result.artifacts_manifest or {}).keys())
    for v in result.variants:
        vpath = f"variants[{_id(v.variant_id)}]"
        arts = v.artifacts
        if arts is not None:
            for name in (arts.pptx, arts.pdf, arts.html):
                if name is not None and manifest and name not in manifest:
                    out.append(
                        Violation(
                            "artifact_unlisted", f"артефакт {name} отсутствует в манифесте", vpath
                        )
                    )
            for t in arts.thumbnails or []:
                if manifest and t.name not in manifest:
                    out.append(
                        Violation(
                            "artifact_unlisted",
                            f"миниатюра {t.name} отсутствует в манифесте",
                            vpath,
                        )
                    )
        if v.status == "ready" and (arts is None or arts.pptx is None):
            out.append(Violation("ready_without_pptx", "вариант ready без PPTX", vpath))
        if v.status == "failed" and v.error is None:
            out.append(
                Violation("failed_without_error", "вариант failed без описания ошибки", vpath)
            )
        if v.revisions:
            if v.revision != max(r.revision for r in v.revisions):
                out.append(
                    Violation(
                        "revision_not_latest",
                        "текущая ревизия не совпадает с последней в истории",
                        vpath,
                    )
                )
    if result.status == "succeeded":
        for v in result.variants:
            if v.audit is not None and (
                not v.audit.coverage_complete or (v.audit.blocking or 0) > 0
            ):
                out.append(
                    Violation(
                        "succeeded_with_gaps",
                        f"succeeded при неполном аудите или блокерах: {_id(v.variant_id)}",
                        "status",
                    )
                )
        if result.execution_mode.mode != "real":
            out.append(
                Violation(
                    "succeeded_stub", "статус succeeded при заглушечных слоях", "execution_mode"
                )
            )
    if result.status == "failed" and result.error is None:
        out.append(Violation("failed_without_error", "задание failed без описания ошибки", "error"))
    if result.partial and result.status == "succeeded":
        out.append(
            Violation(
                "partial_succeeded", "частичный результат не может иметь статус succeeded", "status"
            )
        )
    return out


def raise_if(violations: list[Violation]) -> None:
    if violations:
        raise ContractError(violations)
