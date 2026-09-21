"""Уточнение ролей и теги пиктограмм на заглушке модели (без сети); слоты рендера;
анализ через API с настоящим слоем и переанализ при смене ключа профиля."""

from __future__ import annotations

import pathlib
import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from presentation_designer.export import render_slots as rs
from presentation_designer.llm.client import LlmClient
from presentation_designer.llm.skills import get_skill
from presentation_designer.llm.stub import StubTransport
from presentation_designer.llm.types import Request
from presentation_designer.parsing.template import analyzer as an
from presentation_designer.parsing.template.assets import icon_sheet, tag_icons_with_vlm
from presentation_designer.parsing.template.patterns import (
    refine_roles_with_vlm,
    role_matches_structure,
)


def _vlm_stub(role_answers: dict[int, str], tags: list[str]) -> StubTransport:
    stub = StubTransport()

    def respond(req: Request, _attempt: int) -> Any:
        text = "\n".join(m.text for m in req.messages)
        if "пиктограмм" in text:
            count = int(text.split("На листе ")[1].split(" ")[0])
            return {"items": [{"n": i + 1, "tags": tags, "logo": i == 0} for i in range(count)]}
        n = text.count("макет «")
        return {
            "items": [
                {
                    "n": i + 1,
                    "role": role_answers.get(i + 1, "cards"),
                    "confidence": 0.9,
                    "name": f"Роль {i + 1}",
                }
                for i in range(n)
            ]
        }

    stub.on(lambda _r: True, respond)
    return stub


def test_vlm_refines_roles_and_tags_icons(
    rich_template: pathlib.Path, make_client: Callable[..., LlmClient]
) -> None:
    result = an.analyze_template(
        rich_template, template_id="t", name="x", size_bytes=1, render=False, use_vlm=False
    )
    # Восстанавливаем структуры анализатора для прямого вызова уточнения.
    from presentation_designer.parsing.template.assets import index_assets
    from presentation_designer.parsing.template.classify import classify_all
    from presentation_designer.parsing.template.package import open_template
    from presentation_designer.parsing.template.patterns import build_pattern, group_patterns
    from presentation_designer.parsing.template.styles import StyleResolver

    pkg = open_template(rich_template)
    classes = classify_all(pkg)
    assets, _ = index_assets(pkg, classes)
    patterns = []
    for slide in pkg.slides[:3]:
        master = pkg.master(slide.master_id)
        layout = pkg.layout(slide.layout_id)
        resolver = StyleResolver(master.theme, master.element, layout.element if layout else None)
        cls = next(c for c in classes if c.slide_index == slide.index)
        p = build_pattern(slide, pkg, resolver, cls, {a.sha256: a for a in assets}, set())
        assert p is not None
        patterns.append(p)
    groups = group_patterns(patterns)
    previews = {p.slide.index: b"\x89PNG\r\n\x1a\nfake" for p in patterns}
    # Модель говорит: слайд 2 — process (совместимо), слайд 3 — table (в образце таблицы нет).
    stub = _vlm_stub({1: "title", 2: "process", 3: "table"}, ["безопасность", "замок"])
    client = make_client(stub)
    skill = get_skill("template_analyzer")
    summary = refine_roles_with_vlm(groups, previews, client, skill, batch=6)
    assert summary["asked"] == 3 and summary["rejected"] == 1
    by_slide = {p.slide.index: p for p in patterns}
    assert by_slide[2].role == "process" and by_slide[2].role_source == "vlm"
    assert by_slide[3].role == "kpi", "роль table без таблицы отвергнута, осталась эвристика"
    assert by_slide[2].name == "Роль 2"
    tagged = tag_icons_with_vlm(assets, client, skill, batch=8, limit=10)
    icons = [a for a in assets if a.tags and "безопасность" in a.tags]
    assert tagged == 10 and len(icons) >= 8
    assert any(a.kind == "logo" for a in assets)
    assert len(stub.calls) == 1 + 2, "3 группы — один запрос ролей; 10 иконок по 8 — два листа"
    assert all(m.images for r in stub.calls for m in r.messages if m.role == "user")
    _ = result


def test_icon_sheet_and_structure_guard() -> None:
    from presentation_designer.parsing.template.assets import Asset

    assets = [Asset(f"a{i}", "icon", "ppt/media/x.png", f"{i:064x}", blob=None) for i in range(5)]
    sheet, ids = icon_sheet(assets, cell=32, columns=3)
    assert sheet[:8] == b"\x89PNG\r\n\x1a\n" and ids == [f"a{i}" for i in range(5)]

    class P:
        def __init__(self) -> None:
            self.slots: list[Any] = []
            self.static_ids: list[str] = []

    assert role_matches_structure("bullets", P())  # type: ignore[arg-type]
    assert not role_matches_structure("table", P())  # type: ignore[arg-type]


