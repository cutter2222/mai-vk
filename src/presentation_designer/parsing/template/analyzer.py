"""Анализ шаблона: от PPTX к TemplateProfile и миниатюрам образцов.

Порядок: пакет → классификация слайдов → индекс ресурсов → постоянные элементы → стили и токены →
паттерны с ёмкостью слотов → превью (ONLYOFFICE → PDF → PNG под слотом рендера) → уточнение
ролей и теги пиктограмм через VLM (пакетами, по представителям групп) → правила оформления →
`llm_digest`. Каждый шаг пишет время в отчёт анализа; отсутствие рендерера или модели не роняет
анализ, а даёт предупреждение в профиле и понижает уверенность.

Ключ профиля (`profile_key`) объединяет sha256 файла, версию анализатора, версию контрактов,
версии скилла и промптов классификации, модель VLM, настройки ёмкости/рендера и манифест шрифтов
рендерера: смена любого из них делает сохранённый профиль устаревшим.
"""

from __future__ import annotations

import hashlib
import json
import logging
import pathlib
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.contracts import CONTRACTS_VERSION, TemplateProfile
from presentation_designer.library.register import builtin_patterns
from presentation_designer.parsing.template import assets as assets_mod
from presentation_designer.parsing.template.classify import (
    Classification,
    classify_all,
    placeholder_markers,
)
from presentation_designer.parsing.template.fixed import find_fixed_elements
from presentation_designer.parsing.template.guidelines import extract_guidelines
from presentation_designer.parsing.template.package import (
    PackageError,
    TemplatePackage,
    open_template,
)
from presentation_designer.parsing.template.patterns import (
    Pattern,
    build_pattern,
    group_patterns,
    refine_roles_with_vlm,
)
from presentation_designer.parsing.template.styles import StyleResolver
from presentation_designer.parsing.template.tokens import (
    build_design_tokens,
    collect_stats,
    guides_of,
)
from presentation_designer.shared import text_metrics
from presentation_designer.shared.settings import Settings, get_settings

log = logging.getLogger(__name__)

ANALYZER_NAME = "template_analyzer"
ANALYZER_VERSION = "0.4.4"
# Версия схемы профиля: пишется в документ и входит в ключ кэша разбора.
PROFILE_SCHEMA_VERSION = "1.4"
PREVIEW_DIR = "previews"


@dataclass
class AnalysisReport:
    """Что и за сколько сделал анализ — для CLI, тестов на шаблонах и docs."""

    timings_ms: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    roles: dict[str, int] = field(default_factory=dict)
    excluded_slides: dict[str, list[int]] = field(default_factory=dict)
    vlm: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    previews_rendered: int = 0
    renderer: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "timings_ms": self.timings_ms,
            "total_ms": sum(self.timings_ms.values()),
            "counts": self.counts,
            "roles": self.roles,
            "excluded_slides": self.excluded_slides,
            "vlm": self.vlm,
            "warnings": self.warnings,
            "previews_rendered": self.previews_rendered,
            "renderer": self.renderer,
        }


@dataclass
class AnalysisResult:
    profile: dict[str, Any]
    previews: dict[str, bytes]
    report: AnalysisReport


class _Clock:
    def __init__(self, report: AnalysisReport) -> None:
        self.report = report
        self.t = time.perf_counter()

    def lap(self, name: str) -> None:
        now = time.perf_counter()
        self.report.timings_ms[name] = int((now - self.t) * 1000)
        self.t = now


