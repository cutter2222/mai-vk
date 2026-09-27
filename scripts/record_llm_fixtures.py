"""Записывает ответы модели на собственных входах в tests/fixtures/llm для режима replay.

Входы: фразы брифа из tests/fixtures/llm/brief_phrases.json, смысловой план для
контент-пакета из examples/content (импорт без модели, детерминированный) и планы трёх
вариантов на двух собственных шаблонах (tests/fixtures/pptx/mini_template.pptx и синтетический
rich_template из tests/fixtures/rich_template.py; профили без рендера и VLM). Запросы строятся
тем же кодом, что и в тестах, поэтому ключи записей совпадают; ёмкость слотов зависит от
файлов шрифтов машины, записи делаются на машине, где идут тесты.
Требует настроенного провайдера (PD_QWEN_BASE_URL, PD_QWEN_API_KEY в .env).

    uv run python scripts/record_llm_fixtures.py [--only brief|story|plan]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from tests.fixtures.rich_template import build_rich_template  # noqa: E402

from presentation_designer.generation.story import build_story  # noqa: E402
from presentation_designer.generation.variants import VARIANTS, build_variant_plan  # noqa: E402
from presentation_designer.llm import build_client, get_skill  # noqa: E402
from presentation_designer.parsing.content.brief import extract_brief_with_model  # noqa: E402
from presentation_designer.parsing.content.importer import (  # noqa: E402
    MaterialFile,
    import_content,
)
from presentation_designer.parsing.content.parsers import ParseCache  # noqa: E402
from presentation_designer.parsing.template.analyzer import analyze_template  # noqa: E402
from presentation_designer.pipeline.files import format_for  # noqa: E402
from presentation_designer.pipeline.run import resolve_slide_count  # noqa: E402
from presentation_designer.shared.settings import get_settings  # noqa: E402

MINI_TEMPLATE = ROOT / "tests" / "fixtures" / "pptx" / "mini_template.pptx"

PHRASES = ROOT / "tests" / "fixtures" / "llm" / "brief_phrases.json"
EXAMPLES = ROOT / "examples" / "content"
EXAMPLE_FILES = ("overview.docx", "metrics.xlsx", "notes.md", "openrate_chart.png", "logo.png")


def example_package(asset_dir: pathlib.Path | None = None) -> dict[str, object]:
    files = []
    for i, name in enumerate(EXAMPLE_FILES, start=1):
        path = EXAMPLES / name
        data = path.read_bytes()
        files.append(
            MaterialFile(
                f"file_{i}",
                name,
                hashlib.sha256(data).hexdigest(),
                len(data),
                format_for(name),
                path,
            )
        )
    brief = json.loads((EXAMPLES / "brief.json").read_text())
    result = import_content(
        files,
        brief,
        package_id="pkg_example",
        settings=get_settings(),
        cache=ParseCache(pathlib.Path(tempfile.mkdtemp())),
        use_model=False,
    )
    if asset_dir is not None:
        for relative, data in result.assets.items():
            destination = asset_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
    return result.package


async def record_briefs(client: object) -> None:
    skill = get_skill("brief_extractor")
    phrases = json.loads(PHRASES.read_text())
    for item in phrases:
        result = await extract_brief_with_model(item["text"], None, client=client, skill=skill)
        print(item["id"], json.dumps(result, ensure_ascii=False))


def record_story(client: object) -> None:
    package = example_package()
    result = build_story(
        package, {"language": "ru"}, client=client, skill=get_skill("story_planner")
    )
    print(json.dumps(result.report, ensure_ascii=False))
    print(json.dumps(result.story, ensure_ascii=False, indent=1))


def own_profile(path: pathlib.Path, template_id: str) -> dict[str, object]:
    """Профиль собственного шаблона без рендера и VLM: так же его строят тесты."""
    data = path.read_bytes()
    return analyze_template(
        path,
        template_id=template_id,
        name=path.name,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        settings=get_settings(),
        llm_client=None,
        render_slots=None,
        render=False,
        use_vlm=False,
        workdir=pathlib.Path(tempfile.mkdtemp()),
    ).profile


def record_plans(client: object) -> None:
    package = example_package()
    story = build_story(
        package, {"language": "ru"}, client=client, skill=get_skill("story_planner")
    ).story
    templates = {
        "tpl_mini": own_profile(MINI_TEMPLATE, "tpl_mini"),
        "tpl_rich": own_profile(
            build_rich_template(pathlib.Path(tempfile.mkdtemp()) / "rich_template.pptx"),
            "tpl_rich",
        ),
    }
    skill = get_skill("variant_planner")
    for template_id, profile in templates.items():
        for variant_id in VARIANTS:
            result = build_variant_plan(
                story,
                profile,
                package,
                variant_id,
                {"language": "ru"},
                slide_count=resolve_slide_count(variant_id, {"language": "ru"}),
                client=client,
                skill=skill,
            )
            counts = result.report["counts"]
            print(template_id, variant_id, json.dumps(counts, ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["brief", "story", "plan"], default=None)
    args = parser.parse_args()
    settings = get_settings()
    client = build_client(settings, cache_mode="record")
    if not client.target("llm").provider.configured():
        print("провайдер не настроен: задайте ключ в .env", file=sys.stderr)
        return 2
    if args.only in (None, "brief"):
        asyncio.run(record_briefs(client))
    if args.only in (None, "story"):
        record_story(client)
    if args.only in (None, "plan"):
        record_plans(client)
    return 0


if __name__ == "__main__":
    sys.exit(main())