def test_local_render_slots_limit_concurrency() -> None:
    slots = rs.LocalRenderSlots(2)
    peak = 0
    active = 0
    lock = threading.Lock()

    def job() -> None:
        nonlocal peak, active
        with slots.acquire(timeout_s=5, ttl_s=10) as lease:
            assert lease.backend == "local"
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)
            with lock:
                active -= 1

    threads = [threading.Thread(target=job) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 2 and slots.active() == 0
    with slots.acquire(timeout_s=1, ttl_s=10), slots.acquire(timeout_s=1, ttl_s=10):
        with pytest.raises(rs.RenderSlotTimeoutError):
            with slots.acquire(timeout_s=0.2, ttl_s=10):
                pass


def test_valkey_render_slots() -> None:
    from tests.llm.conftest import TEST_VALKEY_URL, valkey_available

    if not valkey_available():
        pytest.skip("Valkey для тестов недоступен")
    import uuid

    slots = rs.ValkeyRenderSlots(TEST_VALKEY_URL, 1, key=f"pd:test:render:{uuid.uuid4().hex[:6]}")
    with slots.acquire(timeout_s=1, ttl_s=0.3):
        assert slots.active() == 1
        with pytest.raises(rs.RenderSlotTimeoutError):
            with slots.acquire(timeout_s=0.1, ttl_s=1):
                pass
        time.sleep(0.4)
        # аренда «упавшего» процесса истекла по ttl — слот снова свободен
        with slots.acquire(timeout_s=1, ttl_s=1) as lease:
            assert lease.backend == "valkey"
    assert slots.active() == 0


def test_api_real_analysis_and_profile_key(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from presentation_designer.api.app import create_app
    from presentation_designer.pipeline.artifacts import ArtifactStore
    from presentation_designer.pipeline.files import FileStore
    from presentation_designer.pipeline.jobs import InlineExecutor, Orchestrator
    from presentation_designer.pipeline.real import RealLayers
    from presentation_designer.pipeline.state import State
    from presentation_designer.shared.settings import Settings

    monkeypatch.setenv("PD_QWEN_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("PD_QWEN_API_KEY", "replace-me")
    settings = Settings()
    settings.paths.data_dir = tmp_path / "data"
    settings.paths.artifacts_dir = tmp_path / "artifacts"
    settings.paths.runs_dir = tmp_path / "runs"
    state = State(settings.db_path)
    orch = Orchestrator(
        settings,
        state,
        FileStore(settings.uploads_dir, max_upload_mb=2, max_unzipped_mb=64),
        ArtifactStore(settings.artifacts_dir),
        RealLayers(settings),
        InlineExecutor(),
    )
    pptx = (
        pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "pptx" / "mini_template.pptx"
    ).read_bytes()
    with TestClient(create_app(orch, reconcile=False)) as client:
        r = client.post(
            "/api/templates", files={"file": ("mini.pptx", pptx, "application/octet-stream")}
        )
        assert r.status_code == 202 and r.json()["cached"] is False
        template_id = r.json()["template_id"]
        detail = client.get(f"/api/templates/{template_id}").json()
        assert detail["status"] == "succeeded", detail.get("error")
        profile = detail["profile"]
        assert profile["analyzer"]["name"] == "template_analyzer" and len(profile["patterns"]) >= 3
        assert any(w["code"] == "previews_unavailable" for w in profile["warnings"]), (
            "без ONLYOFFICE — предупреждение"
        )
        assert detail["previews"] == []
        health = client.get("/api/health").json()
        assert health["execution_mode"]["layers"]["parsing.template"] == "real"
        assert health["execution_mode"]["mode"] == "real"
        # Профиль без превью (не было рендерера) не считается готовым: повтор анализируется заново.
        files = {"file": ("mini.pptx", pptx, "application/octet-stream")}
        r2 = client.post("/api/templates", files=files)
        assert r2.json()["cached"] is False and r2.json()["template_id"] == template_id
        # Полный профиль (снимаем предупреждение, как если бы превью были) отдаётся из кэша.
        full = dict(profile)
        full["warnings"] = [w for w in profile["warnings"] if w["code"] != "previews_unavailable"]
        state.update_template(template_id, profile=full)
        r2 = client.post("/api/templates", files=files)
        assert r2.json()["cached"] is True and r2.json()["template_id"] == template_id
        # Смена версии анализатора меняет ключ профиля: тот же template_id анализируется заново.
        monkeypatch.setattr(an, "ANALYZER_VERSION", "9.9.9")
        r3 = client.post(
            "/api/templates", files={"file": ("mini.pptx", pptx, "application/octet-stream")}
        )
        assert r3.json()["cached"] is False and r3.json()["template_id"] == template_id
        assert (
            client.get(f"/api/templates/{template_id}").json()["profile"]["analyzer"]["version"]
            == "9.9.9"
        )
        # Не PPTX внутри ZIP → понятная ошибка анализа, а не падение воркера.
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("[Content_Types].xml", "<Types/>")
            zf.writestr("ppt/presentation.xml", "<p:presentation/>")
        r4 = client.post(
            "/api/templates",
            files={"file": ("broken.pptx", buf.getvalue(), "application/octet-stream")},
        )
        assert r4.status_code in (202, 415, 422)
        if r4.status_code == 202:
            broken = client.get(f"/api/templates/{r4.json()['template_id']}").json()
            assert broken["status"] == "failed" and broken["error"]["code"].startswith("template_")
