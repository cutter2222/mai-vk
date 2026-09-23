"""Байты и миниатюры файлов проекта для сетки «Файлы» и вставки картинки на слайд."""

from __future__ import annotations

import io
import pathlib
import zipfile
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from presentation_designer.pipeline.jobs import Orchestrator

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"


def _project(client: TestClient) -> str:
    return str(client.post("/api/projects", json={}).json()["project_id"])


def _upload(client: TestClient, project_id: str, name: str, data: bytes) -> dict[str, Any]:
    r = client.post(f"/api/projects/{project_id}/files", files=[("files", (name, data, "x/y"))])
    assert r.status_code == 201, r.text
    return dict(r.json()[0])


def _png(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (10, 120, 250)).save(out, format="PNG")
    return out.getvalue()


def test_image_content_and_thumbnail_are_cached_by_sha(
    client: TestClient, orchestrator: Orchestrator
) -> None:
    pid = _project(client)
    data = _png(1200, 800)
    row = _upload(client, pid, "фото.png", data)
    base = f"/api/projects/{pid}/files/{row['file_id']}"

    content = client.get(base + "/content")
    assert content.status_code == 200
    assert content.content == data
    assert content.headers["content-type"] == "image/png"
    assert content.headers["content-disposition"].startswith("inline")
    assert content.headers["etag"] == f'"{row["sha256"]}"'
    assert content.headers["x-content-type-options"] == "nosniff"
    again = client.get(base + "/content", headers={"If-None-Match": content.headers["etag"]})
    assert again.status_code == 304

    thumb = client.get(base + "/thumbnail")
    assert thumb.status_code == 200, thumb.text
    assert thumb.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(thumb.content)) as image:
        assert image.size == (480, 320)
    cached = orchestrator.files.thumbnail_path(row["sha256"])
    assert cached.is_file()

    # Те же байты в другом проекте берут готовую миниатюру.
    other = _project(client)
    copy = _upload(client, other, "копия.png", data)
    before = cached.stat().st_mtime_ns
    assert client.get(f"/api/projects/{other}/files/{copy['file_id']}/thumbnail").content == (
        thumb.content
    )
    assert cached.stat().st_mtime_ns == before

    # Удаление байтов сборкой мусора уносит и миниатюру.
    orchestrator.files.remove(row["sha256"])
    assert not cached.exists()


def test_pdf_and_pptx_get_covers(client: TestClient) -> None:
    pid = _project(client)
    pdf = _upload(client, pid, "отчёт.pdf", (FIXTURES / "content" / "report.pdf").read_bytes())
    pptx_bytes = (FIXTURES / "pptx" / "mini_template.pptx").read_bytes()
    pptx = _upload(client, pid, "шаблон.pptx", pptx_bytes)
    for row in (pdf, pptx):
        thumb = client.get(f"/api/projects/{pid}/files/{row['file_id']}/thumbnail")
        assert thumb.status_code == 200, thumb.text
        with Image.open(io.BytesIO(thumb.content)) as image:
            assert max(image.size) <= 480
    content = client.get(f"/api/projects/{pid}/files/{pptx['file_id']}/content")
    assert content.headers["content-disposition"].startswith("attachment")
    assert content.content == pptx_bytes


def test_pptx_without_cover_uses_analyzed_template(client: TestClient) -> None:
    source = io.BytesIO((FIXTURES / "pptx" / "mini_template.pptx").read_bytes())
    stripped = io.BytesIO()
    with zipfile.ZipFile(source) as zin, zipfile.ZipFile(stripped, "w") as zout:
        for item in zin.infolist():
            if not item.filename.startswith("docProps/thumbnail"):
                zout.writestr(item, zin.read(item.filename))
    pid = _project(client)
    row = _upload(client, pid, "без обложки.pptx", stripped.getvalue())
    url = f"/api/projects/{pid}/files/{row['file_id']}/thumbnail"
    missing = client.get(url)
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "thumbnail_unavailable"

    r = client.post("/api/templates", json={"file_id": row["file_id"]})
    assert r.status_code == 202, r.text
    assert client.get(f"/api/templates/{r.json()['template_id']}").json()["status"] == "succeeded"
    assert client.get(url).status_code == 200


def test_unsafe_or_unknown_files(client: TestClient) -> None:
    pid = _project(client)
    svg = _upload(client, pid, "logo.svg", b"<svg onload='alert(1)'/>")
    content = client.get(f"/api/projects/{pid}/files/{svg['file_id']}/content")
    assert content.headers["content-type"] == "application/octet-stream"
    assert content.headers["content-disposition"].startswith("attachment")
    thumb = client.get(f"/api/projects/{pid}/files/{svg['file_id']}/thumbnail")
    assert thumb.status_code == 404
    assert thumb.json()["error"]["code"] == "thumbnail_unavailable"

    text = _upload(client, pid, "notes.txt", "заметки".encode())
    assert client.get(f"/api/projects/{pid}/files/{text['file_id']}/thumbnail").status_code == 404

    other = _project(client)
    foreign = client.get(f"/api/projects/{other}/files/{svg['file_id']}/content")
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "file_not_found"
    assert client.get(f"/api/projects/{pid}/files/file_missing/thumbnail").status_code == 404