def profile_key(
    sha256: str,
    *,
    settings: Settings | None = None,
    skill: Any = None,
    vlm_model: str | None = None,
) -> str:
    """Ключ кэша профиля: файл, анализатор, контракты, скилл/промпты, модель, настройки, шрифты."""
    settings = settings or get_settings()
    material = {
        "sha256": sha256,
        "analyzer": ANALYZER_VERSION,
        "contracts": CONTRACTS_VERSION,
        "skill": list(skill.ref) if skill is not None else None,
        "prompts": sorted(p.ref for p in skill.prompts.values()) if skill is not None else None,
        "vlm_model": vlm_model,
        "render": {
            "thumbnail_width_px": settings.render.thumbnail_width_px,
            "vlm_image_max_px": settings.render.vlm_image_max_px,
        },
        "capacity_margin": settings.llm.chars_per_token and 0.92,
        "fonts": text_metrics.fonts_manifest(),
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def analyze_template(
    path: pathlib.Path,
    *,
    template_id: str,
    name: str,
    size_bytes: int,
    sha256: str | None = None,
    settings: Settings | None = None,
    llm_client: Any = None,
    render_slots: Any = None,
    render: bool = True,
    use_vlm: bool = True,
    workdir: pathlib.Path | None = None,
) -> AnalysisResult:
    settings = settings or get_settings()
    report = AnalysisReport()
    clock = _Clock(report)
    path = pathlib.Path(path)
    pkg = open_template(path)  # PackageError уходит вызывающему: это ошибка входа, не анализа
    sha256 = sha256 or hashlib.sha256(path.read_bytes()).hexdigest()
    clock.lap("open")

    classes = classify_all(pkg)
    by_index = {c.slide_index: c for c in classes}
    sample_indexes = {c.slide_index for c in classes if c.kind == "content_sample"}
    for kind in ("style_guide", "asset_catalog", "empty", "hidden", "other"):
        idx = [c.slide_index for c in classes if c.kind == kind]
        if idx:
            report.excluded_slides[kind] = idx
    clock.lap("classify")

    assets, asset_ids_by_sha = assets_mod.index_assets(pkg, classes)
    assets_by_sha = {a.sha256: a for a in assets}
    clock.lap("assets")

    fixed, dynamic = find_fixed_elements(pkg, sample_indexes, asset_ids_by_sha)
    fixed_refs = {(f.source_part, f.element_ref) for f in fixed}
    for f in fixed:
        fixed_refs.update(f.occurrences)
        # Картинка постоянного элемента-логотипа — логотип и в индексе ресурсов, а не пиктограмма.
        if f.kind == "logo" and f.asset_id:
            asset = next((a for a in assets if a.asset_id == f.asset_id), None)
            if asset is not None and asset.kind == "icon":
                asset.kind = "logo"
    clock.lap("fixed")

    resolvers: dict[int, StyleResolver] = {}
    for slide in pkg.slides:
        master = pkg.master(slide.master_id)
        layout = pkg.layout(slide.layout_id)
        resolvers[slide.index] = StyleResolver(
            master.theme if master else None,
            master.element if master else None,
            layout.element if layout else None,
            master_part=master.part if master else "",
            layout_part=layout.part if layout else "",
        )
    stats = collect_stats(pkg, sample_indexes, resolvers)
    tokens = build_design_tokens(pkg, stats, sample_indexes)
    clock.lap("tokens")

    patterns: list[Pattern] = []
    for slide in pkg.slides:
        if slide.index not in sample_indexes:
            continue
        try:
            pattern = build_pattern(
                slide, pkg, resolvers[slide.index], by_index[slide.index], assets_by_sha, fixed_refs
            )
        except Exception as e:  # один сломанный образец не должен ронять весь анализ
            log.exception("паттерн слайда %d не построен", slide.index)
            report.warnings.append(
                {
                    "code": "pattern_failed",
                    "message": f"слайд {slide.index}: {e}",
                    "slide_index": slide.index,
                }
            )
            continue
        if pattern is None:
            by_index[slide.index] = Classification(
                slide.index, "other", 0.5, ["нет слотов содержания"]
            )
            report.excluded_slides.setdefault("other", []).append(slide.index)
            continue
        patterns.append(pattern)
    groups = group_patterns(patterns)
    clock.lap("patterns")

    previews: dict[str, bytes] = {}
    preview_by_index: dict[int, bytes] = {}
    if render:
        try:
            preview_by_index = render_previews(
                pkg, settings, render_slots, workdir=workdir, report=report
            )
        except Exception as e:
            log.warning("превью шаблона не отрендерены: %s", e)
            report.warnings.append(
                {"code": "previews_unavailable", "message": f"рендер не выполнен: {e}"}
            )
    for index, png in preview_by_index.items():
        previews[f"{PREVIEW_DIR}/slide-{index:02d}.png"] = png
    report.previews_rendered = len(preview_by_index)
    if render and preview_by_index:
        # Пустой слайд каждого макета, на который ссылаются паттерны, — подложка холста
        # редактора (этап 23): фон, декор и логотипы макета, которых нет в ComposedDeck.
        layout_ids = sorted({p.slide.layout_id for p in patterns if p.slide.layout_id})
        try:
            for layout_id, png in render_layout_previews(
                pkg, settings, render_slots, layout_ids, workdir=workdir, report=report
            ).items():
                previews[f"{PREVIEW_DIR}/layout-{layout_id}.png"] = png
        except Exception as e:
            log.warning("превью макетов не отрендерены: %s", e)
            report.warnings.append(
                {"code": "layout_previews_unavailable", "message": f"рендер макетов: {e}"}
            )
    clock.lap("render")

    skill = None
    vlm_model: str | None = None
    if use_vlm and llm_client is not None:
        try:
            from presentation_designer.llm.skills import get_skill

            skill = get_skill(ANALYZER_NAME)
            vlm_model = llm_client.target(skill.model_role).model
        except Exception as e:
            report.warnings.append(
                {"code": "vlm_unavailable", "message": f"скилл или модель недоступны: {e}"}
            )
            skill = None
    if skill is not None and preview_by_index:
        from presentation_designer.llm.types import Deadline

        params = skill.manifest.params or {}
        # Общий бюджет времени на все запросы VLM: роли, не успевшие уточниться, остаются
        # эвристикой, разметка иконок пропускается — этап анализа не выходит за свой тайм-аут.
        budget = Deadline.after(float(params.get("time_budget_s", 75)))
        max_px = int(params.get("image_max_px", settings.render.vlm_image_max_px))
        vlm_previews = {i: _downscale(png, max_px) for i, png in preview_by_index.items()}
        report.vlm = refine_roles_with_vlm(
            groups,
            vlm_previews,
            llm_client,
            skill,
            batch=int(params.get("group_batch", 6)),
            min_confidence=float(params.get("min_confidence", 0.7)),
            deadline=budget,
        )
        try:
            report.vlm["assets_tagged"] = assets_mod.tag_icons_with_vlm(
                assets,
                llm_client,
                skill,
                batch=int(params.get("asset_batch", 24)),
                limit=int(params.get("asset_tag_limit", 240)),
                deadline=budget,
            )
        except Exception as e:
            report.vlm["assets_error"] = str(e)[:200]
        report.vlm["budget_left_s"] = round(max(budget.remaining(), 0.0), 1)
    elif skill is not None:
        report.warnings.append(
            {"code": "vlm_skipped", "message": "нет миниатюр: роли определены только эвристиками"}
        )
    if vlm_model:
        report.vlm["model"] = vlm_model
    clock.lap("vlm")

    guidelines = extract_guidelines(pkg, classes)
    markers = placeholder_markers(pkg, classes)
    clock.lap("guidelines")

    for p in patterns:
        key = f"{PREVIEW_DIR}/slide-{p.slide.index:02d}.png"
        if key in previews:
            p.preview_path = key

    profile = _assemble_profile(
        pkg,
        template_id=template_id,
        sha256=sha256,
        name=name,
        size_bytes=size_bytes,
        classes=list(by_index.values()),
        assets=assets,
        fixed=fixed,
        dynamic=dynamic,
        tokens=tokens,
        patterns=patterns,
        guidelines=guidelines,
        markers=markers,
        previews=previews,
        report=report,
        sample_indexes=sample_indexes,
    )
    TemplateProfile.model_validate(profile)
    clock.lap("assemble")
    report.counts = {
        **report.counts,
        "slides": len(pkg.slides),
        "content_samples": len(sample_indexes),
        "patterns": len(patterns),
        "pattern_groups": len(groups),
        "assets": len(assets),
        "fixed_elements": len(fixed),
        "guidelines": len(guidelines),
        "markers": len(markers),
    }
    roles: dict[str, int] = {}
    for p in patterns:
        roles[p.role] = roles.get(p.role, 0) + 1
    report.roles = dict(sorted(roles.items(), key=lambda kv: -kv[1]))
    return AnalysisResult(profile, previews, report)


def _downscale(png: bytes, max_px: int) -> bytes:
    import io

    from PIL import Image

    with Image.open(io.BytesIO(png)) as img:
        if img.width <= max_px:
            return png
        small = img.convert("RGB")
        small.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        small.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def render_previews(
    pkg: TemplatePackage,
    settings: Settings,
    render_slots: Any,
    *,
    workdir: pathlib.Path | None,
    report: AnalysisReport,
) -> dict[int, bytes]:
    """Весь шаблон → PDF под слотом рендера → PNG всех слайдов (образцы и служебные: интерфейс
    показывает образцы, аудит и VLM берут только нужные)."""
    from presentation_designer.export.pdf import (
        convert_to_pdf,
    )
    from presentation_designer.export.render_slots import render_slots_from_env
    from presentation_designer.export.thumbnails import render_thumbnails

    slots = render_slots or render_slots_from_env(settings.render.slots)
    started = time.perf_counter()
    if workdir is not None:
        workdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="tpl-render-", dir=str(workdir) if workdir else None
    ) as tmp:
        tmp_path = pathlib.Path(tmp)
        with slots.acquire(
            timeout_s=settings.timeouts.stage_analyze_s,
            ttl_s=settings.timeouts.render_convert_s * 2,
        ) as lease:
            report.timings_ms["render_slot_wait"] = lease.wait_ms
            pdf = convert_to_pdf(
                pkg.path,
                tmp_path,
                timeout_s=settings.timeouts.render_convert_s,
                settings=settings,
                slot_acquired=True,
            )
        report.renderer = f"onlyoffice ({lease.backend} slots)"
        report.timings_ms["render_pdf"] = int(pdf.seconds * 1000)
        thumbs = render_thumbnails(
            pdf.pdf_path, tmp_path / "png", width_px=settings.render.thumbnail_width_px
        )
        out: dict[int, bytes] = {}
        for i, path in enumerate(thumbs.paths, start=1):
            if i > len(pkg.slides):
                break
            out[i] = path.read_bytes()
    report.timings_ms["render_total"] = int((time.perf_counter() - started) * 1000)
    return out


