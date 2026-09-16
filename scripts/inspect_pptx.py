"""Осмотр PPTX: состав пакета, слайды и признаки, важные для клонирования.

Печатает сводку и пишет JSON. Функции переиспользует scripts/probe_pptx.py, чтобы выбирать
образцы с группами, обрезкой картинок, смешанными стилями текста, таблицами и диаграммами.

Запуск: uv run scripts/inspect_pptx.py data/organizers/*.pptx --json runs/pptx-probe/inspect.json
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
import time
import zipfile
from typing import Any

from lxml import etree
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from presentation_designer.layout import ooxml

NS_A = ooxml.NS_A
NS_P = ooxml.NS_P


def _walk_shapes(shapes: Any, depth: int = 0) -> Any:
    for shape in shapes:
        yield shape, depth
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _walk_shapes(shape.shapes, depth + 1)


def _run_style_key(run_element: Any) -> str:
    rpr = run_element.find(f"{{{NS_A}}}rPr")
    if rpr is None:
        return ""
    return etree.tostring(rpr, method="c14n").decode()


def inspect_slide(slide: Any, index: int) -> dict[str, Any]:
    """Признаки слайда: группы, обрезка, смешанные стили, таблицы, диаграммы, картинки."""
    info: dict[str, Any] = {
        "index": index,
        "id": slide.slide_id,
        "layout": slide.slide_layout.name,
        "shapes": 0,
        "groups": 0,
        "group_depth": 0,
        "pictures": 0,
        "cropped_pictures": 0,
        "tables": 0,
        "charts": 0,
        "placeholders": 0,
        "text_shapes": 0,
        "mixed_style_paragraphs": 0,
        "fonts": [],
        "notes": bool(slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip()),
        "title": "",
    }
    fonts: collections.Counter[str] = collections.Counter()
    sp_tree = slide.shapes._spTree
    for shape, depth in _walk_shapes(slide.shapes):
        info["shapes"] += 1
        info["group_depth"] = max(info["group_depth"], depth)
        kind = shape.shape_type
        if kind == MSO_SHAPE_TYPE.GROUP:
            info["groups"] += 1
        elif kind == MSO_SHAPE_TYPE.PICTURE:
            info["pictures"] += 1
            src_rect = shape._element.find(f".//{{{NS_A}}}srcRect")
            if src_rect is not None and any(v not in ("", "0") for v in src_rect.attrib.values()):
                info["cropped_pictures"] += 1
        elif kind == MSO_SHAPE_TYPE.PLACEHOLDER:
            info["placeholders"] += 1
        if getattr(shape, "has_table", False) and shape.has_table:
            info["tables"] += 1
        if getattr(shape, "has_chart", False) and shape.has_chart:
            info["charts"] += 1
        if shape.has_text_frame and shape.text_frame.text.strip():
            info["text_shapes"] += 1
            if not info["title"]:
                info["title"] = shape.text_frame.text.strip().split("\n")[0][:80]
            for paragraph in shape.text_frame.paragraphs:
                keys = {_run_style_key(r._r) for r in paragraph.runs if r.text.strip()}
                if len(keys) > 1:
                    info["mixed_style_paragraphs"] += 1
    for latin in sp_tree.iter(f"{{{NS_A}}}latin"):
        fonts[latin.get("typeface", "")] += 1
    info["fonts"] = sorted(f for f in fonts if f and not f.startswith("+"))
    return info


def inspect_presentation(path: pathlib.Path) -> dict[str, Any]:
    started = time.perf_counter()
    prs = Presentation(str(path))
    slides = [inspect_slide(slide, i) for i, slide in enumerate(prs.slides)]
    load_seconds = time.perf_counter() - started
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        total_unzipped = sum(i.file_size for i in zf.infolist())
    parts: collections.Counter[str] = collections.Counter()
    for name in names:
        m = re.match(r"ppt/(\w+)/", name)
        if m:
            parts[m.group(1)] += 1
    media_ext = collections.Counter(
        pathlib.Path(n).suffix.lower() for n in names if n.startswith("ppt/media/")
    )
    layouts = [layout.name for master in prs.slide_masters for layout in master.slide_layouts]
    report = ooxml.check_package(path)
    summary = {
        "path": str(path),
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "unzipped_bytes": total_unzipped,
        "load_seconds": round(load_seconds, 3),
        "slide_size_emu": [prs.slide_width, prs.slide_height],
        "slides": len(slides),
        "masters": len(prs.slide_masters),
        "layouts": len(layouts),
        "parts": dict(sorted(parts.items())),
        "media": dict(sorted(media_ext.items())),
        "embedded_fonts": [n for n in names if n.startswith("ppt/fonts/")],
        "chart_parts": sum(1 for n in names if re.match(r"ppt/charts/chart\d+\.xml", n)),
        "smartart_parts": sum(1 for n in names if n.startswith("ppt/diagrams/")),
        "embeddings": sum(1 for n in names if n.startswith("ppt/embeddings/")),
        "sections": _sections(prs),
        "package_check": report.as_dict(),
        "totals": {
            key: sum(int(s[key]) for s in slides)
            for key in (
                "shapes",
                "groups",
                "pictures",
                "cropped_pictures",
                "tables",
                "charts",
                "placeholders",
                "mixed_style_paragraphs",
            )
        },
        "fonts": sorted({f for s in slides for f in s["fonts"]}),
        "slide_details": slides,
    }
    return summary


def _sections(prs: Any) -> list[dict[str, Any]]:
    result = []
    for section in prs.part._element.iter(f"{{{ooxml.NS_P14}}}section"):
        result.append(
            {
                "name": section.get("name"),
                "slides": len(list(section.iter(f"{{{ooxml.NS_P14}}}sldId"))),
            }
        )
    return result


def print_summary(summary: dict[str, Any]) -> None:
    t = summary["totals"]
    print(
        f"{summary['name']}: {summary['size_bytes'] / 1e6:.1f} МБ, слайдов {summary['slides']}, "
        f"макетов {summary['layouts']}, мастеров {summary['masters']}, "
        f"открытие {summary['load_seconds']} с"
    )
    print(
        f"  фигур {t['shapes']}, групп {t['groups']}, картинок {t['pictures']} "
        f"(с обрезкой {t['cropped_pictures']}), таблиц {t['tables']}, "
        f"диаграмм на слайдах {t['charts']} "
        f"(частей {summary['chart_parts']}), плейсхолдеров {t['placeholders']}, "
        f"абзацев со смешанными стилями {t['mixed_style_paragraphs']}"
    )
    fonts = ", ".join(summary["fonts"]) or "—"
    print(f"  шрифты: {fonts}; встроенные: {len(summary['embedded_fonts'])}")
    check = summary["package_check"]
    status = "ок" if check["ok"] else f"ошибок {len(check['errors'])}"
    print(
        f"  пакет: {check['parts']} частей, недостижимых {len(check['unreachable_parts'])}, "
        f"предупреждений {len(check['warnings'])}, связи: {status}"
    )
    for error in check["errors"][:5]:
        print("    ", error)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Осмотр PPTX для проверки движка")
    parser.add_argument("paths", nargs="+", type=pathlib.Path)
    parser.add_argument("--json", type=pathlib.Path, help="куда записать сводку (список по файлам)")
    args = parser.parse_args(argv)
    summaries = []
    for path in args.paths:
        summary = inspect_presentation(path)
        print_summary(summary)
        summaries.append(summary)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + "\n")
        print(f"записано {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
