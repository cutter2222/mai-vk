"""Signed office callbacks, immutable revisions and isolated AI source."""

import io
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import httpx
import pytest
from pptx import Presentation

from presentation_designer.api.errors import ApiError
from presentation_designer.api.routes import onlyoffice
from presentation_designer.pipeline.office import OfficeStore, sign, verify

SECRET = "test-onlyoffice-secret-not-for-production-123456"


def test_edit_exclusive_lease_and_revision(office, pptx_bytes):
    store, doc_id = office
    opened = store.open(doc_id)
    with pytest.raises(ApiError):
        store.begin_edit(doc_id, 0)
    store.callback(doc_id, opened["active_key"], 4, None)
    token = store.begin_edit(doc_id, 0)
    with pytest.raises(ApiError):
        store.open(doc_id)
    with pytest.raises(ApiError):
        store.begin_edit(doc_id, 0)
    store.commit_edit(doc_id, token, 0, pptx_bytes)
    assert store.get(doc_id)["revision"] == 0
    store.end_edit(doc_id, token)
    assert store.open(doc_id)["active_key"] != opened["active_key"]
    with pytest.raises(ApiError):
        store.commit_edit(doc_id, token, 0, pptx_bytes)
    assert not store.callback(doc_id, opened["active_key"], 2, pptx_bytes)


def test_expired_edit_cannot_commit(office, pptx_bytes):
    store, doc_id = office
    token = store.begin_edit(doc_id, 0)
    with store.connect() as db:
        db.execute("UPDATE edit_locks SET expires=0")
    replacement = store.begin_edit(doc_id, 0)
    store.end_edit(doc_id, token)
    with pytest.raises(ApiError):
        store.commit_edit(doc_id, token, 0, pptx_bytes)
    with pytest.raises(ApiError):
        store.open(doc_id)
    store.end_edit(doc_id, replacement)


