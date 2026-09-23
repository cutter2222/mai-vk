"""Схемы из нативных фигур и соединителей: процесс, цикл, пирамида, иерархия, матрица,
воронка, таймлайн, диаграмма Венна.

Компоновщики детерминированные: размеры узлов зависят от числа элементов и слота, текст
внутри узла переносится по словам, кегль — из профиля (роль body/caption). Цвета — акценты
темы шаблона, текст на заливке — светлый. Все узлы одной схемы собираются в группу, чтобы
в PowerPoint схема двигалась целиком; это редактируемые фигуры, но не объект SmartArt
(FRAMEWORKS.md §7), поэтому SmartArt образца удаляется вместе со своими частями.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Pt

from presentation_designer.layout.charts import DEFAULT_ACCENTS, luminance
from presentation_designer.layout.ooxml import NS_A
from presentation_designer.shared import text_metrics

KINDS = ("process", "cycle", "pyramid", "hierarchy", "matrix", "funnel", "timeline", "venn")


@dataclass
class DiagramStyle:
    font_family: str | None = None
    font_size_pt: float = 14.0
    sub_size_pt: float = 11.0
    text_color: str = "#000000"
    on_fill_color: str = "#FFFFFF"
    accents: list[str] = field(default_factory=lambda: list(DEFAULT_ACCENTS))
    line_color: str = "#8C8C8C"

    @classmethod
    def from_profile(cls, profile: dict[str, Any]) -> DiagramStyle:
        tokens = profile.get("design_tokens") or {}
        typography = tokens.get("typography") or {}
        fonts = typography.get("fonts") or []
        family = next(
            (f.get("family") for f in fonts if "body" in (f.get("roles") or [])), None
        ) or (fonts[0].get("family") if fonts else None)
        scale = typography.get("scale") or []
        sizes = sorted({float(s["size_pt"]) for s in scale if s.get("role") == "body"})
        size = next((s for s in sizes if s >= 14), sizes[-1] if sizes else 14.0)
        colors = tokens.get("colors") or {}
        theme = colors.get("theme") or {}
        palette = colors.get("palette") or []
        accents = [
            theme[k]
            for k in sorted(theme)
            if k.startswith("accent") and theme.get(k) and luminance(theme[k]) < 0.85
        ]
        primary = next((e["hex"] for e in palette if e.get("role") == "primary"), None)
        if primary and primary in accents:
            accents.remove(primary)
        if primary:
            accents.insert(0, primary)
        text = next((e["hex"] for e in palette if e.get("role") == "text"), None)
        return cls(
            font_family=family,
            font_size_pt=max(18.0, min(size, 24.0)),
            sub_size_pt=max(16.0, min(size, 24.0) - 3),
            text_color=text or theme.get("dk1") or "#000000",
            accents=accents or list(DEFAULT_ACCENTS),
        )


@dataclass
class DiagramResult:
    group_id: str
    node_ids: list[str]
    kind: str


def _rgb(hex_color: str) -> Any:
    return RGBColor.from_string(hex_color.lstrip("#"))  # type: ignore[no-untyped-call]


def _text(
    shape: Any,
    text: str,
    sub: str | None,
    style: DiagramStyle,
    *,
    color: str,
    align: Any = PP_ALIGN.CENTER,
    size_pt: float | None = None,
) -> None:
    tf = shape.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = tf.margin_right = Emu(45720)
    tf.margin_top = tf.margin_bottom = Emu(27432)
    tf.text = text
    paragraphs = list(tf.paragraphs)
    paragraphs[0].alignment = align
    for run in paragraphs[0].runs:
        run.font.size = Pt(size_pt or style.font_size_pt)
        run.font.color.rgb = _rgb(color)
        if style.font_family:
            run.font.name = style.font_family
    if sub:
        p = tf.add_paragraph()
        p.alignment = align
        run = p.add_run()
        run.text = sub
        run.font.size = Pt(style.sub_size_pt)
        run.font.color.rgb = _rgb(color)
        if style.font_family:
            run.font.name = style.font_family


def _node(
    shapes: Any,
    kind: Any,
    box: tuple[int, int, int, int],
    fill: str | None,
    style: DiagramStyle,
) -> Any:
    x, y, cx, cy = box
    shape = shapes.add_shape(kind, Emu(x), Emu(y), Emu(cx), Emu(cy))
    if fill:
        shape.fill.solid()
        shape.fill.fore_color.rgb = _rgb(fill)
        shape.line.fill.background()
    else:
        shape.fill.background()
        shape.line.color.rgb = _rgb(style.line_color)
        shape.line.width = Pt(1.25)
    shape.shadow.inherit = False
    return shape


def _connector(
    shapes: Any, start: tuple[int, int], end: tuple[int, int], style: DiagramStyle, arrow: bool
) -> Any:
    line = shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Emu(start[0]), Emu(start[1]), Emu(end[0]), Emu(end[1])
    )
    line.line.color.rgb = _rgb(style.line_color)
    line.line.width = Pt(1.5)
    if arrow:
        ln = line.line._get_or_add_ln()
        tail = etree.SubElement(ln, f"{{{NS_A}}}tailEnd")
        tail.set("type", "triangle")
        tail.set("w", "med")
        tail.set("len", "med")
    return line


def _on_fill(style: DiagramStyle, fill: str) -> str:
    return style.on_fill_color if luminance(fill) < 0.6 else style.text_color


def _items(block: dict[str, Any]) -> list[tuple[str, str | None]]:
    out: list[tuple[str, str | None]] = []
    for it in block.get("items") or []:
        text = str(it.get("text", "")).strip()
        sub = str(it["sub"]).strip() if it.get("sub") else None
        if text:
            out.append((text, sub))
    return out or [("—", None)]


# ---------- компоновщики ----------


def _process(
    shapes: Any,
    box: tuple[int, int, int, int],
    items: list[Any],
    style: DiagramStyle,
    vertical: bool,
) -> list[Any]:
    x, y, cx, cy = box
    n = len(items)
    nodes = []
    if vertical:
        gap = max(cy // 40, 60000)
        h = max((cy - gap * (n - 1)) // n, 200000)
        for i, (text, sub) in enumerate(items):
            fill = style.accents[i % len(style.accents)]
            node = _node(
                shapes, MSO_SHAPE.ROUNDED_RECTANGLE, (x, y + i * (h + gap), cx, h), fill, style
            )
            _text(node, text, sub, style, color=_on_fill(style, fill))
            nodes.append(node)
            if i > 0:
                _connector(
                    shapes,
                    (x + cx // 2, y + i * (h + gap) - gap),
                    (x + cx // 2, y + i * (h + gap)),
                    style,
                    True,
                )
        return nodes
    gap = max(cx // 24, 150000)
    w = max((cx - gap * (n - 1)) // n, 300000)
    h = min(cy, max(cy // 2, 700000)) if n <= 4 else min(cy, max(cy * 2 // 3, 700000))
    top = y + (cy - h) // 2
    for i, (text, sub) in enumerate(items):
        fill = style.accents[i % len(style.accents)]
        left = x + i * (w + gap)
        node = _node(shapes, MSO_SHAPE.ROUNDED_RECTANGLE, (left, top, w, h), fill, style)
        _text(node, text, sub, style, color=_on_fill(style, fill))
        nodes.append(node)
        if i > 0:
            _connector(
                shapes,
                (left - gap + 20000, top + h // 2),
                (left - 20000, top + h // 2),
                style,
                True,
            )
    return nodes


def _cycle(
    shapes: Any, box: tuple[int, int, int, int], items: list[Any], style: DiagramStyle
) -> list[Any]:
    x, y, cx, cy = box
    n = len(items)
    radius = min(cx, cy) // 2
    node_size = max(min(radius * 0.9, cx // 3), 500000) if n > 1 else radius * 2
    node_size = int(min(node_size, radius * 1.1))
    center = (x + cx // 2, y + cy // 2)
    orbit = max(radius - node_size // 2, node_size // 2)
    nodes = []
    centers = []
    for i in range(n):
        angle = -math.pi / 2 + 2 * math.pi * i / n
        px = center[0] + orbit * math.cos(angle)
        py = center[1] + orbit * math.sin(angle)
        centers.append((int(px), int(py)))
    for i, (text, sub) in enumerate(items):
        fill = style.accents[i % len(style.accents)]
        px, py = centers[i]
        node = _node(
            shapes,
            MSO_SHAPE.OVAL,
            (px - node_size // 2, py - node_size // 2, node_size, node_size),
            fill,
            style,
        )
        _text(node, text, sub, style, color=_on_fill(style, fill), size_pt=style.font_size_pt - 1)
        nodes.append(node)
    if n > 1:
        for i in range(n):
            a, b = centers[i], centers[(i + 1) % n]
            dx, dy = b[0] - a[0], b[1] - a[1]
            dist = math.hypot(dx, dy) or 1
            shrink = node_size / 2 + 20000
            start = (int(a[0] + dx / dist * shrink), int(a[1] + dy / dist * shrink))
            end = (int(b[0] - dx / dist * shrink), int(b[1] - dy / dist * shrink))
            if dist > 2 * shrink:
                _connector(shapes, start, end, style, True)
    return nodes


def _stack(
    shapes: Any,
    box: tuple[int, int, int, int],
    items: list[Any],
    style: DiagramStyle,
    *,
    narrow_top: bool,
) -> list[Any]:
    """Пирамида (уже сверху) и воронка (уже снизу): ярусы-трапеции одинаковой высоты."""
    x, y, cx, cy = box
    n = len(items)
    gap = max(cy // 60, 30000)
    h = max((cy - gap * (n - 1)) // n, 200000)
    nodes = []
    for i, (text, sub) in enumerate(items):
        level = i if narrow_top else n - 1 - i
        width = int(cx * (0.45 + 0.55 * (level + 1) / n))
        left = x + (cx - width) // 2
        fill = style.accents[i % len(style.accents)]
        node = _node(shapes, MSO_SHAPE.RECTANGLE, (left, y + i * (h + gap), width, h), fill, style)
        _text(node, text, sub, style, color=_on_fill(style, fill))
        nodes.append(node)
    return nodes


def _hierarchy(
    shapes: Any, box: tuple[int, int, int, int], items: list[Any], style: DiagramStyle
) -> list[Any]:
    x, y, cx, cy = box
    root, children = items[0], items[1:]
    node_h = min(max(cy // 3, 400000), cy // 2 if children else cy)
    root_w = min(cx, max(cx // 3, 1500000))
    root_box = (x + (cx - root_w) // 2, y, root_w, node_h)
    fill = style.accents[0]
    root_node = _node(shapes, MSO_SHAPE.ROUNDED_RECTANGLE, root_box, fill, style)
    _text(root_node, root[0], root[1], style, color=_on_fill(style, fill))
    nodes = [root_node]
    if not children:
        return nodes
    n = len(children)
    gap = max(cx // 60, 60000)
    w = max((cx - gap * (n - 1)) // n, 300000)
    top = y + cy - node_h
    bus_y = y + node_h + (top - y - node_h) // 2
    _connector(shapes, (x + cx // 2, y + node_h), (x + cx // 2, bus_y), style, False)
    for i, (text, sub) in enumerate(children):
        cx_i = x + i * (w + gap) + w // 2
        fill = style.accents[(i + 1) % len(style.accents)]
        node = _node(
            shapes, MSO_SHAPE.ROUNDED_RECTANGLE, (x + i * (w + gap), top, w, node_h), fill, style
        )
        _text(node, text, sub, style, color=_on_fill(style, fill))
        nodes.append(node)
        _connector(shapes, (cx_i, bus_y), (cx_i, top), style, False)
    if n > 1:
        _connector(
            shapes, (x + w // 2, bus_y), (x + (n - 1) * (w + gap) + w // 2, bus_y), style, False
        )
    return nodes


def _matrix(
    shapes: Any, box: tuple[int, int, int, int], items: list[Any], style: DiagramStyle
) -> list[Any]:
    x, y, cx, cy = box
    n = len(items)
    cols = 2 if n <= 4 else 3
    rows = max(1, math.ceil(n / cols))
    gap = max(min(cx, cy) // 50, 40000)
    w = (cx - gap * (cols - 1)) // cols
    h = (cy - gap * (rows - 1)) // rows
    nodes = []
    for i, (text, sub) in enumerate(items):
        r, c = divmod(i, cols)
        fill = style.accents[i % len(style.accents)]
        node = _node(
            shapes, MSO_SHAPE.RECTANGLE, (x + c * (w + gap), y + r * (h + gap), w, h), fill, style
        )
        _text(node, text, sub, style, color=_on_fill(style, fill))
        nodes.append(node)
    return nodes


def _timeline(
    shapes: Any, box: tuple[int, int, int, int], items: list[Any], style: DiagramStyle
) -> list[Any]:
    x, y, cx, cy = box
    n = len(items)
    line_y = y + cy // 2
    _connector(shapes, (x, line_y), (x + cx, line_y), style, True)
    marker = max(min(cy // 6, cx // (4 * n)), 120000)
    step = cx / n
    label_h = max(cy // 2 - marker, 300000)
    nodes = []
    for i, (text, sub) in enumerate(items):
        center_x = int(x + step * (i + 0.5))
        fill = style.accents[i % len(style.accents)]
        dot = _node(
            shapes,
            MSO_SHAPE.OVAL,
            (center_x - marker // 2, line_y - marker // 2, marker, marker),
            fill,
            style,
        )
        label_w = int(step * 0.9)
        top = line_y - marker // 2 - label_h if i % 2 == 0 else line_y + marker // 2
        label = _node(
            shapes,
            MSO_SHAPE.RECTANGLE,
            (center_x - label_w // 2, top, label_w, label_h),
            None,
            style,
        )
        label.line.fill.background()
        _text(label, text, sub, style, color=style.text_color, size_pt=style.font_size_pt - 1)
        nodes.extend([dot, label])
    return nodes


def _venn(
    shapes: Any, box: tuple[int, int, int, int], items: list[Any], style: DiagramStyle
) -> list[Any]:
    x, y, cx, cy = box
    n = min(len(items), 3)
    d = int(min(cy, cx * 0.62)) if n > 1 else int(min(cx, cy))
    nodes = []
    if n == 1:
        centers = [(x + cx // 2, y + cy // 2)]
    elif n == 2:
        centers = [(x + cx // 2 - d // 4, y + cy // 2), (x + cx // 2 + d // 4, y + cy // 2)]
    else:
        d = int(min(cy * 0.60, cx * 0.5))
        centers = [
            (x + cx // 2 - d // 4, y + cy // 2 - d // 6),
            (x + cx // 2 + d // 4, y + cy // 2 - d // 6),
            (x + cx // 2, y + cy // 2 + d // 3),
        ]
    for i, (text, sub) in enumerate(items[:n]):
        fill = style.accents[i % len(style.accents)]
        px, py = centers[i]
        node = _node(shapes, MSO_SHAPE.OVAL, (px - d // 2, py - d // 2, d, d), fill, style)
        _set_alpha(node, 70)
        _text(node, text, sub, style, color=_on_fill(style, fill))
        nodes.append(node)
    return nodes


def _set_alpha(shape: Any, percent: int) -> None:
    fill = shape._element.find(".//a:solidFill", {"a": NS_A})
    if fill is None or len(fill) == 0:
        return
    alpha = etree.SubElement(fill[0], f"{{{NS_A}}}alpha")
    alpha.set("val", str(percent * 1000))


def content_fits(block: dict[str, Any], box: tuple[int, ...], style: DiagramStyle) -> bool:
    """Conservative text fit against the renderer's actual node geometry.

    Keep all nodes and readable type; the planner can choose a text composition instead.
    In particular Venn only supports three sets, so never silently truncate a fourth.
    """
    items = block.get("items") or []
    kind = block.get("kind")
    if kind not in KINDS or not 2 <= len(items) <= (3 if kind == "venn" else 6):
        return False
    x, y, width, height = box
    if width <= 0 or height <= 0:
        return False
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    result = draw_diagram(slide, (x, y, width, height), block, style)
    group = slide.shapes[-1]
    nodes = [s for s in group.shapes if str(s.shape_id) in result.node_ids and s.has_text_frame]
    for node in nodes:
        if not node.text_frame.text:
            continue  # timeline markers
        if node.left < x or node.top < y or node.left + node.width > x + width + 1:
            return False
        if node.top + node.height > y + height + 1:
            return False
        # Inscribe text in curved/tapered nodes, not in their rectangular bounding box.
        ratio = 0.68 if kind in ("cycle", "venn", "pyramid", "funnel") else 0.9
        available_w = node.width / 12700 * ratio - 7.2
        available_h = node.height / 12700 * ratio - 4.32
        used_h = 0.0
        for paragraph in node.text_frame.paragraphs:
            run = paragraph.runs[0] if paragraph.runs else None
            size = run.font.size.pt if run and run.font.size else style.font_size_pt
            font = text_metrics.resolve_font(style.font_family)
            lines = 0
            for line in paragraph.text.split("\n"):
                lines += 1
                used_w = 0.0
                for word in line.split():
                    word_w = text_metrics.text_width_pt(word, font, size)
                    if word_w > available_w:
                        return False
                    gap = text_metrics.text_width_pt(" ", font, size) if used_w else 0.0
                    if used_w + gap + word_w > available_w:
                        lines += 1
                        used_w = word_w
                    else:
                        used_w += gap + word_w
            used_h += lines * text_metrics.line_metrics(font, size).line_height_pt
        if used_h > available_h:
            return False
    return True


def draw_diagram(
    slide: Any, box: tuple[int, int, int, int], block: dict[str, Any], style: DiagramStyle
) -> DiagramResult:
    """Схема в прямоугольнике box (EMU): группа фигур с текстом; возвращает id группы и узлов."""
    kind = str(block.get("kind") or "process")
    if kind not in KINDS:
        kind = "process"
    items = _items(block)
    vertical = block.get("direction") == "vertical"
    group = slide.shapes.add_group_shape()
    shapes = group.shapes
    if kind == "process":
        nodes = _process(shapes, box, items, style, vertical)
    elif kind == "cycle":
        nodes = _cycle(shapes, box, items, style)
    elif kind == "pyramid":
        nodes = _stack(shapes, box, items, style, narrow_top=True)
    elif kind == "funnel":
        nodes = _stack(shapes, box, items, style, narrow_top=False)
    elif kind == "hierarchy":
        nodes = _hierarchy(shapes, box, items, style)
    elif kind == "matrix":
        nodes = _matrix(shapes, box, items, style)
    elif kind == "timeline":
        nodes = _timeline(shapes, box, items, style)
    else:
        nodes = _venn(shapes, box, items, style)
    group.name = f"Схема: {kind}"
    return DiagramResult(str(group.shape_id), [str(n.shape_id) for n in nodes], kind)


__all__ = ["KINDS", "DiagramResult", "DiagramStyle", "draw_diagram"]
