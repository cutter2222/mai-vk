"""Приложение на встроенном исполнителе и временных каталогах: задачи выполняются сразу."""

from __future__ import annotations

import pathlib
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from presentation_designer.api.app import create_app
from presentation_designer.pipeline.artifacts import ArtifactStore
from presentation_designer.pipeline.files import FileStore
from presentation_designer.pipeline.jobs import InlineExecutor, Orchestrator
from presentation_designer.pipeline.state import State
from presentation_designer.pipeline.stubs import StubLayers
from presentation_designer.shared.settings import Settings

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def orchestrator(tmp_path: pathlib.Path) -> Orchestrator:
    settings = Settings()
    settings.paths.data_dir = tmp_path / "data"
    settings.paths.artifacts_dir = tmp_path / "artifacts"
    settings.paths.runs_dir = tmp_path / "runs"
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
