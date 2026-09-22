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
import time
from dataclasses import dataclass, field
from typing import Any

from pptx import Presentation

from presentation_designer.generation.matching import SlotInfo, pattern_info
from presentation_designer.layout import charts, diagrams, icons, images, tables
from presentation_designer.layout import text as tx
from presentation_designer.layout.composed import SlideRecord, SlotFill, build_composed_deck
from presentation_designer.layout.integrity import IntegrityReport, check_deck
from presentation_designer.layout.media import export_media
from presentation_designer.layout.ooxml import clone_slide, keep_only_slides
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
    remove_shape,
    set_element_box,
    shape_element,
    shape_map,
)
from presentation_designer.library.build import build_slide as build_builtin_slide
from presentation_designer.library.spec import find_composition
from presentation_designer.library.tokens import DesignCode
from presentation_designer.parsing.template.geometry import walk_shapes

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]
COMPOSER_NAME = "layout_composer"
COMPOSER_VERSION = "0.2.0"
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
    if ctx.preserve:
        _keep_rest(slide, pattern_raw, pinfo, filled, record, from_layout)
        record.removed_object_ids = []
    else:
        record.removed_object_ids = _cleanup(
            ctx, slide, pattern_raw, pinfo, filled, record, slide_index
        )
    apply_overrides(ctx, slide, record, plan_slide, pinfo, slide_index)
    if record.notes:
        try:
            slide.notes_slide.notes_text_frame.text = record.notes
        except Exception:
            ctx.warn("notes_failed", f"{record.slide_id}: заметки не записаны", slide_index)
    return record


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
    if kind == "bullets":
        items = [str(it.get("text", "")) for it in block.get("items") or []]
        result = tx.fill_bullets(element, items, size_pt=size, facts=ctx.facts)
        _record_text(ctx, fill, result, block, slide_index)
        ctx.count("text_objects")
        return fill
    if kind in TEXT_KINDS:
        text = _number_text(ctx, block) if kind == "number" else str(block.get("text") or "")
        if text.strip() in ("", "—") and not slot.sample_text.strip():
            # Планировщик оставил пустой крошечный слот прочерком: содержимого нет,
            # судьбу объекта решают правила карточек.
            return None
        result = tx.fill_text(element, text, size_pt=size, facts=ctx.facts)
        if kind == "number" and "\n" not in text and _is_tiny(slot):
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
        return _apply_diagram(ctx, slide, slot, element, block, fill)
    ctx.warn(
        "block_unsupported", f"блок {kind} в слоте {slot.slot_id} не поддерживается", slide_index
    )
    return None


def _renumbered(ctx: _Context, element: Any) -> str:
    """Новый объект получает свежий id; возвращает id самого элемента."""
    own = _element_id(element)
    return ctx.fresh_ids(element)[own]


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
    result = icons.place_icon(slide, element, asset, color=icon.get("color"))
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


