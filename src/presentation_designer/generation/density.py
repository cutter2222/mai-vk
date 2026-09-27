"""Conservative, render-independent content occupancy (not a visual quality score).

Text contributes its occupied line height, not its entire placeholder. Overlapping
rectangles count once; titles, icons and generated filler never make an empty body full.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

from presentation_designer.generation import capacity
from presentation_designer.generation.matching import PatternInfo
from presentation_designer.shared.slide_text import plain

Rect = tuple[float, float, float, float]
TEXT = {"title", "body", "bullets", "subtitle", "label", "caption", "number"}
VISUAL = {"image", "chart", "table", "diagram"}


def union_area(rects: list[Rect]) -> float:
    """Area of rectangles clipped to the slide; overlapping slots are not summed."""
    boxes = [
        (max(0.0, x), max(0.0, y), min(1.0, x + w), min(1.0, y + h))
        for x, y, w, h in rects
        if w > 0 and h > 0
    ]
    boxes = [(x, y, r, b) for x, y, r, b in boxes if r > x and b > y]
    xs = sorted({v for x, _, r, _ in boxes for v in (x, r)})
    area = 0.0
    for left, right in pairwise(xs):
        intervals = sorted((y, b) for x, y, r, b in boxes if x < right and r > left)
        end = -1.0
        height = 0.0
        for top, bottom in intervals:
            height += max(0.0, bottom - max(top, end))
            end = max(end, bottom)
        area += (right - left) * height
    return area


def assess(
    pattern: PatternInfo,
    blocks: list[dict[str, Any]],
    facts: dict[str, Any],
    slide_w: int,
    slide_h: int,
    *,
    kind: str = "content",
    visual: str = "text",
    filler_slots: set[str] | None = None,
) -> dict[str, Any]:
    slots = {
        sid: s
        for sid, s in pattern.slots.items()
        if s is not pattern.title and s.kind in TEXT | VISUAL
    }
    available = union_area([s.bbox for s in slots.values()])
    occupied: list[Rect] = []
    text_units = visuals = chars = 0
    used_lines = total_lines = 0
    unknown = False
    for block in blocks:
        sid, block_kind = block.get("slot_id"), block.get("kind")
        if sid not in slots or sid in (filler_slots or set()):
            continue
        slot = slots[sid]
        if block_kind in VISUAL and block.get(str(block_kind)):
            occupied.append(slot.bbox)
            visuals += 1
        elif block_kind in TEXT:
            raw = (
                [it.get("text", "") for it in block.get("items", [])]
                if block_kind == "bullets"
                else [str(block.get("text") or "")]
            )
            if block_kind == "number" and block.get("number"):
                fact = facts.get(block["number"].get("fact_id"), {})
                raw = [capacity.fact_text(fact)]
            texts = [capacity.substitute_facts(t, facts) for t in raw if t.strip()]
            if not texts:
                continue
            chars += sum(len(plain(t)) for t in texts)
            text_units += len(texts)
            m = capacity.measure(
                texts, slot, slide_w, slide_h, size_pt=(block.get("fit") or {}).get("size_pt")
            )
            if m.max_lines <= 0 or m.method == "none":
                unknown = True
                continue
            used_lines += m.lines
            total_lines += m.max_lines
            x, y, w, h = slot.bbox
            occupied.append((x, y, w, h * min(1.0, m.lines / m.max_lines)))
    area = union_area(occupied)
    ratio = min(1.0, area / available) if available else 0.0
    applicable = kind == "content" and visual != "quote"
    empty = text_units == 0 and visuals == 0
    # A substantial paragraph or a meaningful figure can intentionally stand alone.
    sparse = applicable and (
        empty
        or (
            not unknown
            and available > 0
            and visuals == 0
            and text_units <= 2
            and chars < 240
            and ratio < 0.28
        )
    )
    return {
        "method": "slot_line_occupancy_v1",
        "applicable": applicable,
        "body_area": round(available, 3),
        "occupied_area": round(area, 3),
        "body_fill_ratio": round(ratio, 3),
        "text_line_ratio": round(used_lines / total_lines, 3) if total_lines else None,
        "text_units": text_units,
        "visual_blocks": visuals,
        "text_chars": chars,
        "measurement_incomplete": unknown,
        "underfilled": sparse,
    }
