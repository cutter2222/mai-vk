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
        json={"schema_version": "1.2", "template_id": template_id, "package_id": package_id},
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


class Recorder:
    """Исполнитель, который только записывает постановки: задачи не выполняются, пока тест
    не позовёт их сам. Статус записанной задачи — «queued», чужой — неизвестен."""

    name = "record"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def enqueue(self, queue: str, func: Any, args: tuple[Any, ...], **kw: Any) -> str:
        rq_id = f"rq{len(self.calls)}"
        self.calls.append(
            {
                "id": rq_id,
                "queue": queue,
                "func": func,
                "args": args,
                "depends_on": kw["depends_on"],
            }
        )
        return rq_id

    def status(self, rq_id: str) -> str | None:
        return "queued" if any(c["id"] == rq_id for c in self.calls) else None

    def workers(self) -> dict[str, int]:
        return {"analysis": 1, "generation": 1}

    def renderer_ok(self) -> bool | None:
        return None

    def ping(self) -> bool:
        return True

    def try_lock(self, key: str, ttl_s: int) -> bool:
        return True

    def run(self, func: Any) -> None:
        """Выполняет записанные задачи этой функции в порядке постановки."""
        for call in [c for c in self.calls if c["func"] is func]:
            func(*call["args"])
