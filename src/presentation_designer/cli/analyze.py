"""CLI `analyze`: профиль шаблона из PPTX с манифестом прогона.

    uv run -m presentation_designer.cli analyze "data/organizers/VK Tech шаблон.pptx" \
        --out runs/analyze/
    … --no-render          без миниатюр (нет ONLYOFFICE)
    … --no-vlm             только эвристики
    … --report             печатать сводку анализа в stderr

Выходы: `<out>/profile.json` (TemplateProfile), `<out>/previews/slide-NN.png`, `<out>/report.json`
(время шагов, роли, исключённые слайды, замечания VLM) и `<out>/manifest.json` со ссылкой на
исходный файл. Тот же анализатор использует воркер (`pipeline/real.py`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time
from typing import Any

from presentation_designer.parsing.template.analyzer import (
    ANALYZER_VERSION,
    PackageError,
    analyze_template,
)
from presentation_designer.pipeline.state import now_iso
from presentation_designer.shared.settings import get_settings


def build_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(prog="presentation-designer analyze")
    parser.add_argument("pptx", help="файл шаблона PPTX")
    parser.add_argument("--out", default="runs/analyze", help="каталог выходов")
    parser.add_argument("--no-render", action="store_true", help="не рендерить миниатюры")
    parser.add_argument("--no-vlm", action="store_true", help="не уточнять роли через VLM")
    parser.add_argument("--report", action="store_true", help="печатать сводку в stderr")
    parser.add_argument("--template-id", default=None)
    return parser


def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    path = pathlib.Path(args.pptx)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
    template_id = args.template_id or f"tpl_{sha256[:12] or 'missing'}"
    client = None
    if not args.no_vlm:
        try:
            from presentation_designer.llm import build_client

            candidate = build_client(settings)
            if candidate.target("vlm").provider.configured():
                client = candidate
            else:
                print("провайдер моделей не настроен: роли только по эвристикам", file=sys.stderr)
        except Exception as e:
            print(f"клиент моделей не создан ({e}): роли только по эвристикам", file=sys.stderr)
    started = time.perf_counter()
    try:
        result = analyze_template(
            path,
            template_id=template_id,
            name=path.name,
            size_bytes=path.stat().st_size if path.is_file() else 0,
            sha256=sha256,
            settings=settings,
            llm_client=client,
            render=not args.no_render,
            use_vlm=not args.no_vlm,
            workdir=out,
        )
    except PackageError as e:
        print(f"шаблон не поддерживается ({e.code}): {e}", file=sys.stderr)
        return 2
    (out / "profile.json").write_text(json.dumps(result.profile, ensure_ascii=False, indent=1))
    for name, data in result.previews.items():
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    report: dict[str, Any] = result.report.as_dict()
    report["wall_ms"] = int((time.perf_counter() - started) * 1000)
    report["vlm_used"] = client is not None and bool(result.report.vlm)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    manifest = {
        "command": "analyze",
        "created_at": now_iso(),
        "analyzer": {"name": "template_analyzer", "version": ANALYZER_VERSION},
        "inputs": {"template": {"path": str(path), "sha256": sha256}},
        "outputs": {
            "profile": "profile.json",
            "report": "report.json",
            "previews": sorted(result.previews),
        },
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    if args.report:
        print(json.dumps(report, ensure_ascii=False, indent=1), file=sys.stderr)
    counts = report["counts"]
    print(
        f"{path.name}: паттернов {counts['patterns']} (групп {counts['pattern_groups']}), "
        f"ресурсов {counts['assets']}, постоянных элементов {counts['fixed_elements']}, "
        f"правил {counts['guidelines']}, превью {report['previews_rendered']}, "
        f"{report['wall_ms']} мс → {out}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