def render_layout_previews(
    pkg: TemplatePackage,
    settings: Settings,
    render_slots: Any,
    layout_ids: list[str],
    *,
    workdir: pathlib.Path | None,
    report: AnalysisReport,
) -> dict[str, bytes]:
    """Временный PPTX из пустых слайдов перечисленных макетов → PDF под слотом рендера →
    PNG по макету. Плейсхолдеры без текста в PDF не печатаются, фон и декор макета — да."""
    from pptx import Presentation

    from presentation_designer.export.pdf import (
        convert_to_pdf,
    )
    from presentation_designer.export.render_slots import render_slots_from_env
    from presentation_designer.export.thumbnails import render_thumbnails
    from presentation_designer.layout.ooxml import keep_only_slides
    from presentation_designer.layout.package import layout_by_id

    if not layout_ids:
        return {}
    slots = render_slots or render_slots_from_env(settings.render.slots)
    started = time.perf_counter()
    if workdir is not None:
        workdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="tpl-layouts-", dir=str(workdir) if workdir else None
    ) as tmp:
        tmp_path = pathlib.Path(tmp)
        prs = Presentation(str(pkg.path))
        keep_only_slides(prs, [])
        rendered: list[str] = []
        for layout_id in layout_ids:
            layout = layout_by_id(prs, layout_id)
            if layout is None:
                continue
            prs.slides.add_slide(layout)
            rendered.append(layout_id)
        if not rendered:
            return {}
        deck_path = tmp_path / "layouts.pptx"
        prs.save(str(deck_path))
        with slots.acquire(
            timeout_s=settings.timeouts.stage_analyze_s,
            ttl_s=settings.timeouts.render_convert_s * 2,
        ) as lease:
            report.timings_ms["render_layouts_slot_wait"] = lease.wait_ms
            pdf = convert_to_pdf(
                deck_path,
                tmp_path,
                timeout_s=settings.timeouts.render_convert_s,
                settings=settings,
                slot_acquired=True,
            )
        thumbs = render_thumbnails(
            pdf.pdf_path, tmp_path / "png", width_px=settings.render.thumbnail_width_px
        )
        out: dict[str, bytes] = {}
        for layout_id, path in zip(rendered, thumbs.paths, strict=False):
            out[layout_id] = path.read_bytes()
    report.timings_ms["render_layouts"] = int((time.perf_counter() - started) * 1000)
    report.counts["layout_previews"] = len(out)
    return out


