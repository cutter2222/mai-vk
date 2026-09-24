"""Сервис распознавания без настоящей модели: ленивая загрузка, выгрузка по простою, отчёт
`model_missing`, одна очередь на процесс, пределы записи и HTTP-приложение."""

from __future__ import annotations

import io
import json
import pathlib
import threading
import time
import urllib.error
import urllib.request
import wave
from typing import Any

import numpy as np
import pytest

from presentation_designer.speech import service as service_module
from presentation_designer.speech.audio import AudioError, read_wav
from presentation_designer.speech.decode import Vocabulary
from presentation_designer.speech.features import FeatureParams, Featurizer
from presentation_designer.speech.server import SpeechServer
from presentation_designer.speech.service import SpeechError, SpeechModel, _Loaded

PARAMS = FeatureParams(n_mels=8, win_length=320, hop_length=160, n_fft=320, center=False)


def wav_bytes(seconds: float, *, rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    frames = int(seconds * rate)
    t = np.arange(frames) / rate
    pcm = (0.3 * np.sin(2 * np.pi * 300 * t) * 32767).astype("<i2")
    if channels == 2:
        pcm = np.repeat(pcm, 2)
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes() if width == 2 else (pcm // 256).astype("i1").tobytes())
    return out.getvalue()


class FakeSession:
    """Сессия ONNX Runtime: в первом кадре — символ «а», дальше пустой символ."""

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self.calls = 0
        self.lock = threading.Lock()

    class _Input:
        def __init__(self, name: str) -> None:
            self.name = name

    def get_inputs(self) -> list[Any]:
        return [self._Input("features"), self._Input("feature_lengths")]

    def run(self, _outputs: Any, feeds: dict[str, np.ndarray]) -> list[np.ndarray]:
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls += 1
        time.sleep(0.02)
        frames = feeds["features"].shape[2]
        log_probs = np.full((1, frames, 3), -5.0, dtype=np.float32)
        log_probs[0, :, 2] = 0.0  # пустой
        log_probs[0, 0, 1] = 1.0  # «а»
        with self.lock:
            self.active -= 1
        return [log_probs, np.array([frames], dtype=np.int64)]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def fake_model(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[SpeechModel, Clock, FakeSession]:
    (tmp_path / "graph.onnx").write_bytes(b"onnx")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"model": "fake", "graph": "graph.onnx", "yaml": "fake.yaml"}), encoding="utf-8"
    )
    clock = Clock()
    session = FakeSession()
    model = SpeechModel(tmp_path, idle_unload_s=600, clock=clock, watch=False)

    def load() -> _Loaded:
        model.loads += 1
        return _Loaded(session, Featurizer.create(PARAMS), Vocabulary([" ", "а"]), "fake")

    monkeypatch.setattr(model, "_load", load)
    return model, clock, session


def test_lazy_load_and_unload_after_idle(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model, clock, _ = fake_model(tmp_path, monkeypatch)
    assert model.status()["state"] == "unloaded" and model.loads == 0
    audio = np.zeros(16000, dtype=np.float32)
    result = model.transcribe(audio)
    assert result.text == "а" and result.duration_ms == 1000 and result.model == "fake"
    assert model.status()["state"] == "loaded" and model.loads == 1
    model.transcribe(audio)
    assert model.loads == 1  # модель уже в памяти
    clock.now += 599
    assert not model.unload_if_idle()
    clock.now += 2
    assert model.unload_if_idle()
    assert model.status()["state"] == "unloaded"
    model.transcribe(audio)
    assert model.loads == 2  # после выгрузки — снова по запросу


def test_missing_model_is_reported(tmp_path: pathlib.Path) -> None:
    model = SpeechModel(tmp_path, watch=False)
    assert model.status() == {"state": "model_missing", "loads": 0}
    assert not model.available()
    with pytest.raises(SpeechError) as e:
        model.transcribe(np.zeros(16000, dtype=np.float32))
    assert e.value.code == "model_missing"
    # Манифест без файла графа — тоже нет модели.
    (tmp_path / "manifest.json").write_text('{"graph": "absent.onnx"}', encoding="utf-8")
    assert model.status()["state"] == "model_missing"


def test_requests_run_one_at_a_time(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model, _, session = fake_model(tmp_path, monkeypatch)
    audio = np.zeros(8000, dtype=np.float32)
    threads = [threading.Thread(target=model.transcribe, args=(audio,)) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert session.calls == 6 and session.max_active == 1


def test_wav_limits() -> None:
    assert read_wav(wav_bytes(1.5), max_seconds=25).seconds == pytest.approx(1.5)
    for data in (
        b"not a wav",
        wav_bytes(1, rate=8000),
        wav_bytes(1, channels=2),
        wav_bytes(1, width=1),
    ):
        with pytest.raises(AudioError) as e:
            read_wav(data, max_seconds=25)
        assert e.value.code == "audio_format"
    with pytest.raises(AudioError) as e:
        read_wav(wav_bytes(26), max_seconds=25)
    assert e.value.code == "audio_too_long"


def test_http_service(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model, _, _ = fake_model(tmp_path, monkeypatch)
    server = SpeechServer(("127.0.0.1", 0), model, max_bytes=1_000_000, max_seconds=25)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=5) as r:
            assert json.loads(r.read())["speech"]["state"] == "unloaded"
        request = urllib.request.Request(
            f"{base}/transcribe", data=wav_bytes(2.0), headers={"Content-Type": "audio/wav"}
        )
        with urllib.request.urlopen(request, timeout=5) as r:
            body = json.loads(r.read())
        assert body["text"] == "а" and body["duration_ms"] == 2000 and body["model"] == "fake"
        bad = urllib.request.Request(f"{base}/transcribe", data=wav_bytes(1, rate=8000))
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad, timeout=5)
        assert e.value.code == 415
        long = urllib.request.Request(f"{base}/transcribe", data=wav_bytes(26))
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(long, timeout=5)
        assert e.value.code == 413
    finally:
        server.shutdown()
        server.server_close()


def test_heap_trim_is_harmless() -> None:
    service_module._trim_heap()  # на macOS libc.so.6 нет — вызов просто ничего не делает
