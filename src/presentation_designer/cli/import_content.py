"""CLI `import`: ContentPackage из каталога материалов или отдельных файлов.

    uv run -m presentation_designer.cli import examples/content/ --out runs/import/
    … --brief brief.json    бриф отдельным файлом (по умолчанию <каталог>/brief.json)
    … --no-model            без уточнения контекста фактов моделью
    … --no-cache            перечитать файлы, минуя кэш разбора
    … --report              печатать сводку импорта в stderr

Выходы: `<out>/package.json` (ContentPackage), `<out>/assets/…` (изображения пакета),
`<out>/report.json` (время шагов, счётчики, кэш, вызовы модели, предупреждения) и
`<out>/manifest.json` со ссылками на входные файлы. Тот же импортёр использует воркер
(`pipeline/real.py`): CLI и API дают одинаково проверяемые пакеты.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import tempfile
import time

from presentation_designer.parsing.content.importer import (
    IMPORTER_VERSION,
    MaterialFile,
    import_content,
)
from presentation_designer.parsing.content.parsers import ParseCache
from presentation_designer.pipeline.files import format_for
from presentation_designer.pipeline.state import now_iso
from presentation_designer.shared.settings import get_settings

BRIEF_FILE = "brief.json"


def build_parser(parser: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    parser = parser or argparse.ArgumentParser(prog="presentation-designer import")
    parser.add_argument("inputs", nargs="+", help="каталог контент-пакета или файлы материалов")
    parser.add_argument("--out", default="runs/import", help="каталог выходов")
    parser.add_argument("--brief", default=None, help="JSON с брифом (purpose, title, …)")
    parser.add_argument("--no-model", action="store_true", help="без уточнения фактов моделью")
    parser.add_argument("--no-cache", action="store_true", help="не использовать кэш разбора")
    parser.add_argument("--report", action="store_true", help="печатать сводку в stderr")
    parser.add_argument("--package-id", default=None)
    return parser


def collect_files(inputs: list[str]) -> tuple[list[pathlib.Path], pathlib.Path | None]:
    """Файлы в порядке имён; brief.json из каталога не считается материалом."""
    files: list[pathlib.Path] = []
    brief: pathlib.Path | None = None
    for raw in inputs:
        path = pathlib.Path(raw)
        if path.is_dir():
            for child in sorted(path.iterdir()):
                if child.name.startswith("."):
                    continue
                if child.name == BRIEF_FILE:
                    brief = child
                elif child.is_file():
                    files.append(child)
        elif path.is_file():
            if path.name == BRIEF_FILE:
                brief = path
            else:
                files.append(path)
        else:
            raise FileNotFoundError(raw)
    return files, brief


def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    out = pathlib.Path(args.out)
    try:
        files, brief_path = collect_files(args.inputs)
    except FileNotFoundError as e:
        print(f"не найдено: {e}", file=sys.stderr)
        return 2
    if args.brief:
        brief_path = pathlib.Path(args.brief)
    brief = None
    if brief_path is not None:
        try:
            brief = json.loads(brief_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"бриф не прочитан ({brief_path}): {e}", file=sys.stderr)
            return 2
    if not files and not brief:
        print("нет ни материалов, ни брифа", file=sys.stderr)
        return 2
    materials: list[MaterialFile] = []
    for i, path in enumerate(files, start=1):
        data = path.read_bytes()
        materials.append(
            MaterialFile(
                f"file_{i}",
                path.name,
                hashlib.sha256(data).hexdigest(),
                len(data),
                format_for(path.name),
                path,
            )
        )
    client = None
    skill = None
    if not args.no_model:
        try:
            from presentation_designer.llm import build_client, get_skill

            candidate = build_client(settings)
            if candidate.target("llm").provider.configured():
                client = candidate
                skill = get_skill("content_importer")
            else:
                print("провайдер моделей не настроен: контекст фактов без модели", file=sys.stderr)
        except Exception as e:
            print(f"клиент моделей не создан ({e}): контекст фактов без модели", file=sys.stderr)
    cache = (
        ParseCache(pathlib.Path(tempfile.mkdtemp(prefix="import-cache-")))
        if args.no_cache
        else ParseCache(settings.import_cache_dir)
    )
    package_id = (
        args.package_id
        or f"pkg_{hashlib.sha256(''.join(m.sha256 for m in materials).encode()).hexdigest()[:12]}"
    )
    started = time.perf_counter()
    try:
        result = import_content(
            materials,
            brief,
            package_id=package_id,
            settings=settings,
            llm_client=client,
            skill=skill,
            cache=cache,
            use_model=None if not args.no_model else False,
        )
    except ValueError as e:
        print(f"импорт не удался: {e}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    (out / "package.json").write_text(
        json.dumps(result.package, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    for name, data in result.assets.items():
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    report = dict(result.report)
    report["wall_ms"] = int((time.perf_counter() - started) * 1000)
    report["model_used"] = client is not None and bool(result.report.get("model", {}).get("calls"))
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    manifest = {
        "command": "import",
        "created_at": now_iso(),
        "importer": {"name": "content_importer", "version": IMPORTER_VERSION},
        "inputs": {
            "files": [
                {"path": str(m.path), "sha256": m.sha256, "format": m.format} for m in materials
            ],
            "brief": str(brief_path) if brief_path else None,
        },
        "outputs": {
            "package": "package.json",
            "report": "report.json",
            "assets": sorted(result.assets),
        },
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    if args.report:
        print(json.dumps(report, ensure_ascii=False, indent=1), file=sys.stderr)
    counts = report["counts"]
    cache_stats = report["cache"]
    total_files = cache_stats["files_hit"] + cache_stats["files_missed"]
    print(
        f"{package_id}: источников {counts['sources']}, блоков {counts['blocks']}, "
        f"фактов {counts['facts']} (обязательных {counts['facts_must_keep']}), "
        f"наборов {counts['datasets']}, изображений {counts['assets']}, "
        f"кэш {cache_stats['files_hit']}/{total_files}, {report['wall_ms']} мс → {out}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