def test_edit_endpoint_uses_saved_pptx(client, office, monkeypatch):
    from presentation_designer.generation.office_edit import EditPlan, TextPatch

    store, doc_id = office
    deck = Presentation()
    deck.slides.add_slide(deck.slide_layouts[0]).shapes.title.text = "Ручная правка"
    output = io.BytesIO()
    deck.save(output)
    opened = store.open(doc_id)
    store.callback(doc_id, opened["active_key"], 2, output.getvalue())

    async def propose(data, instruction, settings):
        assert data == output.getvalue()
        assert instruction == "Измени заголовок"
        with pytest.raises(ApiError):
            store.open(doc_id)
        return EditPlan(
            explanation="Готово",
            patches=[TextPatch(slide=1, run=0, before="Ручная правка", after="После ИИ")],
        )

    monkeypatch.setattr(onlyoffice.office_edit, "propose", propose)
    response = client.post(
        f"/api/office/documents/{doc_id}/edit",
        json={"revision": 1, "instruction": "Измени заголовок"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["changed"] is True
    assert response.json()["document"]["revision"] == 2
    assert store.read(doc_id, 1) == output.getvalue()
    assert Presentation(io.BytesIO(store.read(doc_id, 2))).slides[0].shapes.title.text == "После ИИ"
    assert (
        client.post(
            f"/api/office/documents/{doc_id}/edit",
            json={"revision": 1, "instruction": "Старая база"},
        ).status_code
        == 409
    )
    assert store.open(doc_id)["seed_revision"] == 2


def test_edit_failure_unlocks_without_writing(client, office, monkeypatch):
    store, doc_id = office

    async def fail(*args):
        raise ValueError("Тестовый отказ")

    monkeypatch.setattr(onlyoffice.office_edit, "propose", fail)
    response = client.post(
        f"/api/office/documents/{doc_id}/edit", json={"revision": 0, "instruction": "Правка"}
    )
    assert response.status_code == 422
    assert store.get(doc_id)["revision"] == 0
    assert store.open(doc_id)["seed_revision"] == 0


@pytest.fixture
def office(orchestrator, pptx_bytes):
    orchestrator.settings.onlyoffice.enabled = True
    orchestrator.settings.onlyoffice.jwt_secret = SECRET
    store = OfficeStore(orchestrator.settings.paths.data_dir)
    doc = store.create("job_test/balanced/r1/deck.pptx", "test.pptx", pptx_bytes)
    return store, doc["id"]


def test_disabled(client):
    assert client.get("/api/office/capabilities").json() == {"enabled": False}
    assert client.post("/api/office/documents/no/config").status_code == 503
    assert client.post("/api/office/templates/no/config").status_code == 503


@pytest.mark.parametrize("format,media", [("pdf", "application/pdf"), ("html", "text/html")])
def test_export_saved_revision(client, office, monkeypatch, format, media):
    store, doc_id = office
    original = store.read(doc_id, 0)

    def export(data, requested, settings):
        assert data == original
        assert requested == format
        return b"exported-revision"

    monkeypatch.setattr(onlyoffice, "export_revision", export)
    response = client.get(f"/api/office/documents/{doc_id}/download/0?format={format}")
    assert response.status_code == 200
    assert response.content == b"exported-revision"
    assert response.headers["content-type"].startswith(media)
    assert response.headers["content-disposition"].endswith(f'.{format}"')
    assert store.read(doc_id, 0) == original
    assert store.get(doc_id)["active_key"] is None
    missing = client.get(f"/api/office/documents/{doc_id}/download/99?format={format}")
    assert missing.status_code == 404


def test_export_failure_and_invalid_format(client, office, monkeypatch):
    _, doc_id = office

    def fail(*args):
        raise onlyoffice.ConversionError("unavailable")

    monkeypatch.setattr(onlyoffice, "export_revision", fail)
    url = f"/api/office/documents/{doc_id}/download/0"
    assert client.get(url + "?format=pdf").status_code == 503
    assert client.get(url + "?format=exe").status_code == 422
    assert client.get(url).status_code == 200


def test_template_viewer_is_signed_read_only_and_does_not_change_source(client, office, pptx_bytes):
    from urllib.parse import parse_qs

    uploaded = client.post("/api/templates", files={"file": ("Шаблон.pptx", pptx_bytes)})
    assert uploaded.status_code == 202, uploaded.text
    template_id = uploaded.json()["template_id"]
    before = client.get(f"/api/templates/{template_id}").json()
    response = client.post(f"/api/office/templates/{template_id}/config")
    assert response.status_code == 200, response.text
    config = response.json()["config"]
    signed = verify(config["token"], SECRET)
    assert signed == {k: v for k, v in config.items() if k != "token"}
    assert config["editorConfig"]["mode"] == "view"
    assert config["editorConfig"]["customization"]["anonymous"]["request"] is False
    assert config["editorConfig"]["user"]["name"] == "Просмотр шаблона"
    assert "callbackUrl" not in config["editorConfig"]
    assert config["document"]["permissions"]["edit"] is False
    url = urlsplit(config["document"]["url"])
    assert client.get(url.path + "?" + url.query).content == pptx_bytes
    assert client.get(url.path, params={"token": "bad"}).status_code == 403
    claims = verify(parse_qs(url.query)["token"][0], SECRET)
    claims["scope"] = "office-file"
    assert client.get(url.path, params={"token": sign(claims, SECRET)}).status_code == 403
    claims["scope"] = "office-template"
    claims["sha256"] = "wrong"
    assert client.get(url.path, params={"token": sign(claims, SECRET)}).status_code == 403
    assert client.get(f"/api/templates/{template_id}/source").content == pptx_bytes
    assert client.get(f"/api/templates/{template_id}").json() == before
    assert client.post("/api/office/templates/missing/config").status_code == 404
    assert client.get("/api/templates/missing/source").status_code == 404
    # Download remains useful with ONLYOFFICE disabled.
    client.app.state.orchestrator.settings.onlyoffice.enabled = False
    assert client.get(f"/api/templates/{template_id}/source").content == pptx_bytes


def test_tokens():
    token = sign({"key": "hello", "exp": time.time() + 60}, SECRET)
    assert verify(token, SECRET)["key"] == "hello"
    for invalid in ("", "none", token + "x", sign({"exp": 1}, SECRET)):
        with pytest.raises(ApiError):
            verify(invalid, SECRET)
    with pytest.raises(ApiError):
        verify(token, "wrong secret")


def test_config_simplifies_ui_without_disabling_editing_or_saving(client, office):
    _, doc_id = office
    response = client.post(f"/api/office/documents/{doc_id}/config")
    assert response.status_code == 200, response.text
    config = response.json()["config"]
    editor = config["editorConfig"]
    assert editor["mode"] == "edit"
    assert config["document"]["permissions"] == {
        "edit": True,
        "download": False,
        "print": True,
    }
    assert editor["customization"] == {
        "forcesave": True,
        "autosave": True,
        "compactHeader": True,
        "toolbarHideFileName": True,
        "plugins": False,
        "macros": False,
        "help": False,
        "feedback": {"visible": False},
        "suggestFeature": False,
    }
    # Keep all UI settings inside the signed configuration, not a browser-side override.
    assert verify(config["token"], SECRET)["editorConfig"] == editor


def test_config_source_and_session_key(client, office, pptx_bytes):
    store, doc_id = office
    url = f"/api/office/documents/{doc_id}"
    config = client.post(url + "/config").json()["config"]
    assert verify(config["token"], SECRET)["document"] == config["document"]
    assert config["documentType"] == "slide"
    customization = config["editorConfig"]["customization"]
    assert customization["compactHeader"] is True
    assert customization["toolbarHideFileName"] is True
    assert customization["forcesave"] is True
    assert verify(config["token"], SECRET)["editorConfig"] == config["editorConfig"]
    assert (
        config["document"]["key"]
        == client.post(url + "/config").json()["config"]["document"]["key"]
    )
    source = urlsplit(config["document"]["url"])
    assert client.get(source.path + "?" + source.query).content == pptx_bytes
    assert client.get(source.path + "?token=bad").status_code == 403
    assert client.get(source.path.replace("/0", "/1") + "?" + source.query).status_code == 403
    assert (
        client.post(
            url + "/callback", json={"key": config["document"]["key"], "status": 4}
        ).status_code
        == 403
    )
    claims = {"key": config["document"]["key"], "status": 4}
    assert client.post(url + "/callback", json={"token": sign(claims, SECRET)}).json() == {
        "error": 0
    }
    assert store.get(doc_id)["active_key"] is None
    assert client.post(url + "/config").json()["config"]["document"]["key"] != claims["key"]


@pytest.mark.parametrize("value", [None, True, "NaN", float("nan"), float("inf"), 10**400])
@pytest.mark.parametrize("claim", ["exp", "nbf"])
def test_invalid_token_time_claim(claim, value):
    with pytest.raises(ApiError) as error:
        verify(sign({claim: value}, SECRET), SECRET)
    assert error.value.status == 403


@pytest.mark.parametrize("token", [None, 123, [], {}, "bad.token.signature"])
def test_malformed_callback_token(client, office, token):
    _, doc_id = office
    assert (
        client.post(f"/api/office/documents/{doc_id}/callback", json={"token": token}).status_code
        == 403
    )


@pytest.mark.parametrize("value", [None, 123, [], {}])
def test_malformed_signed_save_url(client, office, value):
    store, doc_id = office
    key = store.open(doc_id)["active_key"]
    response = client.post(
        f"/api/office/documents/{doc_id}/callback",
        json={"token": sign({"key": key, "status": 2, "url": value}, SECRET)},
    )
    assert response.json() == {"error": 1}
    assert store.get(doc_id)["revision"] == 0
    assert store.get(doc_id)["error"]


def test_save_round_trip(client, office, monkeypatch, pptx_bytes):
    store, doc_id = office
    url = f"/api/office/documents/{doc_id}"
    config = client.post(url + "/config").json()["config"]
    key = config["document"]["key"]
    presentation = Presentation(io.BytesIO(pptx_bytes))
    presentation.slides[0].shapes.add_textbox(0, 0, 1000000, 1000000).text = "Manual office edit"
    buffer = io.BytesIO()
    presentation.save(buffer)
    edited = buffer.getvalue()
    real_client = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=edited)

    monkeypatch.setattr(
        onlyoffice.httpx,
        "AsyncClient",
        lambda **kw: real_client(
            transport=httpx.MockTransport(handle),
            **kw,
        ),
    )
    claims = {"key": key, "status": 6, "url": "http://onlyoffice/cache/files/test/output.pptx"}
    # Unsigned siblings are ignored; only signed URL and status are used.
    body = {"token": sign(claims, SECRET), "url": "http://evil.invalid/", "status": 4}
    assert client.post(url + "/callback", json=body).json() == {"error": 0}
    assert requests == [claims["url"]]
    assert client.get(url + "/download/1").content == edited
    assert client.get(url + "/download/0").content == pptx_bytes
    assert client.post(url + "/callback", json=body).json() == {"error": 0}
    assert store.get(doc_id)["revision"] == 1  # idempotent repeated force-save
    # Force-save must NOT rotate a key while users are still connected.
    assert client.post(url + "/config").json()["config"]["document"]["key"] == key
    claims["status"] = 2
    assert client.post(url + "/callback", json={"token": sign(claims, SECRET)}).json() == {
        "error": 0
    }
    assert store.get(doc_id)["active_key"] is None
    reopened = client.post(url + "/config").json()["config"]
    source = urlsplit(reopened["document"]["url"])
    assert client.get(source.path + "?" + source.query).content == edited
    assert reopened["document"]["key"] != key
    assert client.post(url + "/callback", json={"token": sign(claims, SECRET)}).json() == {
        "error": 1
    }
    assert store.create("job_test/balanced/r1/deck.pptx", "test.pptx", pptx_bytes)["id"] == doc_id


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/cache/files/x",
        "http://evil.invalid/cache/files/x",
        "http://onlyoffice/healthcheck",
        "http://onlyoffice/cache/files/../x",
        "http://onlyoffice/cache/files/%2e%2e/x",
        "http://user@onlyoffice/cache/files/x",
        "http://onlyoffice:8000/cache/files/x",
    ],
)
def test_reject_save_ssrf(url):
    with pytest.raises(ValueError):
        onlyoffice.saved_url(url, "http://onlyoffice", "http://localhost:8080/onlyoffice")