def _apply_diagram(
    ctx: _Context, slide: Any, slot: SlotInfo, element: Any, block: JsonDict, fill: SlotFill
) -> SlotFill | None:
    box = _slot_box(ctx, slot, element)
    remove_shape(slide, element)
    result = diagrams.draw_diagram(slide, box, block.get("diagram") or {}, ctx.diagram_style)
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
    min_pt = min_pt_for(slot.kind, body_pt=ctx.fit_min_body_pt, title_pt=ctx.fit_min_title_pt)
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
        if _is_textual(slot) or slot.kind in ("qr", "footer"):
            if slot.kind in ("qr", "footer") or (
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
    if removed:
        ctx.count("removed_objects", len(removed))
    return list(dict.fromkeys(removed))


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
    """Рамка содержит не меньше 80 % площади box."""
    ix = max(0.0, min(frame[0] + frame[2], box[0] + box[2]) - max(frame[0], box[0]))
    iy = max(0.0, min(frame[1] + frame[3], box[1] + box[3]) - max(frame[1], box[1]))
    area = box[2] * box[3]
    return area > 0 and ix * iy >= 0.8 * area


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


def _build_builtin(
    ctx: _Context, pattern_id: str, pattern_raw: JsonDict, source: JsonDict
) -> tuple[Any, JsonDict]:
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
    slide, refs, card_ids = build_builtin_slide(ctx.prs, layout, composition, ctx.design_code)
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
    return slide, patched


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
) -> ComposeResult:
    """Собирает PPTX по плану и возвращает ComposedDeck, заголовки и отчёт.

    `fit_*` — лестница кеглей при сужении слотов из-за бокового декора (как `plan.*` в
    config/app.yaml). `extra_assets` — файлы проекта для ручных правок (`file_id` → путь);
    `media_dir` — куда выложить медиа колоды для интерфейса (имена артефактов получают
    `media_prefix`, как `<variant>/r<N>/`)."""
    started = time.perf_counter()
    template_path = pathlib.Path(template_path)
    if not template_path.is_file():
        raise ComposeError("compose_template_missing", f"файл шаблона не найден: {template_path}")
    if not _hash_matches(profile, template_path):
        raise ComposeError(
            "compose_template_mismatch",
            "профиль построен по другому файлу шаблона: ссылки слотов недействительны",
        )
    patterns_raw = {str(p["pattern_id"]): p for p in profile.get("patterns") or []}
    try:
        prs = Presentation(str(template_path))
    except Exception as e:
        raise ComposeError("compose_template_unreadable", f"шаблон не открывается: {e}") from e
    slide_w, slide_h = int(prs.slide_width or 0), int(prs.slide_height or 0)
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
    )
    samples = list(prs.slides)
    if not samples:
        raise ComposeError("compose_template_empty", "в шаблоне нет слайдов")
    timings: dict[str, int] = {}
    t0 = time.perf_counter()
    records: list[SlideRecord] = []
    new_slides: list[Any] = []
    pinfos: dict[str, Any] = {}
    for index, plan_slide in enumerate(
        sorted(plan.get("slides") or [], key=lambda s: int(s.get("order", 0)))
    ):
        pattern_id = str(plan_slide.get("pattern_id"))
        pattern_raw = patterns_raw.get(pattern_id)
        if pattern_raw is None:
            raise ComposeError(
                "compose_pattern_unknown", f"паттерн {pattern_id} отсутствует в профиле"
            )
        source = pattern_raw.get("source") or {}
        if source.get("kind") == "builtin":
            # Собственная композиция: слайд строится на макете шаблона из его дизайн-кода,
            # а заполняется дальше тем же путём, что и клон образца.
            clone, pattern_raw = _build_builtin(ctx, pattern_id, pattern_raw, source)
            new_slides.append(clone)
            pinfos[pattern_id] = pattern_info(pattern_raw)
            records.append(
                _fill_slide(ctx, clone, plan_slide, pattern_raw, pinfos[pattern_id], None, index)
            )
            continue
        sample_index = int(source.get("slide_index") or 0)
        if not 1 <= sample_index <= len(samples):
            raise ComposeError(
                "compose_sample_missing",
                f"паттерн {pattern_id}: образец {sample_index} отсутствует в шаблоне",
            )
        clone = clone_slide(prs, samples[sample_index - 1])
        new_slides.append(clone)
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
    timings["clone_fill_ms"] = int((time.perf_counter() - t0) * 1000)
    if not new_slides:
        raise ComposeError("compose_plan_empty", "в плане нет слайдов")
    t0 = time.perf_counter()
    keep_only_slides(prs, new_slides)
    # Знак шаблона снимается после отбора слайдов: он лежит на макетах, а не на слайдах, и
    # правкой слайда его не убрать (план: template_logo).
    drop_logos = str(plan.get("template_logo") or "keep") == "drop"
    logos_removed = drop_template_logos(prs, profile) if drop_logos else 0
    layouts_removed = prune_unused_layouts(prs) if prune_layouts else 0
    update_slide_numbers(prs)
    out_pptx = pathlib.Path(out_pptx)
    out_pptx.parent.mkdir(parents=True, exist_ok=True)
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
    )


__all__ = [
    "COMPOSER_NAME",
    "COMPOSER_VERSION",
    "ComposeError",
    "ComposeResult",
    "compose_deck",
    "sha256_of",
]
