"""Диаграммы-картинки в варианте original: предварительная ревизия заменяет их до первого
показа и пишет фразу в задание, полная сборка получает её charts.json и повторяет те же
замены; без отчёта предварительной ревизии полная сборка ничего не заменяет."""

from __future__ import annotations

import pathlib
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from presentation_designer.api.app import create_app
from presentation_designer.pipeline.artifacts import ArtifactStore
from presentation_designer.pipeline.files import FileStore
from presentation_designer.pipeline.jobs import InlineExecutor, Orchestrator, _preview_chart_report
from presentation_designer.pipeline.real import RealLayers
from presentation_designer.pipeline.run import ChartImagesOutput, ComposeInput, ComposeOutput
from presentation_designer.pipeline.state import State
from presentation_designer.pipeline.stubs import StubLayers
from presentation_designer.shared.settings import Settings

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
REPORT: dict[str, Any] = {
    "version": 1,
    "pictures": [
        {"sha256": "a" * 64, "slides": [2], "status": "read", "reason": "", "reading": None}
    ],
    "swaps": [{"slide": 2, "sha256": "a" * 64, "status": "replaced", "reason": ""}],
    "message": "1 диаграмма-картинка стала редактируемой (слайд 2).",
}


class ChartLayers(StubLayers):
    """Заглушки с заменой диаграмм: запоминают, что увидела полная сборка."""

    def __init__(self) -> None:
        super().__init__(stage_delay_ms=0)
        self.progress: list[str] = []
        self.reports: list[Any] = []

    def chart_images(
        self, pptx_path: pathlib.Path, progress: Callable[[str], None] | None = None
    ) -> ChartImagesOutput:
        assert pptx_path.is_file()
        if progress is not None:
            progress("Делаю диаграммы редактируемыми: 1 картинка похожа на диаграммы", 0.0)
            progress("Делаю диаграммы редактируемыми: 1 из 1", 1.0)
            self.progress.append("called")
        return ChartImagesOutput(report=REPORT, message=REPORT["message"], replaced=1)

    def compose(self, inp: ComposeInput) -> ComposeOutput:
        self.reports.append(inp.chart_report)
        return super().compose(inp)


def _orchestrator(tmp_path: pathlib.Path, layers: StubLayers) -> Orchestrator:
    settings = Settings()
    settings.paths.data_dir = tmp_path / "data"
    settings.paths.artifacts_dir = tmp_path / "artifacts"
    settings.paths.runs_dir = tmp_path / "runs"
    settings.paths.backups_dir = tmp_path / "backups"
    return Orchestrator(
        settings,
        State(settings.db_path),
        FileStore(settings.uploads_dir, max_upload_mb=2, max_unzipped_mb=64),
        ArtifactStore(settings.artifacts_dir),
        layers,
        InlineExecutor(),
    )


def test_preview_reports_charts_and_the_full_build_repeats_them(
    tmp_path: pathlib.Path, pptx_bytes: bytes
) -> None:
    layers = ChartLayers()
    with TestClient(create_app(_orchestrator(tmp_path, layers), reconcile=False)) as client:
        project_id = client.post("/api/projects", json={}).json()["project_id"]
        file_id = client.post(
            f"/api/projects/{project_id}/files",
            files=[("files", ("deck.pptx", pptx_bytes, PPTX_MIME))],
        ).json()[0]["file_id"]
        template_id = client.post("/api/templates", json={"file_id": file_id}).json()["template_id"]
        package_id = client.post("/api/content", json={"file_ids": [file_id]}).json()["package_id"]
        job_id = client.post(
            "/api/generations",
            json={
                "schema_version": "1.2",
                "template_id": template_id,
                "package_id": package_id,
                "settings": {"variants": ["original"]},
            },
        ).json()["job_id"]
        result = client.get(f"/api/generations/{job_id}").json()
    assert layers.progress == ["called"]
    # Фраза для чата — в предупреждениях задания, один раз: полная сборка её не повторяет.
    assert [w for w in result["warnings"] if w["code"] == "chart_images"] == [
        {"code": "chart_images", "message": REPORT["message"]}
    ]
    # Полная сборка получила отчёт предварительной ревизии и повторит те же замены.
    assert layers.reports == [REPORT]


