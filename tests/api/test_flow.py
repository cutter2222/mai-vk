"""Сквозной путь через HTTP на заглушках: проект, файлы, бриф, задание, скачивание, исправление."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

from fastapi.testclient import TestClient

from presentation_designer.contracts import models as m
from presentation_designer.pipeline.gc import live_sets
from presentation_designer.pipeline.jobs import Orchestrator

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _project(client: TestClient) -> str:
    r = client.post("/api/projects", json={})
    assert r.status_code == 201, r.text
    doc = r.json()
    m.Project.model_validate(doc)
    return str(doc["project_id"])


def _upload(
    client: TestClient, project_id: str, name: str, data: bytes, mime: str
) -> dict[str, Any]:
    r = client.post(f"/api/projects/{project_id}/files", files=[("files", (name, data, mime))])
    assert r.status_code == 201, r.text
    rows = r.json()
    assert len(rows) == 1
    m.ProjectFile.model_validate(rows[0])
    return dict(rows[0])


def test_health_and_capabilities(client: TestClient) -> None:
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["workers"]["generation"] >= 1
    caps = client.get("/api/capabilities").json()
    assert caps["contracts_version"] == "1.5"
    assert caps["execution_mode"]["mode"] == "stub"
    assert caps["execution_mode"]["layers"]["brief"] == "stub"
    assert caps["limits"]["max_project_files"] > 0


def test_full_flow(client: TestClient, pptx_bytes: bytes, xlsx_bytes: bytes) -> None:
    project_id = _project(client)

    # Файлы проекта: тот же файл дважды хранится один раз и даёт ту же запись.
    tpl = _upload(client, project_id, "Корпоративный шаблон.pptx", pptx_bytes, PPTX_MIME)
    again = _upload(client, project_id, "Корпоративный шаблон.pptx", pptx_bytes, PPTX_MIME)
    assert again["file_id"] == tpl["file_id"]
    assert tpl["check"] == {"status": "ok", "format": "pptx"}
    assert tpl["kind"] == "other"
    data = _upload(client, project_id, "metrics.xlsx", xlsx_bytes, XLSX_MIME)
    assert data["kind"] == "material"

    # Шаблон по file_id: анализ выполняется сразу (встроенный исполнитель).
    r = client.post("/api/templates", json={"file_id": tpl["file_id"]})
    assert r.status_code == 202, r.text
    template_id = r.json()["template_id"]
    assert r.json()["cached"] is False
    r2 = client.post("/api/templates", json={"file_id": tpl["file_id"]})
    assert r2.json()["template_id"] == template_id and r2.json()["cached"] is True
    detail = client.get(f"/api/templates/{template_id}").json()
    assert detail["status"] == "succeeded"
    m.TemplateProfile.model_validate(detail["profile"])
    assert detail["previews"]
    png = client.get(f"/api/templates/{template_id}/assets/{detail['previews'][0]}")
    assert png.status_code == 200 and png.headers["content-type"].startswith("image/png")
    assert client.get(f"/api/templates/{template_id}/assets/../../etc/passwd").status_code == 404
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["files"][0]["kind"] == "template"
    assert project["files"][0]["template_id"] == template_id

    # Бриф из фразы.
    r = client.post(
        "/api/brief",
        json={
            "text": "Сделай презентацию про запуск сервиса умных уведомлений для руководителей, чтобы одобрили пилот, 10-12 слайдов"  # noqa: E501
        },
    )
    brief = r.json()
    m.BriefExtract.model_validate(brief)
    assert brief["source"] == "heuristic" and brief["intent"] == "generate"
    assert brief["brief"]["purpose"] == "product"
    assert brief["slide_count"] == {"min": 10, "max": 12}
    assert set(brief["understood"]) >= {"purpose", "title", "audience", "goal", "slide_count"}

    # Пакет из file_ids и брифа; тот же набор даёт тот же пакет без повторной загрузки байтов.
    body = {
        "file_ids": [data["file_id"]],
        "brief": {
            "purpose": "product",
            "title": "Запуск сервиса умных уведомлений",
            "language": "ru",
        },
    }
    r = client.post("/api/content", json=body)
    assert r.status_code == 202, r.text
    package_id = r.json()["package_id"]
    assert client.post("/api/content", json=body).json()["package_id"] == package_id
    pkg = client.get(f"/api/content/{package_id}").json()
    assert pkg["status"] == "succeeded"
    package = m.ContentPackage.model_validate(pkg["package"])
    assert package.mode == "mixed"
    assert any(s.file_id == data["file_id"] for s in package.sources)
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["files"][1]["package_id"] == package_id

    # Генерация: три варианта, файлы и аудит.
    req = {
        "schema_version": "1.1",
        "template_id": template_id,
        "package_id": package_id,
        "idempotency_key": "t1",
        "settings": {"slide_count": {"min": 10, "max": 15}},
    }
    r = client.post("/api/generations", json=req)
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert job_id.startswith("job_")
    assert client.post("/api/generations", json=req).json()["job_id"] == job_id
    client.patch(
        f"/api/projects/{project_id}",
        json={"job_id": job_id, "template_id": template_id, "package_id": package_id},
    )

    result = client.get(f"/api/generations/{job_id}").json()
    doc = m.GenerationResult.model_validate(result)
    assert doc.status == "needs_review"
    assert doc.execution_mode.mode == "stub"
    statuses = {v.variant_id: v.status for v in doc.variants}
    assert statuses == {"compact": "ready", "balanced": "needs_review", "detailed": "needs_review"}
    assert all(
        v.artifacts and v.artifacts.pptx and v.artifacts.pdf and v.artifacts.html
        for v in doc.variants
    )
    assert doc.metrics.timeline is not None and doc.metrics.timeline.first_file_ready_ms is not None
    assert result["metrics"]["cache"]["story_hit"] is False

    status = client.get(f"/api/jobs/{job_id}").json()
    m.JobStatus.model_validate(status)
    assert status["kind"] == "generation" and status["status"] == "needs_review"

    story = client.get(f"/api/generations/{job_id}/story").json()
    m.StoryPlan.model_validate(story)

    # Артефакты: только имена из манифеста, настоящие файлы.
    manifest = result["artifacts_manifest"]
    pptx_name = "balanced/r1/deck.pptx"
    assert pptx_name in manifest
    r = client.get(f"/api/generations/{job_id}/artifacts/{pptx_name}")
    assert r.status_code == 200
    assert r.content[:2] == b"PK" and "attachment" in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "ppt/presentation.xml" in zf.namelist()
    pdf = client.get(f"/api/generations/{job_id}/artifacts/balanced/r1/deck.pdf")
    assert pdf.content[:5] == b"%PDF-"
    html = client.get(f"/api/generations/{job_id}/artifacts/balanced/r1/deck.html")
    assert "<!doctype html>" in html.text
    thumb = client.get(f"/api/generations/{job_id}/artifacts/balanced/r1/thumbs/slide-01.png")
    assert thumb.headers["content-type"] == "image/png"
    assert (
        client.get(f"/api/generations/{job_id}/artifacts/balanced/r1/missing.txt").status_code
        == 404
    )
    assert client.get(f"/api/generations/{job_id}/artifacts/../../secret").status_code == 404

    # Аудит и исправление: новая ревизия, устаревшая ревизия даёт 409.
    audit = client.get(f"/api/generations/{job_id}/variants/balanced/audit").json()
    report = m.AuditReport.model_validate(audit)
    assert report.revision == 1 and report.issues
    issue = report.issues[0].issue_id
    stale = client.post(
        f"/api/generations/{job_id}/variants/balanced/repairs",
        json={"base_revision": 99, "issue_ids": [issue]},
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_stale"
    assert stale.json()["error"]["details"]["current_revision"] == 1
    empty = client.post(
        f"/api/generations/{job_id}/variants/balanced/repairs",
        json={"base_revision": 1, "issue_ids": []},
    )
    assert empty.status_code == 422
    r = client.post(
        f"/api/generations/{job_id}/variants/balanced/repairs",
        json={"base_revision": 1, "issue_ids": [issue]},
    )
    assert r.status_code == 202, r.text
    repair_id = r.json()["repair_job_id"]
    rs = client.get(f"/api/jobs/{repair_id}").json()
    m.JobStatus.model_validate(rs)
    assert rs["kind"] == "repair" and rs["status"] == "succeeded" and rs["result"]["revision"] == 2
    result2 = m.GenerationResult.model_validate(client.get(f"/api/generations/{job_id}").json())
    balanced = next(v for v in result2.variants if v.variant_id == "balanced")
    assert balanced.revision == 2 and len(balanced.revisions or []) == 2
    assert result2.repairs and result2.repairs[0].result == "applied"
    audit2 = client.get(f"/api/generations/{job_id}/variants/balanced/audit").json()
    assert audit2["revision"] == 2 and all(i["issue_id"] != issue for i in audit2["issues"])
    audit1 = client.get(f"/api/generations/{job_id}/variants/balanced/audit?revision=1").json()
    assert audit1["revision"] == 1
    manifest2 = client.get(f"/api/generations/{job_id}").json()["artifacts_manifest"]
    assert "balanced/r2/deck.pptx" in manifest2 and "balanced/r1/deck.pptx" in manifest2

    # Список проектов несёт состояние и миниатюру.
    items = client.get("/api/projects").json()
    item = next(i for i in items if i["project_id"] == project_id)
    assert item["job_status"] == "needs_review"
    assert item["thumbnail_url"].startswith(f"/api/generations/{job_id}/artifacts/")
    assert item["template_name"] == "Корпоративный шаблон.pptx"

    # Повтор задания переиспользует смысловой план.
    r = client.post(f"/api/jobs/{job_id}/retry")
    assert r.status_code == 202
    new_job = r.json()["job_id"]
    assert new_job != job_id
    retried = client.get(f"/api/generations/{new_job}").json()
    assert retried["metrics"]["cache"]["story_hit"] is True
    assert retried["metrics"]["stages"][0]["cache_hit"] is True


def test_partial_failure_and_cancel(
    client: TestClient, pptx_bytes: bytes, docx_bytes: bytes
) -> None:
    project_id = _project(client)
    tpl = _upload(client, project_id, "Шаблон fail.pptx", pptx_bytes, PPTX_MIME)
    template_id = client.post("/api/templates", json={"file_id": tpl["file_id"]}).json()[
        "template_id"
    ]
    doc = _upload(
        client,
        project_id,
        "brief.docx",
        docx_bytes,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    package_id = client.post("/api/content", json={"file_ids": [doc["file_id"]]}).json()[
        "package_id"
    ]
    pkg = client.get(f"/api/content/{package_id}").json()["package"]
    assert pkg["mode"] == "package" and any(
        w["code"] == "brief_incomplete" for w in pkg["warnings"]
    )

    job_id = client.post(
        "/api/generations",
        json={"schema_version": "1.1", "template_id": template_id, "package_id": package_id},
    ).json()["job_id"]
    result = m.GenerationResult.model_validate(client.get(f"/api/generations/{job_id}").json())
    assert result.status == "needs_review" and result.partial is True
    detailed = next(v for v in result.variants if v.variant_id == "detailed")
    assert (
        detailed.status == "failed"
        and detailed.error is not None
        and detailed.error.code == "compose_failed"
    )
    assert not detailed.artifacts or not detailed.artifacts.pptx
    assert any(w.code == "variant_failed" for w in result.warnings or [])
    assert [s.status for s in detailed.stages or []] == ["done", "failed", "skipped", "skipped"]

    # Отмена уже завершённого задания ничего не меняет; отмена очереди помечает статус.
    assert client.post(f"/api/jobs/{job_id}/cancel").status_code == 202
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "needs_review"
    assert client.get("/api/jobs/job_unknown").status_code == 404
    assert client.get("/api/generations/job_unknown").status_code == 404


def test_validation_and_limits(client: TestClient, pptx_bytes: bytes) -> None:
    project_id = _project(client)
    # Не ZIP под видом pptx отклоняется, мусорный тип принимается без импорта.
    r = client.post(
        f"/api/projects/{project_id}/files",
        files=[("files", ("bad.pptx", b"not a zip", PPTX_MIME))],
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "file_rejected"
    r = client.post(
        f"/api/projects/{project_id}/files",
        files=[("files", ("clip.mp4", b"\x00\x00\x00\x18ftyp", "video/mp4"))],
    )
    assert (
        r.status_code == 201
        and r.json()[0]["kind"] == "other"
        and r.json()[0]["check"]["status"] == "skipped"
    )
    # docx с чужим содержимым: это pptx под другим расширением.
    r = client.post(
        f"/api/projects/{project_id}/files",
        files=[("files", ("fake.docx", pptx_bytes, "application/octet-stream"))],
    )
    assert r.status_code == 422
    # Пустой файл и слишком большой.
    assert (
        client.post(
            f"/api/projects/{project_id}/files", files=[("files", ("empty.txt", b"", "text/plain"))]
        ).status_code
        == 400
    )
    big = b"x" * (3 * 1024 * 1024)
    assert (
        client.post(
            f"/api/projects/{project_id}/files", files=[("files", ("big.txt", big, "text/plain"))]
        ).status_code
        == 413
    )

    tpl = _upload(client, project_id, "t.pptx", pptx_bytes, PPTX_MIME)
    template_id = client.post("/api/templates", json={"file_id": tpl["file_id"]}).json()[
        "template_id"
    ]
    package_id = client.post(
        "/api/content", json={"brief": {"purpose": "report", "title": "Итоги", "language": "ru"}}
    ).json()["package_id"]
    bad = client.post(
        "/api/generations",
        json={
            "schema_version": "1.1",
            "template_id": template_id,
            "package_id": package_id,
            "settings": {"slide_count": {"min": 20, "max": 10}},
        },
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "slide_count_range"
    missing = client.post(
        "/api/generations",
        json={"schema_version": "1.1", "template_id": "tpl_none", "package_id": package_id},
    )
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "template_not_found"
    assert client.post("/api/templates", json={"file_id": "file_none"}).status_code == 404
    assert client.post("/api/content", json={}).status_code == 400
    assert client.get("/api/projects/prj_missing").status_code == 404


def test_events_and_delete(client: TestClient) -> None:
    project_id = _project(client)
    r = client.post(
        f"/api/projects/{project_id}/events",
        json={"role": "user", "kind": "message", "text": "Привет", "file_ids": []},
    )
    assert r.status_code == 201
    event = r.json()
    q = client.post(
        f"/api/projects/{project_id}/events",
        json={"role": "assistant", "kind": "template_question", "file_id": "file_x"},
    ).json()
    patched = client.patch(
        f"/api/projects/{project_id}/events/{q['event_id']}", json={"resolved": "template"}
    ).json()
    assert patched["resolved"] == "template"
    events = client.get(f"/api/projects/{project_id}/events").json()
    assert [e["event_id"] for e in events] == [event["event_id"], q["event_id"]]
    project = client.patch(
        f"/api/projects/{project_id}",
        json={
            "title": "Квартальный отчёт",
            "brief": {
                "purpose": "report",
                "title": "Итоги",
                "audience": "",
                "goal": "",
                "language": "ru",
                "tone": "",
                "must_include": [],
                "avoid": [],
            },
        },
    ).json()
    assert project["title"] == "Квартальный отчёт" and project["brief"]["purpose"] == "report"
    assert len(project["events"]) == 2
    assert client.delete(f"/api/projects/{project_id}").status_code == 204
    assert client.get(f"/api/projects/{project_id}").status_code == 404
    assert client.get(f"/api/projects/{project_id}/events").status_code == 404


def test_template_library_and_delete(
    client: TestClient, orchestrator: Orchestrator, pptx_bytes: bytes
) -> None:
    """Список библиотеки несёт миниатюру и число композиций; удаление убирает строку
    и миниатюры, проект и файл теряют ссылку, генерации остаются."""
    project_id = _project(client)
    tpl = _upload(client, project_id, "Корпоративный шаблон.pptx", pptx_bytes, PPTX_MIME)
    template_id = client.post("/api/templates", json={"file_id": tpl["file_id"]}).json()[
        "template_id"
    ]
    client.patch(f"/api/projects/{project_id}", json={"template_id": template_id})
    client.post(
        f"/api/projects/{project_id}/events",
        json={"role": "assistant", "kind": "template_card", "template_id": template_id},
    )

    item = next(i for i in client.get("/api/templates").json() if i["template_id"] == template_id)
    detail = client.get(f"/api/templates/{template_id}").json()
    assert item["status"] == "succeeded"
    assert item["pattern_count"] == len(detail["profile"]["patterns"])
    assert item["preview"] in detail["previews"]
    assert client.get(f"/api/templates/{template_id}/assets/{item['preview']}").status_code == 200
    previews_dir = orchestrator.artifacts.template_dir(template_id)
    assert previews_dir.is_dir()

    assert client.delete(f"/api/templates/{template_id}").status_code == 204
    assert client.get(f"/api/templates/{template_id}").status_code == 404
    assert client.delete(f"/api/templates/{template_id}").status_code == 404
    assert all(i["template_id"] != template_id for i in client.get("/api/templates").json())
    assert not previews_dir.exists()
    project = client.get(f"/api/projects/{project_id}").json()
    assert project.get("template_id") is None
    assert project["files"][0]["kind"] == "template"
    assert project["files"][0].get("template_id") is None
    # Лента проекта хранит историю: карточка шаблона остаётся, интерфейс покажет её удалённой.
    assert project["events"][0]["template_id"] == template_id
    # Задание анализа без шаблона — сирота для сборки мусора, байты файла живы через проект.
    live = live_sets(orchestrator.state.gc_snapshot())
    assert detail["job_id"] not in live["jobs"]
    assert tpl["sha256"] in live["blobs"]

    # Те же байты снова — новый шаблон с новым анализом, а не запись из кэша.
    r = client.post("/api/templates", json={"file_id": tpl["file_id"]})
    assert r.status_code == 202 and r.json()["cached"] is False
    assert r.json()["template_id"] != template_id
    files = client.get(f"/api/projects/{project_id}").json()["files"]
    assert files[0]["template_id"] == r.json()["template_id"]


def test_multipart_cli_paths(client: TestClient, pptx_bytes: bytes, xlsx_bytes: bytes) -> None:
    """Multipart для CLI и внешних клиентов работает без проекта."""
    r = client.post("/api/templates", files={"file": ("cli.pptx", pptx_bytes, PPTX_MIME)})
    assert r.status_code == 202
    assert (
        client.post(
            "/api/templates", files={"file": ("cli.txt", b"hello", "text/plain")}
        ).status_code
        == 415
    )
    r = client.post(
        "/api/content",
        files=[("files", ("metrics.xlsx", xlsx_bytes, XLSX_MIME))],
        data={"brief": json.dumps({"purpose": "report", "title": "Метрики", "language": "ru"})},
    )
    assert r.status_code == 202
    pkg = client.get(f"/api/content/{r.json()['package_id']}").json()
    assert pkg["status"] == "succeeded" and pkg["package"]["mode"] == "mixed"
