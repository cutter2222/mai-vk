"""Общие фикстуры: примеры контрактов, материалы организаторов, строгий режим приёмки,
приложение на встроенном исполнителе и временных каталогах (задачи выполняются сразу)."""

from __future__ import annotations

import json
import os
import pathlib
from collections.abc import Callable, Iterator

import pytest

# Тесты выполняют задачи в своём процессе, без внешней очереди RQ.
os.environ.setdefault("PD_QUEUE_MODE", "inline")
from fastapi.testclient import TestClient

from presentation_designer.api.app import create_app
from presentation_designer.pipeline.artifacts import ArtifactStore
from presentation_designer.pipeline.files import FileStore
from presentation_designer.pipeline.jobs import InlineExecutor, Orchestrator
from presentation_designer.pipeline.state import State
from presentation_designer.pipeline.stubs import StubLayers
from presentation_designer.shared.settings import Settings

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "contracts" / "examples"
FIXTURES = ROOT / "tests" / "fixtures"
ORGANIZER_DIR = pathlib.Path(os.environ.get("ORGANIZER_DATA_DIR", ROOT / "data" / "organizers"))


@pytest.fixture
def orchestrator(tmp_path: pathlib.Path) -> Orchestrator:
    settings = Settings()
    settings.paths.data_dir = tmp_path / "data"
    settings.paths.artifacts_dir = tmp_path / "artifacts"
    settings.paths.runs_dir = tmp_path / "runs"
    settings.paths.backups_dir = tmp_path / "backups"
    state = State(settings.db_path)
    files = FileStore(settings.uploads_dir, max_upload_mb=2, max_unzipped_mb=64)
    artifacts = ArtifactStore(settings.artifacts_dir)
    return Orchestrator(
        settings, state, files, artifacts, StubLayers(stage_delay_ms=0), InlineExecutor()
    )


@pytest.fixture
def client(orchestrator: Orchestrator) -> Iterator[TestClient]:
    app = create_app(orchestrator, reconcile=False)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def pptx_bytes() -> bytes:
    return (FIXTURES / "pptx" / "mini_template.pptx").read_bytes()


@pytest.fixture
def xlsx_bytes() -> bytes:
    return (FIXTURES / "content" / "metrics.xlsx").read_bytes()


@pytest.fixture
def docx_bytes() -> bytes:
    return (FIXTURES / "content" / "product_description.docx").read_bytes()


@pytest.fixture(scope="session")
def example() -> Callable[[str], dict[str, object]]:
    """Загружает пример по имени: example("slide_plan") или example("job_status.error")."""

    def load(name: str) -> dict[str, object]:
        path = EXAMPLES / f"{name}.example.json"
        return json.loads(path.read_text())  # type: ignore[no-any-return]

    return load


@pytest.fixture(scope="session")
def organizer_dir() -> pathlib.Path:
    return ORGANIZER_DIR


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """organizer_data пропускается без датасета; при REQUIRE_ORGANIZER_DATA=1 падает."""
    manifest = ORGANIZER_DIR / "manifest.json"
    strict = os.environ.get("REQUIRE_ORGANIZER_DATA") == "1"
    if manifest.exists():
        return
    for item in items:
        if "organizer_data" in item.keywords:
            if strict:
                item.add_marker(
                    pytest.mark.xfail(
                        run=False,
                        strict=True,
                        reason="REQUIRE_ORGANIZER_DATA=1, но manifest.json отсутствует",
                    )
                )
            else:
                item.add_marker(
                    pytest.mark.skip(
                        reason="материалы организаторов не скопированы: make organizer-data"
                    )
                )
