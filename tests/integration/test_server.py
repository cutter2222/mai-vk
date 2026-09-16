"""Интеграционные тесты против развёрнутого API.

Запуск: API_BASE_URL=https://… uv run pytest tests/integration.

Проходят и локально (make up), и против сервера. Без переменной пропускаются.
Проекты, созданные тестами, удаляются в конце каждого теста, даже упавшего: на сервере
после прогонов не должно оставаться тестовых проектов.
"""

from __future__ import annotations

import os
import pathlib
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from presentation_designer.contracts import models as m

BASE = os.environ.get("API_BASE_URL", "").rstrip("/")
FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TERMINAL = {"succeeded", "needs_review", "failed", "canceled"}

pytestmark = pytest.mark.skipif(not BASE, reason="API_BASE_URL не задан")


@pytest.fixture(scope="module")
def api() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=f"{BASE}/api", timeout=60) as client:
        yield client


@pytest.fixture
def project(api: httpx.Client) -> Iterator[Callable[[str | None], str]]:
    """Создаёт проекты теста и удаляет их по окончании, даже если тест упал."""
    created: list[str] = []

    def make(title: str | None = None) -> str:
        r = api.post("/projects", json={"title": title} if title else {})
        assert r.status_code == 201, r.text
        created.append(str(r.json()["project_id"]))
        return created[-1]

    yield make
    for project_id in created:
        api.delete(f"/projects/{project_id}")


def wait_for(
    fn: Callable[[], Any], ok: Callable[[Any], bool], timeout_s: float = 120, every: float = 1.0
) -> Any:
    deadline = time.monotonic() + timeout_s
    last: Any = None
    while time.monotonic() < deadline:
        last = fn()
        if ok(last):
            return last
        time.sleep(every)
    raise AssertionError(f"не дождались: {last}")


def _upload(
    api: httpx.Client, project_id: str, name: str, data: bytes, mime: str
) -> dict[str, Any]:
    r = api.post(f"/projects/{project_id}/files", files=[("files", (name, data, mime))])
    assert r.status_code == 201, r.text
    return dict(r.json()[0])


def test_health(api: httpx.Client) -> None:
    health = api.get("/health").json()
    assert health["status"] in {"ok", "degraded"}, health
    assert health["valkey_ok"] is True
    assert health["workers"]["generation"] >= 1 and health["workers"]["analysis"] >= 1
    assert health["renderer_ok"] is True, "воркер генерации не прошёл проверку рендерера"


