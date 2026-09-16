"""Сборка на четырёх целевых шаблонах (закрытые материалы, маркер organizer_data): профили
анализатором без рендера и VLM, планы трёх вариантов без модели, контент-пакет и смысловой
план — собственные. Проверяются свойства, не зависящие от формулировок: PPTX открывается
python-pptx, число и порядок слайдов по плану, пакет проходит проверку целостности без
недостижимых частей, факты подставлены, таблицы и диаграммы плана стали нативными объектами,
ComposedDeck сверяется с файлом по идентификаторам объектов и частям, макеты и мастера
сохранены. Отчёт по каждому шаблону — в runs/compose-tests/<имя>.json.
"""

from __future__ import annotations

import json
import pathlib
import time
import zipfile
from typing import Any

import pytest
from pptx import Presentation

from presentation_designer.contracts import ComposedDeck
from presentation_designer.generation.variants import VARIANTS
from presentation_designer.layout.compose import compose_deck
from presentation_designer.layout.shapes import iter_shapes
from presentation_designer.layout.text import FACT_REF
from presentation_designer.parsing.template.analyzer import analyze_template
from presentation_designer.shared.settings import ROOT

pytestmark = pytest.mark.organizer_data

REPORT_DIR = ROOT / "runs" / "compose-tests"
TEMPLATES = (
    "VK Tech шаблон.pptx",
    "VK_WorkSpace_Клиентская_конференция_Шаблон_03.pptx",
    "Шаблон презентации VK Education.pptx",
    "ЛЦТ2026 Шаблон презентации.pptx",
)


@pytest.mark.parametrize("name", TEMPLATES, ids=lambda n: n.split(".")[0][:24])
def test_compose_three_variants_on_organizer_template(
    organizer_dir: pathlib.Path,
    name: str,
    example_package: dict[str, Any],
    make_plan: Any,
    tmp_path: pathlib.Path,
) -> None:
    path = organizer_dir / name
    if not path.exists():
        pytest.fail(f"нет файла {path}: прогон организаторов не должен пропускаться молча")
    profile = analyze_template(
        path,
        template_id="tpl_org",
        name=name,
        size_bytes=path.stat().st_size,
        render=False,
        use_vlm=False,
    ).profile
    package = {k: v for k, v in example_package.items() if not k.startswith("_")}
    assets = pathlib.Path(example_package["_assets_dir"])
    with zipfile.ZipFile(path) as zf:
        source_layouts = {n for n in zf.namelist() if n.startswith("ppt/slideLayouts/slideLayout")}
        source_masters = {n for n in zf.namelist() if n.startswith("ppt/slideMasters/slideMaster")}
    report: dict[str, Any] = {"template": name, "variants": {}}
    for variant_id in VARIANTS:
        plan = make_plan(profile, variant_id)
        started = time.perf_counter()
        result = compose_deck(
            plan,
            profile,
            path,
            package,
            out_pptx=tmp_path / f"{variant_id}.pptx",
            package_dir=assets,
            job_id="job_org",
            variant_id=variant_id,
        )
        elapsed = int((time.perf_counter() - started) * 1000)
        deck = result.deck
        ComposedDeck.model_validate(deck)
        assert result.integrity.ok, result.integrity.errors
        assert result.integrity.unreachable_parts == []
        prs = Presentation(str(result.pptx_path))
        assert len(prs.slides) == len(plan["slides"]) == deck["stats"]["slides"]
        planned_tables = sum(1 for s in plan["slides"] for b in s["blocks"] if b["kind"] == "table")
        planned_charts = sum(1 for s in plan["slides"] for b in s["blocks"] if b["kind"] == "chart")
        tables = charts = 0
        for slide, plan_slide, deck_slide in zip(
            prs.slides, plan["slides"], deck["slides"], strict=True
        ):
            assert deck_slide["slide_id"] == plan_slide["slide_id"]
            shapes = list(iter_shapes(slide))
            by_id = {str(s.shape_id): s for s in shapes}
            texts = "\n".join(s.text_frame.text for s in shapes if s.has_text_frame)
            assert FACT_REF.search(texts) is None
            # Заголовок плана присутствует на слайде (текст слота title).
            title_obj = next(
                (o for o in deck_slide["objects"] if o.get("slot_kind") == "title"), None
            )
            if title_obj is not None:
                assert title_obj["text"]["plain"] == plan_slide["title"]
            for obj in deck_slide["objects"]:
                assert obj["object_id"] in by_id, (name, deck_slide["slide_id"], obj["object_id"])
            for removed in deck_slide["removed_object_ids"]:
                assert removed not in by_id
            tables += sum(1 for s in shapes if getattr(s, "has_table", False) and s.has_table)
            charts += sum(1 for s in shapes if getattr(s, "has_chart", False) and s.has_chart)
        assert tables >= planned_tables and charts >= planned_charts
        with zipfile.ZipFile(result.pptx_path) as zf:
            names = set(zf.namelist())
            # Макеты и мастера исходного шаблона сохранены (макеты не удаляются по умолчанию).
            assert source_layouts <= names and source_masters <= names
            for deck_slide in deck["slides"]:
                for obj in deck_slide["objects"]:
                    if obj["kind"] == "chart":
                        assert obj["chart"]["chart_part"] in names
                        assert obj["chart"].get("built") in ("replaced", "rebuilt", "added")
        codes = sorted({w["code"] for w in result.warnings})
        report["variants"][variant_id] = {
            "slides": len(plan["slides"]),
            "objects": deck["stats"]["objects"],
            "removed_objects": deck["stats"]["removed_objects"],
            "tables": tables,
            "charts": charts,
            "file_size_kb": deck["stats"]["file_size_bytes"] // 1024,
            "compose_ms": elapsed,
            "timings_ms": result.report["timings_ms"],
            "warnings": codes,
        }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / f"{name.split('.')[0][:40]}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
