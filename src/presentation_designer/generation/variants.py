"""Планы трёх вариантов (SlidePlan) из общего StoryPlan и профиля шаблона.

Порядок для одного варианта: структура колоды кодом (титульный, разделители, финальный слайд
по ролям; содержательные тезисы делятся на пакеты по разделам; бюджет слайдов на пакет) →
запросы модели по пакетам параллельно под общим deadline: модель получает смысловой план,
тезисы пакета с фактами и данными и правила оси плотности; пишет структурированное
содержание, ссылаясь на факты через {fact:<id>} → проверка ресурсов и выбор композиции
по содержанию кодом (`matching`) → сборка слотов и измерение ёмкости
кодом (`capacity`): негодный пакет повторяется с подсказкой, остальные не трогаются →
сборка колоды: разделители, разделение больших таблиц и переполненных списков, точное число
или диапазон слайдов, покрытие обязательных тезисов → проверка контракта и связей.

Содержание не переписывается трижды: тезисы, факты и наборы данных общие, вариант меняет
только группировку, композиции, объём формулировок и способ визуализации. Готовый план
кэшируется по StoryPlan (content_hash), структуре и ёмкости паттернов профиля, метрикам
шрифтов, эффективным настройкам и версиям скилла, промпта и схемы. Три варианта сравниваются
по последовательности паттернов и способам визуализации (`compare_plans`); если шаблон не
даёт трёх разных подач, это записывается предупреждением, а не скрывается.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from presentation_designer.contracts import ContentPackage, SlidePlan, StoryPlan, TemplateProfile
from presentation_designer.contracts.validators import check_slide_plan
from presentation_designer.generation import capacity as cap
from presentation_designer.generation.grounding import (
    CONCEPT_POLICY,
    CONCEPT_WARNING,
    concept_notice,
    concept_title,
    is_concept,
)
from presentation_designer.generation.matching import (
    CONTENT_VISUALS,
    Need,
    PatternInfo,
    SlotInfo,
    candidates_for,
    cover_like,
    fallback_visual,
    fixed_pattern_pool,
    pick_style,
    profile_patterns,
    sequence_ok,
    siblings_of,
)
from presentation_designer.generation.outline import OutlineSlide, slide_of, user_outline
from presentation_designer.layout import diagrams
from presentation_designer.parsing.content.facts import weak_metric
from presentation_designer.shared import text_metrics
from presentation_designer.shared.settings import Settings, get_settings
from presentation_designer.shared.text import plural

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]

PLAN_VERSION = "0.4.6"
PLAN_SCHEMA_VERSION = "1.3"
# Версии плана, отличающиеся от текущей только добавленными необязательными полями: план
# прежней ревизии (правки из чата и редактора читают его с диска) поднимается до текущей.
COMPATIBLE_PLAN_SCHEMAS = ("1.2",)


def upgrade_plan_schema(plan: JsonDict) -> JsonDict:
    """План прежней совместимой версии схемы → текущая (копия, если версия менялась)."""
    version = str(plan.get("schema_version") or "")
    if version == PLAN_SCHEMA_VERSION or version not in COMPATIBLE_PLAN_SCHEMAS:
        return plan
    return {**plan, "schema_version": PLAN_SCHEMA_VERSION}


VARIANTS = ("compact", "balanced", "detailed")
DEFAULT_RANGE = (10, 15)
CHART_TYPES = ("column", "bar", "stacked_column", "line", "area", "pie", "doughnut", "scatter")
MAX_SERIES = 5
MAX_TABLE_COLUMNS = 5
FACT_REF = cap.FACT_REF
SERVICE_KINDS = ("title", "divider", "final")
SERVICE_MIN_FONT_RATIO = 0.5

VARIANT_RULES = {
    "compact": (
        "Компактная подача: слайдов ближе к нижней границе, один слайд может объединять два-три "
        "родственных тезиса одного раздела. Заголовок-вывод и два-три коротких пункта "
        "(до 8 слов) или показателей; пояснительный текст не нужен. Цифры выносятся в "
        "показатели (number), ряды данных — в диаграмму, если для тезиса допустимо chart_to_number "
        "— достаточно итогового показателя; таблицы заменяются диаграммой или показателями. "
        "Тезисы с required=false и допустимым drop_entirely не показываются."
    ),
    "balanced": (
        "Сбалансированная подача: один тезис на слайд, заголовок-вывод, короткое пояснение "
        "(одно-два предложения) и до четырёх пунктов или показателей. Ряды данных — диаграмма "
        "с подписями; таблица — если её нужно читать по строкам. Необязательные тезисы "
        "остаются, если укладываются в бюджет слайдов."
    ),
    "detailed": (
        "Подробная подача: слайдов ближе к верхней границе, разделы получают разделители. "
        "Тезис раскрывается пояснением и примерами, до шести пунктов с подписями (sub), "
        "карточки и две колонки для перечислений, таблицы там, где есть слот таблицы, ряды "
        "данных — диаграмма плюс вывод текстом. Все необязательные тезисы показываются."
    ),
}

# Схема ответа модели на один пакет: короче контракта, слоты и измерения добавляет код.
PLAN_MODEL_SCHEMA: JsonDict = {
    "type": "object",
    "required": ["slides"],
    "properties": {
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                # Содержание обязательно (пустое допустимо): по схеме модель пропускает
                # необязательные поля и возвращала слайды из одного заголовка.
                "required": ["theses", "title", "text", "items", "facts", "visual"],
                "properties": {
                    "theses": {"type": "array", "items": {"type": "string"}},
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["sub", "text", "facts", "icon"],
                            "properties": {
                                "sub": {"type": "string"},
                                "text": {"type": "string"},
                                "facts": {"type": "array", "items": {"type": "string"}},
                                "icon": {"type": "string"},
                            },
                        },
                    },
                    "facts": {"type": "array", "items": {"type": "string"}},
                    "visual": {"type": "string", "enum": list(CONTENT_VISUALS)},
                    "message": {"type": "string"},
                    "pattern": {"type": "string"},
                    "dataset": {"type": "string"},
                    "chart_type": {"type": "string", "enum": list(CHART_TYPES)},
                    "diagram_kind": {"type": "string", "enum": list(diagrams.KINDS)},
                    "columns": {"type": "array", "items": {"type": "string"}},
                    "image": {"type": "string"},
                    "notes": {"type": "string"},
                },
            },
        },
        "rationale": {"type": "string"},
    },
}


class PlanError(RuntimeError):
    """План варианта не построен: код для контракта error и структурированные подробности."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: JsonDict | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.details = details or {}


@dataclass
class PlanResult:
    plan: JsonDict
    report: JsonDict = field(default_factory=dict)


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---------- контекст варианта ----------


@dataclass
class SlideSpec:
    """Требование к числу слайдов: точное число или диапазон и цель варианта внутри него.
    explicit — требование задано запросом или брифом; иначе это диапазон по умолчанию, и
    нехватка содержания на его нижнюю границу записывается предупреждением, а не ошибкой."""

    exact: int | None
    min: int
    max: int
    target: int
    explicit: bool = True

    @property
    def lo(self) -> int:
        return self.exact if self.exact is not None else self.min

    @property
    def hi(self) -> int:
        return self.exact if self.exact is not None else self.max

    def as_dict(self) -> JsonDict:
        if self.exact is not None:
            return {"exact": self.exact, "target": self.exact}
        return {"min": self.min, "max": self.max, "target": self.target}


def slide_spec(
    settings: JsonDict, story: JsonDict, variant_id: str, target: int | None
) -> SlideSpec:
    spec = dict((settings or {}).get("slide_count") or {})
    if not spec:
        spec = dict((story.get("effective_brief") or {}).get("slide_count") or {})
    explicit = bool(spec)
    if spec.get("exact"):
        exact = int(spec["exact"])
        return SlideSpec(exact, exact, exact, exact, True)
    lo = int(spec.get("min") or DEFAULT_RANGE[0])
    hi = int(spec.get("max") or max(DEFAULT_RANGE[1], lo))
    if hi < lo:
        lo, hi = hi, lo
    if target is None:
        share = {"compact": 0.0, "balanced": 0.5, "detailed": 1.0}.get(variant_id, 0.5)
        target = lo + round((hi - lo) * share)
    target = min(max(int(target), lo), hi)
    return SlideSpec(None, lo, hi, target, explicit)


@dataclass
class Thesis:
    id: str
    order: int
    kind: str
    statement: str
    explanation: str
    required: bool
    section: str | None
    fact_refs: list[str]
    dataset_refs: list[str]
    asset_refs: list[str]
    source_refs: list[str]
    visual: str | None


@dataclass
class Context:
    story: JsonDict
    profile: JsonDict
    package: JsonDict
    variant_id: str
    settings: JsonDict
    app: Settings
    spec: SlideSpec
    patterns: list[PatternInfo] = field(default_factory=list)
    facts: dict[str, JsonDict] = field(default_factory=dict)
    datasets: dict[str, JsonDict] = field(default_factory=dict)
    assets: dict[str, JsonDict] = field(default_factory=dict)
    blocks: dict[str, JsonDict] = field(default_factory=dict)
    theses: list[Thesis] = field(default_factory=list)
    reductions: dict[str, list[JsonDict]] = field(default_factory=dict)
    scale: list[float] = field(default_factory=list)
    slide_w: int = 12192000
    slide_h: int = 6858000
    fixes: list[JsonDict] = field(default_factory=list)
    # Пиктограммы шаблона с тегами анализа: asset_id → основы тегов; словарь — частые теги.
    icons: dict[str, set[str]] = field(default_factory=dict)
    icon_vocab: list[str] = field(default_factory=list)
    icon_uses: dict[str, int] = field(default_factory=dict)
    slide_icons: set[str] = field(default_factory=set)
    # Факты, уже показанные на собираемом слайде крупным числом: пункты их не повторяют.
    slide_shown: set[str] = field(default_factory=set)
    # Последняя попытка раскладки: абзац ставится даже туда, где не помещается.
    force_text: bool = False

    @property
    def has_datasets(self) -> bool:
        return bool(self.datasets)

    def icon_for(self, concept: str, text: str, taken: set[str]) -> str | None:
        """Пиктограмма пункта: по слову-понятию от модели, иначе по словам пункта. Уже
        стоящие на слайде не повторяются, реже использованные в колоде идут первыми."""
        best: tuple[int, int, str] | None = None
        for words, weight in ((_icon_stems(concept), 3), (_icon_stems(text), 1)):
            if not words:
                continue
            for asset_id, tags in self.icons.items():
                if asset_id in taken:
                    continue
                hits = len(words & tags)
                if not hits:
                    continue
                key = (hits * weight, -self.icon_uses.get(asset_id, 0), asset_id)
                if best is None or key > best:
                    best = key
            if best is not None:
                break
        if best is None:
            return None
        self.icon_uses[best[2]] = self.icon_uses.get(best[2], 0) + 1
        return best[2]

    def thesis(self, tid: str) -> Thesis | None:
        return next((t for t in self.theses if t.id == tid), None)

    def fix(self, code: str, message: str) -> None:
        self.fixes.append({"code": code, "message": message[:300]})


def build_context(
    story: JsonDict,
    profile: JsonDict,
    package: JsonDict,
    variant_id: str,
    settings: JsonDict,
    app: Settings,
    slide_count: int | None,
) -> Context:
    from presentation_designer.generation.design_mode import profile_for_mode

    profile = profile_for_mode(profile, settings)
    ctx = Context(
        story=story,
        profile=profile,
        package=package,
        variant_id=variant_id,
        settings=settings or {},
        app=app,
        spec=slide_spec(settings or {}, story, variant_id, slide_count),
    )
    ctx.patterns = _without_avoided(
        profile_patterns(profile), (settings or {}).get("avoid_patterns") or []
    )
    ctx.facts = {f["fact_id"]: f for f in package.get("facts", [])}
    ctx.datasets = {d["dataset_id"]: d for d in package.get("datasets", [])}
    ctx.assets = {a["asset_id"]: a for a in package.get("assets", [])}
    ctx.blocks = {b["block_id"]: b for b in package.get("blocks", [])}
    size = profile.get("slide_size") or {}
    ctx.slide_w = int(size.get("width_emu") or ctx.slide_w)
    ctx.slide_h = int(size.get("height_emu") or ctx.slide_h)
    scale = ((profile.get("design_tokens") or {}).get("typography") or {}).get("scale") or []
    ctx.scale = sorted({float(s["size_pt"]) for s in scale if s.get("size_pt")}, reverse=True)
    section: str | None = None
    for raw in sorted(story.get("theses", []), key=lambda t: t["order"]):
        kind = raw["kind"]
        if kind == "section":
            section = raw["thesis_id"]
        ctx.theses.append(
            Thesis(
                id=raw["thesis_id"],
                order=int(raw["order"]),
                kind=kind,
                statement=str(raw.get("statement", "")),
                explanation=str(raw.get("explanation", "") or ""),
                required=bool(raw.get("required", True)),
                section=raw.get("parent_id") or (section if kind != "section" else None),
                fact_refs=list(raw.get("fact_refs") or []),
                dataset_refs=list(raw.get("dataset_refs") or []),
                asset_refs=list(raw.get("asset_refs") or []),
                source_refs=list(raw.get("source_refs") or []),
                visual=raw.get("suggested_visual"),
            )
        )
    for red in story.get("allowed_reductions") or []:
        ctx.reductions.setdefault(red["thesis_id"], []).append(red)
    counts: dict[str, int] = {}
    for asset in profile.get("assets") or []:
        if asset.get("kind") != "icon" or not asset.get("reusable", True):
            continue
        tags = [
            str(t).lower() for t in asset.get("tags") or [] if str(t).lower() not in _ICON_NOISE
        ]
        if not tags:
            continue
        ctx.icons[str(asset["asset_id"])] = _icon_stems(" ".join(tags))
        for tag in tags:
            counts[tag] = counts.get(tag, 0) + 1
    ctx.icon_vocab = [t for t, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:90]
    return ctx


# Теги-пустышки анализа: подложки и заглушки, а не пиктограммы смысла.
_ICON_NOISE = {"catalog", "пусто", "фон", "круг", "заглушка", "декор", "элемент"}


# ---------- структура колоды ----------


@dataclass
class Draft:
    """Черновик слайда до сборки блоков."""

    kind: str  # title | divider | agenda | content | final | summary
    theses: list[str]
    pattern: PatternInfo
    title: str
    message: str = ""
    visual: str = "text"
    text: str = ""
    items: list[JsonDict] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    dataset: str | None = None
    chart_type: str | None = None
    diagram_kind: str | None = None
    columns: list[str] = field(default_factory=list)
    image: str | None = None
    notes: str = ""
    section: str | None = None
    source_refs: list[str] = field(default_factory=list)
    row_offset: int = 0
    max_rows: int | None = None
    total_rows: int | None = None
    blocks: list[JsonDict] = field(default_factory=list)
    overflow: list[JsonDict] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    reduction: str | None = None
    candidates: list[PatternInfo] = field(default_factory=list)
    # Слоты, заполненные подписью-наполнителем (сообщение, пояснение): при переполнении
    # необязательного слота такой блок убирается, а не считается потерей содержания.
    filler_slots: set[str] = field(default_factory=set)
    unplaced_text: str = ""

    @property
    def splittable(self) -> bool:
        return (
            self.kind == "content"
            and len(self.items) >= 2
            and self.visual not in ("chart", "table", "diagram", "timeline")
        )

    @property
    def overflow_in_items(self) -> bool:
        """Переполнены пункты списка или карточки — разделение слайда поможет."""
        for o in self.overflow:
            slot = self.pattern.slots.get(o["slot_id"])
            if o["kind"] == "bullets" or (slot is not None and slot.group):
                return True
        return False


@dataclass
class Packet:
    index: int
    theses: list[Thesis]
    target: int
    lo: int
    hi: int
    candidates: dict[str, list[PatternInfo]] = field(default_factory=dict)
    # Раздел раскладки пользователя «Слайд N», из которого этот слайд.
    outline: OutlineSlide | None = None


@dataclass
class Structure:
    title_pattern: PatternInfo | None
    divider_pattern: PatternInfo | None
    final_pattern: PatternInfo | None
    agenda_pattern: PatternInfo | None
    title_theses: list[str]
    final_thesis: Thesis | None
    content: list[Thesis]
    sections: list[Thesis]
    dividers: list[str]  # идентификаторы разделов с разделителями
    dropped: list[Thesis]
    packets: list[Packet]
    content_budget: int
    # Пулы служебных ролей: весь упорядоченный набор паттернов, из которого выбран стиль.
    title_pool: list[PatternInfo] = field(default_factory=list)
    divider_pool: list[PatternInfo] = field(default_factory=list)
    final_pool: list[PatternInfo] = field(default_factory=list)
    agenda_pool: list[PatternInfo] = field(default_factory=list)
    # Обложка по титульному разделу раскладки пользователя: «Заголовок: …», «Подзаголовок: …».
    cover_title: str = ""
    cover_subtitle: str = ""

    def service_styles(self) -> JsonDict:
        """Стили служебных слайдов колоды для отчёта: паттерн и его style_key по ролям."""

        def entry(p: PatternInfo | None) -> JsonDict | None:
            return {"pattern_id": p.pattern_id, "style_key": p.style_key} if p else None

        return {
            "title": entry(self.title_pattern),
            "divider": entry(self.divider_pattern),
            "final": entry(self.final_pattern),
        }


def _service_order(
    pool: list[PatternInfo], policy: str, tone: str | None, rank: int
) -> list[PatternInfo]:
    """Порядок паттернов служебной роли для варианта: паттерны в тон разделителя идут первыми,
    остальные за ними; список повёрнут на `rank` — порядковый номер варианта среди вариантов с
    тем же тоном разделителя, поэтому два варианта одного тона получают разные образцы (если
    образец этого тона один, второй вариант берёт следующий по пулу — различие вариантов
    важнее тона). Политика `first` — прежнее поведение, порядок пула."""
    if not pool:
        return []
    if policy != "per_variant":
        return list(pool)
    toned = [p for p in pool if tone and tone != "unknown" and p.tone == tone]
    ordered = toned + [p for p in pool if p not in toned]
    shift = rank % len(ordered)
    return ordered[shift:] + ordered[:shift]


def _service_pattern(
    pool: list[PatternInfo], policy: str, tone: str | None, rank: int
) -> PatternInfo | None:
    ordered = _service_order(pool, policy, tone, rank)
    return ordered[0] if ordered else None


def _first_fitting(
    ctx: Context, ordered: list[PatternInfo], title: str, message: str
) -> PatternInfo | None:
    """Первый паттерн порядка, в который заголовок и подпись помещаются без переполнения после
    лестницы ёмкости; если не помещаются никуда — первый (переполнение запишет план)."""
    for p in ordered:
        draft = fit_draft(
            ctx, Draft(kind="title", theses=[], pattern=p, title=title, message=message)
        )
        _drop_overflowing_optional(draft)
        if not draft.overflow:
            return p
    return ordered[0] if ordered else None


def _roomy_pool(pool: list[PatternInfo], ratio: float = 0.5) -> list[PatternInfo]:
    """Паттерны пула, чей заголовок вмещает не меньше `ratio` от самого вместительного: узкий
    заголовок «паттерн + фото» (18 символов при 42 у остальных разделителей VK Education) стиль
    колоды не задаёт — названия разделов в него не помещаются. Пустой результат — весь пул."""
    best = max((p.title.max_chars for p in pool if p.title is not None), default=0)
    if not best:
        return pool
    roomy = [p for p in pool if p.title is None or p.title.max_chars >= ratio * best]
    return roomy or pool


def _style_anchor(
    divider_pool: list[PatternInfo], variant_id: str, policy: str
) -> tuple[PatternInfo | None, str | None, int]:
    """Разделитель варианта, его тон и номер варианта среди вариантов с тем же тоном."""
    chosen = {v: pick_style(divider_pool, v, policy) for v in VARIANTS}
    divider = chosen.get(variant_id) or pick_style(divider_pool, variant_id, policy)
    tone = divider.tone if divider is not None else None
    before = VARIANTS[: VARIANTS.index(variant_id)] if variant_id in VARIANTS else ()
    rank = 0
    for v in before:
        other = chosen[v]
        if (other.tone if other is not None else None) == tone:
            rank += 1
    return divider, tone, rank


