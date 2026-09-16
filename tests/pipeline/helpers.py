"""Сквозной путь через HTTP на заглушках для тестов сборки мусора и резервных копий."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def run_generation(
    client: TestClient, pptx: bytes, xlsx: bytes, *, title: str = "Проект"
) -> dict[str, Any]:
    """Проект с шаблоном и материалом, пакет, генерация, привязка задания к проекту."""
    project_id = client.post("/api/projects", json={"title": title}).json()["project_id"]
    tpl = client.post(
        f"/api/projects/{project_id}/files",
        files=[("files", ("Шаблон.pptx", pptx, PPTX_MIME))],
    ).json()[0]
    data = client.post(
        f"/api/projects/{project_id}/files",
        files=[("files", ("metrics.xlsx", xlsx, XLSX_MIME))],
    ).json()[0]
    template_id = client.post("/api/templates", json={"file_id": tpl["file_id"]}).json()[
        "template_id"
    ]
    package_id = client.post(
        "/api/content",
        json={"file_ids": [data["file_id"]], "brief": {"purpose": "product", "title": title}},
    ).json()["package_id"]
    job_id = client.post(
        "/api/generations",
        json={"schema_version": "1.1", "template_id": template_id, "package_id": package_id},
    ).json()["job_id"]
    client.patch(
        f"/api/projects/{project_id}",
        json={"job_id": job_id, "template_id": template_id, "package_id": package_id},
    )
    client.post(
        f"/api/projects/{project_id}/events",
        json={"role": "assistant", "kind": "job_card", "job_id": job_id},
    )
    result = client.get(f"/api/generations/{job_id}").json()
    return {
        "project_id": project_id,
        "template_id": template_id,
        "package_id": package_id,
        "job_id": job_id,
        "template_sha": tpl["sha256"],
        "data_sha": data["sha256"],
        "result": result,
    }
