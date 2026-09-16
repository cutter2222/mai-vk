"""Постоянные элементы: логотипы, колонтитулы, номера страниц, фон, декор, навигация.

Источники: объекты мастеров и макетов (они видны на всех слайдах данного макета) и объекты
образцов, повторяющиеся на одном месте с одинаковым видом не меньше чем на половине образцов.
Динамические поля (`a:fld` номера слайда, даты) помечаются отдельно: при смене порядка слайдов
их значение пересчитывается.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.parsing.template.geometry import ShapeInfo, normalize_text
from presentation_designer.parsing.template.package import TemplatePackage


@dataclass
class FixedElement:
    element_id: str
    kind: str  # logo | footer | page_number | background | decoration | navigation_dots | qr…
    bbox: dict[str, float]
    appears_on: str
    source_part: str
    element_ref: str
    asset_id: str | None = None
    must_not_move: bool = True
    slide_indexes: list[int] = field(default_factory=list)
    # (часть слайда, id объекта) каждого вхождения на образцах: эти объекты не становятся слотами.
    occurrences: list[tuple[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "element_id": self.element_id,
            "kind": self.kind,
            "bbox": self.bbox,
            "appears_on": self.appears_on,
            "must_not_move": self.must_not_move,
            "element_ref": self.element_ref,
            "source_part": self.source_part,
        }
        if self.asset_id:
            out["asset_id"] = self.asset_id
        return out


def _kind_of(shape: ShapeInfo, *, on_master: bool) -> str | None:
    text = normalize_text(shape.text)
    if shape.placeholder_type == "sldNum" or "slidenum" in {
        f for p in shape.paragraphs for f in p.fields
    }:
        return "page_number"
    if shape.placeholder_type in ("ftr", "dt"):
        return "footer"
    if shape.kind == "picture":
        if shape.area >= 0.85:
            return "background"
        if shape.area <= 0.03 and (
            shape.y < 0.15 or shape.y > 0.8 or shape.x > 0.85 or shape.x < 0.12
        ):
            return "logo"
        if shape.area <= 0.06 and on_master:
            return "logo"
        return "decoration" if on_master else None
    if shape.kind in ("shape", "connector"):
        if "qr" in text:
            return "qr_placeholder"
        if shape.area >= 0.85 and shape.fill_kind in ("solid", "gradient", "picture"):
            return "background"
        if shape.text:
            if shape.y > 0.85 and shape.height < 0.1:
                return "footer"
            return None
        if shape.geometry == "ellipse" and shape.area < 0.001:
            return "navigation_dots"
        return "decoration"
    if shape.kind == "text":
        if "qr" in text and len(text) < 20:
            return "qr_placeholder"
        if shape.y > 0.85 and shape.height < 0.1 and len(text) < 80:
            return "footer"
    return None


def find_fixed_elements(
    pkg: TemplatePackage, sample_indexes: set[int], asset_ids_by_sha: dict[str, str]
) -> tuple[list[FixedElement], list[dict[str, Any]]]:
    fixed: list[FixedElement] = []
    dynamic: list[dict[str, Any]] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"fixed_{counter}"

    # Мастер: всё, что не плейсхолдер содержания, видно на всех слайдах.
    for master in pkg.masters:
        for shape in master.shapes:
            if shape.kind == "group" or shape.placeholder_type in (
                "title",
                "body",
                "ctrTitle",
                "subTitle",
                "obj",
                "pic",
            ):
                continue
            kind = _kind_of(shape, on_master=True)
            if kind is None:
                continue
            fixed.append(
                FixedElement(
                    next_id(),
                    kind,
                    shape.bbox,
                    "all",
                    master.part,
                    shape.element_id,
                    asset_id=asset_ids_by_sha.get(shape.media_sha256 or ""),
                )
            )
            _dynamic_of(shape, "all", dynamic)
    # Макеты: только те, что используются образцами.
    for layout in pkg.layouts:
        if layout.sample_slide_count == 0:
            continue
        for shape in layout.shapes:
            if shape.kind == "group" or shape.placeholder_type in (
                "title",
                "body",
                "ctrTitle",
                "subTitle",
                "obj",
                "pic",
                "chart",
                "tbl",
            ):
                continue
            kind = _kind_of(shape, on_master=True)
            if kind is None:
                continue
            fixed.append(
                FixedElement(
                    next_id(),
                    kind,
                    shape.bbox,
                    f"layout:{layout.layout_id}",
                    layout.part,
                    shape.element_id,
                    asset_id=asset_ids_by_sha.get(shape.media_sha256 or ""),
                )
            )
            _dynamic_of(shape, f"layout:{layout.layout_id}", dynamic)
    # Образцы: объекты, повторяющиеся на одном месте не меньше чем на половине образцов (и ≥ 3).
    samples = [s for s in pkg.slides if s.index in sample_indexes]
    if len(samples) >= 3:
        buckets: dict[tuple[str, int, int, int, int, str], list[tuple[int, ShapeInfo]]] = (
            collections.defaultdict(list)
        )
        for slide in samples:
            for shape in slide.shapes:
                if shape.kind == "group" or shape.is_placeholder:
                    continue
                if shape.kind == "text" and not shape.text:
                    continue
                key = (
                    shape.kind,
                    round(shape.x * 50),
                    round(shape.y * 50),
                    round(shape.width * 50),
                    round(shape.height * 50),
                    shape.media_sha256 or normalize_text(shape.text)[:20]
                    if shape.kind in ("picture", "text")
                    else shape.geometry or "",
                )
                buckets[key].append((slide.index, shape))
        threshold = max(3, len(samples) // 2)
        for items in buckets.values():
            slides_hit = sorted({i for i, _ in items})
            if len(slides_hit) < threshold:
                continue
            first = items[0][1]
            kind = _kind_of(first, on_master=False)
            if kind is None:
                kind = "logo" if first.kind == "picture" else "decoration"
            if first.kind == "text" and kind == "decoration":
                continue
            if any(_same_bbox(first.bbox, f.bbox) for f in fixed):
                continue
            appears = (
                "all" if len(slides_hit) >= len(samples) * 0.9 else f"slides:{len(slides_hit)}"
            )
            fixed.append(
                FixedElement(
                    next_id(),
                    kind,
                    first.bbox,
                    appears,
                    pkg.slides[slides_hit[0] - 1].part,
                    first.element_id,
                    asset_id=asset_ids_by_sha.get(first.media_sha256 or ""),
                    slide_indexes=slides_hit,
                    occurrences=[(pkg.slides[i - 1].part, sh.element_id) for i, sh in items],
                )
            )
    # Динамические поля на самих образцах.
    for slide in samples:
        for shape in slide.shapes:
            _dynamic_of(shape, f"slide:{slide.index}", dynamic)
    dynamic = _dedupe_dynamic(dynamic)
    return fixed, dynamic


class ShapeInfoLike:
    """Минимальный объект с bbox для сравнения положения с уже найденными элементами."""

    def __init__(self, bbox: dict[str, float]) -> None:
        self.x, self.y, self.width, self.height = (
            bbox["x"],
            bbox["y"],
            bbox["width"],
            bbox["height"],
        )


def _same_bbox(a: dict[str, float], b: dict[str, float], tol: float = 0.01) -> bool:
    return all(abs(a[k] - b[k]) <= tol for k in ("x", "y", "width", "height"))


def _dynamic_of(shape: ShapeInfo, appears_on: str, out: list[dict[str, Any]]) -> None:
    kinds = {"slidenum": "slide_number", "datetime": "date"}
    fields = {f for p in shape.paragraphs for f in p.fields}
    if shape.placeholder_type == "sldNum":
        fields.add("slidenum")
    if shape.placeholder_type == "dt":
        fields.add("datetime")
    for f in fields:
        kind = kinds.get(f.split("'")[0].lower().replace("datetime1", "datetime"), None)
        if kind is None and f.lower().startswith("datetime"):
            kind = "date"
        if kind:
            out.append({"kind": kind, "appears_on": appears_on, "element_ref": shape.element_id})


def _dedupe_dynamic(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = (item["kind"], item["appears_on"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    # Если поле есть на мастере/макете, отдельные слайды не перечисляем.
    global_kinds = {
        i["kind"] for i in out if i["appears_on"] == "all" or i["appears_on"].startswith("layout:")
    }
    return [
        i for i in out if not (i["appears_on"].startswith("slide:") and i["kind"] in global_kinds)
    ][:40]
