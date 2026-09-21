"""ONLYOFFICE conversion: signed transport, bounded failures and atomic files."""

from __future__ import annotations

import io
import json
import pathlib
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from PIL import Image

from presentation_designer.export import pdf
from presentation_designer.export.office_source import publish_source, source_path
from presentation_designer.pipeline.office import sign, verify
from presentation_designer.shared.settings import Settings

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures/pptx/mini_template.pptx"
SECRET = "test-conversion-secret-not-for-production-123456"


@pytest.fixture
def settings(tmp_path):
    settings = Settings()
    settings.paths.data_dir = tmp_path / "data"
    settings.onlyoffice.enabled = True
    settings.onlyoffice.jwt_secret = SECRET
    return settings


def pdf_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (160, 90), "white").save(buf, format="PDF")
    return buf.getvalue()


def mock_http(monkeypatch, handler):
    real = httpx.Client
    monkeypatch.setattr(
        pdf.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )


def test_convert_poll_sign_download_cleanup(settings, tmp_path, monkeypatch):
    calls = []
    before = FIXTURE.read_bytes()

    def handle(request):
        calls.append(request)
        if request.method == "POST":
            body = json.loads(request.content)
            assert verify(body.pop("token"), SECRET) == body
            assert body["async"] is True
            key = body["key"]
            assert source_path(settings, key).read_bytes() == before
            claims = verify(parse_qs(urlsplit(body["url"]).query)["token"][0], SECRET)
            assert claims["scope"] == "office-conversion" and claims["key"] == key
            if len(calls) == 1:
                return httpx.Response(200, json={"endConvert": False, "percent": 20})
            return httpx.Response(
                200,
                json={
                    "endConvert": True,
                    "fileUrl": "http://onlyoffice/cache/files/test/output.pdf",
                },
            )
        return httpx.Response(200, content=pdf_bytes())

    mock_http(monkeypatch, handle)
    result = pdf.convert_to_pdf(FIXTURE, tmp_path / "out", settings=settings)
    assert result.pdf_path.read_bytes().startswith(b"%PDF-")
    assert result.seconds >= 0
    assert len(calls) == 3
    assert json.loads(calls[0].content) == json.loads(calls[1].content)
    assert not list((settings.paths.data_dir / "office-conversions").iterdir())
    assert FIXTURE.read_bytes() == before
    assert not (settings.paths.data_dir / "onlyoffice.sqlite3").exists()


@pytest.mark.parametrize(
    "result",
    [
        {"error": -4},
        [],
        {"endConvert": True},
        {"endConvert": True, "fileUrl": "http://evil.invalid/file.pdf"},
        {"endConvert": True, "fileUrl": "http://onlyoffice/cache/files/../private"},
    ],
)
def test_bad_responses_preserve_existing_pdf(settings, tmp_path, monkeypatch, result):
    target = tmp_path / "mini_template.pdf"
    target.write_bytes(b"existing")
    mock_http(monkeypatch, lambda req: httpx.Response(200, json=result))
    with pytest.raises(pdf.ConversionError):
        pdf.convert_to_pdf(FIXTURE, tmp_path, settings=settings)
    assert target.read_bytes() == b"existing"
    assert not list((settings.paths.data_dir / "office-conversions").iterdir())
    assert not list(tmp_path.glob(".office-pdf-*"))


@pytest.mark.parametrize("content", [b"not PDF", b"%PDF-corrupt", b"x" * 1048577])
def test_invalid_or_oversized_pdf(settings, tmp_path, monkeypatch, content):
    settings.onlyoffice.max_pdf_mb = 1

    def handle(req):
        if req.method == "POST":
            return httpx.Response(
                200,
                json={
                    "endConvert": True,
                    "fileUrl": "http://onlyoffice/cache/files/test/output.pdf",
                },
            )
        return httpx.Response(200, content=content)

    mock_http(monkeypatch, handle)
    with pytest.raises(pdf.ConversionError):
        pdf.convert_to_pdf(FIXTURE, tmp_path, settings=settings)
    assert not (tmp_path / "mini_template.pdf").exists()


def test_timeout(settings, tmp_path, monkeypatch):
    mock_http(monkeypatch, lambda req: httpx.Response(200, json={"endConvert": False}))
    with pytest.raises(pdf.ConversionError, match="не уложился"):
        pdf.convert_to_pdf(FIXTURE, tmp_path, timeout_s=1, settings=settings)
    assert not list((settings.paths.data_dir / "office-conversions").iterdir())


@pytest.mark.parametrize("status", [302, 403, 500])
def test_http_failure(settings, tmp_path, monkeypatch, status):
    mock_http(monkeypatch, lambda req: httpx.Response(status, headers={"Location": "http://evil/"}))
    with pytest.raises(pdf.ConversionError):
        pdf.convert_to_pdf(FIXTURE, tmp_path, settings=settings)


def test_unreachable(settings, tmp_path, monkeypatch):
    def handle(req):
        raise httpx.ConnectError("unreachable", request=req)

    mock_http(monkeypatch, handle)
    with pytest.raises(pdf.RendererUnavailableError):
        pdf.convert_to_pdf(FIXTURE, tmp_path, settings=settings)


def test_missing_input_and_configuration(settings, tmp_path):
    with pytest.raises(pdf.ConversionError, match="нет входного файла"):
        pdf.convert_to_pdf(tmp_path / "missing.pptx", tmp_path, settings=settings)
    settings.onlyoffice.enabled = False
    with pytest.raises(pdf.RendererUnavailableError):
        pdf.convert_to_pdf(FIXTURE, tmp_path, settings=settings)


def test_local_converters_share_slots(monkeypatch):
    from presentation_designer.export.render_slots import render_slots_from_env

    monkeypatch.setenv("PD_QUEUE_MODE", "inline")
    assert render_slots_from_env(2) is render_slots_from_env(2)


def test_conversion_endpoint_scoped_expiring_and_cleaned(client, orchestrator):
    settings = orchestrator.settings
    settings.onlyoffice.enabled = True
    settings.onlyoffice.jwt_secret = SECRET
    with publish_source(FIXTURE, settings, 60) as (key, url):
        parts = urlsplit(url)
        route = parts.path + "?" + parts.query
        assert client.get(route).content == FIXTURE.read_bytes()
        for claims in (
            {"scope": "office-file", "key": key, "exp": time.time() + 60},
            {"scope": "office-conversion", "key": "other", "exp": time.time() + 60},
            {"scope": "office-conversion", "key": key, "exp": 1},
            {"scope": "office-conversion", "key": key},
        ):
            assert client.get(parts.path, params={"token": sign(claims, SECRET)}).status_code == 403
        assert client.get(parts.path).status_code == 403
    assert client.get(route).status_code == 404
    assert not (settings.paths.data_dir / "onlyoffice.sqlite3").exists()
