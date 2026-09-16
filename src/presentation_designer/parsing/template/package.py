"""Чтение пакета PPTX: целостность, тема, мастера, макеты, образцы, встроенные шрифты, направляющие.

Проверка пакета выполняется до разбора: повреждённый ZIP, чужой формат или нарушенные связи
дают `PackageError` с конкретной причиной вместо падения внутри python-pptx. Дальше пакет
раскладывается в плоские структуры, с которыми работают остальные модули анализа.
"""

from __future__ import annotations

import pathlib
import zipfile
from dataclasses import dataclass, field
from typing import Any

from pptx import Presentation
from pptx.exc import PackageNotFoundError
from pptx.opc.constants import RELATIONSHIP_TYPE as RT

from presentation_designer.layout.ooxml import check_package
from presentation_designer.parsing.template.geometry import NS, ShapeInfo, walk_shapes
from presentation_designer.parsing.template.styles import Theme, clr_map_of, parse_theme

GUIDE_UNIT_EMU = 1587.5  # позиция направляющей хранится в 1/8 пункта


class PackageError(ValueError):
    """Файл не является поддерживаемым PPTX или повреждён."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class MasterInfo:
    master_id: str
    name: str
    part: str
    theme: Theme
    element: Any
    shapes: list[ShapeInfo]


@dataclass
class LayoutInfo:
    layout_id: str
    name: str
    master_id: str
    part: str
    element: Any
    shapes: list[ShapeInfo]
    placeholders: list[ShapeInfo]
    sample_slide_count: int = 0


@dataclass
class SlideInfo:
    index: int  # с 1
    part: str
    layout_id: str
    master_id: str
    hidden: bool
    shapes: list[ShapeInfo]
    notes_text: str
    element: Any
    slide: Any = field(repr=False, default=None)

    @property
    def text_shapes(self) -> list[ShapeInfo]:
        return [s for s in self.shapes if s.has_text_frame and s.text]

    @property
    def pictures(self) -> list[ShapeInfo]:
        return [s for s in self.shapes if s.kind == "picture"]

    @property
    def all_text(self) -> str:
        return "\n".join(s.text for s in self.shapes if s.text)


@dataclass
class Guide:
    orientation: str  # horizontal | vertical
    pos: float
    source: str = "view_props"


@dataclass
class TemplatePackage:
    path: pathlib.Path
    prs: Any
    width_emu: int
    height_emu: int
    masters: list[MasterInfo]
    layouts: list[LayoutInfo]
    slides: list[SlideInfo]
    embedded_fonts: list[str]  # семейства
    embedded_font_faces: int  # файлов начертаний (regular, bold…)
    guides: list[Guide]
    integrity: dict[str, Any]
    media_count: int
    chart_parts: int
    notes_with_text: int

    @property
    def aspect_ratio(self) -> float:
        return round(self.width_emu / self.height_emu, 4) if self.height_emu else 0.0

    def layout(self, layout_id: str) -> LayoutInfo | None:
        return next((lay for lay in self.layouts if lay.layout_id == layout_id), None)

    def master(self, master_id: str) -> MasterInfo | None:
        return next((m for m in self.masters if m.master_id == master_id), None)

    def theme_for_slide(self, slide: SlideInfo) -> Theme | None:
        master = self.master(slide.master_id)
        return master.theme if master else None


def _safe_id(name: str, fallback: str) -> str:
    """Стабильный идентификатор из имени части: ppt/slideLayouts/slideLayout3.xml → layout3."""
    stem = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return stem if stem else fallback


def open_template(path: pathlib.Path) -> TemplatePackage:
    path = pathlib.Path(path)
    if not path.is_file():
        raise PackageError("file_missing", f"файл не найден: {path}")
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            bad = zf.testzip()
    except zipfile.BadZipFile as e:
        raise PackageError("not_a_zip", "файл не является ZIP-пакетом OOXML") from e
    if bad is not None:
        raise PackageError("corrupt_zip", f"повреждённая запись в пакете: {bad}")
    if "ppt/presentation.xml" not in names:
        kind = (
            "docx"
            if "word/document.xml" in names
            else "xlsx"
            if "xl/workbook.xml" in names
            else "неизвестный"
        )
        raise PackageError("not_pptx", f"внутри пакета нет ppt/presentation.xml (формат: {kind})")
    try:
        report = check_package(path)
    except Exception as e:  # XML не разбирается, нет [Content_Types] и т. п.
        raise PackageError("malformed_package", f"пакет повреждён: {e}") from e
    integrity = report.as_dict()
    if not report.ok:
        raise PackageError(
            "broken_relationships",
            "нарушены связи пакета: " + "; ".join(report.errors[:3]),
        )
    try:
        prs = Presentation(str(path))
    except Exception as e:  # PackageNotFoundError, ошибки XML и связей
        if not isinstance(
            e, PackageNotFoundError | KeyError | ValueError | AttributeError | TypeError
        ):
            if "lxml" not in type(e).__module__ and "pptx" not in type(e).__module__:
                raise
        raise PackageError("unreadable", f"python-pptx не открывает пакет: {e}") from e

    width, height = int(prs.slide_width or 0), int(prs.slide_height or 0)
    if width <= 0 or height <= 0:
        raise PackageError("no_slide_size", "в презентации не задан размер слайда")

    masters: list[MasterInfo] = []
    master_ids: dict[Any, str] = {}
    for master in prs.slide_masters:
        part_name = str(master.part.partname).lstrip("/")
        mid = _safe_id(part_name, f"master{len(masters) + 1}")
        try:
            theme_part = master.part.part_related_by(RT.THEME)
            theme = parse_theme(theme_part)
            theme.part = str(theme_part.partname).lstrip("/")
        except KeyError:
            theme = Theme("Тема", {}, "Calibri", "Calibri")
        theme.clr_map = clr_map_of(master._element)
        shapes = walk_shapes(master, master.part, width, height)
        name = master._element.find("p:cSld", NS)
        masters.append(
            MasterInfo(
                master_id=mid,
                name=(name.get("name") if name is not None else None) or theme.name,
                part=part_name,
                theme=theme,
                element=master._element,
                shapes=shapes,
            )
        )
        master_ids[master.part.partname] = mid

    layouts: list[LayoutInfo] = []
    layout_ids: dict[Any, str] = {}
    for master in prs.slide_masters:
        for layout in master.slide_layouts:
            part_name = str(layout.part.partname).lstrip("/")
            lid = _safe_id(part_name, f"layout{len(layouts) + 1}")
            shapes = walk_shapes(layout, layout.part, width, height)
            layouts.append(
                LayoutInfo(
                    layout_id=lid,
                    name=layout.name or lid,
                    master_id=master_ids[master.part.partname],
                    part=part_name,
                    element=layout._element,
                    shapes=shapes,
                    placeholders=[s for s in shapes if s.is_placeholder],
                )
            )
            layout_ids[layout.part.partname] = lid

    slides: list[SlideInfo] = []
    notes_with_text = 0
    for index, slide in enumerate(prs.slides, start=1):
        part_name = str(slide.part.partname).lstrip("/")
        layout = slide.slide_layout
        lid = layout_ids.get(layout.part.partname, "")
        mid = master_ids.get(layout.slide_master.part.partname, "")
        notes = ""
        if slide.has_notes_slide:
            tf = slide.notes_slide.notes_text_frame
            notes = (tf.text if tf is not None else "").strip()
            if notes:
                notes_with_text += 1
        slides.append(
            SlideInfo(
                index=index,
                part=part_name,
                layout_id=lid,
                master_id=mid,
                hidden=slide._element.get("show") == "0",
                shapes=walk_shapes(slide, slide.part, width, height),
                notes_text=notes,
                element=slide._element,
                slide=slide,
            )
        )
    for lay in layouts:
        lay.sample_slide_count = sum(1 for s in slides if s.layout_id == lay.layout_id)

    return TemplatePackage(
        path=path,
        prs=prs,
        width_emu=width,
        height_emu=height,
        masters=masters,
        layouts=layouts,
        slides=slides,
        embedded_fonts=_embedded_fonts(prs),
        embedded_font_faces=_embedded_font_faces(prs),
        guides=_guides(path, width, height),
        integrity=integrity,
        media_count=sum(1 for n in names if n.startswith("ppt/media/")),
        chart_parts=sum(1 for n in names if n.startswith("ppt/charts/chart")),
        notes_with_text=notes_with_text,
    )


def _embedded_fonts(prs: Any) -> list[str]:
    out: list[str] = []
    lst = prs._element.find("p:embeddedFontLst", NS)
    if lst is None:
        return out
    for font in lst.findall("p:embeddedFont/p:font", NS):
        if font.get("typeface"):
            out.append(font.get("typeface"))
    return sorted(set(out))


def _embedded_font_faces(prs: Any) -> int:
    lst = prs._element.find("p:embeddedFontLst", NS)
    if lst is None:
        return 0
    return sum(
        1
        for font in lst.findall("p:embeddedFont", NS)
        for child in font
        if child.tag.split("}")[-1] in ("regular", "bold", "italic", "boldItalic")
    )


def _guides(path: pathlib.Path, width: int, height: int) -> list[Guide]:
    """Направляющие из ppt/viewProps.xml (p:guideLst и p15:sldGuideLst)."""
    from lxml import etree

    out: list[Guide] = []
    try:
        with zipfile.ZipFile(path) as zf:
            if "ppt/viewProps.xml" not in zf.namelist():
                return out
            root = etree.fromstring(zf.read("ppt/viewProps.xml"))
    except (zipfile.BadZipFile, etree.XMLSyntaxError):
        return out
    seen: set[tuple[str, float]] = set()
    for guide in root.iter():
        tag = guide.tag.split("}")[-1]
        if tag != "guide" or guide.get("pos") is None:
            continue
        orient = "horizontal" if guide.get("orient") == "horz" else "vertical"
        try:
            emu = float(guide.get("pos", 0)) * GUIDE_UNIT_EMU
        except ValueError:
            continue
        pos = emu / (height if orient == "horizontal" else width)
        key = (orient, round(pos, 4))
        if key in seen or not 0 <= pos <= 1:
            continue
        seen.add(key)
        out.append(Guide(orient, round(pos, 4)))
    return out
