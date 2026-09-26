"""Операции над одним объектом офисной копии на месте (этап 38): оформление, абзацы, геометрия,
подпись и копия карточки — без перестройки слайда.

Правятся только XML слайда объекта: остальные части копии остаются байт в байт. Стиль —
токены шаблона (`Tokens`): кегль — ступень шкалы, цвет — из палитры (названный вне палитры
заменяется ближайшим с пояснением), шрифт — только из шаблона. Без профиля шаблона токены
берутся из темы и слайдов самого файла. После правки текст меряется в рамке: не влезает —
предупреждение в ответ, а не молча.
"""

from __future__ import annotations

import io
import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal
from zipfile import ZipFile

from lxml import etree
from pydantic import BaseModel, ConfigDict, Field

from presentation_designer.generation.office_edit import NS, slides
from presentation_designer.generation.office_objects import SlideObject, shape_element, xml

A = f"{{{NS['a']}}}"
P = f"{{{NS['p']}}}"
MIN_PT = 9.0
# Порядок детей a:rPr (DrawingML): заливка идёт после контура, шрифты — после заливки.
RPR_FILLS = ("noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill")
RPR_AFTER_FONT = ("ea", "cs", "sym", "hlinkClick", "hlinkMouseOver", "rtl", "extLst")

OpName = Literal[
    "style.size", "style.bold", "style.italic", "style.underline", "style.color", "style.align",
    "style.font", "text.insert_paragraph", "text.delete_paragraph", "object.resize",
    "object.delete", "object.z_order", "object.add_text", "object.add_block",
    "table.set_cell", "table.add_row", "table.delete_row", "table.add_column",
    "table.delete_column", "table.sort", "table.highlight", "table.align",
    "chart.set_data", "chart.type", "chart.legend", "chart.labels", "chart.gridlines",
    "chart.title", "chart.highlight", "chart.colors", "chart.number_format", "chart.sort",
    "object.to_chart", "object.to_table",
]  # fmt: skip
# Диаграммы и смена подачи (этап 40) исполняет `office_chart` через python-pptx: у диаграммы
# данные и оформление лежат в своей части и во встроенной книге, а не в XML слайда.
CHART_OPS = frozenset(
    {"object.to_chart", "object.to_table"} | {n for n in OpName.__args__ if n.startswith("chart.")}  # type: ignore[attr-defined]
)
ChartKind = Literal["column", "bar", "stacked_column", "line", "area", "pie", "doughnut"]