def deck_structure(ctx: Context, *, use_agenda: bool | None = None) -> Structure:
    variant = ctx.variant_id
    has_ds = ctx.has_datasets
    policy = str(ctx.app.plan.style_policy)
    title_pool = fixed_pattern_pool(ctx.patterns, "title", has_datasets=has_ds)
    # Обложка — образец на титульном макете, если он есть: чередование стилей между
    # вариантами не должно выдавать за титул карточку с макета «Фотографии» (ЛЦТ).
    title_pool = [p for p in title_pool if cover_like(p)] or title_pool
    divider_pool = fixed_pattern_pool(ctx.patterns, "section_divider", has_datasets=has_ds)
    final_pool = fixed_pattern_pool(ctx.patterns, "thanks", has_datasets=has_ds)
    # Шаблон без финального слайда закрывается обложкой (пул взят из титулов): тоже только
    # образцами на титульном макете.
    final_pool = [p for p in final_pool if p.role != "title" or cover_like(p)] or final_pool
    agenda_pool = fixed_pattern_pool(ctx.patterns, "agenda", has_datasets=has_ds)
    # Разделитель задаёт стиль колоды: единый внутри неё, разный у вариантов (per_variant);
    # титул, финал и оглавление берутся в тон разделителя своим номером среди вариантов
    # этого тона. Образцы с заголовком вдвое уже лучшего в пуле стиль не задают.
    divider_pattern, tone, rank = _style_anchor(_roomy_pool(divider_pool), variant, policy)
    # Титул: первый по порядку стиля образец, в который тема и подпись помещаются (у VK
    # Education второй титул уже первого, длинная тема в него не входит).
    brief = ctx.story.get("effective_brief") or {}
    title_pattern = _first_fitting(
        ctx,
        _service_order(title_pool, policy, tone, rank),
        _cover_title(ctx.story),
        _clean(
            concept_notice(str(ctx.story.get("language", "ru")))
            if is_concept(ctx.story)
            else (brief.get("audience") and f"Для: {brief['audience']}")
            or ctx.story.get("key_takeaway"),
            200,
        ),
    )
    final_pattern = _service_pattern(final_pool, policy, tone, rank)
    agenda_pattern = _service_pattern(agenda_pool, policy, tone, rank)
    if title_pattern is None:
        raise PlanError(
            "plan_no_title_pattern",
            "В профиле шаблона нет паттерна с заголовком для титульного слайда",
            details={"template_id": ctx.profile.get("template_id")},
        )
    outline = user_outline(ctx.package)
    if outline:
        n = len(outline) + (0 if outline[0].cover else 1)
        if not ctx.spec.explicit or ctx.spec.lo <= n <= ctx.spec.hi:
            return _outline_structure(
                ctx,
                outline,
                Structure(
                    title_pattern=title_pattern,
                    divider_pattern=divider_pattern,
                    final_pattern=None,
                    agenda_pattern=None,
                    title_theses=[],
                    final_thesis=None,
                    content=[],
                    sections=[],
                    dividers=[],
                    dropped=[],
                    packets=[],
                    content_budget=0,
                    title_pool=title_pool,
                    divider_pool=divider_pool,
                    final_pool=final_pool,
                    agenda_pool=agenda_pool,
                ),
            )
        ctx.fix("user_outline_ignored", f"раскладка на {n} слайдов, просили {ctx.spec.as_dict()}")
    theses = ctx.theses
    title_theses: list[str] = []
    if theses and theses[0].kind in ("section", "context") and theses[0].order == 1:
        # Первый раздел обычно совпадает с темой: его покрывает титульный слайд.
        title_theses.append(theses[0].id)
    final_thesis: Thesis | None = None
    if final_pattern is not None and theses and theses[-1].kind in ("call_to_action", "conclusion"):
        final_thesis = theses[-1]
    dropped: list[Thesis] = []
    content: list[Thesis] = []
    for t in theses:
        if t.id in title_theses or (final_thesis is not None and t.id == final_thesis.id):
            continue
        if t.kind == "section":
            continue
        content.append(t)
    fixed = 1 + (1 if final_pattern is not None else 0)
    if variant == "compact":
        # Необязательные тезисы с допустимым drop_entirely убираются с конца, только пока
        # содержания больше бюджета слайдов: иначе модель раскрывает их сама, а код не
        # возвращает их потом слайдами из одного пункта.
        for t in reversed(list(content)):
            if len(content) <= ctx.spec.target - fixed:
                break
            reds = {r["reduction"] for r in ctx.reductions.get(t.id, [])}
            if not t.required and "drop_entirely" in reds:
                content.remove(t)
                dropped.insert(0, t)
                ctx.fix("reduction_applied", f"{t.id}: drop_entirely в компактном варианте")
    sections = [t for t in theses if t.kind == "section" and t.id not in title_theses]
    spare = ctx.spec.target - fixed - len(content)
    dividers: list[str] = []
    if divider_pattern is not None and variant != "compact" and spare > 0:
        # Разделители получают разделы с наибольшим числом тезисов, не более запаса.
        ranked = sorted(
            [s for s in sections if _divider_fits(ctx, divider_pattern, s)],
            key=lambda s: (-sum(1 for t in content if t.section == s.id), s.order),
        )
        dividers = [s.id for s in ranked[: min(len(ranked), spare)]]
        dividers.sort(key=lambda sid: next(t.order for t in theses if t.id == sid))
        spare -= len(dividers)
    agenda = False
    if use_agenda is None:
        use_agenda = variant == "detailed"
    if use_agenda and agenda_pattern is not None and spare > 0 and len(sections) >= 2:
        agenda = True
        spare -= 1
    content_budget = ctx.spec.target - fixed - len(dividers) - (1 if agenda else 0)
    content_budget = max(content_budget, 1 if content else 0)
    packets = make_packets(ctx, content, content_budget)
    return Structure(
        title_pattern=title_pattern,
        divider_pattern=divider_pattern,
        final_pattern=final_pattern,
        agenda_pattern=agenda_pattern if agenda else None,
        title_theses=title_theses,
        final_thesis=final_thesis,
        content=content,
        sections=sections,
        dividers=dividers,
        dropped=dropped,
        packets=packets,
        content_budget=content_budget,
        title_pool=title_pool,
        divider_pool=divider_pool,
        final_pool=final_pool,
        agenda_pool=agenda_pool,
    )


def _outline_structure(ctx: Context, outline: list[OutlineSlide], base: Structure) -> Structure:
    """Колода по раскладке пользователя «Слайд N»: обложка — титульный раздел, дальше по
    слайду на раздел в его порядке. Тезис относится к разделу по своим блокам и блокам своих
    фактов, без ссылок — к разделу предыдущего тезиса. Разделителей, оглавления и финала
    «Спасибо» нет: их пользователь не раскладывал, и они вытесняли его слайды."""
    cover = outline[0] if outline[0].cover else None
    slides = outline[1:] if cover else outline
    first = 1 if cover else 0
    groups: list[list[Thesis]] = [[] for _ in slides]
    last: int | None = None
    for t in ctx.theses:
        if t.kind == "section":
            continue
        where = slide_of(outline, _thesis_sources(ctx, t, limit=10))
        where = where if where is not None else (last if last is not None else first)
        last = where
        if cover is not None and where == 0:
            base.title_theses.append(t.id)
        else:
            groups[where - first].append(t)
    # Порядок колоды — порядок раскладки, а не порядок тезисов сюжета.
    for i, group in enumerate(groups):
        for n, t in enumerate(group):
            t.order = 1000 * (i + 1) + n
    ctx.theses.sort(key=lambda t: t.order)
    kept = [(s, g) for s, g in zip(slides, groups, strict=True) if g]
    for s, g in zip(slides, groups, strict=True):
        if not g:
            ctx.fix("outline_slide_missing", f"«{s.title[:60]}»: в сюжете нет тезиса раздела")
    base.packets = [Packet(i, g, 1, 1, 1, outline=s) for i, (s, g) in enumerate(kept)]
    base.content = [t for _, g in kept for t in g]
    base.content_budget = len(base.packets)
    if cover is not None:
        base.cover_title = cover.fields.get("заголовок", "")
        base.cover_subtitle = cover.fields.get("подзаголовок", "")
    n = len(base.packets) + 1
    ctx.spec = SlideSpec(n, n, n, n, True)
    ctx.fix("user_outline", f"раскладка пользователя: {len(outline)} слайдов «Слайд N»")
    return base


