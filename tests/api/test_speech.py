"""Голосовой ввод через API: заглушка без модели, проверки записи (415/413/422), сервис
недоступен (503), пересылка в сервис `asr` и признак `features.speech` в /capabilities."""

from __future__ import annotations

import pathlib
import threading
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from presentation_designer.api.routes import speech as speech_route
from presentation_designer.pipeline.jobs import Orchestrator
from presentation_designer.speech.server import SpeechServer
from tests.speech.test_service import fake_model, wav_bytes

URL = "/api/speech/transcribe"


def post(client: TestClient, data: bytes, name: str = "phrase.wav") -> object:
    return client.post(URL, files={"audio": (name, data, "audio/wav")})


@pytest.fixture(autouse=True)
def fresh_health_cache() -> Iterator[None]:
    speech_route._health.update(at=0.0, url=None, ok=False)
    yield
    speech_route._health.update(at=0.0, url=None, ok=False)


def test_stub_answers_without_a_model(client: TestClient) -> None:
    r = post(client, wav_bytes(2.5))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "text": speech_route.STUB_PHRASE,
        "duration_ms": 2500,
        "infer_ms": 0,
        "model": "stub",
    }
    caps = client.get("/api/capabilities").json()
    assert caps["features"]["speech"] is True
    assert caps["speech"] == {"language": "ru", "max_seconds": 25.0}


def test_audio_is_checked(client: TestClient) -> None:
    for data in (b"not a wav", wav_bytes(1, rate=44100), wav_bytes(1, channels=2)):
        r = post(client, data)
        assert r.status_code == 415 and r.json()["error"]["code"] == "audio_format"
    r = post(client, wav_bytes(26))
    assert r.status_code == 413 and r.json()["error"]["code"] == "audio_too_long"
    big = client.post(URL, files={"audio": ("x.wav", b"0" * 1_200_000, "audio/wav")})
    assert big.status_code == 413
    missing = client.post(URL, files={"other": ("x.wav", wav_bytes(1), "audio/wav")})
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "audio_required"


def test_unavailable_service_is_503_and_hides_the_button(
    client: TestClient, orchestrator: Orchestrator
) -> None:
    orchestrator.settings.execution.mode = "real"
    orchestrator.settings.speech.url = "http://127.0.0.1:9"  # закрытый порт
    orchestrator.settings.speech.timeout_s = 2
    r = post(client, wav_bytes(1))
    assert r.status_code == 503 and r.json()["error"]["code"] == "speech_unavailable"
    assert client.get("/api/capabilities").json()["features"]["speech"] is False
    orchestrator.settings.speech.enabled = False
    orchestrator.settings.execution.mode = "stub"
    assert post(client, wav_bytes(1)).status_code == 503
    assert client.get("/api/capabilities").json()["features"]["speech"] is False


def test_phrase_goes_to_the_asr_service(
    client: TestClient,
    orchestrator: Orchestrator,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model, _, session = fake_model(tmp_path, monkeypatch)
    server = SpeechServer(("127.0.0.1", 0), model, max_bytes=1_000_000, max_seconds=25)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        orchestrator.settings.execution.mode = "real"
        orchestrator.settings.speech.url = f"http://127.0.0.1:{server.server_address[1]}"
        # Модель на месте, но не загружена: кнопка есть, модель грузится первым запросом.
        assert client.get("/api/capabilities").json()["features"]["speech"] is True
        r = post(client, wav_bytes(3))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["text"] == "а" and body["duration_ms"] == 3000 and body["model"] == "fake"
        assert session.calls == 1 and model.status()["state"] == "loaded"
    finally:
        server.shutdown()
        server.server_close()


def test_missing_model_hides_the_button(
    client: TestClient, orchestrator: Orchestrator, tmp_path: pathlib.Path
) -> None:
    from presentation_designer.speech.service import SpeechModel

    empty = SpeechModel(tmp_path, watch=False)
    server = SpeechServer(("127.0.0.1", 0), empty, max_bytes=1_000_000, max_seconds=25)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        orchestrator.settings.execution.mode = "real"
        orchestrator.settings.speech.url = f"http://127.0.0.1:{server.server_address[1]}"
        assert client.get("/api/capabilities").json()["features"]["speech"] is False
        r = post(client, wav_bytes(1))
        assert r.status_code == 503 and r.json()["error"]["code"] == "speech_unavailable"
    finally:
        server.shutdown()
        server.server_close()