def _assemble_profile(
    pkg: TemplatePackage,
    *,
    template_id: str,
    sha256: str,
    name: str,
    size_bytes: int,
    classes: list[Classification],
    assets: list[assets_mod.Asset],
    fixed: list[Any],
    dynamic: list[dict[str, Any]],
    tokens: dict[str, Any],
    patterns: list[Pattern],
    guidelines: list[dict[str, Any]],
    markers: list[str],
    previews: dict[str, bytes],
    report: AnalysisReport,
    sample_indexes: set[int],
) -> dict[str, Any]:
    from presentation_designer.pipeline.state import now_iso

    warnings = list(report.warnings)
    if not patterns:
        warnings.append(
            {
                "code": "no_patterns",
                "message": "в шаблоне не найдено образцов содержания: генерация невозможна "
                "без резервного паттерна",
            }
        )
    for font in tokens["typography"]["fonts"]:
        if not font["usage_count"] or font["available_in_renderer"]:
            continue
        if text_metrics.resolve_font(font["family"]).metric_equivalent:
            # Клон с теми же ширинами: миниатюры и PDF отличаются рисунком букв, измерение
            # текста точное, в PPTX остаётся исходное имя — это заметка, а не предупреждение.
            warnings.append(
                {
                    "code": "font_metric_equivalent",
                    "message": f"шрифт {font['family']} в миниатюрах и PDF показан метрически "
                    f"совместимым {font['fallback']}; в PPTX остаётся {font['family']}",
                }
            )
            continue
        warnings.append(
            {
                "code": "font_substituted",
                "message": f"шрифт {font['family']} недоступен в рендерере"
                + (f", подмена {font['fallback']}" if font.get("fallback") else ""),
            }
        )
    if report.vlm and report.vlm.get("errors"):
        warnings.append({"code": "vlm_partial", "message": "; ".join(report.vlm["errors"])[:300]})

    layouts_out = []
    for lay in pkg.layouts:
        placeholders = []
        for ph in lay.placeholders:
            item: dict[str, Any] = {
                "idx": ph.placeholder_idx or 0,
                "type": ph.placeholder_type or "other",
                "bbox": ph.bbox,
            }
            placeholders.append(item)
        layouts_out.append(
            {
                "layout_id": lay.layout_id,
                "name": lay.name,
                "master_id": lay.master_id,
                "placeholders": placeholders,
                "sample_slide_count": lay.sample_slide_count,
            }
        )
    sample_slides = []
    for c in sorted(classes, key=lambda c: c.slide_index):
        slide = pkg.slides[c.slide_index - 1]
        entry: dict[str, Any] = {
            "slide_index": c.slide_index,
            "pptx_slide_part": slide.part,
            "classification": c.kind,
            "confidence": round(c.confidence, 2),
        }
        if slide.layout_id:
            entry["layout_id"] = slide.layout_id
        preview = f"{PREVIEW_DIR}/slide-{c.slide_index:02d}.png"
        if preview in previews:
            entry["preview_path"] = preview
        pattern = next((p for p in patterns if p.slide.index == c.slide_index), None)
        if pattern and pattern.group_id:
            entry["group_id"] = pattern.group_id
        sample_slides.append(entry)

    profile: dict[str, Any] = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "template_id": template_id,
        "template_hash": f"sha256:{sha256}",
        "source_file": {"name": name, "size_bytes": size_bytes, "format": "pptx"},
        "analyzer": {"name": ANALYZER_NAME, "version": ANALYZER_VERSION},
        "created_at": now_iso(),
        "slide_size": {
            "width_emu": pkg.width_emu,
            "height_emu": pkg.height_emu,
            "aspect_ratio": pkg.aspect_ratio,
        },
        "masters": [
            {"master_id": m.master_id, "name": m.name, "theme_name": m.theme.name}
            for m in pkg.masters
        ],
        "layouts": layouts_out,
        "design_tokens": tokens,
        "guides": guides_of(pkg, sample_indexes),
        "fixed_elements": [f.as_dict() for f in fixed],
        "assets": [a.as_dict() for a in assets],
        "patterns": [p.as_dict(pkg) for p in patterns],
        "guidelines": guidelines,
        "placeholder_markers": markers,
        "stats": {
            "slides": len(pkg.slides),
            "layouts": len(pkg.layouts),
            "masters": len(pkg.masters),
            "media": pkg.media_count,
            "native_charts": sum(1 for s in pkg.slides for sh in s.shapes if sh.kind == "chart"),
            "native_tables": sum(1 for s in pkg.slides for sh in s.shapes if sh.kind == "table"),
            "smartart": sum(1 for s in pkg.slides for sh in s.shapes if sh.kind == "smartart"),
            "embedded_fonts": pkg.embedded_font_faces,
            "notes_with_text": pkg.notes_with_text,
        },
        "warnings": warnings,
        "sample_slides": sample_slides,
        "dynamic_fields": dynamic,
    }
    # Собственные композиции библиотеки: они дополняют пул шаблона, а не заменяют его.
    # Паттерны шаблона уже в профиле и в отборе идут первыми; эти подключаются, когда
    # шаблон не покрывает нужную подачу или вместимость.
    profile["patterns"].extend(builtin_patterns(profile))
    profile["llm_digest"] = build_digest(profile)
    return profile


