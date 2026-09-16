"""Фикстуры вёрстки: собственные шаблоны (mini_template, rich_template) с профилями без
рендера и VLM, контент-пакет из examples/content, смысловой план на записи replay и планы
трёх вариантов без модели (детерминированный черновик)."""

from __future__ import annotations

import hashlib
import json
import pathlib
import tempfile
from collections.abc import Callable
from typing import Any

import pytest

from presentation_designer.generation.story import build_story
from presentation_designer.generation.variants import build_variant_plan
from presentation_designer.llm.skills import get_skill
from presentation_designer.parsing.content.importer import import_content
from presentation_designer.parsing.content.parsers import ParseCache
from presentation_designer.parsing.template.analyzer import analyze_template
from presentation_designer.pipeline.run import resolve_slide_count
from presentation_designer.shared.settings import Settings, get_settings
from tests.fixtures.rich_template import build_rich_template, build_scheme_template
from tests.parsing.content.conftest import (  # noqa: F401
    EXAMPLES,
    cache,
    import_settings,
    make_client,
    materials,
    models,
    replay_client,
    settings,
    stub,
)

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
MINI_TEMPLATE = FIXTURES / "pptx" / "mini_template.pptx"
EXAMPLE_FILES = ("overview.docx", "metrics.xlsx", "notes.md", "openrate_chart.png", "logo.png")


def own_profile(path: pathlib.Path, template_id: str) -> dict[str, Any]:
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


@pytest.fixture(scope="module")
def mini_profile() -> dict[str, Any]:
    return own_profile(MINI_TEMPLATE, "tpl_mini")


@pytest.fixture(scope="module")
def rich_template_path(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    return build_rich_template(tmp_path_factory.mktemp("tpl") / "rich_template.pptx")


@pytest.fixture(scope="module")
def rich_profile(rich_template_path: pathlib.Path) -> dict[str, Any]:
    return own_profile(rich_template_path, "tpl_rich")


@pytest.fixture(scope="module")
def scheme_template_path(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    return build_scheme_template(tmp_path_factory.mktemp("tpl") / "scheme_template.pptx")


@pytest.fixture(scope="module")
def scheme_profile(scheme_template_path: pathlib.Path) -> dict[str, Any]:
    return own_profile(scheme_template_path, "tpl_scheme")


@pytest.fixture
def example_package(
    materials: Any,  # noqa: F811
    cache: ParseCache,  # noqa: F811
    import_settings: Settings,  # noqa: F811
    tmp_path: pathlib.Path,
) -> dict[str, Any]:
    files = materials(*EXAMPLE_FILES, root=EXAMPLES)
    brief = json.loads((EXAMPLES / "brief.json").read_text())
    result = import_content(
        files,
        brief,
        package_id="pkg_example",
        settings=import_settings,
        cache=cache,
        use_model=False,
    )
    assets_dir = tmp_path / "package"
    for name, data in result.assets.items():
        target = assets_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    package = dict(result.package)
    package["_assets_dir"] = str(assets_dir)  # только для тестов: где лежат картинки
    return package


@pytest.fixture
def example_story(
    example_package: dict[str, Any],
    replay_client: Any,  # noqa: F811
) -> dict[str, Any]:
    package = {k: v for k, v in example_package.items() if not k.startswith("_")}
    return build_story(
        package, {"language": "ru"}, client=replay_client, skill=get_skill("story_planner")
    ).story


@pytest.fixture
def make_plan(
    example_package: dict[str, Any], example_story: dict[str, Any]
) -> Callable[..., dict[str, Any]]:
    """План варианта без модели на данном профиле."""

    def make(profile: dict[str, Any], variant_id: str = "balanced") -> dict[str, Any]:
        package = {k: v for k, v in example_package.items() if not k.startswith("_")}
        return build_variant_plan(
            example_story,
            profile,
            package,
            variant_id,
            {"language": "ru"},
            slide_count=resolve_slide_count(variant_id, {"language": "ru"}),
            client=None,
            skill=None,
            use_model=False,
            plan_id=f"plan_test_{variant_id}",
        ).plan

    return make