def make_packets(ctx: Context, content: list[Thesis], budget: int) -> list[Packet]:
    """Пакеты по разделам: подряд идущие тезисы, не длиннее packet_theses; раздел не рвётся,
    если помещается целиком. Бюджет слайдов делится по весу тезисов."""
    size = max(1, int(ctx.app.plan.packet_theses))
    groups: list[list[Thesis]] = []
    for t in content:
        if groups and groups[-1][-1].section == t.section and len(groups[-1]) < size:
            groups[-1].append(t)
        elif (
            groups
            and len(groups[-1]) + 1 <= size
            and (groups[-1][-1].section == t.section or len(groups[-1]) < max(2, size // 2))
        ):
            groups[-1].append(t)
        else:
            groups.append([t])
    if not groups:
        return []
    optional_w = 0.3 if ctx.variant_id == "compact" else 0.6
    weights = [sum(1.0 if t.required else optional_w for t in g) for g in groups]
    total = sum(weights) or 1.0
    raw = [budget * w / total for w in weights]
    targets = [max(1, math.floor(r)) for r in raw]
    # Остаток от округления — пакетам с наибольшей дробной частью.
    remainder = budget - sum(targets)
    order = sorted(range(len(groups)), key=lambda i: -(raw[i] - math.floor(raw[i])))
    i = 0
    while remainder > 0 and order:
        targets[order[i % len(order)]] += 1
        remainder -= 1
        i += 1
    packets: list[Packet] = []
    for idx, (g, tgt) in enumerate(zip(groups, targets, strict=True)):
        # Нижняя граница пакета близка к цели и для компактного варианта: его плотность —
        # это объём текста на слайде, а число слайдов задано диапазоном запроса; недобор
        # кодом добирается делением списков, что хуже, чем раскрытие тезисов моделью.
        lo = max(1, math.ceil(tgt * 0.7))
        hi = tgt + 1
        packets.append(Packet(idx, g, tgt, lo, hi))
    return packets


# ---------- потребности и кандидаты ----------


def thesis_need(ctx: Context, t: Thesis) -> Need:
    visual = t.visual or ("number" if t.fact_refs else "text")
    reds = {r["reduction"] for r in ctx.reductions.get(t.id, [])}
    if ctx.variant_id == "compact":
        if visual == "chart" and "chart_to_number" in reds:
            visual = "number"
        if visual == "table":
            visual = "chart"
    if ctx.variant_id == "detailed" and visual == "chart" and t.dataset_refs:
        # Таблица читается по строкам: подробный вариант предпочитает её там, где есть слот.
        if any(p.has_table for p in ctx.patterns):
            visual = "table"
    has_ds = bool(t.dataset_refs) and all(d in ctx.datasets for d in t.dataset_refs)
    if visual in ("chart", "table") and not has_ds:
        visual = "number" if t.fact_refs else "bullets"
    items = 1
    block = next((ctx.blocks[b] for b in t.source_refs if b in ctx.blocks), None)
    if block is not None and block.get("kind") == "bullets":
        items = len(block.get("items") or [])
    if visual in ("bullets", "cards", "timeline", "diagram", "comparison"):
        items = max(items, 3)
    numbers = len([f for f in t.fact_refs if f in ctx.facts])
    return Need(
        visual=fallback_visual(visual, ctx.patterns, has_datasets=has_ds),
        items=items,
        numbers=min(numbers, 4) if visual == "number" else 0,
        text_chars=len(t.statement) + len(t.explanation),
        has_dataset=has_ds,
        has_image=bool(t.asset_refs),
    )


def packet_candidates(ctx: Context, packet: Packet) -> dict[str, list[PatternInfo]]:
    per = max(1, int(ctx.app.plan.candidates_per_thesis))
    out: dict[str, list[PatternInfo]] = {}
    for t in packet.theses:
        need = thesis_need(ctx, t)
        has_ds = need.has_dataset
        found = candidates_for(
            ctx.patterns,
            need,
            ctx.variant_id,
            limit=per,
            has_datasets=has_ds,
            include_library_alternative=True,
        )
        if not found:
            found = candidates_for(
                ctx.patterns,
                Need("text", text_chars=need.text_chars),
                ctx.variant_id,
                limit=per,
                has_datasets=has_ds,
                include_library_alternative=True,
            )
        out[t.id] = found
    return out


def all_candidates(ctx: Context, packets: list[Packet]) -> list[PatternInfo]:
    seen: dict[str, PatternInfo] = {}
    for p in packets:
        for lst in p.candidates.values():
            for c in lst:
                seen.setdefault(c.pattern_id, c)
    limit = max(4, int(ctx.app.plan.max_candidates))
    return list(seen.values())[:limit]


# ---------- выдержка для модели ----------


def _fact_line(f: JsonDict) -> str:
    ctx = f.get("context") or {}
    label = ctx.get("metric") or f.get("label") or "—"
    period = ctx.get("period")
    return f"{f['fact_id']} = «{cap.fact_text(f)}» — {label}" + (f", {period}" if period else "")


def packet_digest(ctx: Context, structure: Structure, packet: Packet) -> str:
    story = ctx.story
    brief = story.get("effective_brief") or {}
    lines: list[str] = []
    lines.append(f"Вариант: {ctx.variant_id}. {VARIANT_RULES.get(ctx.variant_id, '')}")
    if is_concept(story):
        lines.append(CONCEPT_POLICY)
        lines.append("Оговорка на слайдах: " + concept_notice(str(story.get("language", "ru"))))
        lines.append(
            "Каждый слайд пакета — конкретное авторское предложение. Заголовок до 55 знаков, "
            "начинай с «Идея:», «Предлагаем» или «Гипотеза:». Не снимай условность при "
            "сокращении. Дай 2–3 разных пункта: сценарий пользователя, проектное решение, "
            "проверка или компромисс. В items sub — короткий заголовок пункта (до 35 знаков), "
            "text — его пояснение (до 110 знаков). Не заменяй пункты "
            "перефразированием общего заголовка. Не используй сведения о распространённости "
            "поведения пользователей как факты. Не создавай слайды с одним заголовком."
        )
    lines.append(
        "Выбирай подачу по смыслу: показатель, сравнение, этапы, смысловые блоки. "
        "Оформление и композицию выбирает код после получения содержания. "
        "Разнообразие не оправдывает пустые блоки, выдуманные данные или потерю фактов. "
        "Номера шагов — не слоты для показателей."
    )
    lines.append(
        "Бриф: "
        + "; ".join(
            s
            for s in (
                f"тема — «{brief.get('title', '')}»" if brief.get("title") else "",
                f"аудитория — {brief['audience']}" if brief.get("audience") else "",
                f"цель — {brief['goal']}" if brief.get("goal") else "",
                f"тон — {brief['tone']}" if brief.get("tone") else "",
                f"язык — {story.get('language', 'ru')}",
            )
            if s
        )
        + "."
    )
    if brief.get("avoid"):
        lines.append("Избегать: " + "; ".join(brief["avoid"]) + ".")
    lines.append(f"Главный вывод: {story.get('key_takeaway', '')}")
    lines.append("Весь план (для связности; титульный, разделители и финал делает код):")
    packet_ids = {t.id for t in packet.theses}
    for t in ctx.theses:
        mark = " ← этот пакет" if t.id in packet_ids else ""
        flag = "обяз." if t.required else "необяз."
        lines.append(f"{t.id} [{t.kind}, {flag}] {t.statement[:140]}{mark}")
    if packet.outline is not None:
        slide = packet.outline
        lines.append(
            f"Это слайд {slide.number} из раскладки пользователя «{slide.title}»: "
            "ровно один слайд; заголовок — по заголовку раздела (без слов «Слайд N»), "
            "содержание — пункты и цифры раздела, ничего не выдумывай и не теряй."
        )
    lines.append(
        "Тезисы пакета (идентификатор · вид · обязательный · формулировка · пояснение · "
        "факты · данные · изображения · предлагаемая подача · допустимые сокращения):"
    )
    fact_ids: list[str] = []
    ds_ids: list[str] = []
    asset_ids: list[str] = []
    shown: list[str] = []
    for t in packet.theses:
        reds = ", ".join(r["reduction"] for r in ctx.reductions.get(t.id, [])) or "—"
        need = thesis_need(ctx, t)
        lines.append(
            f"{t.id} · {t.kind} · {'да' if t.required else 'нет'} · {t.statement} · "
            f"{t.explanation or '—'} · {', '.join(t.fact_refs) or '—'} · "
            f"{', '.join(t.dataset_refs) or '—'} · {', '.join(t.asset_refs) or '—'} · "
            f"{need.visual} · {reds}"
        )
        fact_ids.extend(f for f in t.fact_refs if f in ctx.facts and f not in fact_ids)
        ds_ids.extend(d for d in t.dataset_refs if d in ctx.datasets and d not in ds_ids)
        asset_ids.extend(a for a in t.asset_refs if a in ctx.assets and a not in asset_ids)
        shown.extend(b for b in _thesis_sources(ctx, t) if b not in shown)
        for b in _thesis_sources(ctx, t):
            lines.extend(_source_lines(ctx, b))
    if packet.outline is not None:
        # Раздел пользователя целиком: его пункты и цифры — содержание этого слайда.
        for b in packet.outline.block_ids[1:]:
            if b not in shown:
                shown.append(b)
                lines.extend(_source_lines(ctx, b))
    if fact_ids:
        lines.append("Факты (только ссылками {fact:id}; значение подставит код):")
        lines.extend(_fact_line(ctx.facts[f]) for f in fact_ids)
    for d in ds_ids:
        ds = ctx.datasets[d]
        cols = "; ".join(
            f"{c['name']}" + (f" ({c['unit']})" if c.get("unit") else "") for c in ds["columns"]
        )
        rows = " / ".join(" | ".join(str(v) for v in row) for row in ds["rows"][:5])
        total = ds.get("total_rows", len(ds["rows"]))
        lines.append(
            f"Набор данных {d}"
            + (f" «{ds['title']}»" if ds.get("title") else "")
            + f": колонки {cols}; строк {total}; первые: {rows}"
        )
    for a in asset_ids:
        asset = ctx.assets[a]
        lines.append(
            f"Изображение {a} ({asset.get('kind', 'image')}, "
            f"{asset.get('width_px', '?')}×{asset.get('height_px', '?')})"
            + (f" «{asset['caption']}»" if asset.get("caption") else "")
        )
    if ctx.icon_vocab:
        lines.append(
            "Словарь пиктограмм шаблона (для поля icon у пунктов): "
            + ", ".join(ctx.icon_vocab)
            + "."
        )
    lines.append(
        "Сначала сформируй содержание слайдов: заголовок и смысловые блоки. "
        "Композицию и слоты подберёт код после ответа по числу блоков и наличию ресурсов. "
        "Не возвращай pattern, координаты, шрифты или цвета."
    )
    lines.append(
        f"Слайдов в этом пакете: цель {packet.target}, допустимо от {packet.lo} до {packet.hi}. "
        f"Всего в колоде {ctx.spec.target} слайдов, из них содержательных "
        f"{structure.content_budget}."
    )
    return "\n".join(lines)


def _source_lines(ctx: Context, block_id: str) -> list[str]:
    """Блок источника строкой выдержки: список пунктами, абзац текстом."""
    block = ctx.blocks.get(block_id)
    if block is None:
        return []
    if block.get("kind") == "bullets" and block.get("items"):
        items = " | ".join(str(i)[:120] for i in block["items"][:8])
        return [f"  список из источника {block_id}: {items}"]
    if block.get("kind") in ("paragraph", "quote", "kpi", "code") and block.get("text"):
        # Формулировка тезиса — вывод, а раскрывают его подробности источника:
        # без них модель пересказывает заголовок одной фразой.
        return [f"  текст источника {block_id}: {_clean(block['text'], 500)}"]
    return []


def _thesis_sources(ctx: Context, t: Thesis, limit: int = 4) -> list[str]:
    """Блоки источника тезиса: его ссылки, затем блоки, откуда взяты его факты."""
    out = [b for b in t.source_refs if b in ctx.blocks]
    for fid in t.fact_refs:
        block_id = (ctx.facts.get(fid) or {}).get("block_id")
        if block_id and block_id in ctx.blocks and block_id not in out:
            out.append(str(block_id))
    if not out:
        # Модель не всегда ставит source_refs: блок с теми же значимыми словами, что в
        # формулировке, — тот самый источник.
        mine = _stems(f"{t.statement} {t.explanation}")
        scored = []
        for block_id, block in ctx.blocks.items():
            if block.get("kind") not in ("paragraph", "bullets", "quote", "kpi"):
                continue
            text = (
                str(block.get("text") or "")
                + " "
                + " ".join(str(i) for i in block.get("items") or [])
            )
            theirs = _stems(text)
            common = len(mine & theirs)
            if common >= 2 and common >= 0.3 * min(len(mine), len(theirs)):
                scored.append((common, block_id))
        out = [b for _, b in sorted(scored, reverse=True)[:2]]
    return out[:limit]


def _icon_stems(text: str) -> set[str]:
    words = re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", text.lower())
    return {w[:5] for w in words if w not in _ICON_STOP}


_ICON_STOP = {
    "для",
    "что",
    "это",
    "как",
    "при",
    "или",
    "его",
    "все",
    "без",
    "над",
    "под",
    "the",
    "and",
}


def _stems(text: str) -> set[str]:
    return {w[:5].lower() for w in re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", text)}


# ---------- ответ модели → черновики ----------


def _clean(text: Any, limit: int = 600) -> str:
    """Текст одной строкой не длиннее `limit`. Длинный режется по концу предложения, а если
    его нет в последних трёх пятых — по границе слова с многоточием: обрубок «цикл зависим»
    на слайде хуже, чем на предложение короче."""
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    head = value[: limit + 1]
    ends = [m.end() for m in re.finditer(r"[.!?…](?=\s|$)", head) if m.end() <= limit]
    if ends and ends[-1] >= limit * 0.4:
        return head[: ends[-1]].strip()
    cut = head.rfind(" ", 0, limit)
    if cut <= limit * 0.4:
        return value[:limit]
    return head[:cut].rstrip(" ,;:—–-") + "…"


def drafts_from_answer(
    ctx: Context, packet: Packet, answer: JsonDict, *, content_first: bool = False
) -> list[Draft]:
    slides = answer.get("slides")
    if not isinstance(slides, list) or not slides:
        raise ValueError("нужен непустой список slides")
    packet_ids = [t.id for t in packet.theses]
    by_id = {p.pattern_id: p for p in ctx.patterns}
    drafts: list[Draft] = []
    skipped_foreign = 0
    for raw in slides:
        if not isinstance(raw, dict):
            continue
        title = _clean(raw.get("title"), 200)
        if not title:
            raise ValueError("у каждого слайда нужен непустой title")
        theses = [str(t) for t in (raw.get("theses") or []) if str(t) in packet_ids]
        if not theses:
            # Слайд о тезисе чужого пакета (чаще вывод, сославшийся на тезис колоды):
            # его содержание раскрывает свой пакет, а обязательное добирает проверка
            # покрытия. Отказ всего ответа ронял вариант после повторов (25.09.2026).
            ctx.fix("slide_foreign_theses", f"«{title[:40]}»: тезисы не из пакета")
            skipped_foreign += 1
            continue
        visual = str(raw.get("visual") or "text")
        if visual not in CONTENT_VISUALS:
            visual = "text"
        head = ctx.thesis(theses[0])
        cands = packet.candidates.get(theses[0], [])
        pattern = by_id.get(str(raw.get("pattern", "")))
        allowed = {c.pattern_id for t in theses for c in packet.candidates.get(t, [])}
        automatic = content_first or not raw.get("pattern")
        if automatic:
            # Temporary anchor only; the actual choice follows resource validation.
            pattern = next(iter(cands or ctx.patterns), None)
            if pattern is None:
                raise ValueError("нет композиций для содержания")
        elif pattern is None or pattern.pattern_id not in allowed:
            replacement = cands[0] if cands else None
            if replacement is None:
                raise ValueError(
                    f"слайд «{title[:40]}»: композиция {raw.get('pattern')!r} не из списка"
                )
            ctx.fix(
                "pattern_remapped",
                f"«{title[:40]}»: {raw.get('pattern')} → {replacement.pattern_id}",
            )
            pattern = replacement
        items: list[JsonDict] = []
        for it in raw.get("items") or []:
            if isinstance(it, dict) and _clean(it.get("text")):
                item: JsonDict = {"text": _clean(it.get("text"), 300)}
                if _clean(it.get("sub")):
                    item["sub"] = _clean(it.get("sub"), 300)
                if _clean(it.get("icon")):
                    item["icon"] = _clean(it.get("icon"), 40)
                facts = [str(f) for f in (it.get("facts") or []) if str(f) in ctx.facts] + [
                    f
                    for f in FACT_REF.findall(item["text"] + " " + item.get("sub", ""))
                    if f in ctx.facts
                ]
                if facts:
                    item["fact_refs"] = list(dict.fromkeys(facts))
                items.append(item)
            elif isinstance(it, str) and it.strip():
                items.append({"text": _clean(it, 300)})
        items = _distinct_items(ctx, items)
        facts = [str(f) for f in (raw.get("facts") or []) if str(f) in ctx.facts]
        for item in items:
            facts.extend(f for f in item.get("fact_refs", []) if f not in facts)
        text = _clean(raw.get("text"), 1200)
        for ref in FACT_REF.findall(text + " " + title):
            if ref in ctx.facts and ref not in facts:
                facts.append(ref)
        dataset = str(raw.get("dataset") or "") or None
        if dataset and dataset not in ctx.datasets:
            ctx.fix("dataset_unknown", f"«{title[:40]}»: {dataset}")
            dataset = None
        if dataset is None and head is not None and visual in ("chart", "table"):
            dataset = next((d for d in head.dataset_refs if d in ctx.datasets), None)
        image = str(raw.get("image") or "") or None
        if image and image not in ctx.assets:
            ctx.fix("asset_unknown", f"«{title[:40]}»: {image}")
            image = None
        chart_type = raw.get("chart_type") if raw.get("chart_type") in CHART_TYPES else None
        diagram_kind = raw.get("diagram_kind")
        if visual == "timeline":
            diagram_kind = "timeline"
        elif visual != "diagram" or diagram_kind not in diagrams.KINDS:
            diagram_kind = None
        if visual == "diagram" and not diagram_kind:
            # An unordered list is not evidence of a process or a hierarchy.
            visual = "bullets" if items else "text"
        if visual == "quote" and not re.search(r"[«»\"“”„]", text + " " + title):
            # Цитата — чужие слова в кавычках; утверждение в композиции цитаты теряет
            # пояснение (у образца цитаты нет места под текст).
            visual = "bullets" if items else "text"
        columns = _match_columns(ctx, dataset, [str(c) for c in (raw.get("columns") or [])])
        source_refs: list[str] = []
        written = " ".join(
            cap.substitute_facts(
                " ".join([title, text, *[f"{it.get('sub', '')} {it['text']}" for it in items]]),
                ctx.facts,
            ).split()
        )
        for tid in theses:
            t = ctx.thesis(tid)
            if t:
                source_refs.extend(b for b in t.source_refs if b in ctx.blocks)
                # Факт тезиса привязывается к слайду, если его значение на слайде написано
                # (модель забыла поле facts). Все факты тезиса подряд сюда не идут: их
                # дописывали пунктами, и в карточках «Рисков» появлялись 62 % и «11 минут»
                # из другого раздела. Покрытие обязательных фактов проверяет колода.
                facts.extend(
                    f
                    for f in t.fact_refs
                    if f in ctx.facts
                    and f not in facts
                    and " ".join(str(ctx.facts[f].get("raw") or "").split()) in written
                )
        if not items and not text and dataset is None and image is None:
            # Модель вернула слайд из одного заголовка (бывает и после повтора с подсказкой):
            # раскрытие берётся из пояснения тезиса и его источника, а не остаётся пустым.
            items, text, dataset = _backfill(ctx, theses, title)
            if items or text or dataset:
                ctx.fix("content_backfilled", f"«{title[:40]}»: содержание из источника тезиса")
                if dataset:
                    visual = "table"
                elif items and visual in ("text", "quote", "number", "image", "chart", "table"):
                    visual = "bullets"
                for item in items:
                    facts.extend(f for f in item.get("fact_refs", []) if f not in facts)
                facts.extend(f for f in FACT_REF.findall(text) if f in ctx.facts and f not in facts)
        if automatic:
            visual, cands = content_patterns(
                ctx,
                visual,
                items,
                text,
                facts,
                dataset=dataset,
                image=image,
                diagram_kind=diagram_kind,
            )
            pattern = cands[0]
        drafts.append(
            Draft(
                kind="content",
                theses=theses,
                pattern=pattern,
                title=title,
                message=_clean(raw.get("message"), 300),
                visual=visual,
                text=text,
                items=items,
                facts=list(dict.fromkeys(facts)),
                dataset=dataset,
                chart_type=chart_type,
                diagram_kind=diagram_kind,
                columns=columns,
                image=image,
                notes=_clean(raw.get("notes"), 600),
                section=head.section if head else None,
                source_refs=list(dict.fromkeys(source_refs)),
                candidates=[c for c in cands if c.pattern_id != pattern.pattern_id],
            )
        )
    if not drafts:
        if skipped_foreign:
            raise ValueError(f"theses слайдов должны быть из пакета {packet_ids}")
        raise ValueError("нужен непустой список slides")
    return drafts


def _distinct_items(ctx: Context, items: list[JsonDict]) -> list[JsonDict]:
    """Пункты, одинаковые после подстановки значений, — один пункт: модель пишет
    «Плюс-минус {fact:f3} секунд в 90 % случаев» и то же с {fact:f4}, на слайде — две
    одинаковые карточки."""
    out: list[JsonDict] = []
    shown: dict[str, JsonDict] = {}
    for item in items:
        key = " ".join(
            cap.substitute_facts(f"{item.get('sub', '')} {item['text']}", ctx.facts).lower().split()
        )
        twin = shown.get(key)
        if twin is not None:
            refs = list(dict.fromkeys(twin.get("fact_refs", []) + item.get("fact_refs", [])))
            if refs:
                twin["fact_refs"] = refs
            continue
        shown[key] = item
        out.append(item)
    return out


def _backfill(
    ctx: Context, theses: list[str], title: str
) -> tuple[list[JsonDict], str, str | None]:
    """Содержание пустого слайда из его тезисов: пункты пояснения, списки и предложения
    блоков-источников (числа — ссылками на факты этих блоков), иначе набор данных."""
    seen = {title.strip().lower()}
    items: list[JsonDict] = []

    def add(sentence: str, block_id: str | None = None) -> None:
        text = _clean(sentence, 240).strip(" ;")
        if len(text) < 12 or text.lower() in seen or _similar(text, title) >= 0.7:
            return
        seen.add(text.lower())
        refs: list[str] = []
        if block_id:
            for fid, fact in ctx.facts.items():
                raw = str(fact.get("raw") or "")
                if fact.get("block_id") == block_id and raw and raw in text:
                    text = text.replace(raw, f"{{fact:{fid}}}", 1)
                    refs.append(fid)
        items.append({"text": text, **({"fact_refs": refs} if refs else {})})

    for tid in theses:
        t = ctx.thesis(tid)
        if t is None:
            continue
        for sentence in _sentences(t.explanation or ""):
            add(sentence)
        for block_id in _thesis_sources(ctx, t):
            block = ctx.blocks[block_id]
            if block.get("kind") == "bullets":
                for entry in block.get("items") or []:
                    add(str(entry), block_id)
            elif block.get("kind") in ("paragraph", "quote", "kpi"):
                for sentence in _sentences(str(block.get("text") or "")):
                    add(sentence, block_id)
        if len(items) >= 4:
            break
    items = items[:5]
    if len(items) == 1:
        return [], str(items[0]["text"]), None
    if items:
        return items, "", None
    for tid in theses:
        t = ctx.thesis(tid)
        dataset = next((d for d in (t.dataset_refs if t else []) if d in ctx.datasets), None)
        if dataset:
            return [], "", dataset
    return [], "", None


def content_patterns(
    ctx: Context,
    visual: str,
    items: list[JsonDict],
    text: str,
    facts: list[str],
    *,
    dataset: str | None,
    image: str | None,
    diagram_kind: str | None = None,
) -> tuple[str, list[PatternInfo]]:
    """Bind validated content to layouts, independently of model-proposed pattern IDs."""
    if (
        (visual in ("chart", "table") and not dataset)
        or (visual == "image" and not image)
        or (visual == "number" and not facts)
    ):
        visual = "bullets" if items else "text"
    need = Need(
        visual,
        items=max(1, len(items)),
        numbers=len(facts),
        text_chars=len(text) + sum(len(i["text"]) + len(i.get("sub", "")) for i in items),
        has_dataset=bool(dataset),
        has_image=bool(image),
        has_diagram=diagram_kind in diagrams.KINDS and 2 <= len(items) <= 6,
    )
    candidates = candidates_for(
        ctx.patterns, need, ctx.variant_id, limit=len(ctx.patterns), has_datasets=bool(dataset)
    )
    if not candidates:
        need.visual = "bullets" if items else "text"
        candidates = candidates_for(
            ctx.patterns, need, ctx.variant_id, limit=len(ctx.patterns), has_datasets=bool(dataset)
        )
    if not candidates:
        raise ValueError("нет подходящей композиции для содержания и доступных ресурсов")
    # Reorder only equivalent fixed grids. Do not promote a generic list over a
    # comparison/chart, lose text capacity, or override the template/library ranking.
    if items:
        groups: dict[tuple[str, bool, bool, bool], list[int]] = {}
        for index, p in enumerate(candidates):
            if p.cards is not None and not p.list_columns:
                key = (
                    p.role,
                    p.builtin,
                    p.text_capacity >= need.text_chars,
                    p.number_capacity >= need.numbers,
                )
                groups.setdefault(key, []).append(index)
        for indices in groups.values():
            ranked = sorted(
                (candidates[i] for i in indices),
                key=lambda p: (
                    p.item_capacity < len(items),
                    abs(p.item_capacity - len(items)),
                ),
            )
            for index, p in zip(indices, ranked, strict=True):
                candidates[index] = p
    return need.visual, candidates[:12]


def _match_columns(ctx: Context, dataset: str | None, names: list[str]) -> list[str]:
    """Имена колонок из ответа модели → имена колонок набора: выдержка показывает колонки как
    «Сумма (млн ₽)», модель часто возвращает их с единицей, без неё или в другом регистре."""
    if not dataset or dataset not in ctx.datasets:
        return names
    actual = [str(c["name"]) for c in ctx.datasets[dataset]["columns"]]
    by_key = {_column_key(c): c for c in actual}
    out: list[str] = []
    for name in names:
        match = by_key.get(_column_key(name))
        if match is None:
            match = next((c for c in actual if c.lower() == name.lower()), None)
        if match is not None and match not in out:
            out.append(match)
    return out


def _column_key(name: str) -> str:
    base = re.split(r"\s*[(,]", name.strip(), maxsplit=1)[0]
    return re.sub(r"\s+", " ", base).strip().lower()


def drafts_without_model(ctx: Context, packet: Packet) -> list[Draft]:
    """Детерминированный черновик: слайд на тезис, первый кандидат, пункты из источника.
    Только для CLI --no-model и тестов на реальных профилях; помечается в плане."""
    drafts: list[Draft] = []
    for t in packet.theses:
        need = thesis_need(ctx, t)
        cands = packet.candidates.get(t.id, [])
        if not cands:
            continue
        items: list[JsonDict] = []
        block = next((ctx.blocks[b] for b in t.source_refs if b in ctx.blocks), None)
        if block is not None and block.get("kind") == "bullets":
            items = [{"text": _clean(i, 200)} for i in (block.get("items") or [])[:6]]
        elif t.explanation and need.visual in ("bullets", "cards", "timeline", "diagram"):
            items = [
                {"text": _clean(s, 200)} for s in re.split(r"(?<=[.!?])\s+", t.explanation) if s
            ][:5]
        if not items and t.fact_refs and need.visual not in ("chart", "table"):
            items = [
                {"text": _clean(_fact_phrase(ctx, f), 120), "fact_refs": [f]}
                for f in t.fact_refs
                if f in ctx.facts
            ][:4]
        drafts.append(
            Draft(
                kind="content",
                theses=[t.id],
                pattern=cands[0],
                title=cap.substitute_facts(t.statement, ctx.facts)[:200],
                message=cap.substitute_facts(t.statement, ctx.facts)[:300],
                visual=need.visual,
                text=t.explanation if ctx.variant_id != "compact" else "",
                items=items,
                facts=[f for f in t.fact_refs if f in ctx.facts],
                dataset=next((d for d in t.dataset_refs if d in ctx.datasets), None),
                image=next((a for a in t.asset_refs if a in ctx.assets), None),
                section=t.section,
                source_refs=[b for b in t.source_refs if b in ctx.blocks],
                candidates=cands[1:],
            )
        )
    return drafts


# ---------- сборка блоков слайда ----------


def _fact_label(ctx: Context, fact_id: str) -> str:
    f = ctx.facts.get(fact_id) or {}
    c = f.get("context") or {}
    label = c.get("metric") or f.get("label") or ""
    if c.get("period"):
        label = f"{label}, {c['period']}" if label else str(c["period"])
    return _clean(label, 120)


def _label_for_fact(ctx: Context, items: list[JsonDict], fact_id: str) -> str:
    """Подпись показателя: текст пункта, который его показывает, если это не само значение;
    иначе показатель и период из контекста факта."""
    value = cap.fact_text(ctx.facts.get(fact_id) or {})
    for it in items:
        if fact_id not in (it.get("fact_refs") or []):
            continue
        text = str(it.get("text", "")).strip()
        bare = FACT_REF.sub("", text).strip(" —–:-,.")
        if not bare or cap.substitute_facts(text, ctx.facts).strip() == value:
            continue
        return text
    return _fact_label(ctx, fact_id)


_CLAUSE_BREAK = re.compile(r"[.;:()—–]|,\s")
_LEADING_JOINER = re.compile(
    r"^(?:(?:что|и|а|но|это|который|которая|которые|где|при этом|а также|также)\s+)+",
    re.IGNORECASE,
)


def _fact_phrase(ctx: Context, fact_id: str) -> str:
    """Факт, не упомянутый в тексте слайда, строкой для списка. С внятной подписью —
    «Подписка: {fact}»; со слабой («дней», «даёт») — отрезок исходной фразы вокруг числа:
    «{fact} подготовки» читается, «дней: 5 дней» — нет."""
    fact = ctx.facts[fact_id]
    context = fact.get("context") or {}
    label = str(context.get("metric") or fact.get("label") or "").strip()
    ref = f"{{fact:{fact_id}}}"
    raw = str(fact.get("raw") or "")
    if label and not weak_metric(label, fact.get("unit")):
        return f"{label[:1].upper()}{label[1:]}: {ref}"
    fragment = str((fact.get("source_location") or {}).get("fragment") or "").rstrip("…")
    at = fragment.find(raw) if raw else -1
    if at >= 0:
        starts = [m.end() for m in _CLAUSE_BREAK.finditer(fragment, 0, at)]
        start = starts[-1] if starts else 0
        stop_match = _CLAUSE_BREAK.search(fragment, at + len(raw))
        stop = stop_match.start() if stop_match else len(fragment)
        clause = fragment[start:stop].strip(" ,")
        clause = _LEADING_JOINER.sub("", clause)
        if 0 < len(clause) <= 90 and len(clause) > len(raw) + 2:
            phrase = clause.replace(raw, ref, 1)
            return phrase[:1].upper() + phrase[1:]
    return f"{label[:1].upper()}{label[1:]}: {ref}" if label else ref


def _number_block(ctx: Context, slot: SlotInfo, fact_id: str) -> JsonDict:
    fact = ctx.facts[fact_id]
    return {
        "slot_id": slot.slot_id,
        "kind": "number",
        "number": {"fact_id": fact_id, "format": cap.number_format(fact)},
        "text": cap.fact_text(fact),
        "fact_refs": [fact_id],
    }


def _text_block(slot: SlotInfo, text: str, *, fact_refs: list[str] | None = None) -> JsonDict:
    out: JsonDict = {"slot_id": slot.slot_id, "kind": slot.kind, "text": text}
    refs = list(dict.fromkeys((fact_refs or []) + FACT_REF.findall(text)))
    if refs:
        out["fact_refs"] = refs
    return out


def _one_scale(ds: JsonDict, series: list[str]) -> list[str]:
    """Ряды одной оси: вид и единица как у первого ряда, максимумы не дальше чем в 20 раз.
    Проценты рядом с числом пользователей (десятки против сотен тысяч) прижимаются к нулю,
    и диаграмма перестаёт что-либо показывать."""
    cols = {c["name"]: c for c in ds["columns"]}
    index = {c["name"]: i for i, c in enumerate(ds["columns"])}

    def peak(name: str) -> float:
        values = [row[index[name]] for row in ds["rows"] if index[name] < len(row)]
        return max(
            (
                abs(float(v))
                for v in values
                if isinstance(v, int | float) and not isinstance(v, bool)
            ),
            default=0.0,
        )

    first = series[0]
    scale = (cols[first]["type"], cols[first].get("unit") or "")
    top = peak(first)
    kept = [first]
    for name in series[1:]:
        if (cols[name]["type"], cols[name].get("unit") or "") != scale:
            continue
        value = peak(name)
        if top and value and max(top, value) / min(top, value) > 20:
            continue
        kept.append(name)
    return kept


def _chart_block(ctx: Context, draft: Draft, slot: SlotInfo) -> JsonDict:
    ds = ctx.datasets[draft.dataset or ""]
    cols = ds["columns"]
    category = next((c["name"] for c in cols if c["type"] in ("string", "date")), cols[0]["name"])
    numeric = [c["name"] for c in cols if c["type"] in ("number", "percent", "money")]
    series = [c for c in draft.columns if c in numeric] or numeric
    series = series[:MAX_SERIES] or [cols[-1]["name"]]
    units = {c["name"]: c.get("unit") for c in cols}
    source_chart = ds.get("source_chart") or {}
    chart_type = source_chart.get("type") or draft.chart_type
    if source_chart:
        # A raster transcription preserves all series and their original order.
        series = numeric
    else:
        kept = _one_scale(ds, series)
        if len(kept) < len(series):
            ctx.fix(
                "chart_series_dropped",
                f"{draft.dataset}: {', '.join(s for s in series if s not in kept)} — другая шкала",
            )
            series = kept
    if chart_type is None:
        date_like = any(c["type"] == "date" for c in cols)
        chart_type = "line" if date_like and len(ds["rows"]) >= 4 else "column"
    chart: JsonDict = {
        "type": chart_type,
        "dataset_id": draft.dataset,
        "category_column": category,
        "series": series,
        "show_legend": len(series) > 1,
        "show_axis_labels": True,
        "show_data_labels": len(ds["rows"]) <= 8,
    }
    unit = next((units[s] for s in series if units.get(s)), None)
    if unit:
        chart["units"] = unit
    if ds.get("title"):
        chart["title"] = str(ds["title"])[:120]
    return {"slot_id": slot.slot_id, "kind": "chart", "chart": chart}


def _table_block(ctx: Context, draft: Draft, slot: SlotInfo) -> JsonDict:
    ds = ctx.datasets[draft.dataset or ""]
    cols = [c["name"] for c in ds["columns"]]
    chosen = [c for c in draft.columns if c in cols] or cols
    chosen = chosen[:MAX_TABLE_COLUMNS]
    max_rows = draft.max_rows or int(ctx.app.plan.table_max_rows)
    table: JsonDict = {
        "dataset_id": draft.dataset,
        "columns": chosen,
        "max_rows": max_rows,
        "row_offset": draft.row_offset,
    }
    return {"slot_id": slot.slot_id, "kind": "table", "table": table}


def _item_line(ctx: Context, item: JsonDict) -> str:
    """Строка пункта для списка или абзаца: с подписью и значениями его фактов, если
    модель привязала факт, но не написала число в тексте."""
    line = _item_text(item)
    written = cap.substitute_facts(line, ctx.facts)
    missing = [
        f
        for f in item.get("fact_refs") or []
        if f in ctx.facts
        and f not in ctx.slide_shown
        and f"{{fact:{f}}}" not in line
        and str(ctx.facts[f].get("raw") or "") not in written
    ]
    if missing:
        line = f"{line} — {', '.join(f'{{fact:{f}}}' for f in missing[:2])}"
    return line


def _item_text(item: JsonDict) -> str:
    """Keep a block heading when the layout has no separate heading slot."""
    text = str(item["text"])
    sub = str(item.get("sub") or "").strip()
    return f"{sub}: {text}" if sub and sub != text.strip() else text


def fill_blocks(ctx: Context, draft: Draft) -> list[JsonDict]:
    """Блоки по слотам паттерна для черновика: заголовок, подача, обязательные слоты."""
    p = draft.pattern
    blocks: list[JsonDict] = []
    used: set[str] = set()
    draft.filler_slots = set()
    draft.unplaced_text = ""
    ctx.slide_icons = set()
    ctx.slide_shown = set()

    def put(block: JsonDict, *, filler: bool = False) -> None:
        if block["slot_id"] in used:
            return
        used.add(block["slot_id"])
        blocks.append(block)
        if filler:
            draft.filler_slots.add(block["slot_id"])

    def free(kind: str) -> SlotInfo | None:
        return next((s for s in p.single(kind) if s.slot_id not in used), None)

    if p.title is not None:
        put(_text_block(p.title, draft.title))
    items = list(draft.items)
    text = draft.text
    message = draft.message
    facts = list(draft.facts)
    visual = draft.visual

    # Данные.
    if visual == "chart" and draft.dataset and p.chart_slot is not None:
        put(_chart_block(ctx, draft, p.chart_slot))
    elif visual == "table" and draft.dataset and p.single("table"):
        put(_table_block(ctx, draft, p.single("table")[0]))
    elif visual in ("chart", "table") and draft.dataset:
        # Слота нет: подача остаётся текстовой, факты — показателями.
        ctx.fix("visual_downgraded", f"«{draft.title[:40]}»: {visual} → показатели и текст")
        visual = "number" if facts else "bullets"
        draft.visual = visual
    if draft.image and p.has_image_slot:
        slot = free("image")
        if slot is not None:
            asset = ctx.assets[draft.image]
            put(
                {
                    "slot_id": slot.slot_id,
                    "kind": "image",
                    "image": {
                        "asset_id": draft.image,
                        "fit": "cover",
                        "alt": _clean(asset.get("caption") or draft.title, 200),
                    },
                }
            )
    # Показатели: слоты чисел в порядке чтения (одиночные и в карточках), подпись — парный
    # текстовый слот той же карточки, иначе слот по тому же индексу в соседней группе.
    if visual == "number" or (facts and p.number_capacity and visual not in ("chart", "table")):
        number_slots = _number_slots(p, used)
        pending = list(facts)
        placed_numbers: list[tuple[SlotInfo, str]] = []
        for slot in number_slots:
            if not pending:
                break
            fid = next(
                (f for f in pending if _fits_slot(ctx, slot, cap.fact_text(ctx.facts[f]))), None
            )
            if fid is None:
                continue
            pending.remove(fid)
            put(_number_block(ctx, slot, fid))
            placed_numbers.append((slot, fid))
        for index, (slot, fid) in enumerate(placed_numbers):
            label_text = _label_for_fact(ctx, items, fid)
            caption = _caption_slot_for(p, slot, index, used)
            if caption is not None and label_text:
                put(_text_block(caption, label_text, fact_refs=[fid]))
        if placed_numbers:
            shown = {fid for _, fid in placed_numbers}
            shown_texts = {
                cap.substitute_facts(str(b.get("text") or ""), ctx.facts).strip()
                for b in blocks
                if b["kind"] != "title"
            }
            # Показанное число не заменяет пояснение к нему. Убираем только пункт,
            # текст которого уже выведен числом или подписью; пустой набор fact_refs
            # не означает, что обычный пункт покрыт показателями. Отдельный sub также
            # нельзя потерять при переносе основного текста в подпись.
            items = [
                it
                for it in items
                if not (
                    it.get("fact_refs")
                    and set(it["fact_refs"]) <= shown
                    and not it.get("sub")
                    and cap.substitute_facts(it["text"], ctx.facts).strip() in shown_texts
                )
            ]
    # A fact reference in slide metadata is not visible content. Quantities which
    # did not fit a numeric slot must remain in prose, with their source labels.
    visible = " ".join(
        [draft.title, text, *[_item_text(it) for it in items]]
        + [str(b.get("text") or "") for b in blocks]
    )
    shown_ids = {b["number"]["fact_id"] for b in blocks if b.get("number")}
    visible_values = " ".join(cap.substitute_facts(visible, ctx.facts).split())
    # На титуле, разделителе и финале числа не дописываются: «• Бюджет: 18 млн руб» под
    # «Спасибо за внимание» — не подпись, а хвост тезиса.
    item_refs = {f for it in items for f in it.get("fact_refs") or []}
    ctx.slide_shown = set(shown_ids)
    for fid in facts if draft.kind == "content" else []:
        if fid in shown_ids or f"{{fact:{fid}}}" in visible or fid in item_refs:
            # Факт пункта дописывает сам пункт (карточка или строка списка) — второй раз
            # отдельным пунктом он не нужен.
            continue
        raw = " ".join(str(ctx.facts[fid].get("raw") or "").split())
        if raw and raw in visible_values:
            # Значение уже написано словами («5 дней подготовки» в узле схемы): вторая
            # строка «Подготовка: 5 дней» над схемой ложилась на неё.
            continue
        phrase = _fact_phrase(ctx, fid)
        if visual in ("diagram", "timeline"):
            # A missing quantity is a note, not an invented process step/child node.
            text = "\n".join(t for t in (text, phrase) if t)
            continue
        items.append({"text": phrase, "fact_refs": [fid]})
    # Схема потребляет те же смысловые пункты, но только после проверки реальных узлов.
    diagram_slot = free("diagram")
    if diagram_slot is not None and visual in ("diagram", "timeline"):
        kind = "timeline" if visual == "timeline" else draft.diagram_kind
        payload = {
            "kind": kind,
            "direction": "vertical" if kind == "process" and len(items) > 3 else "horizontal",
            "items": [
                {
                    "text": cap.substitute_facts(it.get("sub") or it["text"], ctx.facts),
                    **(
                        {"sub": cap.substitute_facts(it["text"], ctx.facts)}
                        if it.get("sub")
                        else {}
                    ),
                }
                for it in items
            ],
        }
        box = tuple(
            int(v * (ctx.slide_w if i % 2 == 0 else ctx.slide_h))
            for i, v in enumerate(diagram_slot.bbox)
        )
        if diagrams.content_fits(payload, box, diagrams.DiagramStyle.from_profile(ctx.profile)):
            put(
                {
                    "slot_id": diagram_slot.slot_id,
                    "kind": "diagram",
                    "diagram": payload,
                    "fact_refs": list(
                        dict.fromkeys(f for it in items for f in it.get("fact_refs", []))
                    ),
                }
            )
            items = []
        else:
            # Keep the entire relation together; fit_draft will try a text layout.
            draft.unplaced_text = "\n".join(_item_text(it) for it in items)
    # Пункты: слот списка, иначе карточки, иначе текстом.
    if items:
        bullets = free("bullets")
        cards = p.cards
        card_slots_free = cards is not None and any(
            kind in cards.by_kind and cards.by_kind[kind][0].slot_id not in used
            for kind in cards.text_kinds
        )
        if bullets is not None and (cards is None or not card_slots_free or visual == "bullets"):
            put(
                {
                    "slot_id": bullets.slot_id,
                    "kind": "bullets",
                    # Без отдельного слота заголовок остаётся частью пункта.
                    "items": [
                        {
                            k: v
                            for k, v in {
                                "text": _item_line(ctx, it),
                                "fact_refs": it.get("fact_refs"),
                            }.items()
                            if v
                        }
                        for it in items
                    ],
                }
            )
            items = []
        elif cards is not None and card_slots_free and p.list_columns:
            # Колонки списков: пункты делятся между колонками по порядку, а не по одному.
            columns = [cards.card(i)["bullets"] for i in range(p.list_columns)]
            columns = [c for c in columns if c.slot_id not in used]
            per = max(1, math.ceil(len(items) / max(len(columns), 1)))
            for i, column in enumerate(columns):
                chunk = items[i * per : (i + 1) * per]
                if not chunk:
                    break
                put(
                    {
                        "slot_id": column.slot_id,
                        "kind": "bullets",
                        "items": [
                            {
                                k: v
                                for k, v in {
                                    "text": _item_line(ctx, it),
                                    "fact_refs": it.get("fact_refs"),
                                }.items()
                                if v
                            }
                            for it in chunk
                        ],
                    }
                )
            items = items[per * len(columns) :]
        elif cards is not None and card_slots_free:
            unplaced = [
                it
                for i, it in enumerate(items[: cards.count])
                if not _fill_card(ctx, cards.card(i), it, put, used)
            ]
            items = unplaced + items[cards.count :]
        if items:
            # Не поместившиеся пункты — в текст.
            extra = "\n".join(f"• {_item_line(ctx, it)}" for it in items)
            text = f"{text}\n{extra}".strip() if text else extra
            items = []
    # Текст и подзаголовок.
    if visual == "quote" and p.role == "quote" and message:
        # Цитата — в самом крупном слоте (заголовке), автор — подписью.
        slot = free("label") or free("caption") or free("body")
        if slot is not None:
            put(_text_block(slot, message), filler=True)
            message = ""
    if text:
        # Самый вместительный свободный слот, а не первый по порядку: у шаблона ЛЦТ первый
        # «body» — однострочный заголовок карточки, и абзац с пунктами уходил в него.
        slot = _slot_for_text(ctx, p, used, ("body", "subtitle", "caption"), text)
        if slot is not None:
            put(_text_block(slot, text))
            text = ""
    placed_texts = [str(b.get("text") or "") for b in blocks] + [
        it.get("text", "") for b in blocks for it in b.get("items") or []
    ]
    if (
        message
        and message.strip() != draft.title.strip()
        and not _text_repeats(message, placed_texts)
    ):
        # На содержательном слайде message — смысл слайда для аудита, а не текст: крупным
        # шрифтом он выглядит пересказом («Поэтапный план с конкретными этапами»). Ему место
        # только в подзаголовке; у титула и финала это настоящая подпись.
        slot = (
            free("subtitle")
            if draft.kind == "content"
            else (
                free("subtitle")
                or (free("body") if not text else None)
                or free("label")
                or free("caption")
                or free("position")
            )
        )
        if slot is not None and not _tiny(slot):
            put(_text_block(slot, message), filler=True)
            message = ""
    # Слайд не должен состоять из одного заголовка, если есть что сказать.
    if len(blocks) <= 1 and draft.kind not in ("title", "final"):
        filler = text or message or _explanation(ctx, draft) or draft.message
        slot = free("body") or free("subtitle") or free("bullets") or free("label")
        cards = p.cards
        if filler and slot is not None and not _tiny(slot):
            if slot.kind == "bullets":
                put(
                    {
                        "slot_id": slot.slot_id,
                        "kind": "bullets",
                        "items": [{"text": t} for t in _sentences(filler)[:5]],
                    }
                )
            else:
                put(_text_block(slot, filler), filler=True)
            text = message = ""
        elif filler and cards is not None:
            for i, sentence in enumerate(_sentences(filler)[: cards.count]):
                _fill_card(ctx, cards.card(i), {"text": sentence}, put, used, filler=True)
            text = message = ""
    # Обязательные слоты, оставшиеся пустыми (в том числе заголовки карточек в группах).
    section = ctx.thesis(draft.section) if draft.section else None
    for slot in p.slots.values():
        if not slot.required or slot.slot_id in used:
            continue
        if slot.group:
            # Слот карточки: незаполненные карточки убирает композер, наполнитель из заголовка
            # или сообщения в них не идёт.
            continue
        if _tiny(slot):
            # Крошечный обязательный слот (единица, номер): остаётся текст образца.
            put(_text_block(slot, slot.sample_text or "—"))
            ctx.fix("sample_kept", f"{p.pattern_id}.{slot.slot_id}: оставлен текст образца")
            continue
        if slot.kind in ("body", "subtitle"):
            options = [text, message, draft.message, _explanation(ctx, draft), draft.title]
            if draft.kind != "content":
                # На содержательном слайде главный вывод колоды в пустом слоте повторялся
                # на каждом втором слайде; пустой слот уберёт вёрстка.
                options.append(ctx.story.get("key_takeaway", ""))
            # Текст образца — не содержание: ни подписи-заглушки («Заголовок»), ни подсказки
            # автора шаблона («Иконки можно брать из VKUI Icons Library») в колоду не идут.
            placed_now = [str(b.get("text") or "") for b in blocks] + [
                str(it.get("text", "")) for b in blocks for it in b.get("items") or []
            ]
            # Уже стоящий на слайде текст второй раз не ставится: основной текст и та же
            # фраза мелкой сноской под ним выглядят как сбой вёрстки.
            options = [o for o in options if o and o.strip() and not _text_repeats(o, placed_now)]
            if not options:
                # Сказать нечего нового: прочерк — «слот сознательно пуст» (контракт требует
                # блок с текстом); вёрстка такой слот не заполняет и убирает рамку.
                put(_text_block(slot, "—"))
                continue
            filler = next((o for o in options if _fits_slot(ctx, slot, o)), options[0])
            put(_text_block(slot, filler))
            text = ""
        elif slot.kind == "bullets":
            fill_items = [
                {"text": s}
                for s in _sentences(_explanation(ctx, draft) or draft.message or draft.title)[:4]
            ]
            put(
                {
                    "slot_id": slot.slot_id,
                    "kind": "bullets",
                    "items": fill_items or [{"text": draft.title}],
                }
            )
        elif slot.kind in ("label", "title"):
            # Дополнительный заголовок или подпись: раздел, иначе очередной пункт/сообщение.
            spare_items = [it["text"] for it in draft.items if not _item_placed(blocks, it)]
            put(
                _text_block(
                    slot,
                    spare_items[0]
                    if spare_items
                    else cap.substitute_facts(section.statement, ctx.facts)
                    if section
                    else draft.message or draft.title,
                )
            )
        elif slot.kind == "caption":
            put(_text_block(slot, _source_note(ctx, draft)))
        elif slot.kind == "date":
            put(_text_block(slot, _period(ctx, draft) or slot.sample_text or "—"))
        elif slot.kind == "table" and draft.dataset:
            put(_table_block(ctx, draft, slot))
        elif slot.kind == "chart" and draft.dataset:
            put(_chart_block(ctx, draft, slot))
        elif slot.kind in ("table", "chart") and ctx.datasets:
            draft.dataset = next(iter(ctx.datasets))
            put(
                _table_block(ctx, draft, slot)
                if slot.kind == "table"
                else _chart_block(ctx, draft, slot)
            )
    draft.unplaced_text = "\n".join(t for t in (draft.unplaced_text, text) if t)
    _assign_icons(ctx, draft, p, blocks, used, put)
    return blocks


def _icon_distance(center: tuple[float, float], slot: SlotInfo) -> float:
    """Расстояние от иконки до текста; колонка важнее строки — подпись обычно под иконкой."""
    x, y, w, h = slot.bbox
    dx = max(x - center[0], 0.0, center[0] - (x + w))
    dy = max(y - center[1], 0.0, center[1] - (y + h))
    return dx * 2 + dy


def _assign_icons(
    ctx: Context,
    draft: Draft,
    p: PatternInfo,
    blocks: list[JsonDict],
    used: set[str],
    put: Any,
) -> None:
    """Свободные иконки слайда — по смыслу ближайшего текста под ними или рядом.

    У образцов, где анализ выделил иконки отдельной группой (VK Education: иконки ряда
    отдельно от подписей), карточка их не видит, и на каждом слайде оставались лампочка,
    книга и календарь образца. Иконка без подходящего слова остаётся иконкой образца."""
    if not ctx.icons:
        return
    icon_slots = [s for s in p.slots.values() if s.kind == "icon" and s.slot_id not in used]
    if not icon_slots:
        return
    concepts = {
        " ".join(str(it.get("text", "")).split()).lower(): str(it.get("icon") or "")
        for it in draft.items
    }
    texts: list[tuple[SlotInfo, str]] = []
    for b in blocks:
        slot = p.slots.get(str(b.get("slot_id")))
        if slot is None or slot.kind in ("title", "number", "icon", "image"):
            continue
        body = str(b.get("text") or "") or " ".join(
            str(it.get("text", "")) for it in b.get("items") or []
        )
        if body.strip():
            texts.append((slot, body))
    if not texts:
        return
    for icon in sorted(icon_slots, key=lambda s: (s.bbox[0], s.bbox[1])):
        center = (icon.bbox[0] + icon.bbox[2] / 2, icon.bbox[1] + icon.bbox[3] / 2)
        slot, body = min(texts, key=lambda entry: _icon_distance(center, entry[0]))
        if _icon_distance(center, slot) > 0.25:
            continue
        concept = concepts.get(" ".join(body.split()).lower(), "")
        asset_id = ctx.icon_for(concept, cap.substitute_facts(body, ctx.facts), ctx.slide_icons)
        if asset_id:
            ctx.slide_icons.add(asset_id)
            put({"slot_id": icon.slot_id, "kind": "icon", "icon": {"asset_id": asset_id}})


TINY_SLOT_CHARS = 12


def _slot_for_text(
    ctx: Context, p: PatternInfo, used: set[str], kinds: tuple[str, ...], text: str
) -> SlotInfo | None:
    """Слот под абзац: первый по порядку видов (основной текст раньше сноски), куда текст
    помещается, — внутри вида от вместительного к тесному; не помещается никуда — самый
    вместительный. Сноска мельче и формально вмещает больше знаков, но главное — не в ней."""
    free = [
        slot
        for kind in kinds
        for slot in sorted(p.single(kind), key=lambda s: -s.max_chars)
        if slot.slot_id not in used
    ]
    if not free:
        return None
    body = cap.substitute_facts(text, ctx.facts)
    fitting = next((slot for slot in free if not _tiny(slot) and _fits_slot(ctx, slot, body)), None)
    if fitting is not None:
        return fitting
    roomiest = _roomiest(p, used, kinds)
    if (
        not ctx.force_text
        and roomiest is not None
        and roomiest.max_chars
        and len(body) > 2 * roomiest.max_chars
    ):
        # Абзац вдвое длиннее самого вместительного слота: в нём он ляжет на соседние рамки.
        # Неразмещённый текст заставит подгонку искать другую композицию.
        return None
    return roomiest


def _roomiest(p: PatternInfo, used: set[str], kinds: tuple[str, ...]) -> SlotInfo | None:
    """Свободный одиночный слот с наибольшей ёмкостью среди видов `kinds`; при равенстве —
    по порядку видов."""
    best: tuple[int, int, SlotInfo] | None = None
    for rank, kind in enumerate(kinds):
        for slot in p.single(kind):
            if slot.slot_id in used:
                continue
            key = (slot.max_chars, -rank)
            if best is None or key > best[:2]:
                best = (key[0], key[1], slot)
    return best[2] if best else None


def _number_slots(p: PatternInfo, used: set[str]) -> list[SlotInfo]:
    """Свободные слоты чисел в порядке чтения: одиночные и из всех групп."""
    slots = [
        s
        for s in p.slots.values()
        if s.kind == "number" and s.slot_id not in used | p.ordinal_slot_ids
    ]
    slots.sort(key=lambda s: (round(s.bbox[1], 2), s.bbox[0]))
    return slots


def _caption_slot_for(
    p: PatternInfo, number: SlotInfo, index: int, used: set[str]
) -> SlotInfo | None:
    """Подпись к числу: текстовый слот той же карточки; иначе слот того же индекса в группе
    подписей; иначе одиночная подпись по индексу."""
    kinds = ("label", "body", "caption", "subtitle", "title")
    if number.group:
        group = next((g for g in p.groups if g.group_id == number.group), None)
        if group is not None:
            numbers = group.by_kind.get("number", [])
            pos = next((i for i, s in enumerate(numbers) if s.slot_id == number.slot_id), index)
            for kind in kinds:
                slots = group.by_kind.get(kind, [])
                if pos < len(slots) and slots[pos].slot_id not in used and not _tiny(slots[pos]):
                    return slots[pos]
    for g in p.groups:
        if g.group_id == number.group:
            continue
        for kind in kinds:
            slots = g.by_kind.get(kind, [])
            if index < len(slots) and slots[index].slot_id not in used and not _tiny(slots[index]):
                return slots[index]
    for kind in ("label", "caption", "body"):
        free_slots = [s for s in p.single(kind) if s.slot_id not in used and not _tiny(s)]
        # Предыдущие подписи уже исключены через used: индекс показателя здесь
        # пропускал каждый следующий свободный слот.
        if free_slots:
            return free_slots[0]
    return None


def _fits_slot(ctx: Context, slot: SlotInfo, text: str) -> bool:
    """Помещается ли текст в слот при исходном кегле или после допустимых ступеней."""
    if slot.size_pt is None:
        return True
    plan_cfg = ctx.app.plan
    if cap.measure(text, slot, ctx.slide_w, ctx.slide_h, margin_ratio=plan_cfg.margin_ratio).fits:
        return True
    min_pt = cap.min_pt_for(
        slot.kind,
        body_pt=plan_cfg.min_body_pt,
        title_pt=plan_cfg.min_title_pt,
        slide_w_emu=ctx.slide_w,
    )
    return any(
        cap.measure(
            text, slot, ctx.slide_w, ctx.slide_h, size_pt=size, margin_ratio=plan_cfg.margin_ratio
        ).fits
        for size in cap.font_steps(
            slot, ctx.scale, min_ratio=plan_cfg.min_font_ratio, min_pt=min_pt
        )
    )


def _tiny(slot: SlotInfo) -> bool:
    """Слот на несколько символов (единица измерения, номер шага): текст тезиса туда не идёт."""
    return slot.is_text and 0 < slot.max_chars < TINY_SLOT_CHARS


def _fill_card(
    ctx: Context,
    card: dict[str, SlotInfo],
    item: JsonDict,
    put: Any,
    used: set[str],
    *,
    filler: bool = False,
) -> bool:
    """Карточка: основной текст пункта — в текстовый слот, короткая подпись (sub) — в слот
    заголовка карточки; без подписи заголовок выводится из текста, если помещается.
    Наполнитель (пояснение вместо пунктов) в карточку из одной подписи попадает только
    в той части, что помещается, и не считается потерей содержания. Возвращает False,
    если карточка состоит из одного крошечного слота (номер шага) и текст в неё не идёт."""
    heading = next(
        (k for k in ("label", "title", "subtitle") if k in card and card[k].slot_id not in used),
        None,
    )
    body = next(
        (k for k in ("body", "caption", "bullets") if k in card and card[k].slot_id not in used),
        None,
    )
    text = str(item["text"])
    sub = str(item.get("sub") or "")
    refs = item.get("fact_refs")
    number_free = (
        "number" in card and card["number"].slot_id not in used and not card["number"].is_ordinal
    )
    if refs and not number_free:
        # Показателю некуда встать отдельно (номера карточек — порядковые): значение
        # дописывается к тексту пункта, иначе «общий бюджет» остаётся без суммы.
        written = cap.substitute_facts(f"{sub} {text}", ctx.facts)
        missing = [
            f
            for f in refs
            if f in ctx.facts
            and f not in ctx.slide_shown
            and f"{{fact:{f}}}" not in f"{sub} {text}"
            and str(ctx.facts[f].get("raw") or "") not in written
        ]
    else:
        missing = []
    if heading is None or body is None:
        text = _item_text(item)
    if missing:
        text = f"{text} — {', '.join(f'{{fact:{f}}}' for f in missing[:2])}"
    if heading is not None and body is not None:
        head_text = ""
        if sub:
            head_text = (
                sub
                if _fits_slot(ctx, card[heading], cap.substitute_facts(sub, ctx.facts))
                else _heading_from(ctx, card[heading], sub)
            )
        if not head_text:
            # Без подписи: «Название: пояснение» делится на заголовок и текст карточки, чтобы
            # заголовок не повторял начало текста; иначе заголовок — первые слова.
            split = _split_heading(text)
            if split is not None and _fits_slot(
                ctx, card[heading], cap.substitute_facts(split[0], ctx.facts)
            ):
                head_text, text = split
            else:
                head_text = _heading_from(ctx, card[heading], text)
        if body == "bullets":
            put({"slot_id": card[body].slot_id, "kind": "bullets", "items": [{"text": text}]})
        else:
            put(_text_block(card[body], text, fact_refs=refs))
        if head_text:
            put(_text_block(card[heading], head_text))
    elif heading is not None:
        if _tiny(card[heading]):
            return False
        if filler:
            head_text = (
                text
                if _fits_slot(ctx, card[heading], cap.substitute_facts(text, ctx.facts))
                else _heading_from(ctx, card[heading], text)
            )
            if head_text:
                put(_text_block(card[heading], head_text), filler=True)
        else:
            put(_text_block(card[heading], text, fact_refs=refs))
    elif body is not None:
        if body == "bullets":
            put({"slot_id": card[body].slot_id, "kind": "bullets", "items": [{"text": text}]})
        else:
            put(_text_block(card[body], text, fact_refs=refs))
    if (
        "number" in card
        and card["number"].slot_id not in used
        and not card["number"].is_ordinal
        and refs
    ):
        put(_number_block(ctx, card["number"], refs[0]))
    if "icon" in card and card["icon"].slot_id not in used and ctx.icons:
        # Пиктограмма по смыслу пункта из каталога шаблона: иначе на каждом слайде стоят
        # те же три иконки образца (лампочка, книга, календарь), о чём бы ни шла речь.
        asset_id = ctx.icon_for(str(item.get("icon") or ""), f"{sub} {text}", ctx.slide_icons)
        if asset_id:
            ctx.slide_icons.add(asset_id)
            put({"slot_id": card["icon"].slot_id, "kind": "icon", "icon": {"asset_id": asset_id}})
    return True


def _split_heading(text: str) -> tuple[str, str] | None:
    """«Название: пояснение» или «Название — пояснение» → (название, пояснение); None, если
    разделителя нет или части слишком коротки/длинны для заголовка карточки."""
    m = re.match(r"^\s*([^:—–]{3,60}?)\s*[:—–]\s+(.{8,})$", text)
    if m is None:
        return None
    head, rest = m.group(1).strip(), m.group(2).strip()
    if FACT_REF.search(head) or len(head.split()) > 6:
        return None
    return head, rest


def _heading_from(ctx: Context, slot: SlotInfo, text: str) -> str:
    """Заголовок карточки из её текста: целиком, до первого знака препинания или первые
    слова — что помещается в слот; пустая строка, если не помещается ничего."""
    candidates = [text]
    clause = re.split(r"[:;,.!?—–(]", text, maxsplit=1)[0].strip()
    if clause and clause != text:
        candidates.append(clause)
    words = text.split()
    for n in (4, 3, 2):
        if len(words) > n:
            candidates.append(" ".join(words[:n]))
    for c in candidates:
        substituted = cap.substitute_facts(c, ctx.facts)
        if substituted and _fits_slot(ctx, slot, substituted):
            return c
    return ""


def _text_repeats(text: str, placed: list[str]) -> bool:
    """Сообщение повторяет уже поставленный текст (совпадает или вложено): второй раз не идёт."""
    key = re.sub(r"\W+", " ", text).strip().lower()
    if len(key) < 12:
        return False
    for other in placed:
        norm = re.sub(r"\W+", " ", other).strip().lower()
        if norm and (key in norm or norm in key):
            return True
    return False


def _item_placed(blocks: list[JsonDict], item: JsonDict) -> bool:
    text = item["text"]
    for b in blocks:
        if b.get("text") == text or text in str(b.get("text", "")):
            return True
        if any(it.get("text", "").startswith(text) for it in b.get("items") or []):
            return True
    return False


def _explanation(ctx: Context, draft: Draft) -> str:
    for tid in draft.theses:
        t = ctx.thesis(tid)
        # Раздел закрывается слайдом без разделителя, но его пояснение — не материал.
        if t and t.kind != "section" and t.explanation:
            return t.explanation
    return ""


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _source_note(ctx: Context, draft: Draft) -> str:
    names: list[str] = []
    sources = {s["source_id"]: s for s in ctx.package.get("sources", [])}
    for b in draft.source_refs:
        block = ctx.blocks.get(b)
        if block:
            src = sources.get(block.get("source_id", ""))
            if src and src.get("name") and src["name"] not in names:
                names.append(str(src["name"]))
    return ("Источник: " + ", ".join(names[:2])) if names else "По материалам проекта"


def _period(ctx: Context, draft: Draft) -> str | None:
    for fid in draft.facts:
        c = (ctx.facts.get(fid) or {}).get("context") or {}
        if c.get("period"):
            return str(c["period"])
    return None


# ---------- измерение и лестница ёмкости ----------


def _block_text(ctx: Context, block: JsonDict) -> str | list[str] | None:
    kind = block["kind"]
    if kind == "bullets":
        return [cap.substitute_facts(it["text"], ctx.facts) for it in block.get("items", [])]
    if block.get("text") is not None and kind in cap_text_kinds():
        return cap.substitute_facts(str(block["text"]), ctx.facts)
    return None


def cap_text_kinds() -> tuple[str, ...]:
    return (
        "title",
        "subtitle",
        "body",
        "label",
        "caption",
        "date",
        "name",
        "position",
        "code",
        "number",
    )


def measure_blocks(ctx: Context, draft: Draft, blocks: list[JsonDict]) -> list[JsonDict]:
    """Измеряет каждый текстовый блок и подбирает кегль по лестнице; переполнения — в draft."""
    p = draft.pattern
    plan_cfg = ctx.app.plan
    overflow: list[JsonDict] = []
    out: list[JsonDict] = []
    for block in blocks:
        slot = p.slots.get(block["slot_id"])
        text = _block_text(ctx, block)
        if slot is None or text is None or slot.size_pt is None:
            out.append(block)
            continue
        base = float(slot.size_pt)
        m = cap.measure(text, slot, ctx.slide_w, ctx.slide_h, margin_ratio=plan_cfg.margin_ratio)
        action = "as_is"
        note = None
        if not m.fits:
            min_pt = cap.min_pt_for(
                slot.kind,
                body_pt=plan_cfg.min_body_pt,
                title_pt=plan_cfg.min_title_pt,
                slide_w_emu=ctx.slide_w,
            )
            # Обложку, разделитель и финал шаблон рисует под короткое имя («VK Tech» в 48 pt):
            # тема презентации длиннее, и ей можно уменьшить кегль вдвое, а не на четверть.
            ratio = SERVICE_MIN_FONT_RATIO if draft.kind in SERVICE_KINDS else None
            for size in cap.font_steps(
                slot,
                ctx.scale,
                min_ratio=min(ratio or plan_cfg.min_font_ratio, plan_cfg.min_font_ratio),
                min_pt=min_pt,
                fill_below=ratio is not None,
            ):
                m2 = cap.measure(
                    text,
                    slot,
                    ctx.slide_w,
                    ctx.slide_h,
                    size_pt=size,
                    margin_ratio=plan_cfg.margin_ratio,
                )
                if m2.fits:
                    m = m2
                    action = "font_step"
                    note = f"кегль {base:g} → {size:g}"
                    break
        if not m.fits:
            # Сокращение без потери обязательного: пункты и предложения без фактов.
            shortened = False
            if block["kind"] == "bullets":
                items = list(block.get("items", []))
                while True:
                    reduced = cap.shorten_items(items)
                    if reduced is None:
                        break
                    items = reduced
                    m3 = cap.measure(
                        [cap.substitute_facts(it["text"], ctx.facts) for it in items],
                        slot,
                        ctx.slide_w,
                        ctx.slide_h,
                        size_pt=m.size_pt,
                        margin_ratio=plan_cfg.margin_ratio,
                    )
                    if m3.fits:
                        block = {**block, "items": items}
                        m = m3
                        shortened = True
                        break
            elif block["kind"] in ("body", "subtitle", "caption", "title", "label"):
                current = str(block["text"])
                while True:
                    reduced_text = (
                        cap.shorten_title(current)
                        if block["kind"] in ("title", "label")
                        else cap.shorten_text(current)
                    )
                    if reduced_text is None:
                        break
                    current = reduced_text
                    m3 = cap.measure(
                        cap.substitute_facts(current, ctx.facts),
                        slot,
                        ctx.slide_w,
                        ctx.slide_h,
                        size_pt=m.size_pt,
                        margin_ratio=plan_cfg.margin_ratio,
                    )
                    if m3.fits:
                        block = {**block, "text": current}
                        m = m3
                        shortened = True
                        break
            if shortened:
                action = "shortened"
                note = (note + "; " if note else "") + "убраны части без фактов"
        if not m.fits:
            action = "overflow"
            overflow.append(
                {
                    "slot_id": slot.slot_id,
                    "kind": block["kind"],
                    "lines": m.lines,
                    "max_lines": m.max_lines,
                    "chars": m.chars,
                    "max_chars": slot.max_chars,
                    "size_pt": m.size_pt,
                }
            )
        if m.substituted and not any(f["code"] == "font_substituted" for f in ctx.fixes):
            ctx.fix("font_substituted", "; ".join(m.notes))
        out.append({**block, "fit": m.as_fit(base, action, note)})
    draft.overflow = overflow
    return out


def _drop_overflowing_fillers(draft: Draft, measured: list[JsonDict]) -> list[JsonDict]:
    """Наполнитель в необязательном слоте, который не поместился, убирается: слайд без
    подписи лучше слайда с обрезанной подписью; содержание тезиса так не теряется."""
    p = draft.pattern
    bad = {
        o["slot_id"]
        for o in draft.overflow
        if o["slot_id"] in draft.filler_slots
        and (slot := p.slots.get(o["slot_id"])) is not None
        and not slot.required
    }
    if not bad:
        return measured
    draft.overflow = [o for o in draft.overflow if o["slot_id"] not in bad]
    draft.actions.extend(f"filler_dropped:{sid}" for sid in sorted(bad))
    return [b for b in measured if b["slot_id"] not in bad]


def thin_ratio(draft: Draft) -> tuple[int, int] | None:
    """(пунктов, ёмкость) для содержательного слайда, чья композиция заметно больше
    содержания: один пункт на три и больше карточек или меньше половины карточек заполнено."""
    if draft.kind != "content" or draft.visual in ("chart", "table", "image", "quote"):
        return None
    cap_items = draft.pattern.item_capacity
    n = len(draft.items)
    if cap_items < 3:
        return None
    columns = draft.pattern.list_columns
    if columns:
        # Колонки списков: меньше двух пунктов на колонку — колонки полупустые.
        return (n, cap_items) if n < 2 * columns else None
    if draft.pattern.cards is None:
        # Один список: пустым выглядит слайд из одного пункта.
        return (n, cap_items) if n <= 1 else None
    if n <= 1 or cap_items >= 2 * n + 1:
        return (n, cap_items)
    return None


# Сколько раз одна композиция может встретиться в колоде, прежде чем слайды
# начнут разводить по другим. Две — это ещё ритм (повтор поддерживает
# структуру), три и больше — однообразие: колода из восьми слайдов, где пять
# подряд собраны одинаково, читается как один слайд, размноженный пять раз.
#
# Плотному варианту позволено больше: у него слайдов больше, и требовать от
# него столько же разных композиций — значит выгребать весь шаблон. Пороги
# разные ещё и потому, что три варианта обязаны отличаться (ТЗ п.2.5): с
# одинаковыми порогами подгонка приводила их к одной и той же колоде.
MAX_PATTERN_USES = {"compact": 2, "balanced": 2, "detailed": 3}
DEFAULT_MAX_USES = 2

# Во сколько раз мест под числа должно быть больше самих чисел, чтобы менять
# композицию. Плотный вариант терпит просторную композицию, сжатый — нет.
NUMBERS_SLACK = {"compact": 2.5, "balanced": 3.0, "detailed": 4.0}
DEFAULT_NUMBERS_SLACK = 3.0


def numbers_thin(draft: Draft, slack: float = DEFAULT_NUMBERS_SLACK) -> bool:
    """Слайд показателей, где мест под числа втрое больше самих чисел.

    Пунктами такой слайд не меряется: у него их нет, есть числа. Замер от
    18.09.2026 (шаблон ЛЦТ): два показателя в композиции на шестнадцать полос —
    четырнадцать полос стоят пустыми, а крайнее число обрезается краем слайда.
    """
    numbers = sum(1 for b in draft.blocks if b.get("number")) or len([f for f in draft.facts if f])
    capacity = draft.pattern.number_capacity
    return bool(numbers) and capacity >= max(slack * numbers, numbers + 3)


def right_size_pattern(ctx: Context, draft: Draft) -> bool:
    """Композиция по объёму: под один-два пункта берётся ближайшая по вместимости
    композиция (текст, список, две-три карточки), а не карточки на 4–7. Возвращает True,
    если композиция заменена."""
    slack = NUMBERS_SLACK.get(ctx.variant_id, DEFAULT_NUMBERS_SLACK)
    if numbers_thin(draft, slack) and "right_size_numbers:trial" not in draft.actions:
        return _right_size_numbers(ctx, draft)
    thin = thin_ratio(draft)
    if thin is None:
        return False
    n, cap_items = thin
    visual = "text" if n <= 1 and not draft.items else ("bullets" if n <= 2 else draft.visual)
    if visual not in ("text", "bullets", "cards"):
        visual = "bullets"
    chars = len(draft.text) + sum(len(it["text"]) for it in draft.items)
    need = Need(visual, items=max(n, 1), numbers=0, text_chars=max(chars, 40))
    pool = candidates_for(
        ctx.patterns, need, ctx.variant_id, limit=8, has_datasets=ctx.has_datasets
    ) + [c for c in draft.candidates if c.pattern_id != draft.pattern.pattern_id]
    best: PatternInfo | None = None
    for cand in pool:
        c_items = cand.item_capacity
        if cand.pattern_id == draft.pattern.pattern_id or c_items >= cap_items:
            continue
        if n >= 2 and c_items < n:
            continue
        if n <= 1 and c_items > 2 and cand.cards is not None:
            continue
        if best is None or c_items < best.item_capacity:
            best = cand
    if best is None:
        return False
    original = draft.pattern
    draft.pattern = best
    draft.candidates = [c for c in draft.candidates if c is not best] + [original]
    draft.actions.append(f"right_size:{original.pattern_id}→{best.pattern_id}")
    ctx.fix(
        "pattern_right_sized",
        f"«{draft.title[:40]}»: {n} пункт(ов) на {cap_items} карточек — {original.pattern_id}"
        f" → {best.pattern_id}",
    )
    return True


def density_hint(drafts: list[Draft]) -> str | None:
    """Подсказка модели о пустых слайдах: композиция заметно больше содержания."""
    lines = []
    for d in drafts:
        thin = thin_ratio(d)
        if thin is None:
            continue
        n, cap_items = thin
        alternatives = ", ".join(c.pattern_id for c in d.candidates if c.item_capacity < cap_items)
        lines.append(
            f"слайд «{d.title[:40]}» ({d.pattern.pattern_id}): {n} пункт(ов) на композицию из "
            f"{cap_items} карточек — раскрой тезис до {min(cap_items, 4)} пунктов по пояснению "
            "(без выдуманных цифр)"
            + (f" или возьми композицию по объёму: {alternatives}" if alternatives else "")
        )
    return "; ".join(lines) if lines else None


def count_hint(packet: Packet, drafts: list[Draft]) -> str | None:
    """Подсказка модели о недоборе слайдов пакета: раскрыть тезисы по сторонам, а не
    оставлять код делить списки."""
    if len(drafts) >= packet.lo:
        return None
    ids = ", ".join(t.id for t in packet.theses)
    return (
        f"в пакете {len(drafts)} слайд(ов) при допустимых {packet.lo}–{packet.hi} (цель "
        f"{packet.target}) — раскрой тезисы {ids} на отдельные слайды по разным сторонам "
        "(контекст, механизм, следствия, пример), каждый с 2–4 пунктами и своим заголовком-выводом"
    )


def _right_size_numbers(ctx: Context, draft: Draft) -> bool:
    """Композиция под столько показателей, сколько их есть.

    Кандидат обязан вместить все числа и не быть сам избыточным: менять
    «Цифры × 16» на «Цифры × 11» под два показателя незачем — пустых полос
    остаётся столько же.
    """
    numbers = sum(1 for b in draft.blocks if b.get("number")) or len(draft.facts)
    need = Need("number", items=max(len(draft.items), 1), numbers=numbers, text_chars=40)
    pool = candidates_for(
        ctx.patterns, need, ctx.variant_id, limit=8, has_datasets=ctx.has_datasets
    ) + [c for c in draft.candidates if c.pattern_id != draft.pattern.pattern_id]
    ranked = sorted(
        (
            c
            for c in pool
            if c.pattern_id != draft.pattern.pattern_id
            and numbers <= c.number_capacity < draft.pattern.number_capacity
            and c.number_capacity <= 2 * numbers + 1
        ),
        key=lambda c: c.number_capacity,
    )
    # Кандидат проверяется сборкой: у компактных композиций места под число
    # рассчитаны на две-три цифры, и «3400» рядом со «120» слипалось в
    # «1203400». Подгонять размер, не примерив содержание, нельзя.
    best: PatternInfo | None = None
    for cand in ranked:
        trial = copy.deepcopy(draft)
        trial.pattern = cand
        trial.overflow = []
        trial.actions.append("right_size_numbers:trial")
        fit_draft(ctx, trial)
        if not trial.overflow:
            best = cand
            break
    if best is None:
        return False
    original = draft.pattern
    draft.pattern = best
    draft.candidates = [c for c in draft.candidates if c is not best] + [original]
    draft.actions.append(f"right_size_numbers:{original.pattern_id}→{best.pattern_id}")
    ctx.fix(
        "pattern_right_sized",
        f"«{draft.title[:40]}»: {numbers} показател(я) на {original.number_capacity} мест — "
        f"{original.pattern_id} → {best.pattern_id}",
    )
    return True


def thin_count(drafts: list[Draft]) -> int:
    return sum(1 for d in drafts if thin_ratio(d) is not None)


def fit_draft(ctx: Context, draft: Draft) -> Draft:
    """Блоки и измерение; композиция по объёму содержания; при переполнении — более
    вместительный кандидат, затем лестница."""
    right_size_pattern(ctx, draft)
    blocks = fill_blocks(ctx, draft)
    measured = _drop_overflowing_fillers(draft, measure_blocks(ctx, draft, blocks))
    if (draft.overflow or draft.unplaced_text) and draft.kind == "content":
        # Служебные слайды (титул, разделитель, финал) композицию не меняют: у них она
        # задана стилем колоды, а подобранный содержательный образец терял бы заголовок.
        # Более вместительная композиция: кандидаты тезиса и подбор по фактическому объёму
        # слайда (пунктов и знаков), включая свои композиции — у шаблона карточки бывают
        # рассчитаны на три слова, а содержание раскрыто предложениями.
        original = draft.pattern
        need = _need_of(draft)
        need.has_image = bool(draft.image)
        need.has_dataset = bool(draft.dataset)
        alternatives = (
            list(draft.candidates)
            + candidates_for(
                ctx.patterns,
                need,
                ctx.variant_id,
                limit=10,
                has_datasets=ctx.has_datasets,
                include_library_alternative=True,
            )
            # Свои композиции пробуются всегда: в первых десяти их нет, когда в шаблоне
            # полсотни образцов, а сетка карточек часто единственная, куда три пункта
            # по предложению встают, не сливаясь в абзац.
            + candidates_for(
                [q for q in ctx.patterns if q.builtin],
                need,
                ctx.variant_id,
                limit=4,
                has_datasets=ctx.has_datasets,
            )
        )
        if draft.unplaced_text:
            alternatives += candidates_for(
                ctx.patterns,
                Need("text", text_chars=len(draft.unplaced_text)),
                ctx.variant_id,
                limit=8,
                has_datasets=ctx.has_datasets,
                include_library_alternative=True,
            )
        best: tuple[tuple[int, int, int, int], PatternInfo, Draft, list[JsonDict]] | None = None
        seen: set[str] = {original.pattern_id}
        for rank, alt in enumerate(alternatives):
            if alt.pattern_id in seen:
                continue
            seen.add(alt.pattern_id)
            if not draft.image and _big_image_slot(alt):
                # Картинка во весь слайд без картинки — пустая рамка, а не композиция.
                continue
            trial = copy.copy(draft)
            trial.pattern = alt
            trial.overflow = []
            alt_blocks = _drop_overflowing_fillers(
                trial, measure_blocks(ctx, trial, fill_blocks(ctx, trial))
            )
            if trial.overflow or trial.unplaced_text:
                continue
            trial.blocks = alt_blocks
            if len(FACT_REF.sub("", _visible_text(trial)).strip()) < 0.85 * _content_chars(draft):
                # Композиция «вместила» содержание, свернув абзац до первых слов карточки:
                # это потеря, а не раскладка.
                continue
            stepped = any(b.get("fit", {}).get("action", "as_is") != "as_is" for b in alt_blocks)
            # Пункты должны остаться пунктами: композиция, где три функции продукта
            # слились в абзац с «•», хуже карточек с кеглем на ступень меньше.
            flattened = len(draft.items) >= 2 and alt.item_capacity < len(draft.items)
            # Порядок предпочтения: шаблон в своих кеглях, своя композиция в своих кеглях,
            # шаблон с уменьшенным кеглем, своя с уменьшенным — всё лучше переполнения.
            key = (int(flattened), int(stepped), int(alt.builtin), rank)
            if best is None or key < best[0]:
                best = (key, alt, trial, alt_blocks)
            if not flattened and not stepped and not alt.builtin:
                break
        if best is not None:
            _, alt, trial, alt_blocks = best
            draft.pattern = alt
            draft.visual = trial.visual
            draft.candidates = [c for c in draft.candidates if c is not alt] + [original]
            draft.actions.append(f"pattern_swap:{original.pattern_id}→{alt.pattern_id}")
            measured = [
                {**b, "fit": {**b["fit"], "action": "pattern_swap"}} if "fit" in b else b
                for b in alt_blocks
            ]
            draft.overflow = []
            draft.unplaced_text = ""
    if draft.unplaced_text and draft.kind == "content" and not ctx.force_text:
        # Другой композиции, где абзац помещается, нет: он встаёт в самый вместительный слот
        # с отметкой о переполнении. Потерять содержание молча нельзя — переполнение видят
        # подсказка модели при повторе, аудит и режим «только шаблон».
        ctx.force_text = True
        try:
            draft.overflow = []
            measured = _drop_overflowing_fillers(
                draft, measure_blocks(ctx, draft, fill_blocks(ctx, draft))
            )
        finally:
            ctx.force_text = False
    draft.blocks = measured
    for b in measured:
        act = b.get("fit", {}).get("action")
        if act in ("font_step", "shortened"):
            draft.actions.append(f"{act}:{b['slot_id']}")
    return draft


def overflow_hint(drafts: list[Draft]) -> str | None:
    lines = []
    for d in drafts:
        if d.unplaced_text:
            lines.append(
                f"слайд «{d.title[:40]}»: часть содержания не размещена; "
                "выбери текстовую подачу или переразложи содержание без потери пунктов и связей"
            )
        for o in d.overflow:
            if d.splittable and d.overflow_in_items and o["kind"] == "bullets":
                continue
            lines.append(
                f"слайд «{d.title[:40]}» ({d.pattern.pattern_id}), слот {o['slot_id']}: "
                f"текст не помещается — {o['chars']} символов / {o['lines']} строк при ёмкости "
                f"{o['max_chars']} символов / {o['max_lines']} строк — сократи формулировку "
                "или выбери более вместительную композицию из списка"
            )
    return "; ".join(lines) if lines else None


def make_validator(ctx: Context, packet: Packet) -> Any:
    """Проверка структуры ответа пакета: тезисы из пакета, композиции из списка, непустые
    заголовки. Детерминирована для одного и того же ответа, поэтому совместима с кэшем и
    replay; ёмкость проверяется отдельно (`fit_packet`) с одним повтором по подсказке."""

    def validate(value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("ответ должен быть объектом")
        drafts = drafts_from_answer(ctx, packet, value, content_first=True)
        return {"drafts": drafts, "rationale": _clean(value.get("rationale"), 300)}

    return validate


def retry_content_loss(before: list[Draft], after: list[Draft]) -> str | None:
    """Консервативный барьер, не семантический судья и не замена validate_facts.

    Сравниваем исходные формулировки, а не уже подогнанные блоки. Разрешены переносы
    между полями/слайдами того же тезиса и удаление дублей. Произвольный пересказ
    автоматически не доказывает сохранность условий. Заметки не заменяют видимый текст.
    """

    def normalized(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().casefold().rstrip(". ")

    def visible(draft: Draft) -> list[str]:
        return [draft.title, draft.message, draft.text] + [
            str(item.get(key) or "") for item in draft.items for key in ("text", "sub")
        ]

    def facts(draft: Draft) -> set[str]:
        return (
            set(draft.facts)
            | {f for item in draft.items for f in item.get("fact_refs", [])}
            | set(FACT_REF.findall(" ".join(visible(draft))))
        )

    for original in before:
        for tid in original.theses:
            candidates = [d for d in after if tid in d.theses]
            if not candidates:
                return f"потерян тезис {tid}"
            texts = {normalized(t) for d in candidates for t in visible(d) if normalized(t)}
            # Только регистр, пробелы и конечная точка несущественны. Поиск подстроки
            # опасен: «разрешено» входит в «не разрешено», но смысл противоположный.
            for text in visible(original):
                value = normalized(text)
                if value and value not in texts:
                    return f"{tid}: не подтверждено сохранение текста «{text[:100]}»"
            if not facts(original) <= set().union(*(facts(d) for d in candidates)):
                return f"{tid}: потеряны ссылки на факты"
            note = normalized(original.notes)
            if note and not any(note == normalized(d.notes) for d in candidates):
                return f"{tid}: потеряны заметки"
            if original.dataset and not any(
                d.dataset == original.dataset and d.columns == original.columns for d in candidates
            ):
                return f"{tid}: изменены набор данных или колонки"
            if original.image and not any(d.image == original.image for d in candidates):
                return f"{tid}: потеряно изображение"
    return None


def with_capacity_hint(req: Any, answer_text: str, hint: str) -> Any:
    """Повтор пакета после переполнения: модель видит свой ответ и измеренные переполнения.
    Новый запрос — новый ключ кэша, поэтому повтор воспроизводим в replay."""
    from dataclasses import replace

    from presentation_designer.llm.types import Message

    text = (
        "Проверка плана по шаблону: "
        + hint[:1800]
        + ". Верни исправленный документ целиком в том же формате, без пояснений."
    )
    return replace(
        req,
        messages=[*req.messages, Message("assistant", answer_text[:4000]), Message("user", text)],
    )


async def fit_packet(
    ctx: Context, packet: Packet, req: Any, client: Any, *, capacity_retries: int = 1
) -> tuple[list[Draft], str, list[Any]]:
    """Запрос пакета, сборка блоков и измерение; при переполнении — повтор с подсказкой
    (не больше capacity_retries). Повтор с потерей содержания не принимается; остаток
    переполнения сохранённого ответа уходит в лестницу сборки и аудит."""
    responses: list[Any] = []
    resp = await client.complete(req, validator=make_validator(ctx, packet))
    responses.append(resp)
    raw = list(resp.parsed["drafts"])
    baseline = copy.deepcopy(raw)
    accepted_text = resp.text
    # Пустые слайды и недобор считаются по ответу модели до подгонки композиции кодом.
    thin_before = thin_count(raw)
    thin_hint = "; ".join(h for h in (density_hint(raw), count_hint(packet, raw)) if h) or None
    drafts = [fit_draft(ctx, d) for d in raw]
    rationale = str(resp.parsed.get("rationale") or "")
    for _ in range(capacity_retries):
        over = overflow_hint(drafts)
        hint = "; ".join(h for h in (over, thin_hint) if h)
        if not hint or (req.deadline is not None and req.deadline.remaining() < 5):
            break
        reason = "переполнения" if over else "пустых слайдов"
        retry = with_capacity_hint(req, accepted_text, hint)
        resp = await client.complete(retry, validator=make_validator(ctx, packet))
        responses.append(resp)
        raw2 = list(resp.parsed["drafts"])
        loss = retry_content_loss(baseline, raw2)
        ctx.fix("packet_retried", f"пакет {packet.index + 1}: повтор из-за {reason}")
        if loss:
            ctx.fix("packet_retry_rejected_content", f"пакет {packet.index + 1}: {loss}")
            continue
        thin_after = thin_count(raw2)
        candidate_baseline = copy.deepcopy(raw2)
        retried = [fit_draft(ctx, d) for d in raw2]
        # Повтор принимается, если стал не хуже по переполнениям, пустым слайдам и числу.
        if (
            sum(len(d.overflow) for d in retried) <= sum(len(d.overflow) for d in drafts)
            and thin_after <= thin_before
            and abs(len(raw2) - packet.target) <= abs(len(raw) - packet.target)
        ):
            drafts = retried
            baseline = candidate_baseline
            accepted_text = resp.text
            raw = raw2
            thin_before = thin_after
            rationale = str(resp.parsed.get("rationale") or rationale)
        thin_hint = None
    return drafts, rationale, responses


# ---------- сборка колоды ----------


def _cover_title(story: JsonDict) -> str:
    brief = story.get("effective_brief") or {}
    title = str(brief.get("title") or story.get("key_takeaway") or "")
    if is_concept(story):
        title = concept_title(title, str(story.get("language", "ru")))
    return _clean(title, 160)


def _title_draft(ctx: Context, structure: Structure) -> Draft:
    brief = ctx.story.get("effective_brief") or {}
    p = structure.title_pattern
    assert p is not None
    title = _clean(structure.cover_title, 160) or _cover_title(ctx.story)
    # Подпись обложки — о чём колода, а не для кого. В шаблоне ЛЦТ на этом
    # месте стоит «Разработчик корпоративного ПО»: жанр — описание, а не
    # адресат. «Для: инвесторы» читается как поле формы.
    subtitle = _clean(
        structure.cover_subtitle
        or ctx.story.get("key_takeaway")
        or (brief.get("audience") and f"Для: {brief['audience']}"),
        200,
    )
    if is_concept(ctx.story):
        subtitle = concept_notice(str(ctx.story.get("language", "ru")))
    d = Draft(
        kind="title",
        theses=list(structure.title_theses),
        pattern=p,
        title=title,
        message=subtitle,
        visual="text",
        text="",
    )
    return d


def _divider_fits(ctx: Context, pattern: PatternInfo, section: Thesis) -> bool:
    """Название раздела помещается в заголовок разделителя (с запасом на уменьшение
    кегля). Разделитель рассчитан на пару крупных слов: вывод целым предложением в нём
    обрезается, и такой раздел остаётся без разделителя — место уходит содержанию."""
    if pattern.title is None or not pattern.title.max_chars:
        return True
    title = cap.substitute_facts(section.statement, ctx.facts)
    return len(title) <= pattern.title.max_chars * 1.2


def _divider_draft(
    ctx: Context, structure: Structure, section: Thesis, pattern: PatternInfo | None = None
) -> Draft:
    p = pattern or structure.divider_pattern
    assert p is not None
    return Draft(
        kind="divider",
        theses=[section.id],
        pattern=p,
        title=_clean(cap.substitute_facts(section.statement, ctx.facts), 120),
        message="" if _DECK_META.search(section.explanation) else _clean(section.explanation, 200),
        visual="text",
        section=section.id,
    )


# Пояснение раздела о самой презентации («Введение в раздел с планом действий»,
# «Заключительный раздел. Резюме презентации») — служебное, аудитории не показывается.
_DECK_META = re.compile(r"(?i)\b(раздел|презентаци|слайд|доклад)")


def _agenda_draft(ctx: Context, structure: Structure) -> Draft:
    p = structure.agenda_pattern
    assert p is not None
    items = [
        {"text": _clean(cap.substitute_facts(s.statement, ctx.facts), 80)}
        for s in structure.sections
    ]
    return Draft(
        kind="agenda",
        theses=[],
        pattern=p,
        title="Содержание",
        visual="bullets",
        items=items,
    )


def _final_draft(ctx: Context, structure: Structure) -> Draft:
    """Финальный слайд: призыв к действию заголовком, если он помещается по измерению,
    иначе короткий заголовок и призыв текстом; если призыв не поместился нигде, финал не
    засчитывает его тезис (тот получает отдельный слайд в сборке)."""
    p = structure.final_pattern
    assert p is not None
    t = structure.final_thesis
    statement = _clean(cap.substitute_facts(t.statement, ctx.facts), 200) if t else ""
    message = _clean(t.explanation if t else ctx.story.get("key_takeaway"), 200)
    options = [statement, "Спасибо за внимание", "Спасибо!"]
    if p.title is not None and p.title.sample_text.strip():
        options.append(_clean(p.title.sample_text, 80))
    title = next(
        (o for o in options if o and (p.title is None or _fits_slot(ctx, p.title, o))),
        "Спасибо!",
    )
    if t and title != statement:
        message = statement
    draft = Draft(
        kind="final",
        theses=[t.id] if t else [],
        pattern=p,
        title=title,
        message=message,
        visual="text",
        facts=[f for f in (t.fact_refs if t else []) if f in ctx.facts],
        section=t.section if t else None,
    )
    fitted = fit_draft(ctx, draft)
    _drop_overflowing_optional(fitted)
    if t and not any(
        statement in str(b.get("text", "")) for b in fitted.blocks if b["kind"] != "number"
    ):
        fitted.theses = []
        fitted.facts = []
    return fitted


def _drop_overflowing_optional(draft: Draft) -> None:
    """У служебных слайдов (титул, финал) необязательный блок, который не помещается,
    убирается целиком: подпись важнее пустоты, но не важнее чистого слайда."""
    p = draft.pattern
    bad = {
        o["slot_id"]
        for o in draft.overflow
        if (slot := p.slots.get(o["slot_id"])) is not None and not slot.required
    }
    if not bad:
        return
    draft.blocks = [b for b in draft.blocks if b["slot_id"] not in bad]
    draft.overflow = [o for o in draft.overflow if o["slot_id"] not in bad]


def _summary_draft(ctx: Context) -> Draft | None:
    takeaway = _clean(ctx.story.get("key_takeaway"), 300)
    if not takeaway:
        return None
    # Одна мысль крупно: композиция цитаты, если она есть и вывод помещается в её большой слот.
    quote = next(
        (
            c
            for c in candidates_for(
                ctx.patterns,
                Need("quote", text_chars=len(takeaway)),
                ctx.variant_id,
                limit=2,
                has_datasets=ctx.has_datasets,
            )
            if c.role == "quote" and c.title is not None and _fits_slot(ctx, c.title, takeaway)
        ),
        None,
    )
    if quote is not None:
        return Draft(
            kind="summary",
            theses=[],
            pattern=quote,
            title=takeaway,
            message="Главный вывод",
            visual="quote",
        )
    cands = [
        c
        for c in candidates_for(
            ctx.patterns,
            Need("text", text_chars=len(takeaway)),
            ctx.variant_id,
            limit=4,
            has_datasets=ctx.has_datasets,
        )
        if any(
            _fits_slot(ctx, slot, takeaway)
            for kind in ("body", "subtitle", "bullets")
            for slot in c.single(kind)
        )
    ]
    if not cands:
        return None
    return Draft(
        kind="summary",
        theses=[],
        pattern=cands[0],
        title="Главный вывод",
        message=_clean(ctx.story.get("key_takeaway"), 300),
        visual="text",
        text=_clean(ctx.story.get("key_takeaway"), 300),
    )


def split_draft(ctx: Context, draft: Draft) -> tuple[Draft, Draft] | None:
    if not draft.splittable:
        return None
    half = (len(draft.items) + 1) // 2
    a = copy.deepcopy(draft)
    b = copy.deepcopy(draft)
    a.items, b.items = draft.items[:half], draft.items[half:]
    a.facts = [
        f for f in draft.facts if any(f in (it.get("fact_refs") or []) for it in a.items)
    ] or draft.facts
    b.facts = [f for f in draft.facts if any(f in (it.get("fact_refs") or []) for it in b.items)]
    b.text = ""
    b.title = draft.title if len(draft.title) < 60 else draft.title[:57] + "…"
    b.title = f"{b.title} (продолжение)"
    a.actions.append("split")
    b.actions.append("split")
    a.overflow, b.overflow = [], []
    return fit_draft(ctx, a), fit_draft(ctx, b)


def merge_drafts(
    ctx: Context, a: Draft, b: Draft, *, across_sections: bool = False, relaxed: bool = False
) -> Draft | None:
    """Два соседних содержательных слайда одного раздела → один список/карточки.

    `relaxed` — последний шаг перед отказом по числу слайдов: схемы и этапы тоже
    становятся пунктами списка, пунктов до десяти."""
    if a.kind != "content" or b.kind != "content":
        return None
    if a.section != b.section and not across_sections:
        return None
    unmergeable = ("chart", "table") if relaxed else ("chart", "table", "diagram", "timeline")
    if a.visual in unmergeable or b.visual in unmergeable:
        return None

    def content_items(draft: Draft) -> list[JsonDict]:
        if draft.items:
            return copy.deepcopy(draft.items)
        text = draft.text or draft.message or draft.title
        # IDs alone do not render a quantity in a text list. Keep every source fact visible.
        missing = [f for f in draft.facts if f"{{fact:{f}}}" not in text]
        if missing:
            text += "; " + "; ".join(_fact_phrase(ctx, f) for f in missing if f in ctx.facts)
        return [{"text": text, "fact_refs": draft.facts[:]}]

    items = content_items(a) + content_items(b)
    if len(items) > (10 if relaxed else 8):
        return None
    need = Need(
        "bullets", items=len(items), numbers=0, text_chars=sum(len(i["text"]) for i in items)
    )
    cands = candidates_for(
        ctx.patterns, need, ctx.variant_id, limit=3, has_datasets=ctx.has_datasets
    )
    if not cands:
        return None
    section = ctx.thesis(a.section) if a.section and a.section == b.section else None
    merged = Draft(
        kind="content",
        theses=list(dict.fromkeys(a.theses + b.theses)),
        pattern=cands[0],
        title=_clean(cap.substitute_facts(section.statement, ctx.facts), 120)
        if section
        else a.title,
        message=a.message,
        visual="bullets",
        items=items,
        facts=list(dict.fromkeys(a.facts + b.facts)),
        section=a.section,
        source_refs=list(dict.fromkeys(a.source_refs + b.source_refs)),
        candidates=cands[1:],
        actions=["merge"],
    )
    fitted = fit_draft(ctx, merged)
    return fitted if not fitted.overflow else None


def _expand_tables(ctx: Context, drafts: list[Draft], room: int) -> list[Draft]:
    """Большие наборы данных: продолжения таблицы в пределах свободного места, иначе усечение."""
    out: list[Draft] = []
    max_rows = int(ctx.app.plan.table_max_rows)
    for d in drafts:
        out.append(d)
        if d.kind != "content" or d.visual != "table" or not d.dataset:
            continue
        ds = ctx.datasets.get(d.dataset)
        if ds is None:
            continue
        total = len(ds["rows"])
        d.max_rows = max_rows
        d.total_rows = total
        if total <= max_rows:
            continue
        pages = math.ceil(total / max_rows) - 1
        allowed = pages if ctx.variant_id == "detailed" else 0
        allowed = min(allowed, max(room, 0))
        for i in range(allowed):
            cont = copy.deepcopy(d)
            cont.row_offset = (i + 1) * max_rows
            cont.title = f"{d.title} (продолжение {i + 2})"[:200]
            cont.text = ""
            cont.items = []
            cont.actions.append("table_continuation")
            out.append(fit_draft(ctx, cont))
        room -= allowed
        shown = (allowed + 1) * max_rows
        if shown < total:
            ctx.fix(
                "table_truncated",
                f"«{d.title[:40]}»: показаны первые {min(shown, total)} из {total} строк "
                f"набора {d.dataset}",
            )
    return out


def assemble(ctx: Context, structure: Structure, packet_drafts: list[list[Draft]]) -> list[Draft]:
    """Колода: титульный, содержание с разделителями, финал; таблицы; число слайдов; покрытие."""
    spec = ctx.spec
    title_draft = fit_draft(ctx, _title_draft(ctx, structure))
    _drop_overflowing_optional(title_draft)
    drafts: list[Draft] = [title_draft]
    if structure.agenda_pattern is not None:
        drafts.append(fit_draft(ctx, _agenda_draft(ctx, structure)))
    content: list[Draft] = [d for pack in packet_drafts for d in pack]
    # Порядок по первому тезису; разделители перед первым слайдом раздела.
    order = {t.id: t.order for t in ctx.theses}
    content.sort(key=lambda d: min((order.get(t, 10**6) for t in d.theses), default=10**6))
    placed_dividers: set[str] = set()
    body: list[Draft] = []
    for d in content:
        if d.section and d.section in structure.dividers and d.section not in placed_dividers:
            sec = ctx.thesis(d.section)
            if sec is not None:
                body.append(fit_draft(ctx, _divider_draft(ctx, structure, sec)))
                placed_dividers.add(d.section)
        body.append(d)
    final = _final_draft(ctx, structure) if structure.final_pattern else None
    if final is not None and structure.final_thesis is not None and not final.theses:
        # Призыв не поместился на финальный слайд: отдельный содержательный слайд перед ним.
        t = structure.final_thesis
        need = thesis_need(ctx, t)
        cands = candidates_for(
            ctx.patterns, need, ctx.variant_id, limit=3, has_datasets=need.has_dataset
        )
        if cands:
            body.append(
                fit_draft(
                    ctx,
                    Draft(
                        kind="content",
                        theses=[t.id],
                        pattern=cands[0],
                        title=_clean(cap.substitute_facts(t.statement, ctx.facts), 160),
                        message=_clean(t.explanation, 300),
                        visual=need.visual,
                        text=t.explanation,
                        facts=[f for f in t.fact_refs if f in ctx.facts],
                        section=t.section,
                        source_refs=[b for b in t.source_refs if b in ctx.blocks],
                        candidates=cands[1:],
                    ),
                )
            )
            ctx.fix("final_thesis_slide", f"{t.id}: призыв не помещается на финальный слайд")
    fixed_n = len(drafts) + (1 if final else 0)
    body = _expand_tables(ctx, body, spec.hi - fixed_n - len(body))
    # Переполненные списки делятся, пока есть место.
    i = 0
    while i < len(body):
        d = body[i]
        if d.overflow_in_items and d.splittable and fixed_n + len(body) < spec.hi:
            pair = split_draft(ctx, d)
            if pair is not None:
                body[i : i + 1] = list(pair)
                i += 2
                continue
        i += 1
    body = _drop_duplicates(ctx, body)
    deck = drafts + body + ([final] if final else [])
    deck = _control_count(ctx, structure, deck)
    deck = _ensure_coverage(ctx, structure, deck)
    deck = _ensure_facts(ctx, deck)
    deck = _diversify(ctx, deck)
    deck = _limit_series(ctx, deck)
    _distinct_titles(ctx, deck)
    return deck


def _distinct_titles(ctx: Context, deck: list[Draft]) -> None:
    """Заголовки содержательных слайдов не повторяются: слияние слайдов одного раздела даёт
    обоим формулировку раздела. Повтор получает формулировку своего тезиса."""
    seen: list[str] = []
    for d in deck:
        if d.kind != "content":
            continue
        if any(_similar(d.title, o) >= 0.8 for o in seen):
            for tid in d.theses:
                t = ctx.thesis(tid)
                statement = _clean(cap.substitute_facts(t.statement, ctx.facts), 120) if t else ""
                if statement and all(_similar(statement, o) < 0.8 for o in seen):
                    ctx.fix("title_deduplicated", f"«{d.title[:40]}» → «{statement[:40]}»")
                    d.title = statement
                    d.overflow = []
                    fit_draft(ctx, d)
                    break
        seen.append(d.title)


def _ensure_facts(ctx: Context, deck: list[Draft]) -> list[Draft]:
    """Обязательный факт, которого нет ни на одном слайде, — пунктом на слайд его тезиса.

    Проверка по колоде, а не по слайду: факт тезиса, уже показанный на соседнем слайде,
    второй раз не дописывается, а на слайд «Риски» не попадают числа из «Проблемы»."""
    shown: set[str] = set()
    written = []
    for d in deck:
        shown.update(b["number"]["fact_id"] for b in d.blocks if b.get("number"))
        text = _visible_text(d) + " " + d.title
        shown.update(FACT_REF.findall(text))
        written.append(" ".join(cap.substitute_facts(text, ctx.facts).split()))
    everything = " ".join(written)
    for fid, fact in ctx.facts.items():
        if not fact.get("must_keep", True) or fact.get("derived") or fid in shown:
            continue
        raw = " ".join(str(fact.get("raw") or "").split())
        if raw and raw in everything:
            continue
        owners = {t.id for t in ctx.theses if fid in t.fact_refs}
        home = next(
            (d for d in deck if d.kind == "content" and owners & set(d.theses)),
            None,
        )
        if home is None:
            continue
        home.items.append({"text": _fact_phrase(ctx, fid), "fact_refs": [fid]})
        home.facts.append(fid)
        home.overflow = []
        fit_draft(ctx, home)
        ctx.fix("fact_restored", f"«{home.title[:40]}»: добавлен показатель {fid}")
    return deck


def _drop_duplicates(ctx: Context, body: list[Draft]) -> list[Draft]:
    """Два содержательных слайда с тем же заголовком и тем же содержанием — один слайд.

    Бюджет пакета просит два слайда на тезис, и модель иногда повторяет первый дословно
    (VK Tech: «Цель: 60 % открываемости…» дважды подряд). Недостачу восполнит контроль
    числа слайдов настоящим раскрытием, а не копией.
    """
    out: list[Draft] = []
    for d in body:
        twin = next(
            (
                o
                for o in out
                if d.kind == o.kind == "content"
                and _similar(d.title, o.title) >= 0.8
                and _similar(_visible_text(d), _visible_text(o)) >= 0.8
            ),
            None,
        )
        if twin is not None:
            twin.theses = list(dict.fromkeys(twin.theses + d.theses))
            ctx.fix("duplicate_dropped", f"«{d.title[:40]}»: повтор предыдущего слайда")
            continue
        if d.kind == "content" and any(
            o.kind == "content" and _similar(d.title, o.title) >= 0.8 for o in out
        ):
            # Разное содержание под одним заголовком: у второго слайда заголовком становится
            # формулировка его тезиса, иначе колода читается как повтор.
            for tid in d.theses:
                t = ctx.thesis(tid)
                statement = _clean(cap.substitute_facts(t.statement, ctx.facts), 120) if t else ""
                if statement and all(_similar(statement, o.title) < 0.8 for o in out):
                    ctx.fix("title_deduplicated", f"«{d.title[:40]}» → «{statement[:40]}»")
                    d.title = statement
                    d.overflow = []
                    fit_draft(ctx, d)
                    break
        out.append(d)
    return out


def _similar(a: str, b: str) -> float:
    left, right = _stems(a), _stems(b)
    if not left and not right:
        return 1.0
    return len(left & right) / max(len(left | right), 1)


def _count(deck: list[Draft]) -> int:
    return len(deck)


def _control_count(ctx: Context, structure: Structure, deck: list[Draft]) -> list[Draft]:
    spec = ctx.spec
    lo, hi, target = spec.lo, spec.hi, spec.target
    # Слишком много: разделители и содержание → убираем/сливаем.
    guard = 0
    while _count(deck) > hi and guard < 60:
        guard += 1
        if _drop_one(ctx, deck, kinds=("agenda", "divider", "summary", "facts")):
            continue
        if _merge_one(ctx, deck):
            continue
        if _merge_one(ctx, deck, across_sections=True):
            continue
        if _drop_optional(ctx, deck):
            continue
        if _merge_one(ctx, deck, across_sections=True, relaxed=True):
            continue
        if _fold_thinnest(ctx, deck):
            continue
        break
    if _count(deck) > hi:
        raise PlanError(
            "plan_slide_count",
            f"Не удалось уложить содержание в {hi} слайдов: получилось {_count(deck)}",
            details={
                "variant_id": ctx.variant_id,
                "required": spec.as_dict(),
                "actual": _count(deck),
                "slides": [{"title": d.title, "theses": d.theses} for d in deck],
            },
        )
    # Слишком мало: разделители, содержание, разделение, итог.
    # Порядок добора: сначала слайды в стиле шаблона с настоящим содержанием (разделители,
    # возвращённые тезисы, итог, оглавление, раскрытие тезиса из пояснения), потом деление
    # списков и слайд показателей; список из одного пункта — только ради точного числа.
    guard = 0
    while _count(deck) < lo and guard < 60:
        guard += 1
        if ctx.variant_id != "compact" and _add_divider(ctx, structure, deck):
            continue
        if _restore_dropped(ctx, structure, deck):
            continue
        if _add_summary(ctx, deck):
            continue
        if _add_divider(ctx, structure, deck, force=True):
            continue
        if _add_agenda(ctx, structure, deck, force=True):
            continue
        if _add_detail_slide(ctx, deck):
            continue
        if _split_one(ctx, deck):
            continue
        if _add_facts_slide(ctx, deck):
            continue
        if spec.exact is not None and _split_one(ctx, deck, min_items=2):
            continue
        break
    if _count(deck) < lo:
        # Содержания меньше, чем нужно на нижнюю границу: колода короче, а не ошибка и не
        # слайды из одного пункта. Просьбу пользователя (точное число или его диапазон)
        # задание называет фразой — предупреждение slide_count_short; диапазон по умолчанию
        # пользователь не выбирал, и его сокращение остаётся только в плане.
        n = _count(deck)
        asked = str(spec.exact) if spec.exact is not None else f"{lo}–{hi}"
        noun = plural(spec.exact, "слайд", "слайда", "слайдов") if spec.exact else "слайдов"
        if spec.explicit:
            ctx.fix("slide_count_short", f"просили {asked} {noun}, содержания хватило на {n}")
        else:
            ctx.fix(
                "slide_count_relaxed",
                f"диапазон по умолчанию {asked} слайдов, содержания хватило на {n}",
            )
        # Точное число становится диапазоном «сколько вышло — сколько просили»: верхняя
        # граница остаётся местом для обязательных тезисов на шаге покрытия.
        spec.exact = None
        spec.min = n
        spec.target = min(spec.target, n)
        return deck
    # К цели — только обратимыми действиями.
    guard = 0
    while _count(deck) < target and guard < 20:
        guard += 1
        if _add_divider(ctx, structure, deck) or _add_agenda(ctx, structure, deck):
            continue
        break
    guard = 0
    while _count(deck) > target and guard < 20:
        guard += 1
        if _drop_one(ctx, deck, kinds=("agenda",)):
            continue
        break
    return deck


def _drop_one(ctx: Context, deck: list[Draft], *, kinds: tuple[str, ...]) -> bool:
    for i in range(len(deck) - 1, -1, -1):
        if deck[i].kind in kinds:
            removed = deck.pop(i)
            ctx.fix("slide_dropped", f"{removed.kind}: «{removed.title[:40]}»")
            return True
    return False


def _drop_optional(ctx: Context, deck: list[Draft]) -> bool:
    for i in range(len(deck) - 1, -1, -1):
        d = deck[i]
        if d.kind != "content":
            continue
        theses = [ctx.thesis(t) for t in d.theses]
        if theses and all(t is not None and not t.required for t in theses):
            deck.pop(i)
            ctx.fix(
                "reduction_applied", f"убран слайд с необязательными тезисами {', '.join(d.theses)}"
            )
            return True
    return False


def _merge_one(
    ctx: Context, deck: list[Draft], *, across_sections: bool = False, relaxed: bool = False
) -> bool:
    best: tuple[int, Draft] | None = None
    for i in range(len(deck) - 1):
        a, b = deck[i], deck[i + 1]
        if a.kind != "content" or b.kind != "content":
            continue
        merged = merge_drafts(ctx, a, b, across_sections=across_sections, relaxed=relaxed)
        if merged is None:
            continue
        size = len(a.items) + len(b.items)
        if best is None or size < len(best[1].items):
            best = (i, merged)
    if best is None:
        return False
    i, merged = best
    ctx.fix("slides_merged", f"«{deck[i].title[:30]}» + «{deck[i + 1].title[:30]}»")
    deck[i : i + 2] = [merged]
    return True


def _fold_thinnest(ctx: Context, deck: list[Draft]) -> bool:
    """Последний шаг перед отказом по верхней границе: самый бедный содержательный слайд
    уходит, его тезисы числятся за соседним. Колода на слайд короче лучше, чем вариант,
    который не собран совсем."""
    content = [
        (len(FACT_REF.sub("", _visible_text(d))), i)
        for i, d in enumerate(deck)
        if d.kind == "content" and not any(b.get("kind") in ("chart", "table") for b in d.blocks)
    ]
    if len(content) < 2:
        return False
    _, i = min(content)
    removed = deck.pop(i)
    neighbour = next(
        (deck[j] for j in (i - 1, i) if 0 <= j < len(deck) and deck[j].kind == "content"),
        None,
    )
    if neighbour is not None:
        neighbour.theses = list(dict.fromkeys(neighbour.theses + removed.theses))
    ctx.fix(
        "slide_folded",
        f"«{removed.title[:40]}»: убран ради верхней границы числа слайдов",
    )
    return True


def _restore_dropped(ctx: Context, structure: Structure, deck: list[Draft]) -> bool:
    """Компактный вариант убрал необязательный тезис, а слайдов не хватает: вернуть его."""
    while structure.dropped:
        t = structure.dropped.pop(0)
        need = thesis_need(ctx, t)
        cands = candidates_for(
            ctx.patterns, need, ctx.variant_id, limit=3, has_datasets=need.has_dataset
        )
        if not cands:
            continue
        d = Draft(
            kind="content",
            theses=[t.id],
            pattern=cands[0],
            title=_clean(cap.substitute_facts(t.statement, ctx.facts), 160),
            message=_clean(t.explanation, 300),
            visual=need.visual,
            text=t.explanation,
            facts=[f for f in t.fact_refs if f in ctx.facts],
            dataset=next((x for x in t.dataset_refs if x in ctx.datasets), None),
            section=t.section,
            source_refs=[b for b in t.source_refs if b in ctx.blocks],
            candidates=cands[1:],
        )
        deck.insert(_insert_index(ctx, deck, t), fit_draft(ctx, d))
        ctx.fix("reduction_reverted", f"{t.id}: возвращён ради числа слайдов")
        return True
    return False


def _add_divider(
    ctx: Context, structure: Structure, deck: list[Draft], *, force: bool = False
) -> bool:
    if structure.divider_pattern is None or (ctx.variant_id == "compact" and not force):
        return False
    present = {d.section for d in deck if d.kind == "divider"}
    for sec in structure.sections:
        if sec.id in present or not _divider_fits(ctx, structure.divider_pattern, sec):
            continue
        idx = next(
            (i for i, d in enumerate(deck) if d.kind == "content" and d.section == sec.id), None
        )
        if idx is None:
            continue
        deck.insert(idx, fit_draft(ctx, _divider_draft(ctx, structure, sec)))
        return True
    return False


def _add_agenda(
    ctx: Context, structure: Structure, deck: list[Draft], *, force: bool = False
) -> bool:
    if (ctx.variant_id == "compact" and not force) or any(d.kind == "agenda" for d in deck):
        return False
    policy = str(ctx.app.plan.style_policy)
    _divider, tone, rank = _style_anchor(
        _roomy_pool(structure.divider_pool), ctx.variant_id, policy
    )
    p = _service_pattern(structure.agenda_pool, policy, tone, rank)
    if p is None or len(structure.sections) < 2:
        return False
    structure.agenda_pattern = p
    deck.insert(1, fit_draft(ctx, _agenda_draft(ctx, structure)))
    return True


def _split_one(ctx: Context, deck: list[Draft], *, min_items: int = 4) -> bool:
    candidates = [
        (len(d.items), i)
        for i, d in enumerate(deck)
        if d.kind == "content" and len(d.items) >= min_items and d.splittable
    ]
    if not candidates:
        return False
    _, i = max(candidates)
    pair = split_draft(ctx, deck[i])
    if pair is None:
        return False
    deck[i : i + 1] = list(pair)
    ctx.fix("slide_split", f"«{deck[i].title[:40]}» разделён на два")
    return True


def _add_detail_slide(ctx: Context, deck: list[Draft]) -> bool:
    """Слайд-раскрытие тезиса из его пояснения в плане (2–4 предложения пунктами) после
    слайда этого тезиса: настоящее содержание вместо деления списка на один пункт."""
    used_text = " ".join(
        str(b.get("text") or "") + " ".join(it.get("text", "") for it in b.get("items") or [])
        for d in deck
        for b in d.blocks
    )
    detailed = {t for d in deck if "detail" in d.actions for t in d.theses}
    for i, d in enumerate(deck):
        if d.kind != "content" or "detail" in d.actions:
            continue
        for tid in d.theses:
            t = ctx.thesis(tid)
            if t is None or tid in detailed or not t.explanation:
                continue
            sentences = [s for s in _sentences(t.explanation) if len(s) >= 20]
            if len(sentences) < 2 or any(sen[:40] in used_text for sen in sentences):
                continue
            need = Need(
                "bullets", items=len(sentences[:4]), text_chars=sum(map(len, sentences[:4]))
            )
            cands = candidates_for(
                ctx.patterns, need, ctx.variant_id, limit=3, has_datasets=ctx.has_datasets
            )
            if not cands:
                continue
            detail = Draft(
                kind="content",
                theses=[tid],
                pattern=cands[0],
                title=_clean(cap.substitute_facts(t.statement, ctx.facts), 120),
                message=_clean(sentences[0], 300),
                visual="bullets",
                items=[{"text": sen} for sen in sentences[:4]],
                facts=[f for f in t.fact_refs if f in ctx.facts and f not in d.facts],
                section=t.section,
                source_refs=[b for b in t.source_refs if b in ctx.blocks],
                candidates=cands[1:],
                actions=["detail"],
            )
            deck.insert(i + 1, fit_draft(ctx, detail))
            ctx.fix("detail_slide_added", f"{tid}: слайд-раскрытие из пояснения ради числа слайдов")
            return True
    return False


def _add_facts_slide(ctx: Context, deck: list[Draft]) -> bool:
    """Слайд с ключевыми показателями: обязательные факты, ещё не вынесенные в числа."""
    if any(d.kind == "facts" for d in deck):
        return False
    shown = {b["number"]["fact_id"] for d in deck for b in d.blocks if b.get("number")}
    facts = [
        f["fact_id"]
        for f in ctx.package.get("facts", [])
        if f.get("must_keep", True) and f["fact_id"] not in shown
    ][:6]
    if not facts:
        return False
    cands = candidates_for(
        ctx.patterns,
        Need("number", items=len(facts), numbers=len(facts)),
        ctx.variant_id,
        limit=2,
        has_datasets=ctx.has_datasets,
    )
    if not cands:
        return False
    d = Draft(
        kind="facts",
        theses=[],
        pattern=cands[0],
        title="Ключевые показатели",
        visual="number",
        items=[{"text": _fact_label(ctx, f) or f, "fact_refs": [f]} for f in facts],
        facts=facts,
        candidates=cands[1:],
    )
    idx = len(deck) - 1 if deck and deck[-1].kind == "final" else len(deck)
    deck.insert(idx, fit_draft(ctx, d))
    ctx.fix("facts_slide_added", "добавлен слайд с ключевыми показателями ради числа слайдов")
    return True


def _add_summary(ctx: Context, deck: list[Draft]) -> bool:
    if any(d.kind == "summary" for d in deck):
        return False
    d = _summary_draft(ctx)
    if d is None:
        return False
    idx = len(deck) - 1 if deck and deck[-1].kind == "final" else len(deck)
    deck.insert(idx, fit_draft(ctx, d))
    return True


def _ensure_coverage(ctx: Context, structure: Structure, deck: list[Draft]) -> list[Draft]:
    """Обязательные тезисы без слайда: раздел — к первому слайду раздела или разделителю;
    остальные — пунктом в слайд того же раздела, иначе отдельный слайд в пределах числа."""
    covered: set[str] = {t for d in deck for t in d.theses}
    # Разделы покрываются первым содержательным слайдом раздела.
    for d in deck:
        if d.kind == "content" and d.section and d.section not in covered:
            d.theses.append(d.section)
            covered.add(d.section)
    missing = [t for t in ctx.theses if t.required and t.id not in covered]
    for t in missing:
        if t.kind == "section":
            host = next((d for d in deck if d.kind == "content" and d.section == t.id), None)
            if host is None:
                # Пустой раздел: разделитель, если есть место, иначе к соседнему слайду.
                if (
                    structure.divider_pattern is not None
                    and ctx.variant_id != "compact"
                    and _count(deck) < ctx.spec.hi
                    and _divider_fits(ctx, structure.divider_pattern, t)
                ):
                    idx = _insert_index(ctx, deck, t)
                    deck.insert(idx, fit_draft(ctx, _divider_draft(ctx, structure, t)))
                    covered.add(t.id)
                    continue
                host = _neighbour(ctx, deck, t)
            if host is not None:
                host.theses.append(t.id)
                covered.add(t.id)
                continue
        host = next((d for d in deck if d.kind == "content" and d.section == t.section), None)
        if host is None and _count(deck) >= ctx.spec.hi:
            # Слайда своего раздела нет, а места больше нет: пункт соседнего слайда лучше
            # отказа всего варианта.
            host = _neighbour(ctx, deck, t)
        attached = False
        if host is not None:
            trial = copy.deepcopy(host)
            trial.theses.append(t.id)
            trial.items = [
                *trial.items,
                {
                    "text": _clean(cap.substitute_facts(t.statement, ctx.facts), 200),
                    "fact_refs": [f for f in t.fact_refs if f in ctx.facts],
                },
            ]
            trial.facts = list(
                dict.fromkeys(trial.facts + [f for f in t.fact_refs if f in ctx.facts])
            )
            trial.overflow = []
            fit_draft(ctx, trial)
            if not trial.overflow:
                deck[deck.index(host)] = trial
                covered.add(t.id)
                ctx.fix("thesis_attached", f"{t.id} → «{host.title[:40]}»")
                attached = True
        if attached:
            continue
        if _count(deck) < ctx.spec.hi:
            need = thesis_need(ctx, t)
            cands = candidates_for(
                ctx.patterns, need, ctx.variant_id, limit=3, has_datasets=need.has_dataset
            )
            if cands:
                d = Draft(
                    kind="content",
                    theses=[t.id],
                    pattern=cands[0],
                    title=_clean(cap.substitute_facts(t.statement, ctx.facts), 160),
                    message=_clean(t.explanation, 300),
                    visual=need.visual,
                    text=t.explanation,
                    facts=[f for f in t.fact_refs if f in ctx.facts],
                    dataset=next((x for x in t.dataset_refs if x in ctx.datasets), None),
                    section=t.section,
                    source_refs=[b for b in t.source_refs if b in ctx.blocks],
                    candidates=cands[1:],
                )
                deck.insert(_insert_index(ctx, deck, t), fit_draft(ctx, d))
                covered.add(t.id)
                ctx.fix("thesis_slide_added", f"{t.id}: отдельный слайд")
                continue
        raise PlanError(
            "plan_coverage",
            f"Обязательный тезис {t.id} не помещается в {ctx.spec.hi} слайдов",
            details={
                "variant_id": ctx.variant_id,
                "thesis_id": t.id,
                "statement": t.statement,
                "required": ctx.spec.as_dict(),
                "actual": _count(deck),
            },
        )
    return deck


def _insert_index(ctx: Context, deck: list[Draft], t: Thesis) -> int:
    order = {x.id: x.order for x in ctx.theses}
    for i, d in enumerate(deck):
        if d.kind == "final":
            return i
        if d.kind in ("content", "divider") and d.theses:
            if min(order.get(x, 10**6) for x in d.theses) > t.order:
                return i
    return len(deck) - 1 if deck and deck[-1].kind == "final" else len(deck)


def _neighbour(ctx: Context, deck: list[Draft], t: Thesis) -> Draft | None:
    idx = _insert_index(ctx, deck, t)
    for j in (idx - 1, idx):
        if 0 <= j < len(deck) and deck[j].kind == "content":
            return deck[j]
    return next((d for d in deck if d.kind == "content"), None)


def _need_of(draft: Draft) -> Need:
    """Что слайду нужно от композиции: подача, число пунктов, объём текста."""
    chars = len(draft.text) + sum(len(it.get("text") or "") for it in draft.items)
    return Need(
        draft.visual,
        items=max(len(draft.items), 1),
        numbers=sum(1 for b in draft.blocks if b.get("number")),
        text_chars=max(chars, 40),
        has_diagram=bool(draft.diagram_kind) and 2 <= len(draft.items) <= 6,
    )


def _alternatives(ctx: Context, draft: Draft) -> list[PatternInfo]:
    """Чем можно заменить композицию слайда, в порядке предпочтения.

    Сначала братья по группе образцов: это та же композиция в другом
    исполнении, язык шаблона сохраняется полностью. Затем — подбор по
    потребности слайда и уже рассмотренные кандидаты.
    """
    pool = siblings_of(ctx.patterns, draft.pattern)
    pool += candidates_for(
        ctx.patterns, _need_of(draft), ctx.variant_id, limit=12, has_datasets=ctx.has_datasets
    )
    pool += list(draft.candidates)
    seen: set[str] = {draft.pattern.pattern_id}
    out: list[PatternInfo] = []
    for cand in pool:
        if cand.pattern_id in seen:
            continue
        seen.add(cand.pattern_id)
        out.append(cand)
    # Свой стиль вперёд: светлый слайд не должен без нужды становиться тёмным.
    out.sort(key=lambda c: c.style_key != draft.pattern.style_key)
    return out


def _diversify(ctx: Context, deck: list[Draft]) -> list[Draft]:
    """Разные слайды — разные композиции.

    Подгонка по объёму (`right_size_pattern`) выбирает композицию, ближайшую
    по вместимости, и для одинаковых по объёму слайдов выбирает одну и ту же:
    в замере от 18.09.2026 пять слайдов подряд получили `pat_s40`, а вся
    колода уложилась в четыре композиции из пятидесяти четырёх, какие есть в
    шаблоне. Формально дефекта нет — каждый слайд по отдельности собран
    правильно. Смотреть такую колоду невозможно.

    Здесь повторы разводятся: слайд переезжает на композицию, которой в колоде
    ещё нет. Замена принимается только если содержание в неё помещается —
    проверяется тем же измерением, что и всё остальное.
    """
    from collections import Counter

    limit = MAX_PATTERN_USES.get(ctx.variant_id, DEFAULT_MAX_USES)
    used = Counter(d.pattern.pattern_id for d in deck)
    for d in deck:
        if d.kind != "content" or used[d.pattern.pattern_id] <= limit:
            continue
        for alt in _alternatives(ctx, d):
            if used.get(alt.pattern_id, 0) >= limit:
                continue
            trial = copy.deepcopy(d)
            trial.pattern = alt
            trial.overflow = []
            fit_draft(ctx, trial)
            if trial.overflow or trial.pattern.pattern_id == d.pattern.pattern_id:
                continue
            if not _keeps_content(d, trial):
                # Разнообразие не стоит содержания: подгонка вмещает текст в мелкие слоты
                # сокращением, и «Каналы / Фокус» вместо трёх функций продукта хуже повтора.
                continue
            used[d.pattern.pattern_id] -= 1
            used[trial.pattern.pattern_id] += 1
            ctx.fix(
                "pattern_diversified",
                f"«{d.title[:40]}»: {d.pattern.pattern_id} → {trial.pattern.pattern_id} "
                f"(эта композиция уже занята {used[d.pattern.pattern_id] + 1} раз)",
            )
            d.pattern, d.blocks, d.overflow = trial.pattern, trial.blocks, []
            d.actions.append(f"diversify:{trial.pattern.pattern_id}")
            break
    return deck


def _content_chars(draft: Draft) -> int:
    """Объём содержания черновика без заголовка: текст и пункты (с подписями)."""
    parts = [draft.text] + [f"{it.get('sub', '')} {it.get('text', '')}" for it in draft.items]
    return len(FACT_REF.sub("", " ".join(p for p in parts if p)).strip())


def _big_image_slot(p: PatternInfo) -> bool:
    return p.role != "chart" and any(
        s.kind == "image" and s.bbox[2] * s.bbox[3] >= 0.18 for s in p.slots.values()
    )


def _visible_text(draft: Draft) -> str:
    parts: list[str] = []
    for b in draft.blocks:
        if b.get("kind") == "title":
            continue
        if b.get("text"):
            parts.append(str(b["text"]))
        parts.extend(str(it.get("text") or "") for it in b.get("items") or [])
        if b.get("diagram"):
            parts.extend(
                f"{it.get('text', '')} {it.get('sub', '')}"
                for it in b["diagram"].get("items") or []
            )
    return " ".join(parts)


def _keeps_content(before: Draft, after: Draft, ratio: float = 0.85) -> bool:
    """Замена композиции не теряет содержание: все показанные факты на месте, видимого
    текста не меньше доли `ratio`, ничего не осталось неразмещённым."""
    if after.unplaced_text.strip() and not before.unplaced_text.strip():
        return False
    media = ("chart", "table", "image", "diagram")
    if {b.get("kind") for b in before.blocks if b.get("kind") in media} - {
        b.get("kind") for b in after.blocks
    }:
        return False
    facts_before = set(FACT_REF.findall(_visible_text(before))) | {
        b["number"]["fact_id"] for b in before.blocks if b.get("number")
    }
    facts_after = set(FACT_REF.findall(_visible_text(after))) | {
        b["number"]["fact_id"] for b in after.blocks if b.get("number")
    }
    if not facts_before <= facts_after:
        return False
    chars_before = len(FACT_REF.sub("", _visible_text(before)).strip())
    chars_after = len(FACT_REF.sub("", _visible_text(after)).strip())
    return chars_after >= ratio * chars_before


def _limit_series(ctx: Context, deck: list[Draft]) -> list[Draft]:
    """Серии одинаковых композиций не длиннее max_consecutive, если есть замена: сначала
    братья по группе образцов (та же композиция, другой образец), затем кандидаты тезиса;
    замена принимается только без переполнений по измерению."""
    sequence: list[str] = []
    for d in deck:
        if not sequence_ok(sequence, d.pattern) and d.kind == "content":
            for alt in siblings_of(ctx.patterns, d.pattern) + d.candidates:
                if not sequence_ok(sequence, alt):
                    continue
                trial = copy.deepcopy(d)
                trial.pattern = alt
                trial.overflow = []
                fit_draft(ctx, trial)
                if trial.overflow or not sequence_ok(sequence, trial.pattern):
                    continue
                if not _keeps_content(d, trial):
                    continue
                sibling = bool(alt.group_id) and alt.group_id == d.pattern.group_id
                ctx.fix(
                    "series_limited",
                    f"«{d.title[:40]}»: {d.pattern.pattern_id} → {trial.pattern.pattern_id}"
                    + (" (брат по группе)" if sibling else ""),
                )
                d.pattern, d.blocks, d.overflow = trial.pattern, trial.blocks, []
                break
        sequence.append(d.pattern.pattern_id)
    return deck


# ---------- документ SlidePlan ----------


def _visual_kind(d: Draft) -> str:
    if d.kind == "title":
        return "title"
    if d.kind == "divider":
        return "section"
    if d.kind == "final":
        return "thanks"
    if d.kind == "agenda":
        return "agenda"
    kinds = {b["kind"] for b in d.blocks}
    if d.visual in kinds:
        return d.visual
    for k in ("chart", "table", "number", "diagram", "image", "bullets"):
        if k in kinds:
            return k
    return d.visual if d.visual in ("quote", "cards", "text") else "text"


def slide_from_draft(ctx: Context, d: Draft, *, slide_id: str, order: int) -> JsonDict:
    """Слайд плана из измеренного черновика: факты собираются из блоков, чисел и пунктов."""
    fact_refs: list[str] = []
    for b in d.blocks:
        fact_refs.extend(b.get("fact_refs") or [])
        if b.get("number"):
            fact_refs.append(b["number"]["fact_id"])
        for it in b.get("items") or []:
            fact_refs.extend(it.get("fact_refs") or [])
    title = cap.substitute_facts(d.title, ctx.facts)
    # Лестница ёмкости могла сократить блок заголовка (а замена композиции — перезаписать
    # отметку действия): заголовок слайда — то, что стоит в его слоте заголовка.
    title_slot = d.pattern.title.slot_id if d.pattern.title is not None else None
    shortened = next(
        (
            b
            for b in d.blocks
            if b.get("kind") == "title" and b.get("slot_id") == title_slot and b.get("text")
        ),
        None,
    )
    if shortened is not None:
        title = cap.substitute_facts(str(shortened["text"]), ctx.facts)
    slide: JsonDict = {
        "slide_id": slide_id,
        "order": order,
        "role": d.pattern.role,
        "pattern_id": d.pattern.pattern_id,
        "title": title,
        "key_message": cap.substitute_facts(d.message or d.title, ctx.facts)[:300],
        "blocks": d.blocks,
        "thesis_refs": list(dict.fromkeys(d.theses)),
    }
    if d.notes:
        slide["notes"] = d.notes
    if d.source_refs:
        slide["source_refs"] = d.source_refs
    if fact_refs:
        slide["fact_refs"] = list(dict.fromkeys(fact_refs))
    return slide


def slide_chars(ctx: Context, blocks: list[JsonDict]) -> int:
    """Объём текста слайда для сравнения вариантов."""
    total = 0
    for b in blocks:
        if b.get("diagram"):
            total += sum(
                len(cap.substitute_facts(str(it.get(key) or ""), ctx.facts))
                for it in b["diagram"].get("items", [])
                for key in ("text", "sub")
            )
        text = _block_text(ctx, b)
        if isinstance(text, list):
            total += sum(len(t) for t in text)
        elif text:
            total += len(text)
    return total


def build_document(
    ctx: Context,
    deck: list[Draft],
    *,
    plan_id: str,
    generation_meta: JsonDict,
    rationale: str,
) -> JsonDict:
    story = ctx.story
    slides: list[JsonDict] = []
    chars_total = 0
    for i, d in enumerate(deck, start=1):
        slides.append(slide_from_draft(ctx, d, slide_id=f"s{i}", order=i))
        chars_total += slide_chars(ctx, d.blocks)
    required = [t.id for t in ctx.theses if t.required]
    covered: list[JsonDict] = []
    missing: list[str] = []
    for tid in required:
        sids = [s["slide_id"] for s in slides if tid in s["thesis_refs"]]
        if sids:
            entry: JsonDict = {"thesis_id": tid, "slide_ids": sids}
            reds = [r["reduction"] for r in ctx.reductions.get(tid, [])]
            if ctx.variant_id == "compact" and reds:
                entry["reduction"] = reds[0]
            covered.append(entry)
        else:
            missing.append(tid)
    sections = [t for t in ctx.theses if t.kind == "section"]
    doc: JsonDict = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "plan_id": plan_id,
        "template_id": ctx.profile["template_id"],
        "package_id": ctx.package["package_id"],
        "story_id": story["story_id"],
        "language": story.get("language", "ru"),
        "variant": {
            "variant_id": ctx.variant_id,
            "axis": "density",
            "value": ctx.variant_id,
            "rationale": rationale or VARIANT_RULES.get(ctx.variant_id, "")[:200],
        },
        "story": {
            "purpose": str(story.get("purpose", "other")),
            "key_takeaway": str(story.get("key_takeaway", "")),
            "outline": [cap.substitute_facts(s.statement, ctx.facts)[:80] for s in sections],
        },
        "slide_count": ctx.spec.as_dict(),
        "slides": slides,
        "coverage": {"required_thesis_ids": required, "covered": covered, "missing": missing},
        "comparison": {
            "pattern_sequence": [s["pattern_id"] for s in slides],
            "visual_kinds": [_visual_kind(d) for d in deck],
            "text_chars_total": chars_total,
        },
        "generation_meta": generation_meta,
        "warnings": [
            *[
                {"code": w["code"], "message": w["message"]}
                for w in ctx.story.get("warnings", [])
                if w.get("code") == CONCEPT_WARNING
            ],
            *[
                {"code": f["code"], "message": f["message"]}
                for f in ctx.fixes
                if f["code"] not in ("pattern_remapped", "dataset_unknown", "asset_unknown")
            ],
        ],
    }
    return doc


# ---------- ключ кэша ----------


def profile_digest(profile: JsonDict) -> str:
    """Структура и ёмкость паттернов без превью и путей: смена анализа меняет ключ."""
    material = {
        "template_hash": profile.get("template_hash"),
        "analyzer": profile.get("analyzer"),
        "slide_size": profile.get("slide_size"),
        "design_tokens": profile.get("design_tokens"),
        "patterns": [
            {
                "id": p.get("pattern_id"),
                "role": p.get("role"),
                "name": p.get("name"),
                "source": p.get("source"),
                "confidence": p.get("confidence"),
                "constraints": p.get("constraints"),
                "group_id": p.get("group_id"),
                "tone": (p.get("tone") or {}).get("background"),
                "style_key": p.get("style_key"),
                "slots": [
                    {
                        k: s.get(k)
                        for k in (
                            "slot_id",
                            "kind",
                            "bbox",
                            "font",
                            "required",
                            "repeat_group",
                            "capacity",
                            "paragraph_params",
                            "sample_text",
                        )
                    }
                    for s in p.get("slots", [])
                ],
            }
            for p in profile.get("patterns", [])
        ],
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _without_avoided(patterns: list[PatternInfo], avoid: list[str]) -> list[PatternInfo]:
    """Без композиций, на которых проверка вёрстки по картинке нашла дефекты. Обложка,
    разделитель и финал без единого образца не остаются: для их роли запрет снимается."""
    banned = {str(a) for a in avoid}
    if not banned:
        return patterns
    kept = [p for p in patterns if p.pattern_id not in banned]
    for role in ("title", "section_divider", "thanks"):
        if not any(p.role == role for p in kept):
            kept += [p for p in patterns if p.pattern_id in banned and p.role == role]
    return kept


def plan_key(
    story: JsonDict,
    profile: JsonDict,
    variant_id: str,
    settings: JsonDict | None,
    *,
    slide_count: int | None = None,
    skill: Any = None,
    model: JsonDict | None = None,
    app: Settings | None = None,
    nonce: str | None = None,
) -> str:
    app = app or get_settings()
    settings = settings or {}
    spec = slide_spec(settings, story, variant_id, slide_count)
    material: JsonDict = {
        "plan_version": PLAN_VERSION,
        "schema": PLAN_SCHEMA_VERSION,
        "story": story.get("content_hash"),
        "story_theses": hashlib.sha256(
            json.dumps(
                [
                    {
                        k: t.get(k)
                        for k in (
                            "thesis_id",
                            "kind",
                            "statement",
                            "explanation",
                            "required",
                            "fact_refs",
                            "dataset_refs",
                            "asset_refs",
                            "suggested_visual",
                        )
                    }
                    for t in story.get("theses", [])
                ]
                + [story.get("allowed_reductions") or []],
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest(),
        "profile": profile_digest(profile),
        "fonts": text_metrics.fonts_manifest(),
        "variant": variant_id,
        "design_mode": settings.get("design_mode") or "mixed",
        "slide_count": spec.as_dict(),
        "language": settings.get("language"),
        "seed": settings.get("seed"),
        "avoid_patterns": sorted(settings.get("avoid_patterns") or []),
        "plan_settings": app.plan.model_dump(mode="json", exclude={"cache_dir"}),
        "skill": list(skill.ref) if skill is not None else None,
        "prompts": sorted(p.ref for p in skill.prompts.values()) if skill is not None else None,
        "params": dict(skill.manifest.params or {}) if skill is not None else None,
        "model": model,
        "nonce": nonce,
    }
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return f"sha256:{digest}"


class PlanCache:
    """Готовые планы по ключу в каталоге: идентификаторы задания подставляются при чтении."""

    def __init__(self, root: Any) -> None:
        from presentation_designer.llm.cache import FileCache

        self.store = FileCache(root)

    def get(self, key: str) -> JsonDict | None:
        entry = self.store.get(key[7:] if key.startswith("sha256:") else key)
        return copy.deepcopy(entry.get("plan")) if entry else None

    def put(self, key: str, plan: JsonDict, report: JsonDict) -> None:
        self.store.put(
            key[7:] if key.startswith("sha256:") else key,
            {"key": key, "plan": plan, "report": report, "stored_at": now_iso()},
        )


def rebind_plan(
    plan: JsonDict, *, plan_id: str, template_id: str, package_id: str, story_id: str
) -> JsonDict:
    out = copy.deepcopy(plan)
    out["plan_id"] = plan_id
    out["template_id"] = template_id
    out["package_id"] = package_id
    out["story_id"] = story_id
    out.setdefault("generation_meta", {})["cache_hit"] = True
    return out


# ---------- сравнение вариантов ----------


def compare_plans(plans: dict[str, JsonDict]) -> JsonDict:
    """Различимость по последовательности паттернов и способам визуализации; пары без
    различий перечисляются явно."""
    ids = sorted(plans)
    pairs: list[JsonDict] = []
    indistinct: list[JsonDict] = []
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            ca = plans[a].get("comparison") or {}
            cb = plans[b].get("comparison") or {}
            same_patterns = ca.get("pattern_sequence") == cb.get("pattern_sequence")
            same_visuals = ca.get("visual_kinds") == cb.get("visual_kinds")
            same_count = len(plans[a].get("slides", [])) == len(plans[b].get("slides", []))
            same_content = _content_sequence(plans[a]) == _content_sequence(plans[b])
            chars_a = int(ca.get("text_chars_total") or 0)
            chars_b = int(cb.get("text_chars_total") or 0)
            entry = {
                "variants": [a, b],
                "same_pattern_sequence": same_patterns,
                "same_visual_kinds": same_visuals,
                # Одинаковые содержательные композиции при разных служебных слайдах: разные
                # стили разделителей не маскируют одинаковое содержание.
                "same_content_sequence": same_content,
                "same_slide_count": same_count,
                "text_chars": [chars_a, chars_b],
                "distinct": not ((same_patterns or same_content) and same_visuals),
            }
            pairs.append(entry)
            if not entry["distinct"]:
                indistinct.append(entry)
    return {"pairs": pairs, "indistinct": indistinct, "all_distinct": not indistinct}


def _content_sequence(plan: JsonDict) -> list[str]:
    """Композиции содержательных слайдов без титула, оглавления, разделителей и финала; у
    плана без ролей у слайдов — вся последовательность из comparison."""
    slides = plan.get("slides", [])
    if not all(isinstance(s, dict) and s.get("pattern_id") and s.get("role") for s in slides):
        return list((plan.get("comparison") or {}).get("pattern_sequence") or [])
    return [
        str(s["pattern_id"])
        for s in slides
        if s["role"] not in ("title", "agenda", "section_divider", "thanks", "qr")
    ]


def indistinct_warning(entry: JsonDict) -> JsonDict:
    a, b = entry["variants"]
    ca, cb = entry["text_chars"]
    return {
        "code": "variants_indistinct",
        "message": (
            f"Варианты {a} и {b} совпадают по последовательности композиций и визуализации; "
            f"различие только в объёме текста ({ca} и {cb} символов): шаблон не даёт трёх "
            "разных подач для этого содержания"
        ),
    }


# ---------- главный вызов ----------


def build_variant_plan(
    story: JsonDict,
    profile: JsonDict,
    package: JsonDict,
    variant_id: str,
    settings: JsonDict | None,
    *,
    slide_count: int | None = None,
    client: Any = None,
    skill: Any = None,
    app_settings: Settings | None = None,
    deadline_s: float | None = None,
    use_model: bool = True,
    cache: PlanCache | None = None,
    nonce: str | None = None,
    plan_id: str | None = None,
) -> PlanResult:
    """SlidePlan одного варианта. С моделью — скилл variant_planner по пакетам; без неё
    (use_model=False) — детерминированный черновик с отметкой в плане."""
    app = app_settings or get_settings()
    started = time.perf_counter()
    settings = settings or {}
    if variant_id not in VARIANTS:
        raise PlanError("plan_variant_unknown", f"Неизвестный вариант {variant_id!r}")
    model_ref: JsonDict | None = None
    if use_model and client is not None:
        from presentation_designer.generation.story import llm_model_ref

        model_ref = llm_model_ref(client, skill)
    key = plan_key(
        story,
        profile,
        variant_id,
        settings,
        slide_count=slide_count,
        skill=skill if use_model else None,
        model=model_ref,
        app=app,
        nonce=nonce,
    )
    plan_id = plan_id or f"plan_{key[7:19]}_{variant_id}"
    report: JsonDict = {"plan_key": key, "variant_id": variant_id, "cache_hit": False}
    if cache is not None and use_model:
        hit = cache.get(key)
        if hit is not None:
            plan = rebind_plan(
                hit,
                plan_id=plan_id,
                template_id=profile["template_id"],
                package_id=package["package_id"],
                story_id=story["story_id"],
            )
            report["cache_hit"] = True
            report["total_ms"] = int((time.perf_counter() - started) * 1000)
            return PlanResult(plan=plan, report=report)
    ctx = build_context(story, profile, package, variant_id, settings, app, slide_count)
    structure = deck_structure(ctx)
    for packet in structure.packets:
        packet.candidates = packet_candidates(ctx, packet)
    report["structure"] = {
        "title_pattern": structure.title_pattern.pattern_id if structure.title_pattern else None,
        "divider_pattern": structure.divider_pattern.pattern_id
        if structure.divider_pattern
        else None,
        "final_pattern": structure.final_pattern.pattern_id if structure.final_pattern else None,
        "dividers": structure.dividers,
        "content_theses": [t.id for t in structure.content],
        "dropped": [t.id for t in structure.dropped],
        "packets": [
            {"theses": [t.id for t in p.theses], "target": p.target, "lo": p.lo, "hi": p.hi}
            for p in structure.packets
        ],
        "content_budget": structure.content_budget,
        "slide_count": ctx.spec.as_dict(),
        "candidates": sorted({c.pattern_id for c in all_candidates(ctx, structure.packets)}),
        "style_policy": str(ctx.app.plan.style_policy),
        "service_styles": structure.service_styles(),
        "pools": {
            "title": [p.pattern_id for p in structure.title_pool],
            "divider": [p.pattern_id for p in structure.divider_pool],
            "final": [p.pattern_id for p in structure.final_pool],
            "agenda": [p.pattern_id for p in structure.agenda_pool],
        },
    }
    meta: JsonDict = {"skills": [], "prompts": [], "models": [], "created_at": now_iso()}
    rationale = ""
    packet_drafts: list[list[Draft]] = []
    if use_model:
        if client is None or skill is None:
            raise PlanError(
                "plan_llm_not_configured",
                "Провайдер моделей не настроен: план варианта не построен",
            )
        from presentation_designer.llm.types import Deadline, LlmError

        params = skill.manifest.params or {}
        budget = float(deadline_s if deadline_s is not None else params.get("time_budget_s", 100))
        deadline = Deadline.after(budget)
        requests = []
        for packet in structure.packets:
            req = skill.request(
                "plan.slides",
                packet_digest(ctx, structure, packet),
                schema=PLAN_MODEL_SCHEMA,
                stage="plan",
            )
            req.schema_name = "plan_slides"
            req.deadline = deadline
            req.variant_id = variant_id
            if settings.get("seed") is not None:
                req.seed = int(settings["seed"])
            if nonce:
                req.regenerate_nonce = nonce
            requests.append((packet, req))

        async def run_all() -> list[Any]:
            results: list[Any] = await asyncio.gather(
                *(fit_packet(ctx, packet, req, client) for packet, req in requests),
                return_exceptions=True,
            )
            return results

        outcomes = asyncio.run(run_all()) if requests else []
        llm_report: list[JsonDict] = []
        prompt_tokens = completion_tokens = 0
        for (packet, _req), outcome in zip(requests, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                if isinstance(outcome, LlmError):
                    raise PlanError(
                        outcome.code,
                        f"План варианта {variant_id}, пакет {packet.index + 1}: {outcome}",
                        retryable=outcome.retryable,
                        details={"packet": [t.id for t in packet.theses]},
                    ) from outcome
                raise outcome
            drafts, packet_rationale, responses = outcome
            packet_drafts.append(list(drafts))
            if packet_rationale and not rationale:
                rationale = packet_rationale
            for resp in responses:
                prompt_tokens += int(resp.usage.prompt_tokens)
                completion_tokens += int(resp.usage.completion_tokens)
            llm_report.append(
                {
                    "packet": packet.index,
                    "latency_ms": sum(r.latency_ms for r in responses),
                    "quota_wait_ms": sum(r.quota_wait_ms for r in responses),
                    "attempts": sum(r.attempts for r in responses),
                    "calls": len(responses),
                    "cache_hit": all(r.cache_hit for r in responses),
                    "prompt_tokens": sum(r.usage.prompt_tokens for r in responses),
                    "completion_tokens": sum(r.usage.completion_tokens for r in responses),
                    "slides": len(drafts),
                    "overflow": sum(len(d.overflow) for d in drafts),
                }
            )
        meta = {
            "skills": [{"name": skill.name, "version": skill.version}],
            "prompts": [{"name": p.id, "version": p.version} for p in skill.prompts.values()],
            "models": [model_ref] if model_ref else [],
            "temperature": float(params.get("temperature", 0.2)),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            # Попадание в кэш готовых планов (см. rebind_plan); попадания кэша ответов
            # модели — в отчёте llm.
            "cache_hit": False,
            "created_at": now_iso(),
        }
        if settings.get("seed") is not None:
            meta["seed"] = int(settings["seed"])
        report["llm"] = llm_report
    else:
        for packet in structure.packets:
            packet_drafts.append([fit_draft(ctx, d) for d in drafts_without_model(ctx, packet)])
        meta["skills"] = [{"name": "variant_planner", "version": "none"}]
    deck = assemble(ctx, structure, packet_drafts)
    unresolved = [
        {"slide": d.title[:60], "pattern": d.pattern.pattern_id, "overflow": d.overflow}
        for d in deck
        if d.overflow
    ]
    if unresolved and settings.get("design_mode") == "template_only":
        raise PlanError(
            "template_capacity_exceeded",
            "Текст не помещается в макеты шаблона. "
            "Увеличьте число слайдов, разделите содержание или сократите текст.",
            details={"slides": unresolved[:5]},
        )
    # Переполнение после лестницы ёмкости остаётся в плане структурированно и уходит в аудит —
    # и при диапазоне, и при точном числе слайдов: колода с подсвеченной строкой лучше отказа
    # от варианта (18.09: в шаблоне со строчными слотами на 52–112 символов не выдался ни один).
    for u in unresolved:
        ctx.fix(
            "capacity_overflow",
            f"«{u['slide']}» ({u['pattern']}): текст не помещается и после лестницы ёмкости — "
            "проверит аудит",
        )
    if not use_model:
        ctx.fix("plan_without_model", "план построен без модели по структуре смыслового плана")
    doc = build_document(ctx, deck, plan_id=plan_id, generation_meta=meta, rationale=rationale)
    validated = SlidePlan.model_validate(doc)
    violations = check_slide_plan(
        validated,
        TemplateProfile.model_validate(profile),
        ContentPackage.model_validate(package),
        StoryPlan.model_validate(story),
    )
    violations = [
        v
        for v in violations
        if v.code != "package_mismatch" or story.get("package_id") == package.get("package_id")
    ]
    if violations:
        raise PlanError(
            "plan_invalid",
            "План нарушает связи контракта: " + "; ".join(str(v) for v in violations[:6]),
            details={"violations": [str(v) for v in violations[:20]]},
        )
    report["fixes"] = list(ctx.fixes)
    report["counts"] = {
        "slides": len(doc["slides"]),
        "content": sum(1 for d in deck if d.kind == "content"),
        "dividers": sum(1 for d in deck if d.kind == "divider"),
        "required_theses": len(doc["coverage"]["required_thesis_ids"]),
        "covered": len(doc["coverage"]["covered"]),
        "font_steps": sum(
            1
            for s in doc["slides"]
            for b in s["blocks"]
            if b.get("fit", {}).get("action") == "font_step"
        ),
        "shortened": sum(
            1
            for s in doc["slides"]
            for b in s["blocks"]
            if b.get("fit", {}).get("action") == "shortened"
        ),
        "pattern_swaps": sum(1 for d in deck for a in d.actions if a.startswith("pattern_swap")),
        "overflow": len(unresolved),
    }
    report["comparison"] = doc["comparison"]
    report["total_ms"] = int((time.perf_counter() - started) * 1000)
    if cache is not None and use_model:
        try:
            cache.put(key, doc, {k: v for k, v in report.items() if k != "fixes"})
        except OSError:
            log.warning("кэш планов недоступен для записи", exc_info=True)
    return PlanResult(plan=doc, report=report)


__all__ = [
    "PLAN_MODEL_SCHEMA",
    "PLAN_VERSION",
    "VARIANTS",
    "PlanCache",
    "PlanError",
    "PlanResult",
    "build_context",
    "build_variant_plan",
    "compare_plans",
    "deck_structure",
    "drafts_from_answer",
    "drafts_without_model",
    "indistinct_warning",
    "packet_digest",
    "plan_key",
    "profile_digest",
    "rebind_plan",
    "slide_spec",
]
