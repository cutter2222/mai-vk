"""ComposedDeck по сохранённому файлу: объекты, геометрия, вычисленные стили, ресурсы, связи
со слотами плана и образцами шаблона.

Документ строится не по намерениям композера, а по факту: сохранённый PPTX открывается
заново, дерево фигур обходится тем же кодом, что и при анализе шаблона (`geometry.walk_shapes`,
`styles.StyleResolver`), а записи композера (`SlideRecord`, `SlotFill`) лишь связывают
объекты со слотами, блоками плана, фактами и наборами данных. Так аудит и HTML получают
описание, которое сверяется с файлом по идентификаторам объектов и частям пакета.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import mimetypes
import pathlib
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.layout.images import read_crop
from presentation_designer.layout.shapes import shape_map
from presentation_designer.parsing.template.geometry import Paragraph, ShapeInfo
from presentation_designer.parsing.template.package import open_template
from presentation_designer.parsing.template.styles import StyleResolver, resolve_color

JsonDict = dict[str, Any]
ALIGN = {"l": "left", "ctr": "center", "r": "right", "just": "justify"}
SUPPORTED_KINDS = ["text", "picture", "table", "chart", "shape", "connector", "group"]


@dataclass
class SlotFill:
    """Что композер сделал со слотом: объект результата, источник содержимого, связи."""

    slot_id: str
    slot_kind: str
    block_kind: str
    element_id: str
    source_object_id: str | None = None
    content_source: str = "plan"
    text: str | None = None
    fact_refs: list[str] = field(default_factory=list)
    size_pt: float | None = None
    fit: JsonDict | None = None
    dataset_id: str | None = None
    asset_id: str | None = None
    built: str | None = None
    table: JsonDict | None = None
    chart: JsonDict | None = None
    picture: JsonDict | None = None
    diagram: JsonDict | None = None


@dataclass
class SlideRecord:
    slide_id: str
    order: int
    pattern_id: str
    source_slide_index: int
    source_slide_part: str
    layout_id: str
    title: str
    notes: str = ""
    static_object_ids: list[str] = field(default_factory=list)
    fills: list[SlotFill] = field(default_factory=list)
    removed_object_ids: list[str] = field(default_factory=list)
    # Ручные правки (этап 22): применённые по объектам, отброшенные с причиной, описание
    # картинок, заменённых у объектов без слота, правка фона слайда.
    overrides_applied: dict[str, list[JsonDict]] = field(default_factory=dict)
    overrides_dropped: list[JsonDict] = field(default_factory=list)
    user_pictures: dict[str, JsonDict] = field(default_factory=dict)
    background_override: JsonDict | None = None


def _kind(info: ShapeInfo) -> str:
    if info.kind == "group":
        return "group"
    if info.kind == "picture":
        return "picture"
    if info.kind == "table":
        return "table"
    if info.kind == "chart":
        return "chart"
    if info.kind == "connector":
        return "connector"
    if info.kind in ("graphic", "smartart", "unknown"):
        return "other"
    if info.has_text_frame and info.text.strip():
        return "text"
    if info.is_placeholder:
        return "placeholder_empty"
    return "text" if info.kind == "text" else "shape"


def _paragraph_entry(info: ShapeInfo, para: Paragraph, resolver: StyleResolver | None) -> JsonDict:
    entry: JsonDict = {
        "text": para.text,
        "level": para.level,
        "bullet": para.bullet in ("char", "number", "picture"),
    }
    if para.align in ALIGN:
        entry["align"] = ALIGN[para.align]
    if resolver is not None:
        run = next((r for r in para.runs if r.get("text", "").strip()), None)
        try:
            entry["style"] = resolver.resolve(info, para, run).computed_style()
        except Exception:
            pass
    return entry


def _fill_entry(info: ShapeInfo, theme: Any) -> JsonDict | None:
    if info.fill_kind in (None, "inherit"):
        return {"kind": "inherited"}
    if info.fill_kind == "none":
        return {"kind": "none"}
    if info.fill_kind == "picture":
        return {"kind": "image"}
    if info.fill_kind == "gradient":
        return {"kind": "gradient"}
    out: JsonDict = {"kind": "solid"}
    if info.fill_hex and info.fill_hex.startswith("#"):
        out["color"] = info.fill_hex
        out["source"] = {"level": "shape"}
    elif info.element is not None:
        sp_pr = info.element.find(
            "{http://schemas.openxmlformats.org/presentationml/2006/main}spPr"
        )
        solid = (
            sp_pr.find("{http://schemas.openxmlformats.org/drawingml/2006/main}solidFill")
            if sp_pr is not None
            else None
        )
        resolved = resolve_color(solid, theme) if solid is not None else None
        if resolved is not None:
            out["color"] = resolved.hex
            source: JsonDict = {"level": "theme" if resolved.theme_ref else "shape"}
            if resolved.theme_ref:
                source["theme_ref"] = resolved.theme_ref
            if resolved.modifiers:
                source["modifiers"] = resolved.modifiers
            out["source"] = source
    return out


def _line_entry(info: ShapeInfo, theme: Any) -> JsonDict | None:
    """Контур фигуры: цвет и толщина.

    Цвет ищется там же, где его ищет PowerPoint: сначала у самой фигуры, потом в стиле темы
    (`p:style/a:lnRef`). Толщина — своя, иначе из списка линий темы по тому же номеру. Без
    этого рамки карточек шаблона терялись: в файле у них пусто, всё живёт в стиле.
    """
    color: str | None = None
    if info.line_hex and info.line_hex.startswith("#"):
        color = info.line_hex
    elif info.line_hex and info.line_hex.startswith("scheme:") and info.element is not None:
        sp_pr = info.element.find(
            "{http://schemas.openxmlformats.org/presentationml/2006/main}spPr"
        )
        ln = (
            sp_pr.find("{http://schemas.openxmlformats.org/drawingml/2006/main}ln")
            if sp_pr is not None
            else None
        )
        solid = (
            ln.find("{http://schemas.openxmlformats.org/drawingml/2006/main}solidFill")
            if ln is not None
            else None
        )
        resolved = resolve_color(solid, theme) if solid is not None else None
        color = resolved.hex if resolved else None
    if color is None and info.line_ref_element is not None:
        resolved = resolve_color(info.line_ref_element, theme)
        color = resolved.hex if resolved else None
    if color is None:
        return None
    out: JsonDict = {"color": color}
    width = info.line_width_pt
    if width is None and info.line_ref_idx and theme is not None:
        widths = getattr(theme, "line_widths_pt", []) or []
        if 0 < info.line_ref_idx <= len(widths):
            width = widths[info.line_ref_idx - 1]
    if width:
        out["width_pt"] = width
    return out


def _content_type(partname: str) -> str:
    guessed, _ = mimetypes.guess_type(partname)
    return guessed or "application/octet-stream"


def build_composed_deck(
    pptx_path: pathlib.Path,
    *,
    plan: JsonDict,
    profile: JsonDict,
    package: JsonDict,
    records: list[SlideRecord],
    job_id: str,
    variant_id: str,
    revision: int,
    pptx_artifact: str,
    warnings: list[JsonDict],
    composer: JsonDict,
    layouts_removed: int = 0,
) -> JsonDict:
    pkg = open_template(pathlib.Path(pptx_path))
    if len(pkg.slides) != len(records):
        raise ValueError(f"в файле {len(pkg.slides)} слайдов, записей композера {len(records)}")
    profile_assets_by_sha = {
        str(a.get("sha256", "")).split(":", 1)[-1]: a for a in profile.get("assets") or []
    }
    package_assets_by_sha = {
        str(a.get("sha256", "")).split(":", 1)[-1]: a for a in package.get("assets") or []
    }
    fixed_refs = {
        (str(f.get("source_part", "")), str(f.get("element_ref", "")))
        for f in profile.get("fixed_elements") or []
    }
    plan_slides = {str(s.get("slide_id")): s for s in plan.get("slides") or []}
    assets: dict[str, JsonDict] = {}
    slides_out: list[JsonDict] = []
    fallback_elements: list[JsonDict] = []
    stats = {
        "slides": len(records),
        "objects": 0,
        "text_objects": 0,
        "pictures": 0,
        "tables": 0,
        "charts": 0,
        "diagrams": 0,
        "removed_objects": 0,
        "layouts_kept": len(pkg.layouts),
        "layouts_removed": layouts_removed,
        "file_size_bytes": pathlib.Path(pptx_path).stat().st_size,
    }
    for index, (slide_info, record) in enumerate(zip(pkg.slides, records, strict=True)):
        master = pkg.master(slide_info.master_id)
        layout = pkg.layout(slide_info.layout_id)
        resolver = StyleResolver(
            master.theme if master else None,
            master.element if master else None,
            layout.element if layout else None,
            master_part=master.part if master else "",
            layout_part=layout.part if layout else "",
        )
        theme = master.theme if master else None
        fills_by_id = {f.element_id: f for f in record.fills}
        diagram_nodes = {
            node: f for f in record.fills if f.diagram for node in f.diagram.get("node_ids", [])
        }
        proxies = shape_map(slide_info.slide) if slide_info.slide is not None else {}
        objects: list[JsonDict] = []
        for info in slide_info.shapes:
            kind = _kind(info)
            fill = fills_by_id.get(info.element_id)
            obj: JsonDict = {
                "object_id": info.element_id,
                "name": info.name,
                "kind": kind,
                "bbox": info.bbox,
                "z_order": info.z_order,
            }
            if abs(info.rotation_deg) > 0.01:
                obj["rotation_deg"] = round(info.rotation_deg, 2)
            if info.geometry:
                obj["geometry"] = str(info.geometry)
            if info.geometry_adjust is not None:
                obj["geometry_adjust"] = info.geometry_adjust
            if info.group_path:
                obj["group_path"] = list(info.group_path)
            user_ops = record.overrides_applied.get(info.element_id) or []
            if fill is not None:
                obj["slot_id"] = fill.slot_id
                obj["slot_kind"] = fill.slot_kind
                obj["block_kind"] = fill.block_kind
                obj["content_source"] = fill.content_source
                obj["role"] = "content"
                if fill.source_object_id:
                    obj["source_object_id"] = fill.source_object_id
                if fill.fit:
                    obj["fit"] = fill.fit
            elif info.element_id in diagram_nodes:
                parent = diagram_nodes[info.element_id]
                obj["slot_id"] = parent.slot_id
                obj["slot_kind"] = parent.slot_kind
                obj["block_kind"] = parent.block_kind
                obj["content_source"] = "generated"
                obj["role"] = "content"
            elif any(o.get("op") == "add_text" for o in user_ops):
                # Своя надпись из редактора: слота у неё нет, но и к образцу она не относится —
                # иначе аудит счёл бы её статикой шаблона и пропустил бы текст пользователя.
                obj["content_source"] = "user"
                obj["role"] = "content"
            else:
                obj["source_object_id"] = info.element_id
                obj["content_source"] = "template"
                is_fixed = info.element_id in record.static_object_ids or (
                    (record.source_slide_part, info.element_id) in fixed_refs
                )
                if kind in ("picture", "shape") and info.area >= 0.85:
                    obj["role"] = "background"
                elif is_fixed or kind in ("text", "placeholder_empty", "table", "chart"):
                    obj["role"] = "fixed"
                else:
                    obj["role"] = "decoration"
            if user_ops:
                obj["user_overrides"] = [copy.deepcopy(o) for o in user_ops]
                if any(o.get("op") in ("text", "picture") for o in user_ops):
                    obj["content_source"] = "user"
            if kind in ("text", "placeholder_empty") or (info.has_text_frame and info.text):
                text: JsonDict = {"plain": info.text}
                paragraphs = [
                    _paragraph_entry(info, p, resolver) for p in info.paragraphs if p.text.strip()
                ] or [_paragraph_entry(info, p, resolver) for p in info.paragraphs[:1]]
                text["paragraphs"] = paragraphs
                first_style = next((p.get("style") for p in paragraphs if p.get("style")), None)
                if first_style:
                    text["computed_style"] = first_style
                if info.insets_emu and pkg.width_emu and pkg.height_emu:
                    left, top, right, bottom = info.insets_emu
                    text["insets"] = {
                        "left": round(left / pkg.width_emu, 4),
                        "top": round(top / pkg.height_emu, 4),
                        "right": round(right / pkg.width_emu, 4),
                        "bottom": round(bottom / pkg.height_emu, 4),
                    }
                if info.autofit:
                    text["autofit"] = info.autofit
                # Куда прижат текст в рамке: без этого подпись карточки, выровненная в
                # PowerPoint по центру, на холсте и в HTML стояла бы у верхнего края.
                anchor = {"t": "top", "ctr": "middle", "b": "bottom"}.get(info.anchor or "")
                if anchor:
                    text["anchor"] = anchor
                if fill is not None and fill.fact_refs:
                    text["fact_refs"] = list(fill.fact_refs)
                obj["text"] = text
                if kind == "text":
                    stats["text_objects"] += 1
            if kind == "picture":
                sha = info.media_sha256 or ""
                asset_id = _asset_id(sha, info, profile_assets_by_sha, package_assets_by_sha)
                if fill is not None and fill.asset_id and asset_id.startswith("media_"):
                    # Перекрашенная копия иконки шаблона: своя часть, логический ресурс тот же.
                    asset_id = f"{fill.asset_id}:recolored"
                picture: JsonDict = {"asset_id": asset_id}
                crop = read_crop(info.element) if info.element is not None else None
                if crop:
                    picture["crop"] = crop
                if info.media_blob:
                    width, height = _image_size(info.media_blob)
                    if width and height:
                        picture["natural_width_px"] = width
                        picture["natural_height_px"] = height
                user_picture = record.user_pictures.get(info.element_id)
                if fill is not None and fill.picture:
                    for key in ("fit", "origin", "recolored"):
                        if fill.picture.get(key) is not None:
                            picture[key] = fill.picture[key]
                elif user_picture:
                    for key in ("fit", "origin", "recolored"):
                        if user_picture.get(key) is not None:
                            picture[key] = user_picture[key]
                    if user_picture.get("asset_id") and asset_id.startswith("media_"):
                        asset_id = str(user_picture["asset_id"]) + (
                            ":recolored" if user_picture.get("recolored") else ""
                        )
                        picture["asset_id"] = asset_id
                else:
                    picture["origin"] = "template"
                    picture["fit"] = "as_is"
                obj["picture"] = picture
                if info.media_part and sha:
                    assets.setdefault(
                        asset_id,
                        {
                            "asset_id": asset_id,
                            "media_path": info.media_part.lstrip("/"),
                            "sha256": sha,
                            "content_type": _content_type(info.media_part),
                            "origin": picture.get("origin", "template"),
                            "shared_with_template": sha in profile_assets_by_sha,
                        },
                    )
                stats["pictures"] += 1
            if kind == "table":
                table: JsonDict = {
                    "rows": info.table_size[0] if info.table_size else 0,
                    "cols": info.table_size[1] if info.table_size else 0,
                    "header_row": True,
                }
                if fill is not None and fill.table:
                    table.update({k: v for k, v in fill.table.items() if k != "header_row"})
                    table["header_row"] = bool(fill.table.get("header_row", True))
                if fill is not None and fill.dataset_id:
                    table["dataset_id"] = fill.dataset_id
                obj["table"] = table
                stats["tables"] += 1
            if kind == "chart":
                chart: JsonDict = {}
                proxy = proxies.get(info.element_id)
                if proxy is not None and getattr(proxy, "has_chart", False):
                    chart["chart_part"] = str(proxy.chart.part.partname).lstrip("/")
                    from presentation_designer.layout.charts import describe_chart

                    chart.update(describe_chart(proxy.chart))
                if fill is not None and fill.chart:
                    for key in ("dataset_id", "categories_count", "built", "units"):
                        if fill.chart.get(key) is not None:
                            chart[key] = fill.chart[key]
                obj["chart"] = chart
                stats["charts"] += 1
            if fill is not None and fill.diagram and kind == "group":
                obj["diagram"] = dict(fill.diagram)
                stats["diagrams"] += 1
            fill_entry = _fill_entry(info, theme) if kind in ("shape", "text") else None
            if fill_entry and fill_entry.get("kind") != "inherited":
                obj["fill"] = fill_entry
            line_kinds = ("shape", "connector", "text")
            line_entry = _line_entry(info, theme) if kind in line_kinds else None
            if line_entry:
                obj["line"] = line_entry
            if kind == "other":
                fallback_elements.append(
                    {
                        "slide_id": record.slide_id,
                        "object_id": info.element_id,
                        "reason": (
                            f"объект {info.graphic_uri or info.kind} "
                            "не поддерживается HTML-экспортом"
                        ),
                        "fallback": "raster_from_render",
                    }
                )
            objects.append(obj)
        stats["objects"] += len(objects)
        stats["removed_objects"] += len(record.removed_object_ids)
        slide_out: JsonDict = {
            "slide_id": record.slide_id,
            "index": index,
            "pptx_slide_part": slide_info.part,
            "layout_id": slide_info.layout_id or record.layout_id,
            "pattern_id": record.pattern_id,
            "source_slide_index": record.source_slide_index,
            "source_slide_part": record.source_slide_part,
            "title": record.title,
            "background": _background(
                slide_info,
                assets,
                profile_assets_by_sha,
                record.background_override,
                package_assets_by_sha,
            ),
            "objects": objects,
            "removed_object_ids": list(record.removed_object_ids),
        }
        if record.notes:
            slide_out["notes"] = record.notes
        plan_overrides = (plan_slides.get(record.slide_id) or {}).get("overrides") or []
        if plan_overrides or record.overrides_dropped:
            slide_out["overrides"] = copy.deepcopy(plan_overrides)
            slide_out["overrides_dropped"] = copy.deepcopy(record.overrides_dropped)
        slides_out.append(slide_out)
    fonts = [
        {
            "family": str(f.get("family")),
            **(
                {"available_in_renderer": bool(f["available_in_renderer"])}
                if f.get("available_in_renderer") is not None
                else {}
            ),
            **({"fallback": str(f["fallback"])} if f.get("fallback") else {}),
            "embedded": bool(f.get("embedded", False)),
        }
        for f in ((profile.get("design_tokens") or {}).get("typography") or {}).get("fonts") or []
    ]
    return {
        "schema_version": "1.3",
        "deck_id": f"deck_{job_id}_{variant_id}_r{revision}",
        "job_id": job_id,
        "variant_id": variant_id,
        "revision": revision,
        "plan_id": str(plan.get("plan_id")),
        "template_id": str(profile.get("template_id")),
        # Знак шаблона: drop — логотипы сняты с макетов и мастеров при сборке (план template_logo).
        "template_logo": str(plan.get("template_logo") or "keep"),
        "pptx_hash": "sha256:" + _sha256(pathlib.Path(pptx_path)),
        "pptx_artifact": pptx_artifact,
        "composer": composer,
        "created_at": now_iso(),
        "slide_size": {
            "width_emu": pkg.width_emu,
            "height_emu": pkg.height_emu,
            "aspect_ratio": pkg.aspect_ratio,
        },
        "fonts": fonts,
        "stats": stats,
        "slides": slides_out,
        "assets": sorted(assets.values(), key=lambda a: a["asset_id"]),
        "html_support": {
            "full_native": not fallback_elements,
            "supported_kinds": SUPPORTED_KINDS,
            "fallback_elements": fallback_elements,
        },
        "warnings": list(warnings),
    }


def _asset_id(
    sha: str,
    info: ShapeInfo,
    profile_assets: dict[str, JsonDict],
    package_assets: dict[str, JsonDict],
) -> str:
    if sha in package_assets:
        return str(package_assets[sha]["asset_id"])
    if sha in profile_assets:
        return str(profile_assets[sha]["asset_id"])
    if sha:
        return f"media_{sha[:12]}"
    return f"media_{info.element_id}"


def _image_size(blob: bytes) -> tuple[int | None, int | None]:
    from presentation_designer.layout.images import image_size

    return image_size(blob)


def _background(
    slide_info: Any,
    assets: dict[str, JsonDict] | None = None,
    profile_assets_by_sha: dict[str, JsonDict] | None = None,
    override: JsonDict | None = None,
    package_assets_by_sha: dict[str, JsonDict] | None = None,
) -> JsonDict:
    """Фон слайда по XML; фон-картинка регистрируется в ресурсах колоды. `override` — правка
    фона из плана, по ней определяется происхождение картинки (шаблон или содержание)."""
    from presentation_designer.layout import background

    part = getattr(getattr(slide_info, "slide", None), "part", None)
    info = background.read(slide_info.element, part)
    out: JsonDict = {"kind": str(info.get("kind", "inherited"))}
    if info.get("color"):
        out["color"] = str(info["color"])
    sha = str(info.get("sha256") or "")
    media_part = str(info.get("media_part") or "")
    if sha and media_part:
        known = (profile_assets_by_sha or {}).get(sha)
        from_package = (package_assets_by_sha or {}).get(sha)
        source = ((override or {}).get("background") or {}).get("source") or {}
        source_kind = str(source.get("kind") or "")
        if from_package is not None:
            asset_id = str(from_package["asset_id"])
        elif known is not None:
            asset_id = str(known["asset_id"])
        else:
            asset_id = f"media_{sha[:12]}"
        out["asset_id"] = asset_id
        if assets is not None:
            assets.setdefault(
                asset_id,
                {
                    "asset_id": asset_id,
                    "media_path": media_part,
                    "sha256": sha,
                    "content_type": _content_type(media_part),
                    "origin": (
                        "content"
                        if from_package is not None or source_kind in ("package", "file")
                        else "template"
                    ),
                    "shared_with_template": known is not None,
                },
            )
    return out


def now_iso() -> str:
    now = datetime.datetime.now(datetime.UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["SUPPORTED_KINDS", "SlideRecord", "SlotFill", "build_composed_deck"]