class ChartSeries(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(max_length=120)
    values: list[float | None] = Field(max_length=60)


class ObjectOp(BaseModel):
    """Операция над выбранным объектом; поля — аргументы тех операций, которым они нужны."""

    model_config = ConfigDict(extra="forbid", strict=True)
    op: OpName
    step: int | None = Field(default=None, ge=-3, le=3)  # style.size: ступеней шкалы
    size_pt: float | None = Field(default=None, ge=6, le=120)
    on: bool | None = None  # bold/italic/underline
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    align: Literal["left", "center", "right", "justify"] | None = None
    font: str | None = Field(default=None, max_length=80)
    paragraph: int | None = Field(default=None, ge=0, le=200)  # с единицы; 0 — в начало
    text: str | None = Field(default=None, max_length=2000)
    width: float | None = Field(default=None, gt=0, le=1)  # доли слайда
    height: float | None = Field(default=None, gt=0, le=1)
    to: Literal["front", "back"] | None = None
    place: Literal["below", "above", "right", "left"] | None = None
    role: Literal["caption", "body"] | None = None
    # Таблица: строки и столбцы с единицы (строка 1 — шапка); 0 — вставить в начало.
    row: int | None = Field(default=None, ge=0, le=200)
    column: int | None = Field(default=None, ge=0, le=50)
    cells: list[str] | None = Field(default=None, max_length=50)
    descending: bool | None = None
    # Диаграмма: данные целиком (категории и ряды), тип, выделяемая категория или ряд, формат.
    chart_type: ChartKind | None = None
    categories: list[str] | None = Field(default=None, max_length=60)
    series: list[ChartSeries] | None = Field(default=None, max_length=10)
    category: str | None = Field(default=None, max_length=120)
    number_format: str | None = Field(default=None, pattern=r'^[#0,. %]{1,16}("[^"]{0,12}")?$')


@dataclass
class Tokens:
    """Стиль шаблона для правок на месте."""

    palette: list[str] = field(default_factory=list)  # «#RRGGBB»
    scale: list[float] = field(default_factory=list)  # кегли шкалы, по возрастанию
    fonts: list[str] = field(default_factory=list)
    text_color: str | None = None
    caption_pt: float | None = None
    body_pt: float | None = None
    # Профиль шаблона целиком: по нему диаграммы и таблицы берут тот же стиль, что в сборке.
    profile: dict[str, Any] | None = field(default=None, repr=False)

    @classmethod
    def from_profile(cls, profile: dict[str, Any] | None) -> Tokens | None:
        tokens = (profile or {}).get("design_tokens") or {}
        colors = tokens.get("colors") or {}
        typography = tokens.get("typography") or {}
        palette = [str(c["hex"]).upper() for c in colors.get("palette") or [] if c.get("hex")]
        palette += [str(v).upper() for v in (colors.get("theme") or {}).values() if v]
        scale = typography.get("scale") or []
        if not palette and not scale:
            return None
        text = next(
            (str(c["hex"]) for c in colors.get("palette") or [] if c.get("role") == "text"), None
        )
        by_role = {str(s.get("role")): float(s["size_pt"]) for s in scale if s.get("size_pt")}
        fonts = [str(f["family"]) for f in typography.get("fonts") or [] if f.get("family")]
        theme = typography.get("theme_fonts") or {}
        fonts += [str(v) for v in theme.values() if v]
        return cls(
            palette=list(dict.fromkeys(palette)),
            scale=sorted({float(s["size_pt"]) for s in scale if s.get("size_pt")}),
            fonts=list(dict.fromkeys(fonts)),
            text_color=text or (colors.get("theme") or {}).get("dk1"),
            caption_pt=by_role.get("caption"),
            body_pt=by_role.get("body"),
            profile=profile,
        )

    @classmethod
    def from_pptx(cls, data: bytes) -> Tokens:
        """Без профиля: цвета темы и кегли, которые уже есть на слайдах файла."""
        palette: list[str] = []
        sizes: set[float] = set()
        fonts: list[str] = []
        with ZipFile(io.BytesIO(data)) as archive:
            for name in archive.namelist():
                if re.fullmatch(r"ppt/theme/theme\d+\.xml", name):
                    root = xml(archive.read(name))
                    palette += [f"#{n.get('val')}".upper() for n in root.iter(f"{A}srgbClr")]
                    fonts += [
                        n.get("typeface") for n in root.iter(f"{A}latin") if n.get("typeface")
                    ]
            for name in slides(archive):
                for node in xml(archive.read(name)).iter(f"{A}rPr", f"{A}defRPr"):
                    if node.get("sz"):
                        sizes.add(int(node.get("sz", "0")) / 100)
        return cls(
            palette=list(dict.fromkeys(palette))[:12],
            scale=sorted(s for s in sizes if s >= MIN_PT),
            fonts=list(dict.fromkeys(f for f in fonts if not f.startswith("+"))),
            text_color=palette[0] if palette else None,
        )

    def lines(self) -> list[str]:
        """Токены строками для модели."""
        out = []
        if self.palette:
            out.append("палитра: " + ", ".join(self.palette[:12]))
        if self.scale:
            out.append("шкала кеглей: " + ", ".join(f"{s:g}" for s in self.scale))
        if self.fonts:
            out.append("шрифты: " + ", ".join(self.fonts[:6]))
        return out


def _rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def nearest_color(color: str, palette: list[str]) -> tuple[str, bool]:
    """Цвет из палитры, ближайший к названному; второй — был ли он в палитре."""
    wanted = color.upper()
    if not palette or wanted in palette:
        return wanted, True
    r, g, b = _rgb(wanted)
    best = min(
        palette, key=lambda c: sum((x - y) ** 2 for x, y in zip(_rgb(c), (r, g, b), strict=True))
    )
    return best, False


def next_size(current: float, step: int, scale: list[float]) -> float:
    """Ступень шкалы от текущего кегля; без шкалы — ×1,15 на ступень. Не меньше 9 пт."""
    ladder = sorted({s for s in scale if s >= MIN_PT})
    if not ladder:
        return max(MIN_PT, round(current * (1.15**step)))
    if step > 0:
        bigger = [s for s in ladder if s > current + 0.1]
        return bigger[min(step, len(bigger)) - 1] if bigger else round(current * (1.15**step))
    smaller = [s for s in ladder if s < current - 0.1]
    return (
        smaller[-min(-step, len(smaller))]
        if smaller
        else max(MIN_PT, round(current * (1.15**step)))
    )


def nearest_size(size: float, scale: list[float]) -> float:
    ladder = [s for s in scale if s >= MIN_PT]
    return min(ladder, key=lambda s: abs(s - size)) if ladder else max(MIN_PT, size)


# --- XML ---------------------------------------------------------------------------------


def _run_props(body: Any) -> list[Any]:
    """a:rPr всех прогонов и полей объекта (создаются, где их нет) и a:endParaRPr."""
    props = []
    for run in [*body.iter(f"{A}r"), *body.iter(f"{A}fld")]:
        rpr = run.find(f"{A}rPr")
        if rpr is None:
            rpr = etree.Element(f"{A}rPr", lang="ru-RU")
            run.insert(0, rpr)
        props.append(rpr)
    props += list(body.iter(f"{A}endParaRPr"))
    return props


def _set_fill(rpr: Any, color: str) -> None:
    for tag in RPR_FILLS:
        for node in rpr.findall(f"{A}{tag}"):
            rpr.remove(node)
    fill = etree.Element(f"{A}solidFill")
    etree.SubElement(fill, f"{A}srgbClr", val=color.lstrip("#").upper())
    line = rpr.find(f"{A}ln")
    if line is not None:
        line.addnext(fill)
    else:
        rpr.insert(0, fill)


def _set_font(rpr: Any, family: str) -> None:
    for node in rpr.findall(f"{A}latin"):
        rpr.remove(node)
    latin = etree.Element(f"{A}latin", typeface=family)
    after = next((c for c in rpr if etree.QName(c).localname in RPR_AFTER_FONT), None)
    if after is not None:
        after.addprevious(latin)
    else:
        rpr.append(latin)


def _paragraphs(body: Any) -> list[Any]:
    return list(body.findall(f"{A}p"))


def _set_text(paragraph: Any, text: str) -> None:
    """Текст абзаца вместо прежнего: первый прогон с его оформлением, остальные — прочь."""
    runs = paragraph.findall(f"{A}r")
    for node in [*runs[1:], *paragraph.findall(f"{A}br"), *paragraph.findall(f"{A}fld")]:
        paragraph.remove(node)
    if runs:
        runs[0].find(f"{A}t").text = text
        return
    run = etree.Element(f"{A}r")
    end = paragraph.find(f"{A}endParaRPr")
    rpr = deepcopy(end) if end is not None else etree.Element(f"{A}rPr", lang="ru-RU")
    rpr.tag = f"{A}rPr"
    run.append(rpr)
    etree.SubElement(run, f"{A}t").text = text
    if end is not None:
        end.addprevious(run)
    else:
        paragraph.append(run)


def _xfrm(element: Any) -> Any | None:
    tag = etree.QName(element).localname
    if tag == "graphicFrame":
        return element.find("p:xfrm", NS)
    if tag == "grpSp":
        return element.find("p:grpSpPr/a:xfrm", NS)
    return element.find("p:spPr/a:xfrm", NS)


def _box(element: Any) -> tuple[int, int, int, int] | None:
    transform = _xfrm(element)
    if transform is None:
        return None
    off, ext = transform.find("a:off", NS), transform.find("a:ext", NS)
    if off is None or ext is None:
        return None
    return int(off.get("x", 0)), int(off.get("y", 0)), int(ext.get("cx", 0)), int(ext.get("cy", 0))


def _set_box(element: Any, box: tuple[float, float, float, float]) -> None:
    transform = _xfrm(element)
    if transform is None:
        raise ValueError("у объекта нет своей рамки")
    off, ext = transform.find("a:off", NS), transform.find("a:ext", NS)
    off.set("x", str(round(box[0])))
    off.set("y", str(round(box[1])))
    ext.set("cx", str(round(box[2])))
    ext.set("cy", str(round(box[3])))


def _ids(root: Any) -> list[int]:
    return [int(n.get("id")) for n in root.iter(f"{P}cNvPr") if str(n.get("id", "")).isdigit()]


def _renumber(element: Any, root: Any) -> None:
    next_id = max(_ids(root), default=1) + 1
    for node in element.iter(f"{P}cNvPr"):
        node.set("id", str(next_id))
        next_id += 1


def _siblings_in_row(element: Any) -> tuple[list[Any], str] | None:
    """Ряд одинаковых карточек вокруг объекта: соседи того же уровня похожего размера на одной
    высоте (ряд) или на одной линии по x (столбец), по порядку."""
    own = _box(element)
    parent = element.getparent()
    if own is None or parent is None:
        return None
    x, y, w, h = own
    same = []
    for other in parent:
        if etree.QName(other).localname not in ("sp", "grpSp", "pic", "graphicFrame"):
            continue
        box = _box(other)
        if box is None or not w or not h:
            continue
        if abs(box[2] - w) <= 0.15 * w and abs(box[3] - h) <= 0.15 * h:
            same.append((other, box))
    rows = [(e, b) for e, b in same if abs(b[1] - y) <= 0.1 * h]
    cols = [(e, b) for e, b in same if abs(b[0] - x) <= 0.1 * w]
    if len(rows) >= len(cols):
        return [e for e, _ in sorted(rows, key=lambda item: item[1][0])], "row"
    return [e for e, _ in sorted(cols, key=lambda item: item[1][1])], "column"


def _add_block(root: Any, element: Any, text: str | None) -> str:
    """Ещё одна такая же карточка: копия выбранной в конец ряда, ряд делится поровну в прежних
    границах с прежним промежутком. Текст копии — из просьбы, иначе как у образца."""
    found = _siblings_in_row(element)
    if found is None:
        raise ValueError("у объекта нет рамки — добавить такой же нельзя")
    items, axis = found
    boxes = [b for b in (_box(e) for e in items) if b is not None]
    if len(boxes) != len(items):
        raise ValueError("у карточек ряда нет рамок — добавить такую же нельзя")
    clone = deepcopy(element)
    _renumber(clone, root)
    for node in clone.iter(f"{P}cNvPr"):
        node.set("name", f"{node.get('name', 'Объект')} (копия)")
    items[-1].addnext(clone)
    if text is not None:
        shapes = [n for n in clone.iter(f"{P}txBody")]
        lines = [line for line in text.split("\n") if line.strip()] or [""]
        for index, body in enumerate(shapes):
            paragraphs = _paragraphs(body)
            for extra in paragraphs[1:]:
                body.remove(extra)
            if paragraphs:
                _set_text(paragraphs[0], lines[index] if index < len(lines) else "")
    count = len(items) + 1
    if axis == "row":
        start = min(b[0] for b in boxes)
        end = max(b[0] + b[2] for b in boxes)
        gaps = [boxes[i + 1][0] - (boxes[i][0] + boxes[i][2]) for i in range(len(boxes) - 1)]
        gap = max(0, sum(gaps) / len(gaps)) if gaps else boxes[0][2] * 0.08
        size = (end - start - gap * (count - 1)) / count
        for index, item in enumerate([*items, clone]):
            x, y, _, h = boxes[min(index, len(boxes) - 1)]
            _set_box(item, (start + index * (size + gap), y, size, h))
    else:
        start = min(b[1] for b in boxes)
        end = max(b[1] + b[3] for b in boxes)
        gaps = [boxes[i + 1][1] - (boxes[i][1] + boxes[i][3]) for i in range(len(boxes) - 1)]
        gap = max(0, sum(gaps) / len(gaps)) if gaps else boxes[0][3] * 0.08
        size = (end - start - gap * (count - 1)) / count
        for index, item in enumerate([*items, clone]):
            x, _, w, _ = boxes[min(index, len(boxes) - 1)]
            _set_box(item, (x, start + index * (size + gap), w, size))
    return f"добавил ещё одну карточку, их теперь {count}"


def _add_text(
    root: Any, obj: SlideObject, op: ObjectOp, tokens: Tokens, slide_w: int, slide_h: int
) -> str:
    """Надпись в стиле подписи шаблона рядом с объектом: под ним по умолчанию."""
    text = (op.text or "").strip()
    if not text:
        raise ValueError("нет текста надписи")
    role = op.role or "caption"
    size = (tokens.caption_pt if role == "caption" else tokens.body_pt) or (
        min(tokens.scale) if tokens.scale else 12
    )
    size = max(MIN_PT, size)
    x, y = obj.bbox.x * slide_w, obj.bbox.y * slide_h
    w, h = obj.bbox.width * slide_w, obj.bbox.height * slide_h
    line = size * 12700 * 1.5
    place = op.place or "below"
    if place == "below":
        box = (x, min(y + h + line * 0.3, slide_h - line), w, line)
    elif place == "above":
        box = (x, max(0, y - line * 1.3), w, line)
    elif place == "right":
        box = (min(x + w + line * 0.3, slide_w - w * 0.5), y, max(w * 0.5, slide_w * 0.2), h)
    else:
        box = (max(0, x - slide_w * 0.2 - line * 0.3), y, slide_w * 0.2, h)
    tree = root.find("p:cSld/p:spTree", NS)
    shape = etree.SubElement(tree, f"{P}sp")
    nv = etree.SubElement(shape, f"{P}nvSpPr")
    etree.SubElement(nv, f"{P}cNvPr", id=str(max(_ids(root), default=1) + 1), name="Подпись")
    etree.SubElement(etree.SubElement(nv, f"{P}cNvSpPr"), f"{A}spLocks", noGrp="1").getparent().set(
        "txBox", "1"
    )
    etree.SubElement(nv, f"{P}nvPr")
    sp_pr = etree.SubElement(shape, f"{P}spPr")
    xfrm = etree.SubElement(sp_pr, f"{A}xfrm")
    etree.SubElement(xfrm, f"{A}off", x=str(round(box[0])), y=str(round(box[1])))
    etree.SubElement(xfrm, f"{A}ext", cx=str(round(box[2])), cy=str(round(box[3])))
    etree.SubElement(etree.SubElement(sp_pr, f"{A}prstGeom", prst="rect"), f"{A}avLst")
    etree.SubElement(sp_pr, f"{A}noFill")
    body = etree.SubElement(shape, f"{P}txBody")
    etree.SubElement(body, f"{A}bodyPr", wrap="square", lIns="0", tIns="0", rIns="0", bIns="0")
    etree.SubElement(body, f"{A}lstStyle")
    for line_text in text.split("\n"):
        paragraph = etree.SubElement(body, f"{A}p")
        run = etree.SubElement(paragraph, f"{A}r")
        rpr = etree.SubElement(run, f"{A}rPr", lang="ru-RU", sz=str(round(size * 100)), dirty="0")
        if tokens.text_color:
            _set_fill(rpr, tokens.text_color)
        if tokens.fonts:
            _set_font(rpr, tokens.fonts[0])
        etree.SubElement(run, f"{A}t").text = line_text
    return f"добавил надпись «{text[:40]}»"


# --- таблица --------------------------------------------------------------------------------


def _merged(tbl: Any) -> bool:
    return any(
        tc.get("gridSpan") or tc.get("rowSpan") or tc.get("hMerge") or tc.get("vMerge")
        for tc in tbl.iter(f"{A}tc")
    )


def _cell_text(tc: Any) -> str:
    return " ".join(
        "".join(t.text or "" for t in p.iter(f"{A}t")) for p in tc.iter(f"{A}p")
    ).strip()


def _write_cell(tc: Any, text: str) -> None:
    body = tc.find(f"{A}txBody")
    if body is None:
        body = etree.Element(f"{A}txBody")
        etree.SubElement(body, f"{A}bodyPr")
        etree.SubElement(body, f"{A}lstStyle")
        etree.SubElement(body, f"{A}p")
        tc.insert(0, body)
    paragraphs = _paragraphs(body)
    for extra in paragraphs[1:]:
        body.remove(extra)
    _set_text(paragraphs[0], text)


def _number(text: str) -> float | None:
    cleaned = re.sub(r"[\s\u00a0₽$€%]", "", text).replace(",", ".").replace("−", "-")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _grow_frame(element: Any, dy: int) -> None:
    transform = _xfrm(element)
    ext = transform.find("a:ext", NS) if transform is not None else None
    if ext is not None:
        ext.set("cy", str(max(0, int(ext.get("cy", "0")) + dy)))


def _table_op(element: Any, op: ObjectOp, tokens: Tokens) -> str:
    """Операция над таблицей объекта; объединённые ячейки не поддерживаются."""
    tbl = element.find(".//a:tbl", NS)
    if tbl is None:
        raise ValueError("это не таблица")
    rows = tbl.findall(f"{A}tr")
    grid = tbl.find(f"{A}tblGrid")
    columns = grid.findall(f"{A}gridCol") if grid is not None else []
    if _merged(tbl) and op.op not in ("table.set_cell", "table.highlight", "table.align"):
        raise ValueError("в таблице есть объединённые ячейки — меняйте её структуру в редакторе")

    def row_at(n: int | None) -> Any:
        if n is None or not 1 <= n <= len(rows):
            raise ValueError(f"строки {n} нет: в таблице {len(rows)}")
        return rows[n - 1]

    def column_index(n: int | None) -> int:
        if n is None or not 1 <= n <= len(columns):
            raise ValueError(f"столбца {n} нет: в таблице {len(columns)}")
        return n - 1

    if op.op == "table.set_cell":
        cells = row_at(op.row).findall(f"{A}tc")
        _write_cell(cells[column_index(op.column)], (op.text or "").strip())
        return f"ячейка {op.row}×{op.column} изменена"
    if op.op == "table.add_row":
        after = len(rows) if op.row is None else min(op.row, len(rows))
        model = rows[max(after - 1, 1 if len(rows) > 1 else 0)]
        clone = deepcopy(model)
        values = list(op.cells or [])
        for index, tc in enumerate(clone.findall(f"{A}tc")):
            _write_cell(tc, values[index] if index < len(values) else "")
        (rows[after - 1].addnext if after > 0 else rows[0].addprevious)(clone)
        _grow_frame(element, int(model.get("h", "0")))
        return "добавил строку"
    if op.op == "table.delete_row":
        target = row_at(op.row)
        if len(rows) <= 2:
            raise ValueError("в таблице останется одна строка — удалите таблицу целиком")
        tbl.remove(target)
        _grow_frame(element, -int(target.get("h", "0")))
        return f"строка {op.row} удалена"
    if op.op in ("table.add_column", "table.delete_column"):
        total = sum(int(c.get("w", "0")) for c in columns)
        if op.op == "table.add_column":
            after = len(columns) if op.column is None else min(op.column, len(columns))
            values = list(op.cells or [])
            new_col = deepcopy(columns[max(after - 1, 0)])
            (columns[after - 1].addnext if after > 0 else columns[0].addprevious)(new_col)
            for index, tr in enumerate(rows):
                cells = tr.findall(f"{A}tc")
                clone = deepcopy(cells[max(after - 1, 0)])
                _write_cell(clone, values[index] if index < len(values) else "")
                (cells[after - 1].addnext if after > 0 else cells[0].addprevious)(clone)
            note = "добавил столбец"
        else:
            if len(columns) <= 1:
                raise ValueError("это последний столбец — удалите таблицу целиком")
            index = column_index(op.column)
            grid.remove(columns[index])
            for tr in rows:
                tr.remove(tr.findall(f"{A}tc")[index])
            note = f"столбец {op.column} удалён"
        # Ширина таблицы прежняя: столбцы делят её в прежних пропорциях.
        now = grid.findall(f"{A}gridCol")
        widths = [int(c.get("w", "0")) for c in now]
        scale = total / sum(widths) if sum(widths) else 1
        for column in now:
            column.set("w", str(round(int(column.get("w", "0")) * scale)))
        return note
    if op.op == "table.sort":
        index = column_index(op.column)
        body = rows[1:]
        values = [_cell_text(r.findall(f"{A}tc")[index]) for r in body]
        numeric = all(_number(v) is not None for v in values if v)

        def key(r: Any) -> Any:
            text = _cell_text(r.findall(f"{A}tc")[index])
            return (_number(text) or 0.0) if numeric else text.lower()

        ordered = sorted(body, key=key, reverse=bool(op.descending))
        for r in body:
            tbl.remove(r)
        for r in ordered:
            tbl.append(r)
        return "строки отсортированы"
    if op.op == "table.highlight":
        accent = next((c for c in tokens.palette if c not in ("#FFFFFF", "#000000")), None)
        if op.row is not None:
            targets = row_at(op.row).findall(f"{A}tc")
        else:
            index = column_index(op.column)
            targets = [tr.findall(f"{A}tc")[index] for tr in rows[1:]]
        for tc in targets:
            for rpr in _run_props(tc):
                rpr.set("b", "1")
                if accent:
                    _set_fill(rpr, accent)
        return "выделил акцентом шаблона"
    if op.op == "table.align":
        index = column_index(op.column)
        algn = {"left": "l", "center": "ctr", "right": "r", "justify": "just"}[op.align or "right"]
        for tr in rows:
            for paragraph in tr.findall(f"{A}tc")[index].iter(f"{A}p"):
                ppr = paragraph.find(f"{A}pPr")
                if ppr is None:
                    ppr = etree.Element(f"{A}pPr")
                    paragraph.insert(0, ppr)
                ppr.set("algn", algn)
        return "выравнивание столбца изменено"
    raise ValueError(f"неизвестная операция {op.op}")


def apply(
    data: bytes, obj: SlideObject, ops: list[ObjectOp], tokens: Tokens, current_pt: float | None
) -> tuple[bytes, list[str]]:
    """PPTX с операциями над объектом `obj` и заметки: что сделано и что подменено токенами."""
    if not ops:
        return data, []
    notes: list[str] = []
    with ZipFile(io.BytesIO(data)) as archive:
        name = slides(archive)[obj.slide - 1]
        root = xml(archive.read(name))
        size = xml(archive.read("ppt/presentation.xml")).find("p:sldSz", NS)
        slide_w, slide_h = int(size.attrib["cx"]), int(size.attrib["cy"])
        element = shape_element(root, obj.shape_id)
        if element is None:
            raise ValueError("объект не найден на слайде")
        # Текст фигуры; у таблицы — все её ячейки (оформление — таблице целиком).
        body = element.find(".//p:txBody", NS)
        if body is None:
            body = element.find(".//a:tbl", NS)
        for op in ops:
            if op.op.startswith("table."):
                notes.append(_table_op(element, op, tokens))
                continue
            if op.op.startswith("style.") or op.op.startswith("text."):
                if body is None:
                    raise ValueError(
                        "у объекта нет текста — оформление и абзацы к нему неприменимы"
                    )
            if op.op == "style.size":
                base = current_pt or 18.0
                if op.size_pt is not None:
                    target = nearest_size(op.size_pt, tokens.scale)
                    if abs(target - op.size_pt) > 0.5:
                        notes.append(f"кегля {op.size_pt:g} нет в шкале шаблона — взял {target:g}")
                else:
                    target = next_size(base, op.step or 1, tokens.scale)
                for rpr in _run_props(body):
                    rpr.set("sz", str(round(target * 100)))
                notes.append(f"кегль {target:g} пт")
            elif op.op in ("style.bold", "style.italic", "style.underline"):
                attr = {"style.bold": "b", "style.italic": "i", "style.underline": "u"}[op.op]
                on = op.on is not False
                value = ("sng" if on else "none") if attr == "u" else ("1" if on else "0")
                for rpr in _run_props(body):
                    rpr.set(attr, value)
            elif op.op == "style.color":
                if not op.color:
                    raise ValueError("не указан цвет")
                color, exact = nearest_color(op.color, tokens.palette)
                if not exact:
                    notes.append(f"такого цвета в палитре шаблона нет — взял ближайший {color}")
                for rpr in _run_props(body):
                    _set_fill(rpr, color)
            elif op.op == "style.align":
                for paragraph in _paragraphs(body):
                    ppr = paragraph.find(f"{A}pPr")
                    if ppr is None:
                        ppr = etree.Element(f"{A}pPr")
                        paragraph.insert(0, ppr)
                    ppr.set(
                        "algn",
                        {"left": "l", "center": "ctr", "right": "r", "justify": "just"}[
                            op.align or "left"
                        ],
                    )
            elif op.op == "style.font":
                allowed = {f.lower(): f for f in tokens.fonts}
                if not op.font or op.font.lower() not in allowed:
                    fonts = ", ".join(tokens.fonts[:4]) or "шрифты шаблона"
                    notes.append(f"шрифта «{op.font}» в шаблоне нет — доступны: {fonts}")
                    continue
                for rpr in _run_props(body):
                    _set_font(rpr, allowed[op.font.lower()])
            elif op.op == "text.insert_paragraph":
                paragraphs = _paragraphs(body)
                after = min(
                    op.paragraph if op.paragraph is not None else len(paragraphs), len(paragraphs)
                )
                model = paragraphs[max(after - 1, 0)]
                clone = deepcopy(model)
                _set_text(clone, (op.text or "").strip())
                if after == 0:
                    model.addprevious(clone)
                else:
                    model.addnext(clone)
            elif op.op == "text.delete_paragraph":
                paragraphs = _paragraphs(body)
                index = (op.paragraph or 0) - 1
                if not 0 <= index < len(paragraphs):
                    raise ValueError(f"абзаца {op.paragraph} нет")
                if len(paragraphs) == 1:
                    _set_text(paragraphs[0], "")
                else:
                    body.remove(paragraphs[index])
            elif op.op == "object.resize":
                box = _box(element)
                properties = element.find("p:spPr", NS)
                if box is None and properties is not None and not obj.group_path:
                    # Плейсхолдер с рамкой макета: своя рамка появляется на слайде.
                    transform = etree.Element(f"{A}xfrm")
                    properties.insert(0, transform)
                    etree.SubElement(
                        transform,
                        f"{A}off",
                        x=str(round(obj.bbox.x * slide_w)),
                        y=str(round(obj.bbox.y * slide_h)),
                    )
                    etree.SubElement(
                        transform,
                        f"{A}ext",
                        cx=str(round(obj.bbox.width * slide_w)),
                        cy=str(round(obj.bbox.height * slide_h)),
                    )
                    box = _box(element)
                if box is None:
                    raise ValueError("у объекта нет своей рамки — размер задаётся в редакторе")
                scale = box[2] / (obj.bbox.width * slide_w) if obj.bbox.width else 1.0
                width = (op.width or obj.bbox.width) * slide_w * scale
                height = (op.height or obj.bbox.height) * slide_h * scale
                if etree.QName(element).localname == "pic" and (op.width is None) != (
                    op.height is None
                ):
                    ratio = box[3] / box[2] if box[2] else 1.0
                    height = width * ratio if op.width is not None else height
                    width = height / ratio if op.height is not None else width
                _set_box(element, (box[0], box[1], width, height))
            elif op.op == "object.delete":
                parent = element.getparent()
                parent.remove(element)
                notes.append("объект удалён")
                break
            elif op.op == "object.z_order":
                parent = element.getparent()
                parent.remove(element)
                if op.to == "back":
                    head = [
                        c for c in parent if etree.QName(c).localname in ("nvGrpSpPr", "grpSpPr")
                    ]
                    if head:
                        head[-1].addnext(element)
                    else:
                        parent.insert(0, element)
                else:
                    parent.append(element)
            elif op.op == "object.add_text":
                notes.append(_add_text(root, obj, op, tokens, slide_w, slide_h))
            elif op.op == "object.add_block":
                notes.append(_add_block(root, element, op.text))
        output = io.BytesIO()
        with ZipFile(output, "w") as result:
            result.comment = archive.comment
            for entry in archive.infolist():
                result.writestr(
                    entry,
                    etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
                    if entry.filename == name
                    else archive.read(entry),
                )
    return output.getvalue(), notes


def paragraphs_of(data: bytes, obj: SlideObject) -> list[dict[str, Any]]:
    """Абзацы объекта с номерами с единицы — те же, что у операций над абзацами."""
    with ZipFile(io.BytesIO(data)) as archive:
        root = xml(archive.read(slides(archive)[obj.slide - 1]))
    element = shape_element(root, obj.shape_id)
    body = element.find(".//p:txBody", NS) if element is not None else None
    if body is None:
        return []
    out = []
    for n, paragraph in enumerate(_paragraphs(body), 1):
        text = "".join(t.text or "" for t in paragraph.iter(f"{A}t"))
        out.append({"paragraph": n, "text": text})
    return out


def object_info(data: bytes, obj: SlideObject) -> dict[str, Any]:
    """Объект по снимку (как у ComposedDeck): действующий стиль текста и таблица строками."""
    from presentation_designer.generation.snapshot import deck_snapshot

    try:
        snapshot = deck_snapshot(data, source={"kind": "file"})
    except Exception:
        return {}
    slide = next((s for s in snapshot["slides"] if s["index"] == obj.slide), None)
    found: dict[str, Any] = next(
        (
            o
            for o in (slide or {}).get("objects") or []
            if o["address"]["object_id"] == obj.shape_id
        ),
        {},
    )
    info: dict[str, Any] = {"style": dict(found.get("style") or {})}
    table = found.get("table")
    if table:
        info["table"] = {"rows": table.get("rows") or [], "merged": bool(table.get("merged"))}
    chart = found.get("chart")
    if chart:
        info["chart"] = {k: chart.get(k) for k in ("type", "categories", "series")}
    return info


def style_of(data: bytes, obj: SlideObject) -> dict[str, Any]:
    """Действующий стиль текста объекта (кегль, цвет, шрифт)."""
    return dict(object_info(data, obj).get("style") or {})


def overflow_note(data: bytes, slide: int, name: str) -> str | None:
    """Текст объекта после правки не влезает в рамку — фраза для ответа, иначе None."""
    from presentation_designer.generation.snapshot import deck_snapshot
    from presentation_designer.library.dress import text_height

    try:
        snapshot = deck_snapshot(data, source={"kind": "file"})
    except Exception:
        return None
    slide_doc = next((s for s in snapshot["slides"] if s["index"] == slide), None)
    obj = next((o for o in (slide_doc or {}).get("objects") or [] if o.get("name") == name), None)
    if obj is None or not obj.get("text"):
        return None
    style = obj.get("style") or {}
    size = float(style.get("size_pt") or 0)
    if not size:
        return None
    width = snapshot["slide_size"]["width_emu"] * float(obj["bbox"]["width"])
    height = snapshot["slide_size"]["height_emu"] * float(obj["bbox"]["height"])
    text = "\n".join(p["text"] for p in obj["text"]["paragraphs"])
    needed = text_height(text, style.get("family"), size, int(width), bold=bool(style.get("bold")))
    if needed > height * 1.08:
        return "текст не помещается в рамку — сократите его или попросите «мельче»"
    return None


__all__ = [
    "CHART_OPS", "ChartSeries", "ObjectOp", "Tokens", "apply", "nearest_color", "next_size",
    "object_info", "overflow_note", "paragraphs_of", "style_of",
]  # fmt: skip
