"""Фикстуры импорта содержания: собственные файлы каждого формата, материалы как записи
хранилища, клиент моделей на заглушке транспорта и клиент в режиме replay."""

from __future__ import annotations

import hashlib
import pathlib
from collections.abc import Callable
from typing import Any

import pytest

from presentation_designer.parsing.content.importer import MaterialFile
from presentation_designer.parsing.content.parsers import ParseCache
from presentation_designer.pipeline.files import format_for
from presentation_designer.shared import settings as s
from tests.llm.conftest import (  # noqa: F401  # фикстуры клиента моделей
    make_client,
    models,
    settings,
    stub,
)

ROOT = pathlib.Path(__file__).resolve().parents[3]
CONTENT = ROOT / "tests" / "fixtures" / "content"
EXAMPLES = ROOT / "examples" / "content"
LLM_FIXTURES = ROOT / "tests" / "fixtures" / "llm"


def material(path: pathlib.Path, index: int = 1) -> MaterialFile:
    data = path.read_bytes()
    return MaterialFile(
        f"file_{index}",
        path.name,
        hashlib.sha256(data).hexdigest(),
        len(data),
        format_for(path.name),
        path,
    )


@pytest.fixture
def materials() -> Callable[..., list[MaterialFile]]:
    def make(*names: str, root: pathlib.Path = CONTENT) -> list[MaterialFile]:
        return [material(root / name, i) for i, name in enumerate(names, start=1)]

    return make


@pytest.fixture
def cache(tmp_path: pathlib.Path) -> ParseCache:
    return ParseCache(tmp_path / "import-cache")


@pytest.fixture
def import_settings(tmp_path: pathlib.Path) -> s.Settings:
    cfg = s.Settings()
    cfg.content_import.cache_dir = tmp_path / "import-cache"
    cfg.llm.cache_dir = tmp_path / "llm-cache"
    return cfg


@pytest.fixture
def replay_client(tmp_path: pathlib.Path, models: s.ModelsConfig) -> Any:  # noqa: F811
    """Клиент, отвечающий только записями tests/fixtures/llm: сеть не вызывается."""
    from presentation_designer.llm.cache import ResponseCache
    from presentation_designer.llm.client import LlmClient
    from presentation_designer.llm.limiter import LocalLimiter, Quota
    from presentation_designer.llm.retry import RetryPolicy
    from presentation_designer.llm.stub import StubTransport

    cfg = s.Settings()
    cfg.llm.cache_dir = tmp_path / "llm-cache"
    cfg.timeouts.llm_call_s = 5
    return LlmClient(
        settings=cfg,
        models=models,
        transport=StubTransport(),
        limiter=LocalLimiter(Quota(4, 600, 1_000_000), max_wait_s=5),
        cache=ResponseCache("replay", cfg.llm_cache_dir, LLM_FIXTURES),
        retry_policy=RetryPolicy(max_retries=0, base_s=0.01, max_s=0.05),
    )
