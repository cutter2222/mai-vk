"""Сборка PPTX варианта по SlidePlan: копия исходного пакета, клоны образцов, заполнение слотов.

Порядок работы:

1. Исходный шаблон открывается как рабочая основа (`Presentation`), профиль сверяется с файлом
   по sha256 — ссылки слотов на объекты образцов (`element_ref`) действительны только для того
   пакета, по которому построен профиль.
2. Для каждого слайда плана клонируется образец паттерна с графом связей (`ooxml.clone_slide`),
   слоты заполняются по `element_ref`: текст с сохранением оформления и явным кеглем из
   `fit.size_pt`, факты значениями как в источнике, число из `number.text`, списки абзацами,
   нативные таблицы и диаграммы (в слоте chart, вместо картинки диаграммы в слоте image
   паттерна роли chart, с независимой книгой данных), схемы из фигур, картинки пакета,
   иконки шаблона.
3. Незаполненные карточки убираются по `removable_object_ids`, незаполненные необязательные
   слоты — без потери статики; крошечные слоты сохраняют текст образца (`content_source =
   sample`).
   После чистки применяются ручные правки объектов из `slides[].overrides` (этап 22,
   `layout/overrides.py`): текст, стиль, положение, картинки, фон — по `object_id` из
   ComposedDeck прошлой ревизии, с охраной адреса.
4. Образцы удаляются, номера слайдов пересчитываются, пакет сохраняется (python-pptx пишет
   только части, достижимые от корня, — недостижимые ресурсы образцов в файл не попадают),
   проверяется `check_package`, по сохранённому файлу строится ComposedDeck.

Переполнение слотов здесь не измеряется заново: план уже несёт результат измерения по метрикам
шрифта (`fit.action`), блоки с `overflow` попадают в предупреждения ComposedDeck для аудита.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import pathlib
import re
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pptx import Presentation
from pptx.util import Pt

from presentation_designer.generation import reflow
from presentation_designer.generation.matching import SlotInfo, pattern_info
from presentation_designer.generation.original import original_profile, unchanged_slide
from presentation_designer.layout import (
    chart_images,
    charts,
    composite_charts,
    diagrams,
    icons,
    images,
    tables,
)
from presentation_designer.layout import text as tx
from presentation_designer.layout.composed import SlideRecord, SlotFill, build_composed_deck
from presentation_designer.layout.integrity import IntegrityReport, check_deck
from presentation_designer.layout.media import export_media
from presentation_designer.layout.ooxml import (
    NS_A,
    NS_P,
    NS_R,
    clone_slide,
    keep_only_slides,
    retarget_slide_links,
)
from presentation_designer.layout.overrides import apply_overrides
from presentation_designer.layout.package import (
    drop_template_logos,
    ensure_placeholder,
    layout_by_id,
    prune_unused_layouts,
    update_slide_numbers,
)
from presentation_designer.layout.shapes import (
    connection_ids,
    connector_endpoints,
    element_box,
    emu_box,
    ensure_xfrm,
    in_group,
    next_shape_id,
    part_by_name,
    remove_shape,
    set_element_box,
    set_element_box_absolute,
    shape_element,
    shape_map,
)
from presentation_designer.library import iconset
from presentation_designer.library.build import build_slide as build_builtin_slide
from presentation_designer.library.build import slide_backdrop
from presentation_designer.library.dress import dress_slide, look_for
from presentation_designer.library.skin import Skin, template_skin
from presentation_designer.library.spec import find_composition
from presentation_designer.library.tokens import DesignCode
from presentation_designer.parsing.content.stock import load_assets as load_stock_assets
from presentation_designer.parsing.raster_charts.model import ChartReading
from presentation_designer.parsing.template.geometry import walk_shapes
from presentation_designer.shared import text_metrics

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]
COMPOSER_NAME = "layout_composer"
COMPOSER_VERSION = "0.3.5"
TEXT_KINDS = (
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
TINY_SLOT_CHARS = 12
# Боковой декор макета или образца (картинка справа/слева от текста): слот сужается, если
# перекрытие заметно, декор занимает высоту слота, а свободная часть не уже трети слайда.
# Кегль в суженном слоте может опускаться ниже лестницы планировщика (до этой доли исходного):
# рамка стала уже, а текст, налезающий на подпись ниже, хуже более мелкого заголовка.
DECOR_MIN_AREA = 0.02
DECOR_MIN_FONT_RATIO = 0.6
DECOR_OVERLAP_RATIO = 0.15
DECOR_MIN_FREE_WIDTH = 0.3
DECOR_GAP = 0.012
# Конец соединителя «упирается» в объект, если лежит внутри его рамки с этим запасом.
CONNECTOR_TOUCH = 0.02
TINY_DECOR_AREA = 0.002
# Запас при раскладке карточек: объект относится к карточке, если его середина в её колонке
# с половиной этого запаса, и не выходит за колонку больше чем на весь запас.
REFLOW_SLACK = 0.02


class ComposeError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class ComposeResult:
    pptx_path: pathlib.Path
    deck: JsonDict
    slide_titles: list[str]
    warnings: list[JsonDict]
    report: JsonDict
    integrity: IntegrityReport
    # Диаграммы-картинки, заменённые нативными (или оставленные с причиной), — вариант original.
    chart_swaps: list[chart_images.ChartSwap] = field(default_factory=list)


@dataclass
class _Context:
    plan: JsonDict
    profile: JsonDict
    package: JsonDict
    package_dir: pathlib.Path | None
    prs: Any
    slide_w: int
    slide_h: int
    facts: dict[str, JsonDict]
    datasets: dict[str, JsonDict]
    package_assets: dict[str, JsonDict]
    profile_assets: dict[str, JsonDict]
    # Файлы проекта для ручных правок (источник `file`): file_id → путь в хранилище.
    extra_assets: dict[str, pathlib.Path]
    markers: set[str]
    language: str
    chart_style: charts.ChartStyle
    table_style: tables.TableStyle
    diagram_style: diagrams.DiagramStyle
    warnings: list[JsonDict] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    # Вариант original: загруженная презентация как готовый результат. Каждый слайд плана —
    # собственный образец с его же текстами; объекты образца не удаляются, текст, равный
    # образцу, не перезаписывается (сохраняется оформление абзацев), отличающийся — правка.
    preserve: bool = False
    # Области нативных диаграмм, построенных на месте картинок текущего слайда (доли слайда).
    chart_areas: list[tuple[float, float, float, float]] = field(default_factory=list)
    next_id: int = 1  # свободный id объекта на текущем слайде: новые объекты не занимают
    # идентификаторы удалённых, иначе removed_object_ids и новые объекты путаются
    # Боковой декор текущего слайда (доли слайда), от которого отодвигается текст.
    decor_boxes: list[tuple[float, float, float, float]] = field(default_factory=list)
    # Лестница кеглей при сужении слота: как у планировщика (config plan.*).
    fit_min_ratio: float = 0.75
    fit_min_body_pt: float = 12.0
    fit_min_title_pt: float = 20.0
    # Дизайн-код шаблона: нужен только собственным композициям, поэтому считается лениво.
    design_code: DesignCode | None = None
    # Слайды образцов до сборки и снятая с них кожа (фон и декор) по макету — тоже лениво.
    samples: list[Any] = field(default_factory=list)
    skins: dict[str, Skin] = field(default_factory=dict)
    # Текст, не вошедший на текущий слайд даже на минимальном кегле: уходит в заметки.
    spill: list[str] = field(default_factory=list)
    # Оставшиеся карточки однорядной сетки расходятся на всю ширину ряда (`_reflow_cards`);
    # режим «По шаблону» обещает не двигать блоки и выключает это.
    reflow_cards: bool = True

    def fresh_ids(self, element: Any) -> dict[str, str]:
        """Перенумеровывает cNvPr новых объектов (python-pptx заполняет пропуски id)."""
        from presentation_designer.layout.shapes import CNVPR

        mapping: dict[str, str] = {}
        for cnvpr in element.iter(CNVPR):
            old = str(cnvpr.get("id"))
            cnvpr.set("id", str(self.next_id))
            mapping[old] = str(self.next_id)
            self.next_id += 1
        return mapping

    def warn(self, code: str, message: str, slide_index: int | None = None) -> None:
        entry: JsonDict = {"code": code, "message": message[:300]}
        if slide_index is not None:
            entry["slide_index"] = slide_index
        self.warnings.append(entry)

    def count(self, key: str, n: int = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + n


def sha256_of(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_matches(profile: JsonDict, path: pathlib.Path) -> bool:
    declared = str(profile.get("template_hash") or "")
    declared = declared.split(":", 1)[-1].lower()
    if not declared:
        return True
    actual = sha256_of(path)
    return actual.startswith(declared) or declared.startswith(actual)


def _asset_bytes(ctx: _Context, asset: JsonDict) -> bytes | None:
    if ctx.package_dir is None:
        return None
    rel = str(asset.get("path") or "")
    if not rel:
        return None
    path = ctx.package_dir / rel
    if not path.is_file():
        return None
    return path.read_bytes()


# ---------- заполнение одного слайда ----------


def _fit_size(block: JsonDict) -> float | None:
    fit = block.get("fit") or {}
    size = fit.get("size_pt")
    return float(size) if size else None


def _number_text(ctx: _Context, block: JsonDict) -> str:
    text = block.get("text")
    if text:
        return str(text)
    number = block.get("number") or {}
    fact = ctx.facts.get(str(number.get("fact_id", "")))
    if fact is None:
        return ""
    value = tx.fact_text(fact)
    fmt = str(number.get("format") or "{value}")
    return fmt.replace("{value}", value) if "{value}" in fmt else value


def _slot_box(ctx: _Context, slot: SlotInfo, element: Any) -> tuple[int, int, int, int]:
    """Положение слота в EMU: bbox профиля (координаты слайда); для объекта вне групп
    совпадает с его собственным xfrm."""
    x, y, w, h = slot.bbox
    box = emu_box({"x": x, "y": y, "width": w, "height": h}, ctx.slide_w, ctx.slide_h)
    if box[2] <= 0 or box[3] <= 0:
        own = element_box(element) if element is not None else None
        if own is not None:
            return own
    return box


def _fill_slide(
    ctx: _Context,
    slide: Any,
    plan_slide: JsonDict,
    pattern_raw: JsonDict,
    pinfo: Any,
    layout: Any | None,
    slide_index: int,
) -> SlideRecord:
    record = SlideRecord(
        slide_id=str(plan_slide["slide_id"]),
        order=int(plan_slide.get("order", slide_index + 1)),
        pattern_id=str(pattern_raw["pattern_id"]),
        source_slide_index=int(pattern_raw["source"].get("slide_index") or 0),
        source_slide_part=str(pattern_raw["source"].get("pptx_slide_part") or ""),
        layout_id=str(pattern_raw["source"].get("layout_id") or ""),
        title=str(plan_slide.get("title") or ""),
        notes=str(plan_slide.get("notes") or ""),
        static_object_ids=[str(i) for i in pattern_raw.get("static_object_ids") or []],
    )
    from_layout = pattern_raw["source"].get("kind") == "layout"
    slots: dict[str, SlotInfo] = pinfo.slots
    filled: dict[str, SlotFill] = {}
    ctx.next_id = next_shape_id(slide)
    ctx.chart_areas = []
    ctx.spill = []
    ctx.decor_boxes = _side_decor(ctx, slide, pattern_raw)

    def resolve(slot: SlotInfo) -> Any | None:
        raw = next(s for s in pattern_raw["slots"] if s["slot_id"] == slot.slot_id)
        ref = str(raw.get("element_ref") or "")
        if from_layout and layout is not None:
            return ensure_placeholder(slide, layout, ref)
        return shape_element(slide, ref) if ref else None

    for block in plan_slide.get("blocks") or []:
        slot = slots.get(str(block.get("slot_id")))
        if slot is None:
            ctx.warn(
                "slot_unknown",
                f"{record.slide_id}: слот {block.get('slot_id')} не найден в паттерне",
                slide_index,
            )
            continue
        element = resolve(slot)
        if element is None:
            ctx.warn(
                "element_missing",
                f"{record.slide_id}: объект слота {slot.slot_id} не найден в образце",
                slide_index,
            )
            continue
        if ctx.preserve and _same_as_sample(slot, block):
            kept = SlotFill(
                slot_id=slot.slot_id,
                slot_kind=slot.kind,
                block_kind=str(block.get("kind")),
                element_id=_element_id(element),
                source_object_id=_element_id(element),
                content_source="sample",
                text=slot.sample_text or None,
            )
            filled[slot.slot_id] = kept
            record.fills.append(kept)
            continue
        try:
            fill = _apply_block(ctx, slide, slot, element, block, slide_index, pinfo)
        except Exception as e:
            log.exception("слот %s на %s не заполнен", slot.slot_id, record.slide_id)
            ctx.warn(
                "slot_failed",
                f"{record.slide_id}.{slot.slot_id}: {e.__class__.__name__}: {e}",
                slide_index,
            )
            continue
        if fill is not None:
            filled[slot.slot_id] = fill
            record.fills.append(fill)
            raw_slot: JsonDict = next(
                (s for s in pattern_raw["slots"] if s["slot_id"] == slot.slot_id), {}
            )
            if raw_slot.get("backing") and fill.text and not from_layout:
                _fit_backing(ctx, slide, slot, raw_slot["backing"], fill)
    if ctx.preserve:
        _keep_rest(slide, pattern_raw, pinfo, filled, record, from_layout)
        record.removed_object_ids = []
    else:
        record.removed_object_ids = _cleanup(
            ctx, slide, pattern_raw, pinfo, filled, record, slide_index
        )
    apply_overrides(ctx, slide, record, plan_slide, pinfo, slide_index)
    if ctx.spill:
        from presentation_designer.generation.capacity import substitute_facts

        more = "\n".join(substitute_facts(text, ctx.facts) for text in ctx.spill)
        label = "Подробнее" if ctx.language.lower().startswith("ru") else "More"
        record.notes = f"{record.notes}\n\n{label}: {more}".strip()
    sources = _web_sources_note(ctx, plan_slide)
    if sources and sources not in record.notes:
        record.notes = f"{record.notes}\n\n{sources}".strip()
    if record.notes:
        try:
            slide.notes_slide.notes_text_frame.text = record.notes
        except Exception:
            ctx.warn("notes_failed", f"{record.slide_id}: заметки не записаны", slide_index)
    return record


def _fit_backing(
    ctx: _Context, slide: Any, slot: SlotInfo, backing: JsonDict, fill: SlotFill
) -> None:
    """Плашка под строкой по ширине текста: не уже образца и не дальше препятствия в полосе
    (логотипы). Длинный заголовок-вывод иначе вылезает с плашки на фон."""
    element = shape_element(slide, str(backing.get("element_ref") or ""))
    if element is None:
        return
    xfrm = element.find(f".//{{{NS_A}}}xfrm")
    ext = xfrm.find(f"{{{NS_A}}}ext") if xfrm is not None else None
    if ext is None:
        return
    font = text_metrics.resolve_font(slot.family, bold=slot.bold, italic=slot.italic)
    size = float(fill.size_pt or slot.size_pt or 18.0)
    lines = [ln for ln in (fill.text or "").split("\n") if ln.strip()] or [""]
    text_pt = max(text_metrics.text_width_pt(ln, font, size) for ln in lines)
    pad = float(backing.get("pad") or 0.01)
    wanted = text_pt * text_metrics.EMU_PER_PT / ctx.slide_w + 2 * pad
    low = float(backing.get("min_width") or 0.0)
    high = float(backing.get("max_width") or low)
    width = min(max(wanted, low), max(high, low))
    ext.set("cx", str(int(width * ctx.slide_w)))
    ctx.count("backing_resized")


def _apply_block(
    ctx: _Context,
    slide: Any,
    slot: SlotInfo,
    element: Any,
    block: JsonDict,
    slide_index: int,
    pinfo: Any,
) -> SlotFill | None:
    kind = str(block.get("kind"))
    element_id = _element_id(element)
    fill = SlotFill(
        slot_id=slot.slot_id,
        slot_kind=slot.kind,
        block_kind=kind,
        element_id=element_id,
        source_object_id=element_id,
        fit=_fit_plain(block),
    )
    size = _fit_size(block)
    if kind == "bullets" or kind in TEXT_KINDS:
        slot, size = _avoid_side_decor(ctx, slot, element, block, size, fill, slide_index)
        if size is not None:
            # Расчёт capacity переносит слова по ширине слота. Запрет переноса
            # из образца иначе превращает рассчитанные две строки в одну за краем.
            tx.set_wrap(element, True)
    if kind in SPILL_KINDS and (block.get("fit") or {}).get("action") == "overflow":
        spilled = _spill_overflow(ctx, slot, block, size)
        if spilled is None:
            return None
        block, size = spilled
        fill.fit = _fit_plain(block)
        tx.set_wrap(element, True)
    if kind == "bullets":
        items = [str(it.get("text", "")) for it in block.get("items") or []]
        result = tx.fill_bullets(element, items, size_pt=size, facts=ctx.facts)
        _record_text(ctx, fill, result, block, slide_index)
        ctx.count("text_objects")
        return fill
    if kind in TEXT_KINDS:
        text = _number_text(ctx, block) if kind == "number" else str(block.get("text") or "")
        if text.strip() == "—" or (not text.strip() and not slot.sample_text.strip()):
            # Планировщик оставил слот прочерком: содержимого нет, судьбу объекта решают
            # правила карточек и уборка пустых слотов (прочерк на слайд не выводится).
            return None
        if kind == "number":
            text, size = _one_line_number(ctx, slot, element, text, size)
        if (slot.clear_width or slot.clear_height) and not in_group(element):
            # Рамка — по месту, которым её мерил план: до препятствия в полосе (логотипы) и
            # до соседнего слота снизу. Рамка образца шире и выше, текст уходил под логотипы,
            # а рамка заголовка лежала на подзаголовке.
            own = element_box(element)
            if own is not None:
                width = int(slot.clear_width * ctx.slide_w) if slot.clear_width else own[2]
                height = int(slot.clear_height * ctx.slide_h) if slot.clear_height else own[3]
                if 0 < width < own[2] or 0 < height < own[3]:
                    set_element_box(
                        element, (own[0], own[1], min(width, own[2]), min(height, own[3]))
                    )
        result = tx.fill_text(element, text, size_pt=size, facts=ctx.facts)
        if kind == "number" and " " in text:
            tx.set_wrap(element, False)
        if (
            kind == "number"
            and re.fullmatch(r"\d{1,2}", text.strip())
            and _is_tiny(slot)
            and int((block.get("fit") or {}).get("lines") or 1) <= 1
        ):
            tx.set_wrap(element, False)
        _record_text(ctx, fill, result, block, slide_index)
        fill.content_source = (
            "sample" if slot.sample_text and text.strip() == slot.sample_text.strip() else "plan"
        )
        ctx.count("text_objects")
        return fill
    if kind == "table":
        return _apply_table(ctx, slide, slot, element, block, fill, slide_index)
    if kind == "chart":
        return _apply_chart(ctx, slide, slot, element, block, fill, slide_index, pinfo)
    if kind == "image":
        return _apply_image(ctx, slide, slot, element, block, fill, slide_index)
    if kind == "icon":
        return _apply_icon(ctx, slide, slot, element, block, fill, slide_index)
    if kind == "diagram":
        return _apply_diagram(ctx, slide, slot, element, block, fill, slide_index)
    ctx.warn(
        "block_unsupported", f"блок {kind} в слоте {slot.slot_id} не поддерживается", slide_index
    )
    return None


_NUMBER_GAP = re.compile(r"(?<=[\d%])\s+(?=\S)|(?<=\S)\s+(?=[%₽$€])")


def _one_line_number(
    ctx: _Context, slot: SlotInfo, element: Any, text: str, size: float | None
) -> tuple[str, float | None]:
    """Показатель — одна строка: «60 %» с переносом знака на вторую строку ложится поверх
    цифр (у крупного числа плотный интервал). Пробелы внутри значения неразрывные, а если
    строка шире рамки, кегль уменьшается под ширину, не ниже половины исходного."""
    if not text.strip() or len(text) > 24:
        return text, size
    joined = _NUMBER_GAP.sub(" ", text.strip())
    base = float(size or slot.size_pt or 0.0)
    if base <= 0:
        return joined, size
    font = text_metrics.resolve_font(slot.family, bold=slot.bold, italic=slot.italic)
    width_pt = text_metrics.text_width_pt(joined.replace(" ", " "), font, base)
    _, _, box_w, _ = _slot_box(ctx, slot, element)
    insets = slot.insets or {}
    inner_w = (
        box_w - (float(insets.get("left", 0.0)) + float(insets.get("right", 0.0))) * ctx.slide_w
    )
    avail_pt = max(inner_w, 0) / text_metrics.EMU_PER_PT * 0.95
    if width_pt <= avail_pt or avail_pt <= 0:
        return joined, size
    fitted = max(base * avail_pt / width_pt, base * 0.5)
    ctx.count("number_shrunk")
    return joined, round(fitted, 1)


def _renumbered(ctx: _Context, element: Any) -> str:
    """Новый объект получает свежий id; возвращает id самого элемента."""
    own = _element_id(element)
    return ctx.fresh_ids(element)[own]


SPILL_KINDS = ("title", "subtitle", "body", "caption", "label", "bullets")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
# Подпись, от которой осталось меньше этой доли слов, уходит в заметки целиком: «Свободные
# сахара…» в кружке ничего не сообщает.
SPILL_MIN_WORDS = 0.5
_CLAUSE_END = re.compile(r"(?<=[,;:])\s+|\s+(?=[—–]\s)")
SPILL_MIN_CLAUSE_WORDS = 4


def _spill_overflow(
    ctx: _Context, slot: SlotInfo, block: JsonDict, size: float | None
) -> tuple[JsonDict, float | None] | None:
    """Последняя страховка от текста за рамкой.

    План отметил блок как не помещающийся (`overflow`): лестница кеглей и сокращение не
    помогли, а короткую версию модели отклонила проверка сохранения смысла. На слайде такой
    текст лезет на заголовок и соседние блоки. Здесь остаются целые предложения (пункты
    списка), которые по замеру входят в рамку на допустимом кегле, а остаток уходит в
    заметки докладчика: содержание не теряется, вёрстка не ломается. None — в рамку не
    входит ничего осмысленного, блок целиком в заметках, слот остаётся пустым.
    """
    from presentation_designer.generation.capacity import (
        font_steps,
        measure,
        min_pt_for,
        substitute_facts,
    )

    kind = str(block.get("kind"))
    scale = [float(t["size_pt"]) for t in ctx.profile["design_tokens"]["typography"]["scale"]]
    min_pt = min_pt_for(
        slot.kind,
        body_pt=ctx.fit_min_body_pt,
        title_pt=ctx.fit_min_title_pt,
        slide_w_emu=ctx.slide_w,
    )
    base = float(size or slot.size_pt or 18.0)
    # Заголовок, который не встал в план: образец бывает нарисован под одно крупное слово
    # («STEPS» в 62 pt), а тезис — 40 знаков. Половина кегля такой заголовок не спасает —
    # он расползается на пять строк поверх соседних блоков. Заголовку можно уменьшаться до
    # нижнего предела по роли: мелковатый заголовок лучше налезающего.
    ratio = min(0.5, min_pt / base) if kind == "title" else ctx.fit_min_ratio
    sizes = [base, *font_steps(slot, scale, min_ratio=ratio, min_pt=min_pt, fill_below=True)]

    def fitting(value: str | list[str]) -> tuple[float, Any] | None:
        text = (
            [substitute_facts(v, ctx.facts) for v in value]
            if isinstance(value, list)
            else substitute_facts(value, ctx.facts)
        )
        for candidate in sizes:
            measured = measure(text, slot, ctx.slide_w, ctx.slide_h, size_pt=candidate)
            if measured.fits:
                return candidate, measured
        return None

    fit = dict(block.get("fit") or {})

    def done(new_block: JsonDict, found: tuple[float, Any], rest: str) -> tuple[JsonDict, float]:
        chosen, measured = found
        if rest.strip():
            ctx.spill.append(rest.strip())
        ctx.count("text_spilled_to_notes" if rest.strip() else "text_fit_smaller")
        new_block["fit"] = {
            **fit,
            "size_pt": chosen,
            "lines": measured.lines,
            "max_lines": measured.max_lines,
            "action": "shortened" if rest.strip() else "font_step",
            "note": "не поместилось — остаток в заметках" if rest.strip() else "кегль меньше",
        }
        return new_block, chosen

    whole = (
        [str(it.get("text", "")) for it in block.get("items") or []]
        if kind == "bullets"
        else str(block.get("text") or "")
    )
    found = fitting(whole)
    if found is not None:
        # План мерил лестницу крупными ступенями шкалы; мелкие шаги до нижней границы
        # вмещают текст целиком — кегль меньше, но ничего не уходит в заметки.
        return done(dict(block), found, "")
    if kind == "bullets":
        items = list(block.get("items") or [])
        rest_items: list[str] = []
        while items:
            found = fitting([str(it.get("text", "")) for it in items])
            if found is not None:
                return done({**block, "items": items}, found, "\n".join(rest_items))
            rest_items.insert(0, str(items.pop().get("text", "")))
        ctx.spill.append("\n".join(rest_items))
        return None
    text = str(block.get("text") or "")
    sentences = [s for s in _SENTENCE_END.split(text.strip()) if s]
    for count in range(len(sentences) - 1, 0, -1):
        found = fitting(" ".join(sentences[:count]))
        if found is not None:
            return done(
                {**block, "text": " ".join(sentences[:count])}, found, " ".join(sentences[count:])
            )
    # Первое предложение целиком не входит: его начало до запятой, точки с запятой или тире,
    # если это законченная мысль хотя бы из четырёх слов — «Значительная часть сахара
    # «спрятана» в переработанных продуктах.» вместо обрыва на полуслове.
    first = sentences[0] if sentences else text
    clauses = [c for c in _CLAUSE_END.split(first) if c]
    for count in range(len(clauses) - 1, 0, -1):
        head = " ".join(clauses[:count]).rstrip(" ,;:—–-")
        if len(head.split()) < SPILL_MIN_CLAUSE_WORDS:
            break
        cut = head if head.endswith((".", "!", "?", "…")) else head + "."
        found = fitting(cut)
        if found is not None:
            return done({**block, "text": cut}, found, text)
    words = text.split() if kind != "title" else []
    for count in range(len(words) - 1, 0, -1):
        if count < max(2, len(words) * SPILL_MIN_WORDS):
            break
        cut = " ".join(words[:count]).rstrip(" ,;:—–-") + "…"
        found = fitting(cut)
        if found is not None:
            return done({**block, "text": cut}, found, text)
    if kind == "title":
        return block, size  # заголовок не снимается: без него слайд теряет смысл
    ctx.spill.append(text)
    ctx.count("text_spilled_to_notes")
    return None


def _fit_plain(block: JsonDict) -> JsonDict | None:
    fit = block.get("fit")
    if not fit:
        return None
    out: JsonDict = {}
    for key in ("size_pt", "slot_size_pt", "lines", "max_lines", "action"):
        if fit.get(key) is not None:
            out[key] = fit[key]
    return out or None


def _record_text(
    ctx: _Context, fill: SlotFill, result: tx.TextResult, block: JsonDict, slide_index: int
) -> None:
    fill.text = result.plain
    fill.fact_refs = list(dict.fromkeys([*(block.get("fact_refs") or []), *result.fact_refs]))
    fill.size_pt = result.size_pt
    for fid in result.missing_facts:
        ctx.warn("fact_missing", f"ссылка на неизвестный факт {fid}", slide_index)
    if (block.get("fit") or {}).get("action") == "overflow":
        ctx.warn(
            "capacity_overflow",
            f"слот {fill.slot_id}: текст не помещается по измерению плана",
            slide_index,
        )


def _web_sources_note(ctx: _Context, plan_slide: JsonDict) -> str:
    """Страницы из интернета, на которых стоит содержание слайда: докладчик в заметках видит,
    откуда взят факт. Блоки и факты ведут к источнику пакета, у веб-источника есть адрес."""
    package = ctx.package or {}
    blocks = {str(b.get("block_id")): b for b in package.get("blocks") or []}
    sources = {str(s.get("source_id")): s for s in package.get("sources") or []}
    ids: list[str] = []
    for ref in plan_slide.get("source_refs") or []:
        block = blocks.get(str(ref))
        if block is not None:
            ids.append(str(block.get("source_id")))
    for ref in plan_slide.get("fact_refs") or []:
        fact = ctx.facts.get(str(ref))
        if fact is not None:
            ids.append(str(fact.get("source_id")))
    lines = []
    for source_id in dict.fromkeys(ids):
        source = sources.get(source_id) or {}
        url = str(source.get("url") or "")
        if url:
            lines.append(f"{source.get('name') or source.get('domain') or url} — {url}")
    if not lines:
        return ""
    label = "Источники" if ctx.language.lower().startswith("ru") else "Sources"
    return f"{label}: " + "; ".join(lines)


def _element_id(element: Any) -> str:
    from presentation_designer.layout.shapes import CNVPR

    cnvpr = element.find(f".//{CNVPR}")
    return str(cnvpr.get("id")) if cnvpr is not None else ""


def _apply_table(
    ctx: _Context,
    slide: Any,
    slot: SlotInfo,
    element: Any,
    block: JsonDict,
    fill: SlotFill,
    slide_index: int,
) -> SlotFill | None:
    table = block.get("table") or {}
    dataset = ctx.datasets.get(str(table.get("dataset_id", "")))
    if dataset is None:
        ctx.warn("dataset_unknown", f"набор {table.get('dataset_id')} не найден", slide_index)
        return None
    spec = tables.table_spec(table, dataset, language=ctx.language)
    box = _slot_box(ctx, slot, element)
    is_table = element.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}tbl")
    if is_table is not None:
        tables.reshape_sample_table(element, spec, box, style=ctx.table_style)
        fill.built = "replaced"
        fill.content_source = "plan"
    else:
        remove_shape(slide, element)
        frame = tables.add_table(slide, box, spec, ctx.table_style)
        fill.element_id = _renumbered(ctx, frame._element)
        fill.built = "added"
        fill.content_source = "generated"
    fill.dataset_id = spec.dataset_id
    fill.table = {
        "rows": spec.n_rows,
        "cols": spec.n_cols,
        "header_row": True,
        "row_offset": spec.row_offset,
        "truncated": spec.truncated,
    }
    if spec.truncated:
        ctx.warn(
            "table_truncated",
            f"слот {slot.slot_id}: показаны строки {spec.row_offset + 1}–"
            f"{spec.row_offset + len(spec.rows)} набора {spec.dataset_id}",
            slide_index,
        )
    ctx.count("tables")
    return fill


def _chart_area(
    ctx: _Context, slot: SlotInfo, element: Any, pinfo: Any
) -> tuple[int, int, int, int]:
    """Область диаграммы: для картинки диаграммы, нарисованной образцом из нескольких
    картинок (столбики), — объединение всех слотов image паттерна."""
    if slot.kind != "image":
        return _slot_box(ctx, slot, element)
    boxes = [s.bbox for s in pinfo.slots.values() if s.kind == "image"]
    if len(boxes) <= 1:
        return _slot_box(ctx, slot, element)
    x, y, w, h = _union(boxes)
    return emu_box({"x": x, "y": y, "width": w, "height": h}, ctx.slide_w, ctx.slide_h)


def _apply_chart(
    ctx: _Context,
    slide: Any,
    slot: SlotInfo,
    element: Any,
    block: JsonDict,
    fill: SlotFill,
    slide_index: int,
    pinfo: Any,
) -> SlotFill | None:
    chart = block.get("chart") or {}
    dataset = ctx.datasets.get(str(chart.get("dataset_id", "")))
    if dataset is None:
        ctx.warn("dataset_unknown", f"набор {chart.get('dataset_id')} не найден", slide_index)
        return None
    spec = charts.chart_spec(chart, dataset)
    if not spec.series or not spec.categories:
        ctx.warn("chart_empty", f"набор {spec.dataset_id} без числовых рядов", slide_index)
        return None
    box = _chart_area(ctx, slot, element, pinfo)
    if slot.kind == "image":
        ctx.chart_areas.append(
            (box[0] / ctx.slide_w, box[1] / ctx.slide_h, box[2] / ctx.slide_w, box[3] / ctx.slide_h)
        )
    shapes = shape_map(slide)
    proxy = shapes.get(_element_id(element))
    if proxy is not None and getattr(proxy, "has_chart", False):
        frame, how = charts.replace_or_add_chart(slide, proxy, box, spec, ctx.chart_style)
        fill.built = how
        fill.content_source = "plan" if how == "replaced" else "generated"
    else:
        # Картинка диаграммы образца (паттерн роли chart) или плейсхолдер: на её месте
        # строится нативная диаграмма, картинка удаляется вместе с осиротевшей связью.
        remove_shape(slide, element)
        frame = charts.add_chart(slide, box, spec, ctx.chart_style)
        fill.built = "added"
        fill.content_source = "generated"
    fill.element_id = (
        _renumbered(ctx, frame._element) if fill.built != "replaced" else str(frame.shape_id)
    )
    fill.dataset_id = spec.dataset_id
    fill.chart = {
        **charts.describe_chart(frame.chart),
        "dataset_id": spec.dataset_id,
        "categories_count": len(spec.categories),
        "built": fill.built,
        **({"units": spec.units} if spec.units else {}),
    }
    ctx.count("charts")
    return fill


def _apply_image(
    ctx: _Context,
    slide: Any,
    slot: SlotInfo,
    element: Any,
    block: JsonDict,
    fill: SlotFill,
    slide_index: int,
) -> SlotFill | None:
    image = block.get("image") or {}
    asset_id = str(image.get("asset_id") or "")
    asset = ctx.package_assets.get(asset_id)
    if asset is None:
        code = "image_generation_unsupported" if image.get("generate") else "asset_unknown"
        ctx.warn(
            code,
            f"слот {slot.slot_id}: изображение {asset_id or 'по описанию'} недоступно, "
            "оставлена картинка образца",
            slide_index,
        )
        fill.content_source = "sample"
        fill.picture = {"origin": "template", "fit": "as_is"}
        return fill
    blob = _asset_bytes(ctx, asset)
    if blob is None:
        ctx.warn(
            "asset_missing",
            f"слот {slot.slot_id}: файл изображения {asset_id} не найден, "
            "оставлена картинка образца",
            slide_index,
        )
        fill.content_source = "sample"
        fill.picture = {"origin": "template", "fit": "as_is"}
        return fill
    fit_mode = str(image.get("fit") or "cover")
    box = _slot_box(ctx, slot, element)
    if element.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip") is None:
        # Плейсхолдер картинки или фигура без изображения: новая картинка на месте слота.
        remove_shape(slide, element)
        import io

        pic = slide.shapes.add_picture(io.BytesIO(blob), *box)
        element = pic._element
        fill.element_id = _renumbered(ctx, element)
        fill.source_object_id = None
    result = images.place_image(slide, element, blob, fit=fit_mode, box=box)
    fill.content_source = "plan"
    fill.asset_id = asset_id
    fill.picture = {
        "asset_id": asset_id,
        "origin": "content",
        "fit": fit_mode,
        **({"crop": result.crop} if result.crop else {}),
        **(
            {
                "natural_width_px": result.natural_width_px,
                "natural_height_px": result.natural_height_px,
            }
            if result.natural_width_px and result.natural_height_px
            else {}
        ),
    }
    ctx.count("pictures")
    return fill


def _apply_icon(
    ctx: _Context,
    slide: Any,
    slot: SlotInfo,
    element: Any,
    block: JsonDict,
    fill: SlotFill,
    slide_index: int,
) -> SlotFill | None:
    icon = block.get("icon") or {}
    asset_id = str(icon.get("asset_id") or "")
    asset = ctx.profile_assets.get(asset_id)
    if asset is None and icon.get("query"):
        placed = _library_icon(ctx, slide, slot, element, icon, fill)
        if placed is not None:
            return placed
    if asset is None:
        code = "icon_query_unsupported" if icon.get("query") else "asset_unknown"
        ctx.warn(
            code,
            f"слот {slot.slot_id}: иконка {asset_id or icon.get('query') or '?'} недоступна, "
            "оставлена иконка образца",
            slide_index,
        )
        fill.content_source = "sample"
        fill.picture = {"origin": "template", "fit": "as_is"}
        return fill
    color = (
        icon.get("color")
        or _sample_icon_color(slide, element)
        or (ctx.design_code.accent if ctx.design_code is not None else None)
    )
    if element.find(f".//{{{NS_A}}}blip") is None:
        # Якорь своей композиции — фигура, а не картинка: пиктограмма встаёт на её место.
        import io

        part = part_by_name(slide.part.package, str(asset.get("media_path", "")))
        if part is None:
            fill.content_source = "sample"
            return fill
        blob = bytes(part.blob)
        if color and icons.is_monochrome_mask(blob):
            blob = icons.recolor_mask(blob, color)
        box = _slot_box(ctx, slot, element)
        side = min(box[2], box[3])
        remove_shape(slide, element)
        pic = slide.shapes.add_picture(
            io.BytesIO(blob),
            box[0] + (box[2] - side) // 2,
            box[1] + (box[3] - side) // 2,
            side,
            side,
        )
        fill.element_id = _renumbered(ctx, pic._element)
        fill.source_object_id = None
        fill.content_source = "plan"
        fill.asset_id = asset_id
        fill.picture = {"asset_id": asset_id, "origin": "template", "fit": "contain"}
        ctx.count("icons")
        return fill
    result = icons.place_icon(slide, element, asset, color=color)
    fill.content_source = "plan"
    fill.asset_id = asset_id
    fill.picture = {
        "asset_id": asset_id,
        "origin": "template",
        "fit": "contain" if result.recolored else "as_is",
        "recolored": result.recolored,
    }
    ctx.count("icons")
    return fill


def _library_icon(
    ctx: _Context, slide: Any, slot: SlotInfo, element: Any, icon: JsonDict, fill: SlotFill
) -> SlotFill | None:
    """Иконка по запросу модели из набора `library.iconset`: векторная фигура цвета иконки
    образца (или акцента) на месте слота. None — в наборе ничего не нашлось."""
    name = iconset.find_icon(str(icon.get("query") or ""))
    if name is None:
        return None
    if ctx.design_code is None:
        ctx.design_code = DesignCode.from_profile(ctx.profile)
    color = str(icon.get("color") or _sample_icon_color(slide, element) or ctx.design_code.accent)
    box = _slot_box(ctx, slot, element)
    side = min(box[2], box[3])
    remove_shape(slide, element)
    shape = iconset.add_icon(
        slide, name, box[0] + (box[2] - side) // 2, box[1] + (box[3] - side) // 2, side, color
    )
    fill.element_id = _renumbered(ctx, shape._element)
    fill.source_object_id = None
    fill.content_source = "plan"
    ctx.count("icons")
    return fill


def _sample_icon_color(slide: Any, element: Any) -> str | None:
    """Цвет одноцветной иконки образца: новая пиктограмма перекрашивается в него, чтобы
    карточки остались в палитре образца (синяя иконка на белом круге и т. п.)."""
    import io

    blip = element.find(f".//{{{NS_A}}}blip")
    rid = blip.get(f"{{{NS_R}}}embed") if blip is not None else None
    if not rid:
        return None
    try:
        blob = bytes(slide.part.related_part(rid).blob)
    except (KeyError, AttributeError):
        return None
    if not icons.is_monochrome_mask(blob):
        return None
    from PIL import Image

    try:
        with Image.open(io.BytesIO(blob)) as img:
            rgba = img.convert("RGBA")
            rgba.thumbnail((48, 48))
            pixels: list[Any] = list(rgba.getdata())
            opaque = [px[:3] for px in pixels if px[3] > 128]
    except Exception:
        return None
    if not opaque:
        return None
    r, g, b = (round(sum(c[i] for c in opaque) / len(opaque)) for i in range(3))
    return f"#{r:02X}{g:02X}{b:02X}"


def _apply_diagram(
    ctx: _Context,
    slide: Any,
    slot: SlotInfo,
    element: Any,
    block: JsonDict,
    fill: SlotFill,
    slide_index: int,
) -> SlotFill | None:
    box = _slot_box(ctx, slot, element)
    remove_shape(slide, element)
    diagram = dict(block.get("diagram") or {})
    items = []
    refs = list(block.get("fact_refs") or [])
    for raw in diagram.get("items") or []:
        item = dict(raw)
        for key in ("text", "sub"):
            if key not in item:
                continue
            item[key], used, missing = tx.substitute_facts(str(item[key]), ctx.facts)
            refs.extend(used)
            for fid in missing:
                ctx.warn("fact_missing", f"ссылка на неизвестный факт {fid}", slide_index)
        items.append(item)
    diagram["items"] = items
    fill.fact_refs = list(dict.fromkeys(refs))
    result = diagrams.draw_diagram(slide, box, diagram, ctx.diagram_style)
    mapping = ctx.fresh_ids(shape_element(slide, result.group_id))
    fill.element_id = mapping[result.group_id]
    fill.source_object_id = None
    fill.built = "added"
    fill.content_source = "generated"
    fill.diagram = {"kind": result.kind, "node_ids": [mapping[n] for n in result.node_ids]}
    ctx.count("diagrams")
    return fill


# ---------- текст рядом с боковым декором ----------


def _side_decor(
    ctx: _Context, slide: Any, pattern_raw: JsonDict
) -> list[tuple[float, float, float, float]]:
    """Крупные постоянные картинки и декор макета/мастера этого слайда плюс большие статичные
    картинки самого образца: текст поверх них не читается, слоты от них отодвигаются."""
    layout_id = str(pattern_raw["source"].get("layout_id") or "")
    boxes: list[tuple[float, float, float, float]] = []
    for fixed in ctx.profile.get("fixed_elements") or []:
        if fixed.get("kind") not in ("decoration", "logo"):
            continue
        if fixed.get("appears_on") not in ("all", f"layout:{layout_id}"):
            continue
        b = fixed.get("bbox") or {}
        box = (
            float(b.get("x", 0)),
            float(b.get("y", 0)),
            float(b.get("width", 0)),
            float(b.get("height", 0)),
        )
        if box[2] * box[3] >= DECOR_MIN_AREA:
            boxes.append(box)
    static_ids = {str(i) for i in pattern_raw.get("static_object_ids") or []}
    if static_ids:
        for info in walk_shapes(slide, slide.part, ctx.slide_w, ctx.slide_h):
            if info.element_id in static_ids and info.kind == "picture" and info.area >= 0.05:
                boxes.append((info.x, info.y, info.width, info.height))
    return boxes


def _free_span(
    slot_box: tuple[float, float, float, float],
    decor: list[tuple[float, float, float, float]],
) -> tuple[float, float, float, float] | None:
    """Рамка слота без части, перекрытой боковым декором; None — сужать не нужно или нельзя."""
    x, y, w, h = slot_box
    if w <= 0 or h <= 0:
        return None
    for dx, dy, dw, dh in decor:
        ix = max(0.0, min(x + w, dx + dw) - max(x, dx))
        iy = max(0.0, min(y + h, dy + dh) - max(y, dy))
        if ix * iy < DECOR_OVERLAP_RATIO * w * h or iy < 0.5 * h:
            continue
        if x + 0.25 * w < dx < x + w:
            new_w = dx - DECOR_GAP - x
            if new_w >= DECOR_MIN_FREE_WIDTH:
                return (x, y, new_w, h)
        elif x < dx + dw < x + 0.75 * w:
            nx = dx + dw + DECOR_GAP
            new_w = x + w - nx
            if new_w >= DECOR_MIN_FREE_WIDTH:
                return (nx, y, new_w, h)
    return None


def _avoid_side_decor(
    ctx: _Context,
    slot: SlotInfo,
    element: Any,
    block: JsonDict,
    size: float | None,
    fill: SlotFill,
    slide_index: int,
) -> tuple[SlotInfo, float | None]:
    """Сужает текстовый слот, перекрытый боковым декором, и подбирает кегль по лестнице
    планировщика для новой ширины. Объекты внутри групп не трогаются: их xfrm в координатах
    группы."""
    if not ctx.decor_boxes or in_group(element):
        return slot, size
    narrowed = _free_span(slot.bbox, ctx.decor_boxes)
    if narrowed is None:
        return slot, size
    from presentation_designer.generation.capacity import font_steps, measure, min_pt_for

    text: str | list[str]
    if block.get("kind") == "bullets":
        text = [str(it.get("text", "")) for it in block.get("items") or []]
    else:
        text = str(block.get("text") or "")
    new_slot = dataclasses.replace(slot, bbox=narrowed)
    base = float(size or slot.size_pt or 18.0)
    scale = [float(t["size_pt"]) for t in ctx.profile["design_tokens"]["typography"]["scale"]]
    min_pt = min_pt_for(
        slot.kind,
        body_pt=ctx.fit_min_body_pt,
        title_pt=ctx.fit_min_title_pt,
        slide_w_emu=ctx.slide_w,
    )
    ratio = min(ctx.fit_min_ratio, DECOR_MIN_FONT_RATIO)
    steps = [base, *font_steps(new_slot, scale, min_ratio=ratio, min_pt=min_pt)]
    chosen = steps[-1]
    fits = False
    measured = None
    for candidate in steps:
        measured = measure(text, new_slot, ctx.slide_w, ctx.slide_h, size_pt=candidate)
        if measured.fits:
            chosen, fits = candidate, True
            break
    xfrm = ensure_xfrm(element)
    if xfrm is None:
        return slot, size
    set_element_box(
        element,
        emu_box(
            {"x": narrowed[0], "y": narrowed[1], "width": narrowed[2], "height": narrowed[3]},
            ctx.slide_w,
            ctx.slide_h,
        ),
    )
    fill.fit = {
        **(fill.fit or {}),
        "size_pt": round(chosen, 1),
        "slot_size_pt": round(float(slot.size_pt or base), 1),
        "action": "narrowed_for_decor",
    }
    if measured is not None:
        fill.fit["lines"] = measured.lines
        fill.fit["max_lines"] = measured.max_lines
    ctx.count("narrowed_slots")
    if not fits:
        ctx.warn(
            "text_narrowed_overflow",
            f"слот {slot.slot_id} сужен из-за декора, текст не помещается даже при {chosen:g} пт",
            slide_index,
        )
    return new_slot, chosen


# ---------- незаполненные слоты и карточки ----------


def _sample_keepable(sample: str) -> bool:
    """Текст образца, который уместен в заполненной карточке: номер шага («1», «01») или
    короткая подпись без цифр (единица, стрелка); значения вроде «23%» или «10 млн» —
    выдуманные показатели, их оставлять нельзя."""
    import re

    return bool(re.fullmatch(r"\d{1,2}", sample)) or not any(ch.isdigit() for ch in sample)


def _is_textual(slot: SlotInfo) -> bool:
    return slot.is_text or slot.kind == "number"


def _is_tiny(slot: SlotInfo) -> bool:
    return _is_textual(slot) and 0 < slot.max_chars < TINY_SLOT_CHARS


def _same_as_sample(slot: SlotInfo, block: JsonDict) -> bool:
    """Блок плана повторяет текст образца (с точностью до пробелов): в режиме сохранения
    объект остаётся как есть, вместе с абзацами и разнородным оформлением внутри."""
    if str(block.get("kind")) == "bullets":
        text = "\n".join(str(it.get("text", "")) for it in block.get("items") or [])
    elif str(block.get("kind")) in TEXT_KINDS:
        text = str(block.get("text") or "")
    else:
        return False
    mine, sample = " ".join(text.split()), " ".join((slot.sample_text or "").split())
    return bool(mine) and mine == sample


def _keep_rest(
    slide: Any,
    pattern_raw: JsonDict,
    pinfo: Any,
    filled: dict[str, SlotFill],
    record: SlideRecord,
    from_layout: bool,
) -> None:
    """Режим сохранения: слоты без блоков остаются объектами образца, ничего не удаляется."""
    refs = {s["slot_id"]: str(s.get("element_ref") or "") for s in pattern_raw.get("slots") or []}
    for slot in pinfo.slots.values():
        if slot.slot_id in filled:
            continue
        ref = refs.get(slot.slot_id, "")
        element = shape_element(slide, ref) if ref and not from_layout else None
        if element is None:
            continue
        fill = SlotFill(
            slot_id=slot.slot_id,
            slot_kind=slot.kind,
            block_kind=slot.kind,
            element_id=ref,
            source_object_id=ref,
            content_source="sample",
        )
        if _is_textual(slot) or slot.kind in ("qr", "footer"):
            fill.text = slot.sample_text or None
        elif slot.kind in ("image", "icon"):
            fill.picture = {"origin": "template", "fit": "as_is"}
        record.fills.append(fill)


def _cleanup(
    ctx: _Context,
    slide: Any,
    pattern_raw: JsonDict,
    pinfo: Any,
    filled: dict[str, SlotFill],
    record: SlideRecord,
    slide_index: int,
) -> list[str]:
    removable = {str(i) for i in pattern_raw.get("removable_object_ids") or []}
    refs = {s["slot_id"]: str(s.get("element_ref") or "") for s in pattern_raw.get("slots") or []}
    from_layout = pattern_raw["source"].get("kind") == "layout"
    removed: list[str] = []
    # Геометрия до удалений: рамки удалённых объектов нужны, чтобы убрать стрелки к ним.
    infos_before = {
        s.element_id: s for s in walk_shapes(slide, slide.part, ctx.slide_w, ctx.slide_h)
    }

    def drop(element_id: str) -> None:
        element = shape_element(slide, element_id)
        if element is None:
            return
        removed.extend(remove_shape(slide, element))

    # Карточки с содержимым: группы с равным числом карточек (иконки и подписи в одной,
    # описания в другой) — одна и та же карточка по индексу.
    filled_index: set[tuple[int, int]] = set()
    for group in pinfo.groups:
        for i in range(group.count):
            if any(s.slot_id in filled for s in group.card(i).values()):
                filled_index.add((group.count, i))
    # Группы без текста (иконки, подложки карточек): карточка заполнена, если ближайший к ней
    # текст — заполненный слот (карточки или одиночный), а не пустая карточка. Анализ часто
    # кладёт иконки и тексты в группы разного размера, и сверка по индексу их не связывает.
    text_filled = [s.bbox for s in pinfo.slots.values() if s.is_text and s.slot_id in filled]
    text_unfilled = [
        s.bbox
        for s in pinfo.slots.values()
        if s.is_text and s.group and s.slot_id not in filled and s.kind != "number"
    ]
    for group in pinfo.groups:
        if group.text_kinds or "number" in group.by_kind or not text_filled:
            continue
        for i in range(group.count):
            card = group.card(i)
            box = _union(s.bbox for s in card.values())
            center = (box[0] + box[2] / 2, box[1] + box[3] / 2)
            nearest_filled = min(_distance(center, b) for b in text_filled)
            nearest_unfilled = min(
                (_distance(center, b) for b in text_unfilled), default=float("inf")
            )
            if nearest_filled <= nearest_unfilled:
                filled_index.add((group.count, i))

    def card_of(slot: SlotInfo) -> tuple[Any, int] | None:
        if not slot.group:
            return None
        group = next((g for g in pinfo.groups if g.group_id == slot.group), None)
        if group is None:
            return None
        siblings = group.by_kind.get(slot.kind, [])
        index = next((i for i, s in enumerate(siblings) if s.slot_id == slot.slot_id), 0)
        return group, index

    def card_filled(slot: SlotInfo) -> bool:
        card = card_of(slot)
        if card is None:
            return True
        group, index = card
        return (group.count, index) in filled_index

    def card_ordinal(slot: SlotInfo) -> int:
        """Номер карточки среди заполненных карточек группы (1, 2, 3 без пропусков)."""
        card = card_of(slot)
        if card is None:
            return 1
        group, index = card
        return 1 + sum(1 for j in range(index) if (group.count, j) in filled_index)

    # 1. Незаполненные слоты: текст образца остаётся только в крошечных слотах с
    #    неслужебным текстом (номера шагов) заполненных карточек; картинки и иконки
    #    решаются карточками.
    for slot in pinfo.slots.values():
        if slot.slot_id in filled:
            continue
        ref = refs.get(slot.slot_id, "")
        element = shape_element(slide, ref) if ref and not from_layout else None
        if element is None:
            continue
        if element.find(f".//{tx.A_FLD}") is not None:
            continue  # динамическое поле (номер слайда, дата): остаётся и пересчитывается
        sample = slot.sample_text.strip()
        if (
            slot.kind == "number"
            and slot.group is not None
            and re.fullmatch(r"\d{1,2}", sample)
            and card_filled(slot)
        ):
            # Номер шага в заполненной карточке: план его не пишет, но без цифры карточка
            # теряет смысл; нумерация идёт по заполненным карточкам подряд.
            ordinal = str(card_ordinal(slot))
            tx.fill_text(element, ordinal, facts=ctx.facts)
            if _is_tiny(slot):
                tx.set_wrap(element, False)
            record.fills.append(
                SlotFill(
                    slot_id=slot.slot_id,
                    slot_kind=slot.kind,
                    block_kind=slot.kind,
                    element_id=ref,
                    source_object_id=ref,
                    content_source="generated",
                    text=ordinal,
                )
            )
            ctx.count("step_numbers")
            continue
        if slot.kind == "footer" and re.fullmatch(r"\d{1,3}", sample) and element is not None:
            # Номер страницы, набранный на образце текстом («09»): пишется номер этого
            # слайда в той же форме, иначе на десятом слайде стояло бы «09».
            number = str(record.order).zfill(len(sample))
            tx.fill_text(element, number, facts=ctx.facts)
            record.fills.append(
                SlotFill(
                    slot_id=slot.slot_id,
                    slot_kind=slot.kind,
                    block_kind=slot.kind,
                    element_id=ref,
                    source_object_id=ref,
                    content_source="generated",
                    text=number,
                )
            )
            continue
        if _is_textual(slot) or slot.kind in ("qr", "footer"):
            if (slot.kind == "footer" and not _placeholder_text(ctx, sample)) or (
                _is_tiny(slot)
                and sample
                and sample not in ctx.markers
                and slot.group is not None
                and card_filled(slot)
                and _sample_keepable(sample)
            ):
                record.fills.append(
                    SlotFill(
                        slot_id=slot.slot_id,
                        slot_kind=slot.kind,
                        block_kind=slot.kind,
                        element_id=ref,
                        source_object_id=ref,
                        content_source="sample",
                        text=sample,
                    )
                )
                continue
            drop(ref)
            if slot.required and not slot.group:
                ctx.warn(
                    "slot_unfilled",
                    f"обязательный слот {slot.slot_id} без содержимого",
                    slide_index,
                )
        elif slot.kind in ("table", "chart", "diagram"):
            drop(ref)
            ctx.warn("slot_unfilled", f"слот {slot.slot_id} ({slot.kind}) без данных", slide_index)
        elif slot.kind in ("image", "icon") and not _is_picture(slide, ref):
            drop(ref)  # подсказка «Вставить фото» текстом, а не картинкой
        elif slot.kind == "image" and _sample_image_misleading(
            ctx, slide, ref, pattern_raw, pinfo, slot, refs
        ):
            # Картинка диаграммы, скриншот или мокап образца без содержания вводят в
            # заблуждение: убираются; фотографии и иллюстрации остаются оформлением.
            drop(ref)
            ctx.warn(
                "sample_image_removed",
                f"слот {slot.slot_id}: картинка образца удалена, изображения в плане нет",
                slide_index,
            )
        elif slot.kind in ("image", "icon"):
            record.fills.append(
                SlotFill(
                    slot_id=slot.slot_id,
                    slot_kind=slot.kind,
                    block_kind=slot.kind,
                    element_id=ref,
                    source_object_id=ref,
                    content_source="sample",
                    picture={"origin": "template", "fit": "as_is"},
                )
            )
    # 2. Карточки без содержимого: все их объекты из removable_object_ids. Группы с равным
    #    числом карточек (иконки и подписи в одной, описания в другой) — одна и та же
    #    карточка по индексу: заполненное описание сохраняет иконку карточки.
    unfilled_cards: list[tuple[float, float, float, float]] = []
    filled_cards: list[tuple[float, float, float, float]] = []
    for group in pinfo.groups:
        for i in range(group.count):
            card = group.card(i)
            if (group.count, i) in filled_index:
                # Рамка заполненной карточки — по оставшимся слотам: удалённый пустой слот
                # (подпись, описание) не должен растягивать её на соседние карточки.
                kept = [s.bbox for s in card.values() if refs.get(s.slot_id, "") not in removed]
                filled_cards.append(_union(kept or [s.bbox for s in card.values()]))
                continue
            unfilled_cards.append(_union(s.bbox for s in card.values()))
            for s in card.values():
                ref = refs.get(s.slot_id, "")
                if ref in removable:
                    drop(ref)
                    record.fills = [f for f in record.fills if f.element_id != ref]
    # 3. Объекты карточек без слота (подложки, линии): к ближайшей карточке.
    others = removable - set(refs.values())
    if others and unfilled_cards:
        infos = {s.element_id: s for s in walk_shapes(slide, slide.part, ctx.slide_w, ctx.slide_h)}
        for oid in sorted(others):
            info = infos.get(oid)
            if info is None:
                continue
            center = info.center
            nearest_unfilled = min(
                (_distance(center, b) for b in unfilled_cards), default=float("inf")
            )
            nearest_filled = min((_distance(center, b) for b in filled_cards), default=float("inf"))
            if nearest_unfilled < nearest_filled:
                drop(oid)
    # 3а. Нарисованная диаграмма образца: столбики, подписи значений и категорий — обычные
    #     объекты, и после постройки нативной диаграммы на их месте они остались бы поверх неё.
    #     Убираются только когда слот chart действительно заполнен.
    chart_built = any(
        f.block_kind == "chart" and f.built in ("added", "rebuilt") for f in record.fills
    )
    if chart_built:
        for oid in [str(i) for i in pattern_raw.get("chart_parts") or []]:
            drop(oid)
    # 3б. Легенда картинки диаграммы: на месте картинки построена нативная диаграмма со
    #     своей легендой, поэтому в паттерне роли chart подписи образца (оставленные планом
    #     как текст образца) и мелкий декор вне постоянных элементов (точки легенды) убираются.
    if ctx.chart_areas and pattern_raw.get("role") == "chart":
        infos_chart = {
            s.element_id: s for s in walk_shapes(slide, slide.part, ctx.slide_w, ctx.slide_h)
        }
        fixed_refs = {
            str(f.get("element_ref"))
            for f in ctx.profile.get("fixed_elements") or []
            if str(f.get("source_part")) == record.source_slide_part
        }
        for f in list(record.fills):
            if f.content_source == "sample" and f.block_kind in TEXT_KINDS:
                drop(f.element_id)
                record.fills = [x for x in record.fills if x.element_id != f.element_id]
        for oid in [str(i) for i in pattern_raw.get("static_object_ids") or []]:
            info = infos_chart.get(oid)
            if info is None or info.text.strip() or info.area >= 0.002 or oid in fixed_refs:
                continue
            drop(oid)
    # 3в. Стрелки и якоря удалённых карточек: соединитель, конец которого упирался в удалённый
    #     объект (или только в такую же удалённую стрелку), убирается; свободные линии декора
    #     остаются. Мелкий статичный декор без текста (точки на кольце) уходит вместе с
    #     ближайшей пустой карточкой.
    if removed:
        fixed_refs_all = {
            str(f.get("element_ref"))
            for f in ctx.profile.get("fixed_elements") or []
            if str(f.get("source_part")) == record.source_slide_part
        }
        removed_boxes_before = [
            (info.x, info.y, info.width, info.height)
            for oid, info in infos_before.items()
            if oid in removed
        ]
        for oid in _dangling_connectors(
            ctx, slide, set(removed), removed_boxes_before, fixed_refs_all
        ):
            drop(oid)
        if unfilled_cards:
            infos_now = {
                s.element_id: s for s in walk_shapes(slide, slide.part, ctx.slide_w, ctx.slide_h)
            }
            for oid in [str(i) for i in pattern_raw.get("static_object_ids") or []]:
                info = infos_now.get(oid)
                if info is None or info.kind not in ("shape", "text") or info.text.strip():
                    continue
                if info.area >= TINY_DECOR_AREA or oid in fixed_refs_all:
                    continue
                nearest_unfilled = min(
                    (_distance(info.center, b) for b in unfilled_cards), default=float("inf")
                )
                nearest_filled = min(
                    (_distance(info.center, b) for b in filled_cards), default=float("inf")
                )
                if nearest_unfilled < nearest_filled:
                    drop(oid)
    # 3г. Аватар пустого слота спикера: кружок или фото рядом с убранным именем (VK
    #     WorkSpace — серые круги на обложке и финале) сам по себе выглядит заглушкой.
    speaker_boxes = [
        (s.x, s.y, s.width, s.height)
        for sid, ref in refs.items()
        if ref in removed
        and sid in pinfo.slots
        and pinfo.slots[sid].kind in ("name", "position")
        and (s := infos_before.get(ref)) is not None
    ]
    if speaker_boxes:
        fixed_refs_speaker = {
            str(f.get("element_ref"))
            for f in ctx.profile.get("fixed_elements") or []
            if str(f.get("source_part")) == record.source_slide_part
        }
        infos_avatar = {
            s.element_id: s for s in walk_shapes(slide, slide.part, ctx.slide_w, ctx.slide_h)
        }
        for oid, info in infos_avatar.items():
            if (
                oid in fixed_refs_speaker
                or info.kind not in ("shape", "picture", "text")
                or info.text.strip()
            ):
                continue
            if (
                info.area >= 0.02
                or not info.height
                or not 0.6 <= info.width / info.height * (ctx.slide_w / ctx.slide_h) <= 1.6
            ):
                continue
            if any(_beside(info, box) for box in speaker_boxes):
                drop(oid)
    # 3д. QR-код образца (статичная картинка): ведёт на ресурс автора шаблона, к колоде
    #     отношения не имеет. Постоянные элементы (логотипы) не трогаются.
    fixed_refs_qr = {
        str(f.get("element_ref"))
        for f in ctx.profile.get("fixed_elements") or []
        if str(f.get("source_part")) == record.source_slide_part
    }
    for oid in [str(i) for i in pattern_raw.get("static_object_ids") or []]:
        if oid not in fixed_refs_qr and oid not in removed and _looks_like_qr(slide, oid):
            drop(oid)
            ctx.count("sample_qr_removed")
    # 3е. Колонтитул автора шаблона на самом слайде: «JOHN DOE», «NEW YORK», «2023»,
    #     «SLIDESCARNIVAL.COM» — анализ отметил их заготовками, но как постоянные элементы
    #     они иначе остаются на каждом слайде колоды.
    for fixed in ctx.profile.get("fixed_elements") or []:
        oid = str(fixed.get("element_ref"))
        if (
            str(fixed.get("source_part")) != record.source_slide_part
            or fixed.get("kind") not in ("footer", "date")
            or oid in removed
        ):
            continue
        info = infos_before.get(oid)
        if info is not None and _placeholder_text(ctx, info.text):
            drop(oid)
            ctx.count("sample_footer_removed")
    # 4. Пустые рамки карточек: статичная фигура без текста, внутри которой были только
    #    удалённые слоты и не осталось ни одного заполненного или сохранённого объекта,
    #    убирается вместе с содержимым карточки (подложка без содержания — не декор).
    removed_boxes = [
        pinfo.slots[sid].bbox for sid, ref in refs.items() if ref in removed and sid in pinfo.slots
    ]
    if removed_boxes:
        kept_refs = {f.element_id for f in record.fills}
        kept_boxes = [
            pinfo.slots[sid].bbox
            for sid, ref in refs.items()
            if ref in kept_refs and sid in pinfo.slots
        ]
        infos = {s.element_id: s for s in walk_shapes(slide, slide.part, ctx.slide_w, ctx.slide_h)}
        for oid in [str(i) for i in pattern_raw.get("static_object_ids") or []]:
            info = infos.get(oid)
            if info is None or info.kind not in ("shape", "text") or info.text.strip():
                continue
            if info.area >= 0.5 or info.area < 0.004:
                continue  # фон слайда и мелкий декор не трогаем
            frame = (info.x, info.y, info.width, info.height)
            if any(_contains(frame, b) for b in removed_boxes) and not any(
                _overlaps(frame, b) for b in kept_boxes
            ):
                drop(oid)
    # 5. Оставшиеся карточки однорядной сетки — на всю ширину ряда, без дыры на месте убранных.
    if ctx.reflow_cards and pattern_raw["source"].get("kind") == "sample_slide":
        _reflow_cards(
            ctx, slide, pattern_raw, pinfo, refs, filled_index, infos_before, record, drop
        )
    if removed:
        ctx.count("removed_objects", len(removed))
    return list(dict.fromkeys(removed))


def _reflow_cards(
    ctx: _Context,
    slide: Any,
    pattern_raw: JsonDict,
    pinfo: Any,
    refs: dict[str, str],
    filled_index: set[tuple[int, int]],
    infos_before: dict[str, Any],
    record: SlideRecord,
    drop: Any,
) -> None:
    """Карточки однорядной сетки, оставшиеся после уборки пустых, расходятся на всю ширину
    ряда (`generation/reflow.py`): три текстовых блока VK Tech с двумя заполненными дают две
    широкие карточки того же вида, а не две узкие и дыру справа. Колонки — подложки карточек
    (самая большая фигура без текста вокруг текста карточки, не шире шага ряда), без подложек —
    сами тексты. Двигается только ряд из отдельных фигур: группа, соединитель, объект между
    карточками или выходящий за свою карточку оставляют слайд как был."""
    group = pinfo.cards
    if group is None or pattern_raw.get("role") != "cards":
        return
    kept = [i for i in range(group.count) if (group.count, i) in filled_index]
    if len(kept) < 2 or len(kept) == group.count:
        return
    raw_slots = {str(s["slot_id"]): s for s in pattern_raw.get("slots") or []}
    if any(raw_slots.get(sid, {}).get("group_path") for sid in pinfo.slots):
        return
    texts: list[tuple[float, float, float, float]] = []
    for i in range(group.count):
        boxes = [s.bbox for s in group.card(i).values() if s.is_text]
        if not boxes:
            return
        texts.append(_union(boxes))
    if not reflow.single_row(texts):
        return
    pitch = min(texts[i + 1][0] - texts[i][0] for i in range(len(texts) - 1))
    frames: list[tuple[float, float, float, float] | None] = []
    for box in texts:
        # Подложка VK Tech — roundRect с пустым текстовым блоком: вид у неё text, а не shape.
        around = [
            (i.x, i.y, i.width, i.height)
            for i in infos_before.values()
            if i.kind in ("shape", "text")
            and not i.text.strip()
            and i.width <= pitch
            and _contains((i.x, i.y, i.width, i.height), box)
        ]
        frames.append(max(around, key=lambda b: b[2] * b[3]) if around else None)
    columns = [f for f in frames if f is not None]
    if len(columns) != len(texts):
        columns = texts
    placed = reflow.row_columns(columns, kept)
    if placed is None:
        return
    # Полоса ряда — по колонкам и всем слотам карточек: иконки и точки над и под текстом
    # двигаются вместе с ним, даже когда подложек нет.
    card_boxes = [s.bbox for g in pinfo.groups for v in g.by_kind.values() for s in v]
    top = min(b[1] for b in [*columns, *card_boxes])
    bottom = max(b[1] + b[3] for b in [*columns, *card_boxes])
    span = columns[-1][0] + columns[-1][2] - columns[0][0]
    fixed = {
        str(f.get("element_ref"))
        for f in ctx.profile.get("fixed_elements") or []
        if str(f.get("source_part")) == record.source_slide_part
    }
    title_ref = refs.get(pinfo.title.slot_id, "") if pinfo.title is not None else ""
    moves: list[tuple[Any, int]] = []
    leftovers: list[str] = []
    for info in walk_shapes(slide, slide.part, ctx.slide_w, ctx.slide_h):
        if info.element_id in fixed or info.element_id == title_ref:
            continue
        if not top <= info.y + info.height / 2 <= bottom or info.width >= 0.9 * span:
            continue  # вне ряда или подложка всего ряда
        if info.kind in ("group", "connector") or info.group_path:
            return
        cx = info.x + info.width / 2
        col = next(
            (
                i
                for i, c in enumerate(columns)
                if c[0] - REFLOW_SLACK / 2 <= cx <= c[0] + c[2] + REFLOW_SLACK / 2
            ),
            None,
        )
        if col is None:
            return  # объект между карточками
        if col not in placed:
            if info.text.strip():
                return
            leftovers.append(info.element_id)  # декор убранной карточки, не снятый уборкой
            continue
        c = columns[col]
        if info.x < c[0] - REFLOW_SLACK or info.x + info.width > c[0] + c[2] + REFLOW_SLACK:
            return
        moves.append((info, col))
    for oid in leftovers:
        drop(oid)
    new_boxes = {
        info.element_id: reflow.move_box(
            (info.x, info.y, info.width, info.height),
            (columns[col][0], columns[col][2]),
            placed[col],
        )
        for info, col in moves
    }
    texts_of_cards = {
        refs.get(s.slot_id, "") for i in kept for s in group.card(i).values() if s.is_text
    }
    for info, col in moves:
        box = new_boxes[info.element_id]
        if info.element_id in texts_of_cards and info.anchor in (None, "t"):
            # Как в подборе (generation/edit.py: reduced_view): текст растёт вниз до иконки.
            below = [new_boxes[i.element_id] for i, c in moves if c == col and i is not info]
            box = reflow.grow_down(box, below)
        height = info.height_emu if box[3] == info.height else round(box[3] * ctx.slide_h)
        set_element_box_absolute(
            info.element,
            (round(box[0] * ctx.slide_w), info.top_emu, round(box[2] * ctx.slide_w), height),
        )
    ctx.count("cards_reflowed")


# Адреса сервисов бесплатных шаблонов в колонтитулах образцов.
_VENDOR_SITE = re.compile(
    r"(?i)slidescarnival|slidesgo|freepik|slidemania|presentationgo|poweredtemplate|showeet"
)


def _placeholder_text(ctx: _Context, text: str | None) -> bool:
    """Текст образца — заготовка автора шаблона: отмечен анализом или адрес сервиса."""
    plain = " ".join(str(text or "").split())
    return bool(plain) and (plain in ctx.markers or bool(_VENDOR_SITE.search(plain)))


def _beside(info: Any, box: tuple[float, float, float, float]) -> bool:
    """Объект стоит в строку с рамкой: по вертикали пересекается с ней, а по горизонтали
    отстоит не дальше своей ширины (аватар слева или справа от имени)."""
    x, y, w, h = box
    top, bottom = info.y, info.y + info.height
    if bottom < y - h * 0.5 or top > y + h * 1.5:
        return False
    gap = max(x - (info.x + info.width), info.x - (x + w), 0.0)
    return bool(gap <= max(info.width * 1.5, 0.02))


def _touches(point: tuple[float, float], box: tuple[float, float, float, float]) -> bool:
    x, y = point
    return (
        box[0] - CONNECTOR_TOUCH <= x <= box[0] + box[2] + CONNECTOR_TOUCH
        and box[1] - CONNECTOR_TOUCH <= y <= box[1] + box[3] + CONNECTOR_TOUCH
    )


def _dangling_connectors(
    ctx: _Context,
    slide: Any,
    removed: set[str],
    removed_boxes: list[tuple[float, float, float, float]],
    fixed_refs: set[str],
) -> list[str]:
    """Соединители, потерявшие опору: явная привязка (stCxn/endCxn) к удалённому объекту или
    конец, который упирался в рамку удалённого объекта либо только в уже снятую стрелку,
    и при этом не касается оставшихся фигур. Считается до неподвижной точки, чтобы цепочка
    стрелок к удалённой карточке ушла целиком."""
    infos = [s for s in walk_shapes(slide, slide.part, ctx.slide_w, ctx.slide_h)]
    anchors = [
        (i.x, i.y, i.width, i.height)
        for i in infos
        if i.kind not in ("connector", "group") and i.area < 0.5
    ]
    connectors = {i.element_id: i for i in infos if i.kind == "connector"}
    dropped: set[str] = set()
    for oid, info in connectors.items():
        if oid in fixed_refs:
            continue
        if info.element is not None and connection_ids(info.element) & removed:
            dropped.add(oid)
    changed = True
    while changed:
        changed = False
        for oid, info in connectors.items():
            if oid in dropped or oid in fixed_refs:
                continue
            for point in connector_endpoints(info):
                if any(_touches(point, a) for a in anchors):
                    continue
                touched_removed = any(_touches(point, b) for b in removed_boxes)
                neighbours = [
                    other
                    for other, o in connectors.items()
                    if other != oid and _touches(point, (o.x, o.y, o.width, o.height))
                ]
                alive = [n for n in neighbours if n not in dropped]
                gone = [n for n in neighbours if n in dropped]
                if touched_removed or (gone and not alive):
                    dropped.add(oid)
                    changed = True
                    break
    return sorted(dropped)


def _contains(
    frame: tuple[float, float, float, float], box: tuple[float, float, float, float]
) -> bool:
    """Рамка содержит центр box и не меньше 60 % его площади: подпись в карточке бывает
    шире самой карточки (VK Tech, финал с QR: «Вставить QR» выступает за белую плашку)."""
    ix = max(0.0, min(frame[0] + frame[2], box[0] + box[2]) - max(frame[0], box[0]))
    iy = max(0.0, min(frame[1] + frame[3], box[1] + box[3]) - max(frame[1], box[1]))
    area = box[2] * box[3]
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    inside = frame[0] <= cx <= frame[0] + frame[2] and frame[1] <= cy <= frame[1] + frame[3]
    return area > 0 and inside and ix * iy >= 0.6 * area


def _overlaps(
    frame: tuple[float, float, float, float], box: tuple[float, float, float, float]
) -> bool:
    ix = max(0.0, min(frame[0] + frame[2], box[0] + box[2]) - max(frame[0], box[0]))
    iy = max(0.0, min(frame[1] + frame[3], box[1] + box[3]) - max(frame[1], box[1]))
    return ix * iy > 0


MISLEADING_ASSET_KINDS = ("chart_image", "screenshot", "mockup", "qr")


def _is_picture(slide: Any, element_id: str) -> bool:
    element = shape_element(slide, element_id)
    return element is not None and (
        element.find(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip") is not None
    )


_CHART_WORDS = ("диаграмм", "график", "chart", "graph", "скриншот", "screenshot", "мокап", "mockup")


def _looks_like_qr(slide: Any, element_id: str) -> bool:
    """QR-код по самой картинке: почти квадрат, почти только чёрное и белое, яркость
    меняется десятки раз по строке. Каталог ресурсов отмечает QR не всегда."""
    import io

    element = shape_element(slide, element_id)
    if element is None:
        return False
    owner, blip = slide, element.find(f".//{{{NS_A}}}blip")
    if blip is None:
        # Плейсхолдер без своей картинки наследует заливку плейсхолдера макета (VK Education
        # «Финальный с QR»: QR нарисован в макете, на слайде пустой плейсхолдер).
        ph = element.find(f".//{{{NS_P}}}nvPr/{{{NS_P}}}ph")
        idx = ph.get("idx") if ph is not None else None
        layout = getattr(slide, "slide_layout", None)
        for candidate in list(layout.placeholders) if layout is not None and idx else []:
            if str(candidate.placeholder_format.idx) == idx:
                owner, blip = layout, candidate._element.find(f".//{{{NS_A}}}blip")
                break
    rid = blip.get(f"{{{NS_R}}}embed") if blip is not None else None
    if not rid:
        return False
    try:
        blob = bytes(owner.part.related_part(rid).blob)
        from PIL import Image

        with Image.open(io.BytesIO(blob)) as img:
            if not 0.85 <= img.width / max(img.height, 1) <= 1.15:
                return False
            gray = img.convert("L").resize((96, 96), Image.Resampling.NEAREST)
            pixels: list[Any] = list(gray.getdata())
    except Exception:
        return False
    extreme = sum(1 for v in pixels if v < 70 or v > 190) / len(pixels)
    dark = sum(1 for v in pixels if v < 70) / len(pixels)
    if extreme < 0.85 or not 0.15 <= dark <= 0.7:
        return False
    transitions = sum(
        1
        for row in range(96)
        for col in range(95)
        if (pixels[row * 96 + col] < 128) != (pixels[row * 96 + col + 1] < 128)
    )
    return transitions / 96 >= 8


def _asset_kind_of(ctx: _Context, slide: Any, element_id: str) -> str | None:
    element = shape_element(slide, element_id)
    if element is None:
        return None
    sha = images.picture_sha256(slide, element)
    if not sha:
        return None
    asset = next(
        (
            a
            for a in ctx.profile_assets.values()
            if str(a.get("sha256", "")).split(":", 1)[-1] == sha
        ),
        None,
    )
    return str(asset.get("kind")) if asset is not None else None


# Крупная картинка образца на содержательном слайде: в шаблоне ЛЦТ на четверти
# слайда стоит скриншот VK WorkSpace, и в колоде про логистику он читается как
# её содержание. Каталог таких снимков не ловит: из 222 ресурсов «мокапом»
# помечен один, а измеримого признака (белизна, число цветов, текстурность)
# у скриншотов с иллюстрациями не нашлось — проверено на всех ресурсах.
#
# Поэтому условий два. Размер: мелкая картинка — приём оформления, крупная —
# заявление по существу. И вид: фотографии и иллюстрации (`photo`) остаются,
# снимаются плоские картинки (`image` — то, что классификатор счёл
# одноцветным), а это и есть интерфейсные снимки. Одного размера мало:
# по нему уходили и удачные иллюстрации в карточках, слайд становился голым.
BIG_SAMPLE_AREA = 0.12
KEEPABLE_BIG_KINDS = ("photo", "background", "logo", "icon")


def _sample_image_misleading(
    ctx: _Context,
    slide: Any,
    element_id: str,
    pattern_raw: JsonDict,
    pinfo: Any,
    slot: SlotInfo,
    refs: dict[str, str],
) -> bool:
    """Картинка образца без содержания вводит в заблуждение, если это картинка диаграммы,
    скриншот или мокап (по каталогу ресурсов), если паттерн — про диаграммы/скриншоты по
    роли или имени, если она занимает заметную часть содержательного слайда, либо если
    соседняя картинка той же группы карточек — такая."""
    if _asset_kind_of(ctx, slide, element_id) in MISLEADING_ASSET_KINDS:
        return True
    if _looks_like_qr(slide, element_id):
        # QR образца ведёт на ресурс автора шаблона, а не на что-то из этой колоды.
        return True
    role = str(pattern_raw.get("role") or "")
    decorative_role = role in ("title", "section_divider", "thanks", "image_full", "speaker")
    box = slot.bbox
    if (
        not decorative_role
        and box[2] * box[3] >= BIG_SAMPLE_AREA
        and _asset_kind_of(ctx, slide, element_id) not in KEEPABLE_BIG_KINDS
    ):
        return True
    name = str(pattern_raw.get("name") or "").lower()
    if role in ("chart", "screenshot", "mockup") or any(w in name for w in _CHART_WORDS):
        return True
    if slot.group:
        for sibling in pinfo.slots.values():
            if sibling.kind == "image" and sibling.group == slot.group and sibling is not slot:
                if (
                    _asset_kind_of(ctx, slide, refs.get(sibling.slot_id, ""))
                    in MISLEADING_ASSET_KINDS
                ):
                    return True
    return False


def _union(
    boxes: Any,
) -> tuple[float, float, float, float]:
    items = list(boxes)
    x1 = min(b[0] for b in items)
    y1 = min(b[1] for b in items)
    x2 = max(b[0] + b[2] for b in items)
    y2 = max(b[1] + b[3] for b in items)
    return (x1, y1, x2 - x1, y2 - y1)


def _distance(center: tuple[float, float], box: tuple[float, float, float, float]) -> float:
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    return float(((center[0] - cx) ** 2 + (center[1] - cy) ** 2) ** 0.5)


# ---------- сборка колоды ----------


@dataclass
class _BuiltinSlide:
    """Что нужно отделке собственной композиции после заполнения (`library.dress`)."""

    composition: Any
    refs: dict[str, str]
    card_ids: list[str]
    backdrop: str
    title_decor: bool


def _build_builtin(
    ctx: _Context, pattern_id: str, pattern_raw: JsonDict, source: JsonDict
) -> tuple[Any, JsonDict, _BuiltinSlide]:
    """Строит слайд собственной композиции и проставляет слотам ссылки на созданные объекты.

    Дальше паттерн неотличим от образца шаблона: у каждого слота есть `element_ref`, и текст,
    факты, списки и картинки в него пишет общий путь вёрстки.
    """
    composition_id = str(source.get("composition_id") or "")
    composition = find_composition(composition_id)
    if composition is None:
        raise ComposeError(
            "compose_composition_unknown",
            f"паттерн {pattern_id}: композиция {composition_id} отсутствует в библиотеке",
        )
    layout = layout_by_id(ctx.prs, str(source.get("layout_id") or ""))
    if layout is None:
        raise ComposeError(
            "compose_layout_missing",
            f"паттерн {pattern_id}: макет {source.get('layout_id')} отсутствует в шаблоне",
        )
    if ctx.design_code is None:
        ctx.design_code = DesignCode.from_profile(ctx.profile)
    layout_id = str(source.get("layout_id") or "")
    if layout_id not in ctx.skins:
        ctx.skins[layout_id] = template_skin(ctx.samples, ctx.profile, layout_id)
    slide, refs, card_ids = build_builtin_slide(
        ctx.prs, layout, composition, ctx.design_code, ctx.skins[layout_id]
    )
    patched = dict(pattern_raw)
    patched["slots"] = [
        {**slot, "element_ref": refs[str(slot.get("slot_id"))]}
        if str(slot.get("slot_id")) in refs
        else dict(slot)
        for slot in pattern_raw.get("slots") or []
    ]
    # Карточки, которым не досталось содержания, убираются вместе с плашками — как у
    # образцов шаблона по `removable_object_ids`.
    patched["removable_object_ids"] = card_ids + [
        refs[str(slot.get("slot_id"))]
        for slot in patched["slots"]
        if slot.get("repeat_group") and str(slot.get("slot_id")) in refs
    ]
    ctx.count("builtin_slides")
    skin = ctx.skins[layout_id]
    meta = _BuiltinSlide(
        composition=composition,
        refs=dict(refs),
        card_ids=list(card_ids),
        backdrop=slide_backdrop(
            layout, ctx.design_code, skin, int(ctx.prs.slide_width), int(ctx.prs.slide_height)
        ),
        title_decor=bool(skin.decor),
    )
    return slide, patched, meta


# Роли текста, которые растут до заполнения места в образце шаблона, и потолок роста:
# доля кегля образца и доля основного кегля дизайн-кода.
GROW_ROLES = {
    "body": (2.0, 1.6),
    "bullets": (2.0, 1.6),
    "caption": (1.5, 1.15),
    "label": (1.4, 1.15),
    "subtitle": (1.3, 1.6),
}
GROW_FILL = 0.9  # текст занимает не больше этой доли высоты рамки слота
GROW_MIN_STEP = 1.08  # меньший рост незаметен и не стоит правки файла


def _grow_template_text(ctx: _Context, slide: Any, record: SlideRecord, pinfo: Any) -> None:
    """Текст в слоте образца растёт, пока занимает мало места: кегль образца рассчитан на его
    текст-рыбу, и короткая подпись в большой карточке выходит мелкой, а карточка — пустой.

    Кегль растёт до заполнения рамки слота (не выше потолка роли); у карточек одной группы
    повторов кегль общий — ряд выглядит ровно. Заголовки, числа и даты не трогаются: их кегль —
    часть рисунка шаблона. Режим «По шаблону» и готовая презентация не меняются."""
    if ctx.preserve or not ctx.reflow_cards:
        return
    from presentation_designer.library.dress import text_height

    if ctx.design_code is None:
        ctx.design_code = DesignCode.from_profile(ctx.profile)
    code = ctx.design_code
    top_level = {str(s.shape_id): s for s in slide.shapes}
    others = [
        s
        for s in slide.shapes
        if (getattr(s, "has_text_frame", False) and s.text_frame.text.strip())
        or getattr(s, "shape_type", None) == 13
    ]
    plates = [s for s in slide.shapes if _is_plate(s)]
    plans: dict[str, list[tuple[Any, float, float, int]]] = {}
    for fill in record.fills:
        slot = pinfo.slots.get(fill.slot_id)
        shape = top_level.get(str(fill.element_id))
        if slot is None or shape is None or slot.kind not in GROW_ROLES or not fill.text:
            continue
        if not getattr(shape, "has_text_frame", False):
            continue
        runs = [r for p in shape.text_frame.paragraphs for r in p.runs]
        sizes = [float(r.font.size.pt) for r in runs if r.font.size is not None]
        current = min(sizes) if sizes else float(fill.size_pt or slot.size_pt or 0)
        if current <= 0:
            continue
        frame = shape.text_frame
        box_w = max(int(shape.width), int(slot.bbox[2] * ctx.slide_w))
        box_h = max(int(shape.height), int(slot.bbox[3] * ctx.slide_h))
        width = box_w - int(frame.margin_left or 0) - int(frame.margin_right or 0)
        width -= int(slot.indent_emu or 0)
        # Рамки слотов у образцов часто заходят друг на друга: растущий текст не должен
        # доходить до объекта под ним (текст карточки под её заголовком, картинка).
        below = _room_below(shape, others)
        # Текст в карточке образца может занять её свободную высоту: рамка образца
        # рассчитана на рыбу в одну-две строки, а плашка под ней — на весь блок.
        plate_room = _room_in_plate(shape, plates)
        if plate_room is not None:
            box_h = max(box_h, plate_room)
        box_h = min(box_h, below) if below is not None else box_h
        height = box_h - int(frame.margin_top or 0) - int(frame.margin_bottom or 0)
        text = shape.text_frame.text.replace("\v", "\n")
        family = slot.family or code.body_font
        spacing = float(slot.line_spacing or 1.0)
        gap = float(slot.space_before_pt or 0) + float(slot.space_after_pt or 0)
        of_sample, of_body = GROW_ROLES[slot.kind]
        cap = max(current, min(current * of_sample, code.body_pt * of_body))

        # Заголовок карточки и подпись растут без новых строк: вторая строка у короткой
        # подписи ломает ряд карточек.
        keep_lines = slot.kind in ("label", "caption", "subtitle")
        lines = _line_count(text, family, current, width) if keep_lines else 0
        best = current
        while best * 1.05 <= cap:
            trial = best * 1.05
            need = text_height(text, family, trial, width, spacing=spacing, para_gap_pt=gap)
            if need > height * GROW_FILL:
                break
            if keep_lines and _line_count(text, family, trial, width) > lines:
                break
            best = trial
        # Ряд — слоты одного вида на одной высоте (колонки текста, карточки), даже если
        # у образца они разнесены по разным группам повторов.
        key = f"{slot.kind}@{slot.bbox[1]:.2f}"
        need_h = int(
            text_height(text, family, best, width, spacing=spacing, para_gap_pt=gap)
            + int(frame.margin_top or 0)
            + int(frame.margin_bottom or 0)
        )
        plans.setdefault(key, []).append((shape, current, best, need_h))
    for items in plans.values():
        # Общий кегль ряда — самый осторожный из его слотов: ряд выглядит ровно, и тот, что
        # крупнее соседей, уменьшается до них.
        size = min(best for _, _, best, _ in items)
        for shape, current, _, need_h in items:
            if abs(size - current) < current * (GROW_MIN_STEP - 1):
                continue
            if size > current and need_h > int(shape.height):
                shape.height = need_h
            ratio = size / current
            body_pr = shape.text_frame._txBody.find(f"{{{NS_A}}}bodyPr")
            autofit = body_pr.find(f"{{{NS_A}}}normAutofit") if body_pr is not None else None
            if autofit is not None:
                # Сжатие рендерера отменило бы рост: текст измерен и помещается и так.
                for attr in ("fontScale", "lnSpcReduction"):
                    autofit.attrib.pop(attr, None)
            for paragraph in shape.text_frame.paragraphs:
                for run in paragraph.runs:
                    if run.font.size is not None:
                        run.font.size = Pt(round(float(run.font.size.pt) * ratio, 1))
                    else:
                        run.font.size = Pt(round(current * ratio, 1))
            ctx.count("text_grown")


def _room_below(shape: Any, others: list[Any]) -> int | None:
    """Высота от верха рамки до ближайшего объекта под ней, перекрывающего её по ширине."""
    left, top = int(shape.left), int(shape.top)
    right = left + int(shape.width)
    room: int | None = None
    for other in others:
        if other is shape or other.shape_id == shape.shape_id:
            continue
        o_left, o_top = int(other.left), int(other.top)
        o_right = o_left + int(other.width)
        overlap = min(right, o_right) - max(left, o_left)
        if overlap <= 0.3 * min(int(shape.width), int(other.width)) or o_top <= top + 12700:
            continue
        gap = o_top - top - 38100
        room = gap if room is None else min(room, gap)
    return room


def _is_plate(shape: Any) -> bool:
    """Плашка образца: фигура с заливкой без текста (карточка, подложка)."""
    if getattr(shape, "shape_type", None) not in (1, 5):  # AUTO_SHAPE, FREEFORM
        return False
    if getattr(shape, "has_text_frame", False) and shape.text_frame.text.strip():
        return False
    try:
        return shape.fill.type is not None and shape.fill.type != 5  # 5 — BACKGROUND
    except Exception:
        return False


def _room_in_plate(shape: Any, plates: list[Any]) -> int | None:
    """Высота от верха рамки до низа плашки, внутри которой она стоит (с полем снизу)."""
    left, top = int(shape.left), int(shape.top)
    right = left + int(shape.width)
    best: int | None = None
    for plate in plates:
        p_left, p_top = int(plate.left), int(plate.top)
        p_right, p_bottom = p_left + int(plate.width), p_top + int(plate.height)
        if not (p_left - 12700 <= left and right <= p_right + 12700 and p_top <= top < p_bottom):
            continue
        room = p_bottom - top - int(0.06 * int(plate.height))
        if room > 0 and (best is None or room < best):
            best = room
    return best


def _line_count(text: str, family: str | None, size: float, width: int) -> int:
    from presentation_designer.generation.capacity import wrap_lines
    from presentation_designer.shared import text_metrics

    font = text_metrics.resolve_font(family)
    return sum(
        wrap_lines(p, width / 12700 * 0.96, font, size) for p in text.split("\n") if p.strip()
    )


def _dress_builtin(ctx: _Context, slide: Any, meta: _BuiltinSlide, plan_slide: JsonDict) -> None:
    """Отделка собственной композиции по заполненному тексту: кегль, высота карточек по
    тексту, значки с иконками, маркеры списка, акценты (`library/dress.py`)."""
    if not ctx.reflow_cards or ctx.design_code is None:
        return
    variant = str((ctx.plan.get("variant") or {}).get("variant_id") or "balanced")
    # Сами элементы, а не id(): прокси lxml живут, пока на них есть ссылка.
    before = set(slide.shapes._spTree)
    done = dress_slide(
        slide,
        meta.composition,
        meta.refs,
        meta.card_ids,
        ctx.design_code,
        look_for(variant, ctx.design_code),
        width=int(ctx.prs.slide_width),
        height=int(ctx.prs.slide_height),
        backdrop=meta.backdrop,
        blocks=list(plan_slide.get("blocks") or []),
        title_decor=meta.title_decor,
    )
    # Новые фигуры отделки не занимают id удалённых объектов (python-pptx заполняет пропуски).
    for element in list(slide.shapes._spTree):
        if element not in before:
            ctx.fresh_ids(element)
    for item in done:
        ctx.count(f"dressed_{item}")


def compose_deck(
    plan: JsonDict,
    profile: JsonDict,
    template_path: pathlib.Path,
    package: JsonDict,
    *,
    out_pptx: pathlib.Path,
    package_dir: pathlib.Path | None = None,
    job_id: str = "job_local",
    variant_id: str | None = None,
    revision: int = 1,
    pptx_artifact: str | None = None,
    prune_layouts: bool = False,
    fit_min_ratio: float = 0.75,
    fit_min_body_pt: float = 12.0,
    fit_min_title_pt: float = 20.0,
    extra_assets: dict[str, pathlib.Path] | None = None,
    media_dir: pathlib.Path | None = None,
    media_prefix: str = "",
    chart_readings: Mapping[str, Any] | None = None,
    composites: bool = False,
    reflow_cards: bool = True,
) -> ComposeResult:
    """Собирает PPTX по плану и возвращает ComposedDeck, заголовки и отчёт.

    `fit_*` — лестница кеглей при сужении слотов из-за бокового декора (как `plan.*` в
    config/app.yaml). `extra_assets` — файлы проекта для ручных правок (`file_id` → путь);
    `media_dir` — куда выложить медиа колоды для интерфейса (имена артефактов получают
    `media_prefix`, как `<variant>/r<N>/`). `chart_readings` — чтения диаграмм-картинок по
    sha256 картинки (ChartReading или его JSON): в варианте original такие картинки
    заменяются нативными диаграммами. `composites` — там же диаграммы, собранные из фигур
    слайда (столбцы-картинки, кольца с числом в центре), становятся нативными (поиск без
    модели, см. `composite_charts`). `reflow_cards` — оставшиеся карточки однорядной сетки
    расходятся на всю ширину ряда; режим «По шаблону» передаёт False."""
    started = time.perf_counter()
    template_path = pathlib.Path(template_path)
    if not template_path.is_file():
        raise ComposeError("compose_template_missing", f"файл шаблона не найден: {template_path}")
    if not _hash_matches(profile, template_path):
        raise ComposeError(
            "compose_template_mismatch",
            "профиль построен по другому файлу шаблона: ссылки слотов недействительны",
        )
    preserve = str((plan.get("variant") or {}).get("variant_id")) == "original"
    if preserve:
        profile = original_profile(profile)
    patterns_raw = {str(p["pattern_id"]): p for p in profile.get("patterns") or []}
    try:
        prs = Presentation(str(template_path))
    except Exception as e:
        raise ComposeError("compose_template_unreadable", f"шаблон не открывается: {e}") from e
    slide_w, slide_h = int(prs.slide_width or 0), int(prs.slide_height or 0)
    stock_assets = load_stock_assets(package_dir)
    if stock_assets:
        # Фото из фотобанков, загруженные слоем design в каталог пакета (design/photos.py):
        # для вёрстки и ComposedDeck это такие же ресурсы пакета, как картинки материалов.
        own = {str(a.get("asset_id")) for a in package.get("assets") or []}
        package = {
            **package,
            "assets": [
                *(package.get("assets") or []),
                *(a for asset_id, a in stock_assets.items() if asset_id not in own),
            ],
        }
    ctx = _Context(
        plan=plan,
        profile=profile,
        package=package,
        package_dir=pathlib.Path(package_dir) if package_dir else None,
        prs=prs,
        slide_w=slide_w,
        slide_h=slide_h,
        facts={str(f["fact_id"]): f for f in package.get("facts") or []},
        datasets={str(d["dataset_id"]): d for d in package.get("datasets") or []},
        package_assets={str(a["asset_id"]): a for a in package.get("assets") or []},
        profile_assets={str(a["asset_id"]): a for a in profile.get("assets") or []},
        extra_assets={k: pathlib.Path(v) for k, v in (extra_assets or {}).items()},
        markers={str(m).strip() for m in profile.get("placeholder_markers") or []},
        language=str(plan.get("language") or "ru"),
        chart_style=charts.ChartStyle.from_profile(profile),
        table_style=tables.TableStyle.from_profile(profile),
        diagram_style=diagrams.DiagramStyle.from_profile(profile),
        fit_min_ratio=fit_min_ratio,
        fit_min_body_pt=fit_min_body_pt,
        fit_min_title_pt=fit_min_title_pt,
        preserve=str((plan.get("variant") or {}).get("variant_id")) == "original",
        reflow_cards=reflow_cards,
    )
    samples = list(prs.slides)
    needs_samples = any(
        (patterns_raw.get(str(s.get("pattern_id")), {}).get("source") or {}).get("kind")
        == "sample_slide"
        for s in plan.get("slides") or []
    )
    if not samples and needs_samples:
        # Шаблон из одних макетов (так часто выглядят корпоративные .potx) собирается
        # собственными композициями на его макетах; образцы нужны только их клонам.
        raise ComposeError("compose_template_empty", "в шаблоне нет слайдов")
    ctx.samples = samples
    timings: dict[str, int] = {}
    t0 = time.perf_counter()
    records: list[SlideRecord] = []
    new_slides: list[Any] = []
    pinfos: dict[str, Any] = {}
    ordered_slides = sorted(plan.get("slides") or [], key=lambda s: int(s.get("order", 0)))
    unchanged = preserve and len(ordered_slides) == len(samples)
    source_indices = [
        (patterns_raw.get(str(s.get("pattern_id")), {}).get("source") or {}).get("slide_index")
        for s in ordered_slides
    ]
    preserved_slides: dict[int, Any] = {}
    # Образец → его первая копия в колоде: по ней перенаправляются переходы между слайдами.
    origin: dict[Any, Any] = {}
    if preserve:
        seen: set[int] = set()
        # Clone only extra occurrences, before any original is edited. Keeping the first
        # occurrence preserves slide IDs and targets of internal navigation links.
        for index, source_index in enumerate(source_indices):
            sample_index = int(source_index or 0)
            if not 1 <= sample_index <= len(samples):
                continue
            sample = samples[sample_index - 1]
            preserved_slides[index] = (
                clone_slide(prs, sample, copy_notes=True) if sample_index in seen else sample
            )
            seen.add(sample_index)
    for index, plan_slide in enumerate(ordered_slides):
        pattern_id = str(plan_slide.get("pattern_id"))
        pattern_raw = patterns_raw.get(pattern_id)
        if pattern_raw is None:
            raise ComposeError(
                "compose_pattern_unknown", f"паттерн {pattern_id} отсутствует в профиле"
            )
        source = pattern_raw.get("source") or {}
        if source.get("kind") == "builtin":
            unchanged = False
            # Собственная композиция: слайд строится на макете шаблона из его дизайн-кода,
            # а заполняется дальше тем же путём, что и клон образца.
            clone, pattern_raw, meta = _build_builtin(ctx, pattern_id, pattern_raw, source)
            new_slides.append(clone)
            pinfos[pattern_id] = pattern_info(pattern_raw)
            records.append(
                _fill_slide(ctx, clone, plan_slide, pattern_raw, pinfos[pattern_id], None, index)
            )
            _dress_builtin(ctx, clone, meta, plan_slide)
            continue
        sample_index = int(source.get("slide_index") or 0)
        if not 1 <= sample_index <= len(samples):
            raise ComposeError(
                "compose_sample_missing",
                f"паттерн {pattern_id}: образец {sample_index} отсутствует в шаблоне",
            )
        keep = preserve and unchanged_slide(plan_slide, pattern_raw)
        unchanged = unchanged and keep and sample_index == index + 1
        # Reuse original parts: cloning rewrites relationships, slide IDs and notes.
        clone = preserved_slides[index] if preserve else clone_slide(prs, samples[sample_index - 1])
        origin.setdefault(samples[sample_index - 1].part, clone.part)
        new_slides.append(clone)
        if keep:
            records.append(
                SlideRecord(
                    slide_id=str(plan_slide["slide_id"]),
                    order=index + 1,
                    pattern_id=pattern_id,
                    source_slide_index=sample_index,
                    source_slide_part=str(source.get("pptx_slide_part") or ""),
                    layout_id=str(source.get("layout_id") or ""),
                    title=str(plan_slide.get("title") or ""),
                    static_object_ids=[str(i) for i in pattern_raw.get("static_object_ids") or []],
                )
            )
            continue
        if pattern_id not in pinfos:
            pinfos[pattern_id] = pattern_info(pattern_raw)
        layout = (
            layout_by_id(prs, str(source.get("layout_id") or ""))
            if source.get("kind") == "layout"
            else None
        )
        records.append(
            _fill_slide(ctx, clone, plan_slide, pattern_raw, pinfos[pattern_id], layout, index)
        )
        if source.get("kind") == "sample_slide":
            _grow_template_text(ctx, clone, records[-1], pinfos[pattern_id])
    timings["clone_fill_ms"] = int((time.perf_counter() - t0) * 1000)
    if not new_slides:
        raise ComposeError("compose_plan_empty", "в плане нет слайдов")
    swaps: list[chart_images.ChartSwap] = []
    if preserve and composites:
        # Готовая презентация: диаграммы из фигур слайда → нативные, до картинок — как в
        # предварительной ревизии.
        swaps = composite_charts.swap_composites(prs, slides=new_slides)
        assembled = sum(1 for s in swaps if s.status == "replaced")
        if assembled:
            unchanged = False
            ctx.count("charts_assembled", assembled)
    if preserve and chart_readings:
        # Готовая презентация: диаграммы-картинки → нативные диаграммы по готовым чтениям.
        readings = {
            str(sha): r if isinstance(r, ChartReading) else ChartReading.model_validate(r)
            for sha, r in chart_readings.items()
        }
        pictures = chart_images.swap_pictures(prs, readings, slides=new_slides)
        swaps += pictures
        replaced = sum(1 for s in pictures if s.status == "replaced")
        if replaced:
            unchanged = False
            ctx.count("charts_rebuilt", replaced)
    if preserve:
        # Готовая презентация: пустое место, которое редактор подписывает «Заголовок слайда»,
        # получает этот текст — как в предварительной ревизии, чей рендер переиспользуется.
        # Правки из чата и редактора уже записаны, заполненное не трогается.
        prompts = tx.fill_empty_placeholders(new_slides, ctx.language)
        if prompts:
            unchanged = False
            ctx.count("prompts_filled", prompts)
    t0 = time.perf_counter()
    keep_only_slides(prs, new_slides)
    if preserve:
        # keep_only_slides removes pages but does not reorder reused original parts.
        ids = {prs.part.related_part(item.rId): item for item in prs.slides._sldIdLst}
        for slide in new_slides:
            prs.slides._sldIdLst.append(ids[slide.part])
    if retarget_slide_links(prs, origin):
        unchanged = False
    # Знак шаблона снимается после отбора слайдов: он лежит на макетах, а не на слайдах, и
    # правкой слайда его не убрать (план: template_logo).
    drop_logos = str(plan.get("template_logo") or "keep") == "drop"
    logos_removed = drop_template_logos(prs, profile) if drop_logos else 0
    layouts_removed = prune_unused_layouts(prs) if prune_layouts and not preserve else 0
    if not preserve:
        update_slide_numbers(prs)
    out_pptx = pathlib.Path(out_pptx)
    out_pptx.parent.mkdir(parents=True, exist_ok=True)
    if unchanged and not drop_logos:
        if template_path.resolve() != out_pptx.resolve():
            shutil.copyfile(template_path, out_pptx)
    else:
        prs.save(str(out_pptx))
    timings["save_ms"] = int((time.perf_counter() - t0) * 1000)
    if logos_removed:
        ctx.count("logos_removed", logos_removed)
    t0 = time.perf_counter()
    integrity = check_deck(out_pptx, expected_slides=len(new_slides))
    if not integrity.ok:
        raise ComposeError(
            "compose_integrity",
            "собранный пакет не прошёл проверку: " + "; ".join(integrity.errors[:3]),
        )
    for w in integrity.warnings:
        ctx.warn("package_warning", w)
    timings["integrity_ms"] = int((time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter()
    variant = variant_id or str((plan.get("variant") or {}).get("variant_id") or "balanced")
    deck = build_composed_deck(
        out_pptx,
        plan=plan,
        profile=profile,
        package=package,
        records=records,
        job_id=job_id,
        variant_id=variant,
        revision=revision,
        pptx_artifact=pptx_artifact or out_pptx.name,
        warnings=ctx.warnings,
        composer={"name": COMPOSER_NAME, "version": COMPOSER_VERSION},
        layouts_removed=layouts_removed,
    )
    timings["composed_deck_ms"] = int((time.perf_counter() - t0) * 1000)
    media_files = 0
    if media_dir is not None:
        t0 = time.perf_counter()
        media_files = export_media(out_pptx, deck, pathlib.Path(media_dir), media_prefix)
        timings["media_ms"] = int((time.perf_counter() - t0) * 1000)
    total_ms = int((time.perf_counter() - started) * 1000)
    report: JsonDict = {
        "composer": {"name": COMPOSER_NAME, "version": COMPOSER_VERSION},
        "slides": len(new_slides),
        "counts": dict(ctx.counts),
        "layouts_removed": layouts_removed,
        "media_files": media_files,
        "file_size_bytes": out_pptx.stat().st_size,
        "timings_ms": {**timings, "total": total_ms},
        "integrity": integrity.as_dict(),
        "warnings": list(ctx.warnings),
    }
    return ComposeResult(
        pptx_path=out_pptx,
        deck=deck,
        slide_titles=[r.title for r in records],
        warnings=list(ctx.warnings),
        report=report,
        integrity=integrity,
        chart_swaps=swaps,
    )


__all__ = [
    "COMPOSER_NAME",
    "COMPOSER_VERSION",
    "ComposeError",
    "ComposeResult",
    "compose_deck",
    "sha256_of",
]
