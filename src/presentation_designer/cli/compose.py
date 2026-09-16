"""CLI `compose`: PPTX и ComposedDeck по плану варианта, профилю, исходному шаблону и пакету.

    uv run -m presentation_designer.cli compose runs/plan/balanced.json \\
        --profile runs/analyze/profile.json --template tests/fixtures/pptx/mini_template.pptx \\
        --content runs/import/package.json --out runs/compose/balanced/
    … --assets DIR       каталог с ресурсами пакета (по умолчанию каталог package.json)
    … --prune-layouts    убрать неиспользуемые макеты (файл меньше, набор макетов меняется)
    … --pdf              дополнительно PDF через LibreOffice для собственной проверки
    … --report           печатать отчёт в stderr

Выходы: `<out>/deck.pptx`, `<out>/composed.json` (ComposedDeck), `<out>/plan.json` (копия плана),
`<out>/report.json` (время шагов, счётчики, проверка пакета, предупреждения) и
`<out>/manifest.json` со ссылками на входы и sha256 выходов. Тот же композер использует
воркер (`pipeline/real.py`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import sys
from typing import Any

from presentation_designer.contracts import ComposedDeck
from presentation_designer.layout.compose import (
    COMPOSER_NAME,
    COMPOSER_VERSION,
    ComposeError,
    compose_deck,
)
from presentation_designer.layout.composed import now_iso


def build_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(prog="presentation-designer compose")
    parser.add_argument("plan", help="<variant>.json (SlidePlan)")
    parser.add_argument("--profile", required=True, help="profile.json (TemplateProfile)")
    parser.add_argument(
        "--template", required=True, help="исходный PPTX, по которому построен профиль"
    )
    parser.add_argument("--content", required=True, help="package.json (ContentPackage)")
    parser.add_argument("--assets", default=None, help="каталог ресурсов пакета")
    parser.add_argument("--out", default="runs/compose/balanced", help="каталог выходов")
    parser.add_argument("--job-id", default="job_local")
    parser.add_argument("--revision", type=int, default=1)
    parser.add_argument("--prune-layouts", action="store_true", help="убрать неиспользуемые макеты")
    parser.add_argument("--pdf", action="store_true", help="PDF через LibreOffice для проверки")
    parser.add_argument("--report", action="store_true", help="печатать отчёт в stderr")
    return parser


def _load(path: str) -> dict[str, Any]:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args: argparse.Namespace) -> int:
    for path in (args.plan, args.profile, args.template, args.content):
        if not pathlib.Path(path).is_file():
            print(f"файл не найден: {path}", file=sys.stderr)
            return 2
    plan = _load(args.plan)
    profile = _load(args.profile)
    package = _load(args.content)
    assets_dir = pathlib.Path(args.assets) if args.assets else pathlib.Path(args.content).parent
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    variant_id = str((plan.get("variant") or {}).get("variant_id") or "balanced")
    try:
        result = compose_deck(
            plan,
            profile,
            pathlib.Path(args.template),
            package,
            out_pptx=out / "deck.pptx",
            package_dir=assets_dir,
            job_id=args.job_id,
            variant_id=variant_id,
            revision=args.revision,
            pptx_artifact=f"{variant_id}/r{args.revision}/deck.pptx",
            prune_layouts=args.prune_layouts,
        )
    except ComposeError as e:
        print(f"сборка не удалась ({e.code}): {e}", file=sys.stderr)
        return 2
    ComposedDeck.model_validate(result.deck)
    (out / "composed.json").write_text(
        json.dumps(result.deck, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    shutil.copyfile(args.plan, out / "plan.json")
    (out / "report.json").write_text(
        json.dumps(result.report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    outputs: dict[str, Any] = {
        "pptx": "deck.pptx",
        "composed_deck": "composed.json",
        "plan": "plan.json",
        "report": "report.json",
    }
    if args.pdf:
        try:
            from presentation_designer.export.pdf import convert_to_pdf

            pdf = convert_to_pdf(out / "deck.pptx", out)
            outputs["pdf"] = pdf.pdf_path.name
            print(f"PDF: {pdf.pdf_path} за {pdf.seconds:.1f} с")
        except Exception as e:
            print(f"PDF не построен: {e}", file=sys.stderr)
    manifest = {
        "command": "compose",
        "created_at": now_iso(),
        "composer": {"name": COMPOSER_NAME, "version": COMPOSER_VERSION},
        "inputs": {
            name: {"path": path, "sha256": _sha(pathlib.Path(path))}
            for name, path in (
                ("plan", args.plan),
                ("profile", args.profile),
                ("template", args.template),
                ("package", args.content),
            )
        },
        "assets_dir": str(assets_dir),
        "outputs": {
            key: {"path": name, "sha256": _sha(out / name)} for key, name in outputs.items()
        },
        "slides": len(result.slide_titles),
        "warnings": result.warnings,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    if args.report:
        print(json.dumps(result.report, ensure_ascii=False, indent=1), file=sys.stderr)
    stats = result.deck.get("stats") or {}
    print(
        f"{variant_id}: слайдов {len(result.slide_titles)}, объектов {stats.get('objects')}, "
        f"таблиц {stats.get('tables')}, диаграмм {stats.get('charts')}, "
        f"предупреждений {len(result.warnings)}, "
        f"{result.report['timings_ms']['total']} мс, "
        f"{result.report['file_size_bytes'] // 1024} КБ → {out / 'deck.pptx'}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
