"""CLI `plan`: планы трёх вариантов (SlidePlan) из StoryPlan, профиля шаблона и контент-пакета.

    uv run -m presentation_designer.cli plan runs/import/story.json runs/analyze/profile.json \\
        --content runs/import/package.json --variants compact,balanced,detailed --out runs/plan/
    … --settings settings.json   явные настройки запроса (language, slide_count, seed)
    … --no-model                 детерминированный черновик без модели (помечается в плане)
    … --no-cache                 не читать и не писать кэш готовых планов
    … --report                   печатать сводку в stderr

Выходы: `<out>/<variant>.json` (SlidePlan), `<out>/<variant>.report.json` (структура колоды,
пакеты, вызовы модели, правки, счётчики), `<out>/comparison.json` (различимость вариантов) и
`<out>/manifest.json` со ссылками на входы. Тот же планировщик использует воркер
(`pipeline/real.py`) для трёх заданий вариантов.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time
from typing import Any

from presentation_designer.generation.variants import (
    PLAN_VERSION,
    VARIANTS,
    PlanCache,
    PlanError,
    build_variant_plan,
    compare_plans,
    indistinct_warning,
)
from presentation_designer.pipeline.run import resolve_slide_count
from presentation_designer.pipeline.state import now_iso
from presentation_designer.shared.settings import get_settings


def build_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(prog="presentation-designer plan")
    parser.add_argument("story", help="story.json (StoryPlan)")
    parser.add_argument("profile", help="profile.json (TemplateProfile)")
    parser.add_argument("--content", required=True, help="package.json (ContentPackage)")
    parser.add_argument("--variants", default=",".join(VARIANTS), help="через запятую")
    parser.add_argument("--out", default="runs/plan", help="каталог выходов")
    parser.add_argument("--settings", default=None, help="JSON с настройками запроса")
    parser.add_argument("--no-model", action="store_true", help="черновик без модели")
    parser.add_argument("--no-cache", action="store_true", help="без кэша готовых планов")
    parser.add_argument("--report", action="store_true", help="печатать сводку в stderr")
    return parser


def _load(path: str) -> dict[str, Any]:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def run(args: argparse.Namespace) -> int:
    app_settings = get_settings()
    for path in (args.story, args.profile, args.content):
        if not pathlib.Path(path).is_file():
            print(f"файл не найден: {path}", file=sys.stderr)
            return 2
    story = _load(args.story)
    profile = _load(args.profile)
    package = _load(args.content)
    settings: dict[str, Any] = {}
    if args.settings:
        settings = _load(args.settings)
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in variants if v not in VARIANTS]
    if unknown:
        print(f"неизвестные варианты: {', '.join(unknown)}", file=sys.stderr)
        return 2
    client = None
    skill = None
    if not args.no_model:
        try:
            from presentation_designer.llm import build_client, get_skill

            candidate = build_client(app_settings)
            if candidate.target("llm").provider.configured():
                client = candidate
                skill = get_skill("variant_planner")
            else:
                print(
                    "провайдер моделей не настроен: задайте ключ в .env или используйте --no-model",
                    file=sys.stderr,
                )
                return 2
        except Exception as e:
            print(f"клиент моделей не создан: {e}", file=sys.stderr)
            return 2
    cache = None if args.no_cache else PlanCache(app_settings.plan_cache_dir)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    plans: dict[str, dict[str, Any]] = {}
    reports: dict[str, dict[str, Any]] = {}
    failures: dict[str, dict[str, Any]] = {}
    started = time.perf_counter()
    for variant_id in variants:
        t0 = time.perf_counter()
        try:
            result = build_variant_plan(
                story,
                profile,
                package,
                variant_id,
                settings,
                slide_count=resolve_slide_count(variant_id, settings),
                client=client,
                skill=skill,
                app_settings=app_settings,
                use_model=not args.no_model,
                cache=cache,
            )
        except PlanError as e:
            failures[variant_id] = {"code": e.code, "message": str(e), "details": e.details}
            print(f"{variant_id}: план не построен ({e.code}): {e}", file=sys.stderr)
            if e.details:
                print(json.dumps(e.details, ensure_ascii=False, indent=1)[:2000], file=sys.stderr)
            continue
        plans[variant_id] = result.plan
        report = dict(result.report)
        report["wall_ms"] = int((time.perf_counter() - t0) * 1000)
        reports[variant_id] = report
    comparison = compare_plans(plans) if len(plans) >= 2 else {"pairs": [], "indistinct": []}
    for entry in comparison.get("indistinct", []):
        warning = indistinct_warning(entry)
        for vid in entry["variants"]:
            plans[vid].setdefault("warnings", []).append(warning)
    for variant_id, plan in plans.items():
        (out / f"{variant_id}.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        (out / f"{variant_id}.report.json").write_text(
            json.dumps(reports[variant_id], ensure_ascii=False, indent=1), encoding="utf-8"
        )
    (out / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    manifest = {
        "command": "plan",
        "created_at": now_iso(),
        "planner": {"name": "variant_planner", "version": PLAN_VERSION},
        "model_used": client is not None,
        "inputs": {
            name: {
                "path": path,
                "sha256": hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest(),
            }
            for name, path in (
                ("story", args.story),
                ("profile", args.profile),
                ("package", args.content),
            )
        },
        "settings": args.settings,
        "outputs": {
            "plans": {v: f"{v}.json" for v in plans},
            "reports": {v: f"{v}.report.json" for v in plans},
            "comparison": "comparison.json",
        },
        "failures": failures,
        "wall_ms": int((time.perf_counter() - started) * 1000),
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    if args.report:
        print(json.dumps(reports, ensure_ascii=False, indent=1), file=sys.stderr)
    for variant_id, plan in plans.items():
        counts = reports[variant_id].get("counts") or {}
        cov = plan["coverage"]
        hit = " (из кэша)" if reports[variant_id].get("cache_hit") else ""
        print(
            f"{variant_id}: слайдов {len(plan['slides'])}, тезисов {len(cov['covered'])}/"
            f"{len(cov['required_thesis_ids'])}, переполнений {counts.get('overflow', 0)}, "
            f"{reports[variant_id]['wall_ms']} мс{hit} → {out / (variant_id + '.json')}"
        )
    if comparison.get("indistinct"):
        pairs = ", ".join("+".join(e["variants"]) for e in comparison["indistinct"])
        print(f"варианты без различий по композициям и визуализации: {pairs}")
    elif len(plans) >= 2:
        print("варианты различимы по композициям и/или визуализации")
    return 0 if plans and not failures else 2


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
