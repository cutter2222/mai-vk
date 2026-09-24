"""Настоящая модель GigaAM (метка `model`): пропускается без каталога весов.

Запись-пример из репозитория GigaAM распознаётся сервисом (признаки на numpy, рабочий граф из
манифеста) не хуже контрольного распознавания fp32 из экспорта, со знаками препинания."""

from __future__ import annotations

import json
import pathlib
import wave

import numpy as np
import pytest

from presentation_designer.speech.service import SpeechModel

ROOT = pathlib.Path(__file__).resolve().parents[2]
MODEL = ROOT / "models" / "gigaam" / "v3_e2e_ctc"
EXAMPLE = ROOT / "tests" / "fixtures" / "speech" / "example.wav"

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(
        not (MODEL / "manifest.json").is_file() or not EXAMPLE.is_file(),
        reason="нет весов модели: uv run scripts/export_gigaam_onnx.py",
    ),
]


def words(text: str) -> list[str]:
    return "".join(c.lower() if c.isalnum() else " " for c in text).split()


def wer(ref: str, hyp: str) -> float:
    r, h = words(ref), words(hyp)
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hw in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw))
        prev = cur
    return prev[-1] / max(1, len(r))


def test_example_recording_is_transcribed_with_punctuation() -> None:
    manifest = json.loads((MODEL / "manifest.json").read_text(encoding="utf-8"))
    control = next(c for c in manifest["controls"] if c["piece"] == "example")
    with wave.open(str(EXAMPLE)) as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    model = SpeechModel(MODEL, watch=False)
    result = model.transcribe(audio)
    # Не хуже контрольного fp32: расхождение по словам в пределах допуска квантования.
    assert wer(control["fp32"], result.text) <= manifest["quantization"]["max_wer_increase"]
    assert any(mark in result.text for mark in ",.!?")
    assert result.duration_ms == pytest.approx(len(audio) / 16, abs=1)
    print(f"«{result.text}» за {result.infer_ms} мс ({result.duration_ms} мс записи)")