def test_public_save_url():
    assert (
        onlyoffice.saved_url(
            "http://localhost:8080/onlyoffice/cache/files/key/output.pptx?token=abc",
            "http://onlyoffice",
            "http://localhost:8080/onlyoffice",
        )
        == "http://onlyoffice/cache/files/key/output.pptx?token=abc"
    )


def test_failed_save_does_not_ack_or_replace(client, office, monkeypatch, pptx_bytes):
    store, doc_id = office
    doc = store.open(doc_id)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        onlyoffice.httpx,
        "AsyncClient",
        lambda **kw: real_client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=b"not a pptx")
            ),
            **kw,
        ),
    )
    body = {
        "token": sign(
            {"key": doc["active_key"], "status": 2, "url": "http://onlyoffice/cache/files/x"},
            SECRET,
        )
    }
    assert client.post(f"/api/office/documents/{doc_id}/callback", json=body).json() == {"error": 1}
    assert store.get(doc_id)["error"]
    assert store.read(doc_id, 0) == pptx_bytes
    assert store.get(doc_id)["revision"] == 0


def test_concurrent_creation_and_keys(office, pptx_bytes):
    store, doc_id = office

    def open_copy(_):
        doc = store.create("job_test/balanced/r1/deck.pptx", "test.pptx", pptx_bytes)
        return store.open(doc["id"])["active_key"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        keys = list(pool.map(open_copy, range(8)))
    assert len(set(keys)) == 1
    assert store.get(doc_id)["revision"] == 0
