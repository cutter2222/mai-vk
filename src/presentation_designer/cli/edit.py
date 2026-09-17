"""CLI `edit-slide`: правка одного слайда готового плана по инструкции (скилл `slide_editor`).

    uv run -m presentation_designer.cli edit-slide runs/plan/balanced.json \\
        --story runs/import/story.json --profile runs/analyze/profile.json \\
        --content runs/import/package.json --slide 5 --instruction "сделай заголовок короче" \\
        --out runs/edit/
    … --settings settings.json   настройки запроса (language, seed)
    … --report                   печатать сводку в stderr

Выходы: `<out>/plan.json` (новый SlidePlan; при отказе модели файла нет), `<out>/report.json`
(слайд, композиции, вызовы модели, правки, причина отказа) и `<out>/manifest.json` со ссылками
на входы. Тот же слой использует воркер (`pipeline/real.py`) для задания `slide_edit`;
собрать ревизию по новому плану можно командой `compose`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time
from typing import Any

from presentation_designer.generation.edit import EDIT_VERSION, EditError, edit_slide
from presentation_designer.pipeline.state import now_iso
from presentation_designer.shared.settings import get_settings


def build_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(prog="presentation-designer edit-slide")
    parser.add_argument("plan", help="plan.json варианта (SlidePlan)")
    parser.add_argument("--story", required=True, help="story.json (StoryPlan)")
    parser.add_argument("--profile", required=True, help="profile.json (TemplateProfile)")
    parser.add_argument("--content", required=True, help="package.json (ContentPackage)")
    parser.add_argument("--slide", type=int, required=True, help="номер слайда, с единицы")
    parser.add_argument("--instruction", required=True, help="что изменить на слайде")
    parser.add_argument("--out", default="runs/edit", help="каталог выходов")
    parser.add_argument("--settings", default=None, help="JSON с настройками запроса")
    parser.add_argument("--report", action="store_true", help="печатать сводку в stderr")
    return parser


def _load(path: str) -> dict[str, Any]:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _sha(path: str) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def run(args: argparse.Namespace) -> int:
    app_settings = get_settings()
    for path in (args.plan, args.story, args.profile, args.content):
        if not pathlib.Path(path).is_file():
            print(f"файл не найден: {path}", file=sys.stderr)
            return 2
    if args.slide < 1:
        print("номер слайда считается с единицы", file=sys.stderr)
        return 2
    plan = _load(args.plan)
    story = _load(args.story)
    profile = _load(args.profile)
    package = _load(args.content)
    settings: dict[str, Any] = _load(args.settings) if args.settings else {}
    try:
        from presentation_designer.llm import build_client, get_skill

        client = build_client(app_settings)
        if not client.target("llm").provider.configured():
            print("провайдер моделей не настроен: задайте ключ в .env", file=sys.stderr)
            return 2
        skill = get_skill("slide_editor")
    except Exception as e:
        print(f"клиент моделей не создан: {e}", file=sys.stderr)
        return 2
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        result = edit_slide(
            plan,
            story,
            profile,
            package,
            slide_index=args.slide - 1,
            instruction=args.instruction,
            client=client,
            skill=skill,
            settings=settings,
            app_settings=app_settings,
        )
    except EditError as e:
        print(f"правка не выполнена ({e.code}): {e}", file=sys.stderr)
        if e.details:
            print(json.dumps(e.details, ensure_ascii=False, indent=1)[:2000], file=sys.stderr)
        return 1
    report = dict(result.report)
    report["changed"] = result.changed
    report["change_note"] = result.change_note
    report["reason"] = result.reason
    report["wall_ms"] = int((time.perf_counter() - started) * 1000)
    if result.plan is not None:
        (out / "plan.json").write_text(
            json.dumps(result.plan, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    manifest = {
        "command": "edit-slide",
        "edit_version": EDIT_VERSION,
        "created_at": now_iso(),
        "inputs": {
            "plan": {"path": args.plan, "sha256": _sha(args.plan)},
            "story": {"path": args.story, "sha256": _sha(args.story)},
            "profile": {"path": args.profile, "sha256": _sha(args.profile)},
            "content": {"path": args.content, "sha256": _sha(args.content)},
        },
        "slide": args.slide,
        "instruction": args.instruction,
        "outputs": {
            **({"plan": "plan.json"} if result.plan is not None else {}),
            "report": "report.json",
        },
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    if args.report:
        if result.changed:
            print(
                f"слайд {args.slide} ({result.slide_id}): {report.get('pattern_before')} → "
                f"{report.get('pattern_after')}; {result.change_note}; "
                f"переполнений {len(report.get('overflow') or [])}, {report['wall_ms']} мс",
                file=sys.stderr,
            )
        else:
            print(f"слайд {args.slide}: оставлен как есть — {result.reason}", file=sys.stderr)
    print(str(out / ("plan.json" if result.plan is not None else "report.json")))
    return 0