def build_digest(profile: dict[str, Any]) -> str:
    """Компактное описание для промптов планировщика: палитра, шрифты, шкала, паттерны."""
    tokens = profile["design_tokens"]
    palette = ", ".join(f"{c['hex']} ({c['role']})" for c in tokens["colors"]["palette"][:8])
    fonts = ", ".join(
        f"{f['family']}{' (подмена: ' + f['fallback'] + ')' if f.get('fallback') else ''}"
        for f in tokens["typography"]["fonts"][:3]
    )
    scale = ", ".join(f"{s['role']} {s['size_pt']:g}" for s in tokens["typography"]["scale"][:8])
    lines = [
        f"Размер слайда {profile['slide_size']['width_emu']}×"
        f"{profile['slide_size']['height_emu']} EMU.",
        f"Палитра: {palette or 'из темы'}. Шрифты: {fonts or 'тема'}. "
        f"Шкала кеглей: {scale or 'не выведена'}.",
        f"Паттернов: {len(profile['patterns'])}; ресурсов: {len(profile['assets'])}; "
        f"постоянных элементов: {len(profile['fixed_elements'])}.",
    ]
    for p in profile["patterns"][:40]:
        slots = []
        for s in p["slots"]:
            cap = s.get("capacity") or {}
            extra = f"≤{cap['max_chars']} зн." if cap.get("max_chars") else ""
            if s.get("repeat_group"):
                extra = (extra + " " if extra else "") + f"группа {s['repeat_group']}"
            slots.append(f"{s['slot_id']}{' (' + extra + ')' if extra else ''}")
        constraints = p.get("constraints", {})
        items = f", элементов до {constraints['max_items']}" if constraints.get("max_items") else ""
        lines.append(f"- {p['pattern_id']} [{p['role']}] «{p['name']}»: {', '.join(slots)}{items}")
    if profile.get("guidelines"):
        lines.append("Правила шаблона: " + " | ".join(g["text"] for g in profile["guidelines"][:6]))
    return "\n".join(lines)[:6000]


__all__ = [
    "ANALYZER_NAME",
    "ANALYZER_VERSION",
    "AnalysisReport",
    "AnalysisResult",
    "PackageError",
    "analyze_template",
    "build_digest",
    "profile_key",
    "render_previews",
]
