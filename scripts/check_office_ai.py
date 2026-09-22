"""Test a text-only AI edit on a disposable copy, never an OfficeStore revision.

Requires an explicit target and either --plan (offline) or --live-model (network).
--render additionally checks real ONLYOFFICE conversion and saves slide previews.
Output must be a NEW directory. No editor session or plugin is opened/enabled.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from time import monotonic
from typing import Any

from presentation_designer.export.pdf import convert_to_pdf
from presentation_designer.export.thumbnails import pdf_page_count, render_thumbnails
from presentation_designer.generation.office_object_edit import (
    ObjectEditPlan,
    patch_object,
    propose,
)
from presentation_designer.generation.office_objects import ObjectTarget, selected
from presentation_designer.generation.office_preservation import verify_selected_text_edit
from presentation_designer.parsing.template.embedded_fonts import prepare_fonts
from presentation_designer.shared.settings import get_settings


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--slide", type=int, required=True)
    parser.add_argument("--shape-id", required=True)
    parser.add_argument("--instruction", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", type=Path)
    mode.add_argument("--live-model", action="store_true")
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    source = args.source.resolve(strict=True)
    if source.stat().st_size > settings.limits.max_upload_mb * 1024 * 1024:
        raise ValueError("PPTX превышает лимит загрузки")
    original = source.read_bytes()
    target = ObjectTarget(slide=args.slide, shape_id=args.shape_id)
    if not selected(original, target).runs:
        raise ValueError("Выбранный объект не содержит доступного текста")
    args.out.mkdir(parents=True, exist_ok=False)
    baseline = args.out / "before.pptx"
    baseline.write_bytes(original)
    # Isolate model cache/log artifacts; retain shared source publication for conversion.
    settings = settings.model_copy(deep=True)
    settings.llm.cache_dir = args.out / "llm-cache"
    settings.llm.cache_mode = "off"
    started = monotonic()
    plan = (
        ObjectEditPlan.model_validate_json(args.plan.read_text(encoding="utf-8"))
        if args.plan
        else await propose(original, args.instruction, settings, target)
    )
    (args.out / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    if plan.position is not None:
        raise ValueError("Текстовый эксперимент не допускает перемещения объекта")
    plan.validate_facts(args.instruction, original)
    updated = patch_object(original, target, plan)
    preservation = verify_selected_text_edit(original, updated, [target])
    if preservation["changed_text_runs"] == 0:
        raise ValueError("Модель не изменила текст; эксперимент не считается успешным")
    candidate = args.out / "after.pptx"
    candidate.write_bytes(updated)
    report: dict[str, Any] = {
        "mode": "live-model" if args.live_model else "offline-plan",
        "target": target.model_dump(),
        "before_sha256": hashlib.sha256(original).hexdigest(),
        "after_sha256": hashlib.sha256(updated).hexdigest(),
        "preservation": preservation,
        "edit_seconds": round(monotonic() - started, 3),
        "editor_session_opened": False,
        "official_ai_plugin_tested": False,
    }
    if args.render:
        prepare_fonts(baseline, settings.paths.data_dir)
        rendered = []
        for path in (baseline, candidate):
            pdf = convert_to_pdf(path, args.out / path.stem, settings=settings)
            pages = pdf_page_count(pdf.pdf_path)
            if pages != preservation["slides"]:
                raise ValueError("Количество страниц PDF не совпало с PPTX")
            thumbnails = render_thumbnails(
                pdf.pdf_path, args.out / path.stem / "slides", width_px=1600, pages=[target.slide]
            )
            rendered.append(
                {
                    "pdf": str(pdf.pdf_path),
                    "pages": pages,
                    "seconds": round(pdf.seconds, 3),
                    "selected_png": str(thumbnails.paths[0]),
                }
            )
        report["onlyoffice_conversion"] = rendered
    if source.read_bytes() != original:
        raise ValueError("Исходный файл изменился во время эксперимента")
    report["source_unchanged"] = True
    report["limitations"] = (
        "No live editor/plugin, visual overflow or linguistic quality acceptance"
    )
    (args.out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
