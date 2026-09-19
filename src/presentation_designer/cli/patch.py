"""CLI `patch-slides`: ручные правки редактора (документ `slide_patch`) → новый план.

    uv run -m presentation_designer.cli patch-slides runs/compose/balanced/plan.json \\
        --patch runs/patch/patch.json --profile runs/analyze/profile.json \\
        --content runs/import/package.json --deck runs/compose/balanced/composed.json \\
        --out runs/patch/
    … --story runs/import/story.json   проверка покрытия тезисов (необязательно)
    … --report                         печатать сводку в stderr

Выходы: `<out>/plan.json` (новый SlidePlan с overrides и порядком), `<out>/report.json`
(сводка, изменённые слайды, нарушения) и `<out>/manifest.json`. Модель не вызывается; тот же
слой (`generation/patch.py`) использует воркер для задания `slide_patch`. Собрать ревизию по
новому плану — командой `compose` (для правок с источником `file` — с `--uploads`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time
from typing import Any

from presentation_designer.generation.patch import PATCH_VERSION, PatchError, apply_patch
from presentation_designer.pipeline.state import now_iso


def build_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(prog="presentation-designer patch-slides")
    parser.add_argument("plan", help="plan.json варианта (SlidePlan базовой ревизии)")
    parser.add_argument("--patch", required=True, help="patch.json (SlidePatch)")
    parser.add_argument("--profile", required=True, help="profile.json (TemplateProfile)")
    parser.add_argument("--content", required=True, help="package.json (ContentPackage)")
    parser.add_argument("--deck", default=None, help="composed.json базовой ревизии")
    parser.add_argument("--story", default=None, help="story.json (StoryPlan)")
    parser.add_argument("--out", default="runs/patch", help="каталог выходов")
    parser.add_argument("--report", action="store_true", help="печатать сводку в stderr")
    return parser


def _load(path: str) -> dict[str, Any]:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _sha(path: str) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def run(args: argparse.Namespace) -> int:
    paths = [args.plan, args.patch, args.profile, args.content]
    if args.deck:
        paths.append(args.deck)
    if args.story:
        paths.append(args.story)
    for path in paths:
        if not pathlib.Path(path).is_file():
            print(f"файл не найден: {path}", file=sys.stderr)
            return 2
    plan = _load(args.plan)
    patch = _load(args.patch)
    profile = _load(args.profile)
    package = _load(args.content)
    deck = _load(args.deck) if args.deck else None
    story = _load(args.story) if args.story else None
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        result = apply_patch(plan, patch, deck, profile, package, story)
    except PatchError as e:
        print(f"правки не применены ({e.code}): {e}", file=sys.stderr)
        if e.details:
            print(json.dumps(e.details, ensure_ascii=False, indent=1)[:2000], file=sys.stderr)
        return 1
    report = dict(result.report)
    report["warnings"] = result.warnings
    report["wall_ms"] = int((time.perf_counter() - started) * 1000)
    (out / "plan.json").write_text(
        json.dumps(result.plan, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    manifest = {
        "command": "patch-slides",
        "patch_version": PATCH_VERSION,
        "created_at": now_iso(),
        "inputs": {
            name: {"path": path, "sha256": _sha(path)}
            for name, path in (
                ("plan", args.plan),
                ("patch", args.patch),
                ("profile", args.profile),
                ("content", args.content),
                *((("deck", args.deck),) if args.deck else ()),
                *((("story", args.story),) if args.story else ()),
            )
        },
        "outputs": {"plan": "plan.json", "report": "report.json"},
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    if args.report:
        print(
            f"{result.summary}; изменены слайды {', '.join(result.changed_slide_ids)}; "
            f"предупреждений {len(result.warnings)}, {report['wall_ms']} мс",
            file=sys.stderr,
        )
    print(str(out / "plan.json"))
    return 0