def test_end_to_end(api: httpx.Client, project: Callable[[str | None], str]) -> None:
    pptx = (FIXTURES / "pptx" / "mini_template.pptx").read_bytes()
    xlsx = (FIXTURES / "content" / "metrics.xlsx").read_bytes()
    project_id = project("Интеграционный тест")

    tpl = _upload(api, project_id, "Корпоративный шаблон.pptx", pptx, PPTX_MIME)
    assert (
        _upload(api, project_id, "Корпоративный шаблон.pptx", pptx, PPTX_MIME)["file_id"]
        == tpl["file_id"]
    )
    data = _upload(api, project_id, "metrics.xlsx", xlsx, XLSX_MIME)

    template_id = api.post("/templates", json={"file_id": tpl["file_id"]}).json()["template_id"]
    detail = wait_for(
        lambda: api.get(f"/templates/{template_id}").json(), lambda d: d["status"] in TERMINAL, 60
    )
    assert detail["status"] == "succeeded", detail
    m.TemplateProfile.model_validate(detail["profile"])

    brief = api.post(
        "/brief",
        json={
            "text": "Сделай презентацию про запуск сервиса для руководителей, чтобы одобрили пилот"
        },
    ).json()
    m.BriefExtract.model_validate(brief)
    assert "title" in brief["understood"] and brief["intent"] == "generate"
    health = api.get("/health").json()
    if health["execution_mode"]["layers"].get("brief") == "real" and (
        health.get("provider") or {}
    ).get("configured"):
        # Настоящий слой с настроенным провайдером отвечает моделью; эвристика — только резерв.
        assert brief["source"] == "model", brief
        assert brief["model"]["name"]
    else:
        assert brief["source"] == "heuristic"

    body = {
        "file_ids": [data["file_id"]],
        "brief": {"purpose": "product", "title": "Запуск сервиса", "language": "ru"},
    }
    package_id = api.post("/content", json=body).json()["package_id"]
    assert api.post("/content", json=body).json()["package_id"] == package_id
    pkg = wait_for(
        lambda: api.get(f"/content/{package_id}").json(), lambda d: d["status"] in TERMINAL, 60
    )
    assert pkg["status"] == "succeeded", pkg
    package = m.ContentPackage.model_validate(pkg["package"])
    if health["execution_mode"]["layers"].get("parsing.content") == "real":
        # Настоящий импорт: таблица из xlsx стала набором данных, а её ячейки — фактами.
        assert package.datasets and package.datasets[0].columns[1].name == "Открываемость"
        assert any(f.source_location and f.source_location.cell for f in package.facts)

    req = {
        "schema_version": "1.1",
        "template_id": template_id,
        "package_id": package_id,
        "idempotency_key": f"it-{time.time()}",
    }
    job_id = api.post("/generations", json=req).json()["job_id"]
    assert api.post("/generations", json=req).json()["job_id"] == job_id
    api.patch(
        f"/projects/{project_id}",
        json={"job_id": job_id, "template_id": template_id, "package_id": package_id},
    )
    result = wait_for(
        lambda: api.get(f"/generations/{job_id}").json(), lambda d: d["status"] in TERMINAL, 180, 2
    )
    doc = m.GenerationResult.model_validate(result)
    assert doc.status == "needs_review", result.get("error")
    story = api.get(f"/generations/{job_id}/story").json()
    m.StoryPlan.model_validate(story)
    if health["execution_mode"]["layers"].get("generation.story") == "real":
        assert story["generation_meta"]["models"] and story["coverage"]["must_keep_facts"]
    assert {v.variant_id: v.status for v in doc.variants} == {
        "compact": "ready",
        "balanced": "needs_review",
        "detailed": "needs_review",
    }
    assert doc.metrics.queue_wait_ms is not None
    if health["execution_mode"]["layers"].get("generation.plan") == "real":
        # Три настоящих плана из одного смыслового плана: покрытие полное, число слайдов в
        # требовании, композиции из профиля; пары без различий — предупреждение задания.
        plans = {}
        for v in doc.variants:
            assert v.plan_artifact
            plan = api.get(f"/generations/{job_id}/artifacts/{v.plan_artifact}").json()
            m.SlidePlan.model_validate(plan)
            assert plan["story_id"] == story["story_id"] and plan["coverage"]["missing"] == []
            assert plan["generation_meta"]["models"] and plan["variant"]["value"] == v.variant_id
            sc = plan["slide_count"]
            lo, hi = sc.get("exact") or sc["min"], sc.get("exact") or sc["max"]
            assert lo <= len(plan["slides"]) <= hi
            assert v.slide_count == len(plan["slides"])
            plans[v.variant_id] = plan
        sequences = {tuple(p["comparison"]["pattern_sequence"]) for p in plans.values()}
        visuals = {tuple(p["comparison"]["visual_kinds"]) for p in plans.values()}
        indistinct = [w for w in result.get("warnings", []) if w["code"] == "variants_indistinct"]
        assert len(sequences) == 3 or len(visuals) == 3 or indistinct

    pptx_name = next(
        v.artifacts.pptx for v in doc.variants if v.variant_id == "balanced" and v.artifacts
    )
    assert pptx_name
    r = api.get(f"/generations/{job_id}/artifacts/{pptx_name}")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    assert api.get(f"/generations/{job_id}/artifacts/nope.txt").status_code == 404

    audit = api.get(f"/generations/{job_id}/variants/balanced/audit").json()
    issue = audit["issues"][0]["issue_id"]
    stale = api.post(
        f"/generations/{job_id}/variants/balanced/repairs",
        json={"base_revision": 99, "issue_ids": [issue]},
    )
    assert stale.status_code == 409
    repair_id = api.post(
        f"/generations/{job_id}/variants/balanced/repairs",
        json={"base_revision": 1, "issue_ids": [issue]},
    ).json()["repair_job_id"]
    status = wait_for(
        lambda: api.get(f"/jobs/{repair_id}").json(), lambda d: d["status"] in TERMINAL, 120, 2
    )
    assert status["status"] == "succeeded", status
    result2 = api.get(f"/generations/{job_id}").json()
    assert next(v for v in result2["variants"] if v["variant_id"] == "balanced")["revision"] == 2

    # Проект открывается по ссылке с файлами, лентой и заданием.
    restored = api.get(f"/projects/{project_id}").json()
    m.Project.model_validate(restored)
    assert restored["job_id"] == job_id and len(restored["files"]) == 2
    items = api.get("/projects").json()
    assert next(i for i in items if i["project_id"] == project_id)["job_status"] == "needs_review"

    retry = api.post(f"/jobs/{job_id}/retry").json()["job_id"]
    retried = wait_for(
        lambda: api.get(f"/generations/{retry}").json(), lambda d: d["status"] in TERMINAL, 180, 2
    )
    assert retried["metrics"]["cache"]["story_hit"] is True


def test_partial_failure_and_cancel(
    api: httpx.Client, project: Callable[[str | None], str]
) -> None:
    pptx = (FIXTURES / "pptx" / "mini_template_fail.pptx").read_bytes()
    project_id = project(None)
    tpl = _upload(api, project_id, "Шаблон fail.pptx", pptx, PPTX_MIME)
    template_id = api.post("/templates", json={"file_id": tpl["file_id"]}).json()["template_id"]
    wait_for(
        lambda: api.get(f"/templates/{template_id}").json(), lambda d: d["status"] in TERMINAL, 60
    )
    package_id = api.post(
        "/content", json={"brief": {"purpose": "report", "title": "Итоги", "language": "ru"}}
    ).json()["package_id"]
    wait_for(
        lambda: api.get(f"/content/{package_id}").json(), lambda d: d["status"] in TERMINAL, 60
    )

    job_id = api.post(
        "/generations",
        json={"schema_version": "1.1", "template_id": template_id, "package_id": package_id},
    ).json()["job_id"]
    result = wait_for(
        lambda: api.get(f"/generations/{job_id}").json(), lambda d: d["status"] in TERMINAL, 180, 2
    )
    assert result["status"] == "needs_review" and result["partial"] is True
    assert (
        next(v for v in result["variants"] if v["variant_id"] == "detailed")["status"] == "failed"
    )

    canceled_job = api.post(
        "/generations",
        json={"schema_version": "1.1", "template_id": template_id, "package_id": package_id},
    ).json()["job_id"]
    assert api.post(f"/jobs/{canceled_job}/cancel").status_code == 202
    status = wait_for(
        lambda: api.get(f"/jobs/{canceled_job}").json(), lambda d: d["status"] in TERMINAL, 60
    )
    assert status["status"] == "canceled"
