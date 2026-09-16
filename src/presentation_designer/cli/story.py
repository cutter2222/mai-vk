"""CLI `story`: общий смысловой план StoryPlan из ContentPackage.

    uv run -m presentation_designer.cli story runs/import/package.json --out runs/import/story.json
    … --settings settings.json   явные настройки запроса (language, slide_count, seed)
    … --no-model                 детерминированный черновик по структуре материалов (помечается)
    … --report                   печатать сводку в stderr

Выходы: `<out>` (StoryPlan), рядом `<имя>.report.json` (хеш, усечение выдержки, usage,
правки покрытия) и `<имя>.manifest.json` со ссылкой на пакет. Тот же планировщик
использует воркер (`pipeline/real.py`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time

from presentation_designer.generation.story import STORY_VERSION, StoryError, build_story
from presentation_designer.pipeline.state import now_iso
from presentation_designer.shared.settings import get_settings


def build_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(prog="presentation-designer story")
    parser.add_argument("package", help="package.json (ContentPackage)")
    parser.add_argument("--out", default="runs/import/story.json", help="файл StoryPlan")
    parser.add_argument("--settings", default=None, help="JSON с настройками запроса")
    parser.add_argument("--no-model", action="store_true", help="черновик без модели")
    parser.add_argument("--report", action="store_true", help="печатать сводку в stderr")
    return parser


def run(args: argparse.Namespace) -> int:
    app_settings = get_settings()
    package_path = pathlib.Path(args.package)
    if not package_path.is_file():
        print(f"пакет не найден: {package_path}", file=sys.stderr)
        return 2
    package = json.loads(package_path.read_text(encoding="utf-8"))
    settings = {}
    if args.settings:
        settings = json.loads(pathlib.Path(args.settings).read_text(encoding="utf-8"))
    client = None
    skill = None
    if not args.no_model:
        try:
            from presentation_designer.llm import build_client, get_skill

            candidate = build_client(app_settings)
            if candidate.target("llm").provider.configured():
                client = candidate
                skill = get_skill("story_planner")
            else:
                print(
                    "провайдер моделей не настроен: задайте ключ в .env или используйте --no-model",
                    file=sys.stderr,
                )
                return 2
        except Exception as e:
            print(f"клиент моделей не создан: {e}", file=sys.stderr)
            return 2
    started = time.perf_counter()
    try:
        result = build_story(
            package,
            settings,
            client=client,
            skill=skill,
            app_settings=app_settings,
            use_model=not args.no_model,
        )
    except StoryError as e:
        print(f"план не построен ({e.code}): {e}", file=sys.stderr)
        return 2
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result.story, ensure_ascii=False, indent=1), encoding="utf-8")
    report = dict(result.report)
    report["wall_ms"] = int((time.perf_counter() - started) * 1000)
    report["model_used"] = client is not None
    stem = out.with_suffix("")
    pathlib.Path(f"{stem}.report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    manifest = {
        "command": "story",
        "created_at": now_iso(),
        "planner": {"name": "story_planner", "version": STORY_VERSION},
        "inputs": {
            "package": {
                "path": str(package_path),
                "sha256": hashlib.sha256(package_path.read_bytes()).hexdigest(),
                "package_id": package.get("package_id"),
            },
            "settings": args.settings,
        },
        "outputs": {"story": out.name, "report": f"{stem.name}.report.json"},
    }
    pathlib.Path(f"{stem}.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1)
    )
    if args.report:
        print(json.dumps(report, ensure_ascii=False, indent=1), file=sys.stderr)
    counts = report["counts"]
    coverage = report["coverage"]["must_keep_facts"]
    print(
        f"{result.story['story_id']}: тезисов {counts['theses']} "
        f"(обязательных {counts['required']}), факты {coverage['covered']}/{coverage['total']}, "
        f"{'модель' if client is not None else 'без модели'}, {report['wall_ms']} мс → {out}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
