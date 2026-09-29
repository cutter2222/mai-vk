"""Пересборка готового задания по сохранённому плану — без вызовов модели.

Для доработки оформления: план варианта (после слоя design) остаётся тем же, а вёрстка,
собственные композиции и экспорт — из текущего кода. Композиции библиотеки заново
регистрируются в профиле, поэтому правка геометрии видна без повторного анализа шаблона.

С хоста (стенд поднят с docker/compose.dev.yaml — код примонтирован):
    uv run python scripts/rerender.py job_… [job_…] --variants balanced --label look-1
Листы миниатюр — runs/rerender/<label>/<job>/<variant>-contact.png; рядом pptx, pdf и html.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

CONTAINER = "presentation-designer-worker-generation-1"
ROOT = pathlib.Path(__file__).resolve().parent.parent


def inside(job_id: str, variants: list[str], out: pathlib.Path, photos: bool = False) -> None:
    """Сборка и PDF внутри воркера: там шаблоны, пакеты и ONLYOFFICE."""
    from presentation_designer.export.pdf import convert_to_pdf
    from presentation_designer.layout.compose import compose_deck
    from presentation_designer.library.register import builtin_patterns
    from presentation_designer.pipeline.jobs import get_orchestrator
    from presentation_designer.shared.settings import get_settings

    o = get_orchestrator()
    settings = get_settings()
    gen = o.state.get_generation(job_id)
    template = o.state.get_template(gen["template_id"])
    package = o.state.get_package(gen["package_id"])
    template_path = o.files.path_for(template["sha256"])
    profile = dict(template["profile"])
    own = [
        p for p in profile.get("patterns") or [] if (p.get("source") or {}).get("kind") != "builtin"
    ]
    profile["patterns"] = own + builtin_patterns(profile)
    jobs_dir = pathlib.Path(settings.artifacts_dir) / "jobs" / job_id
    for variant in variants:
        revisions = sorted(
            jobs_dir.joinpath(variant).glob("r*/plan.json"), key=lambda p: int(p.parent.name[1:])
        )
        if not revisions:
            print(f"{job_id}/{variant}: плана нет", file=sys.stderr)
            continue
        plan = json.loads(revisions[-1].read_text(encoding="utf-8"))
        from presentation_designer.design.labels import trim_repeated_values

        plan, _trimmed = trim_repeated_values(plan)
        if photos:
            # Подбор фото слоя design (скилл visual_picker и фотобанк) на сохранённом плане.
            import logging
            import types

            from presentation_designer.pipeline.real import RealLayers

            logging.basicConfig(level=logging.WARNING, format="    %(message)s")
            logging.getLogger("presentation_designer.pipeline.real").setLevel(logging.INFO)
            layers = RealLayers(settings)
            inp = types.SimpleNamespace(
                package_dir=o.artifacts.package_dir(gen["package_id"]),
                template_profile=profile,
                variant_id=variant,
                job_id=job_id,
            )
            plan = layers._with_photos(inp, plan, set())  # type: ignore[arg-type]
        target = out / job_id / variant
        target.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        result = compose_deck(
            plan,
            profile,
            pathlib.Path(template_path),
            package["package"] or {},
            out_pptx=target / "deck.pptx",
            package_dir=o.artifacts.package_dir(gen["package_id"]),
            job_id=job_id,
            variant_id=variant,
            media_dir=target / "media",
            fit_min_ratio=float(settings.plan.min_font_ratio),
            fit_min_body_pt=float(settings.plan.min_body_pt),
            fit_min_title_pt=float(settings.plan.min_title_pt),
        )
        composed = time.monotonic() - started
        from presentation_designer.export.html import build_html

        # Нативный HTML, как у конвейера (`export/deck.py`): колода сдаётся в pptx, pdf и html.
        brief = (package["package"] or {}).get("brief") or {}
        title = str(brief.get("title") or (result.slide_titles or [job_id])[0])
        (target / "deck.html").write_text(
            build_html(title, result.deck, target / "deck.pptx", result.slide_titles),
            encoding="utf-8",
        )
        pdf = convert_to_pdf(target / "deck.pptx", target, settings=settings)
        codes = sorted({str(w.get("code")) for w in result.warnings})
        print(
            f"{job_id}/{variant}: {len(result.slide_titles)} слайдов, сборка {composed:.1f} с, "
            f"pdf {time.monotonic() - started - composed:.1f} с; предупреждения: {codes}"
        )
        if pathlib.Path(pdf.pdf_path) != target / "deck.pdf":
            (target / "deck.pdf").write_bytes(pathlib.Path(pdf.pdf_path).read_bytes())


def outside(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT / "scripts"))
    from live_quality import contact_sheet

    remote = f"/tmp/rerender/{args.label}"
    for job_id in args.jobs:
        cmd = [
            "docker", "exec", "-w", "/app", CONTAINER, "python", "/app/scripts/rerender.py",
            job_id, "--variants", args.variants, "--inside", remote,
            *(["--photos"] if args.photos else []),
        ]  # fmt: skip
        if subprocess.run(cmd).returncode:
            return 1
        local = ROOT / "runs" / "rerender" / args.label / job_id
        local.mkdir(parents=True, exist_ok=True)
        for variant in args.variants.split(","):
            src = f"{CONTAINER}:{remote}/{job_id}/{variant}"
            subprocess.run(
                ["docker", "cp", f"{src}/deck.pdf", str(local / f"{variant}.pdf")], check=True
            )
            subprocess.run(
                ["docker", "cp", f"{src}/deck.pptx", str(local / f"{variant}.pptx")], check=True
            )
            subprocess.run(
                ["docker", "cp", f"{src}/deck.html", str(local / f"{variant}.html")], check=True
            )
            contact_sheet(local / f"{variant}.pdf", local, variant)
            print(f"    {local / f'{variant}-contact.png'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("jobs", nargs="+")
    ap.add_argument("--variants", default="balanced")
    ap.add_argument("--label", default="look")
    ap.add_argument("--photos", action="store_true", help="подобрать фото к слайдам (модель)")
    ap.add_argument("--inside", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.inside:
        for job_id in args.jobs:
            inside(job_id, args.variants.split(","), pathlib.Path(args.inside), args.photos)
        return 0
    return outside(args)


if __name__ == "__main__":
    raise SystemExit(main())