def test_full_build_follows_the_preview_or_reads_itself(tmp_path: pathlib.Path) -> None:
    assert _preview_chart_report(None) is None  # предварительной ревизии нет: читать самим
    preview = tmp_path / "r1"
    preview.mkdir()
    # Ревизия показана без отчёта (чтения не было или оно сломалось): не заменять ничего.
    assert _preview_chart_report(preview) == {}
    (preview / "charts.json").write_text('{"version": 1, "pictures": []}', encoding="utf-8")
    assert _preview_chart_report(preview) == {"version": 1, "pictures": []}


def test_real_compose_carries_the_preview_report_into_the_revision(tmp_path: pathlib.Path) -> None:
    layers = RealLayers(Settings())
    store = ArtifactStore(tmp_path / "artifacts")
    with store.stage_revision("job_x", "original", 1) as staging:
        inp = ComposeInput(
            "job_x", "original", 1, {}, {}, None, {}, {}, staging, chart_report=REPORT
        )
        assert layers._chart_report(inp, None, None) is None
    manifest = store.read_manifest("job_x", "original", 1)
    assert "original/r1/charts.json" in manifest


class ShownFirst(ChartLayers):
    """Запоминает, что было опубликовано к началу рендера предварительной ревизии."""

    def __init__(self) -> None:
        super().__init__()
        self.state: State | None = None
        self.at_render: list[dict[str, Any]] = []

    def export(self, inp: Any) -> Any:
        if self.state is not None and not self.at_render:
            self.at_render.append(
                {
                    "revision": self.state.get_revision(inp.job_id, "original", 1),
                    "variant": self.state.get_variant(inp.job_id, "original"),
                    "job": self.state.get_job(inp.job_id),
                }
            )
        return super().export(inp)


def test_preview_shows_the_pptx_before_rendering_pdf_and_thumbnails(
    tmp_path: pathlib.Path, pptx_bytes: bytes
) -> None:
    layers = ShownFirst()
    orch = _orchestrator(tmp_path, layers)
    layers.state = orch.state
    with TestClient(create_app(orch, reconcile=False)) as client:
        project_id = client.post("/api/projects", json={}).json()["project_id"]
        file_id = client.post(
            f"/api/projects/{project_id}/files",
            files=[("files", ("deck.pptx", pptx_bytes, PPTX_MIME))],
        ).json()[0]["file_id"]
        template_id = client.post("/api/templates", json={"file_id": file_id}).json()["template_id"]
        package_id = client.post("/api/content", json={"file_ids": [file_id]}).json()["package_id"]
        job_id = client.post(
            "/api/generations",
            json={
                "schema_version": "1.2",
                "template_id": template_id,
                "package_id": package_id,
                "settings": {"variants": ["original"]},
            },
        ).json()["job_id"]
        result = client.get(f"/api/generations/{job_id}").json()
    # Редактору нужен только PPTX: к началу рендера он уже опубликован, вариант показан,
    # число слайдов известно.
    (seen,) = layers.at_render
    assert any(name.endswith("deck.pptx") for name in seen["revision"]["manifest"])
    assert not any(name.endswith("deck.pdf") for name in seen["revision"]["manifest"])
    assert seen["variant"]["ready_at"] and seen["variant"]["slide_count"]
    assert seen["job"]["progress"]["percent"] == 40
    # Рендер дописан в ту же ревизию, полная сборка её переиспользовала.
    variant = result["variants"][0]
    assert variant["status"] in ("ready", "needs_review")
    assert any(a.endswith("deck.pdf") for a in variant["artifacts"].values() if isinstance(a, str))
