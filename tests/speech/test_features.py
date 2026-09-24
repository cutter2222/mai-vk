"""Лог-мел признаки на numpy против эталона torch из экспорта GigaAM.

Эталон на синтетическом сигнале лежит в тестах (`features_synth_ref.npz`: признаки torch,
окно и мел-фильтры из весов), эталон на записи-примере — в каталоге модели и проверяется, если
модель скачана."""

from __future__ import annotations

import json
import pathlib
import wave

import numpy as np
import pytest

from presentation_designer.speech.features import (
    FeatureParams,
    Featurizer,
    log_mel,
    mel_filterbank,
    out_len,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
SYNTH = ROOT / "tests" / "fixtures" / "speech" / "features_synth_ref.npz"
MODEL = ROOT / "models" / "gigaam" / "v3_e2e_ctc"
EXAMPLE = ROOT / "tests" / "fixtures" / "speech" / "example.wav"
TOLERANCE = 1e-3  # лог-шкала: float32 torch против float32 numpy


def synth_signal(seed: int, seconds: float, rate: int = 16000) -> np.ndarray:
    """Тот же сигнал, что в scripts/export_gigaam_onnx.py (`synth_signal`)."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * rate)) / rate
    sweep = np.sin(2 * np.pi * (200 + 1800 * t) * t)
    tones = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 3100 * t)
    signal = 0.4 * sweep + tones + 0.05 * rng.standard_normal(t.shape)
    pcm = (np.clip(signal, -1, 1) * 32767).astype(np.int16)
    return pcm.astype(np.float32) / 32768.0


def _synth_featurizer(ref: np.lib.npyio.NpzFile) -> Featurizer:
    params = FeatureParams.from_config(json.loads(str(ref["preprocessor"])))
    left = (params.n_fft - params.win_length) // 2
    window = np.zeros(params.n_fft, dtype=np.float32)
    window[left : left + params.win_length] = ref["window"]
    return Featurizer(params, window, np.asarray(ref["fb"], dtype=np.float32))


def test_numpy_log_mel_matches_torch_on_synthetic_signal() -> None:
    with np.load(SYNTH) as ref:
        featurizer = _synth_featurizer(ref)
        audio = synth_signal(int(ref["seed"]), float(ref["seconds"]))
        expected = ref["features"]
        length = int(ref["length"])
    features = featurizer(audio)
    assert features.shape == expected.shape
    assert features.dtype == np.float32
    assert out_len(audio.shape[0], featurizer.params) == length == features.shape[1]
    assert float(np.abs(features - expected).max()) < TOLERANCE


def test_parameters_come_from_the_export_config() -> None:
    with np.load(SYNTH) as ref:
        params = FeatureParams.from_config(json.loads(str(ref["preprocessor"])))
    # У GigaAM v3 окно и шаг БПФ 20 мс без центрирования — не умолчания FeatureExtractor.
    assert (params.n_mels, params.win_length, params.n_fft, params.hop_length) == (
        64,
        320,
        320,
        160,
    )
    assert params.center is False
    assert out_len(16000, params) == 99
    assert out_len(16000, FeatureParams()) == 101  # center=True: кадр на каждый шаг и ещё один


def test_computed_buffers_follow_torchaudio_formulas() -> None:
    """Вычисленные окно и фильтры — периодический Ханн и мел HTK без нормировки: от буферов из
    весов они отличаются только округлением (до 0,002), а не формой."""
    with np.load(SYNTH) as ref:
        params = FeatureParams.from_config(json.loads(str(ref["preprocessor"])))
        stored_fb, stored_window = ref["fb"], ref["window"]
    computed = Featurizer.create(params)
    assert float(np.abs(mel_filterbank(params) - stored_fb).max()) < 0.003
    assert float(np.abs(computed.window - stored_window).max()) < 0.003
    # Без файла буферов признаки считаются по формулам — ровно как log_mel по параметрам.
    with np.load(SYNTH) as ref:
        audio = synth_signal(int(ref["seed"]), float(ref["seconds"]))
    assert float(np.abs(computed(audio) - log_mel(audio, params)).max()) == 0.0


def test_short_audio_is_rejected() -> None:
    with pytest.raises(ValueError):
        log_mel(
            np.zeros(100, dtype=np.float32), FeatureParams(center=False, n_fft=320, win_length=320)
        )


@pytest.mark.skipif(
    not (MODEL / "features_ref.npz").is_file() or not EXAMPLE.is_file(),
    reason="нет каталога модели или примера: uv run scripts/export_gigaam_onnx.py",
)
def test_numpy_log_mel_matches_torch_on_example_recording() -> None:
    import yaml

    cfg = yaml.safe_load((MODEL / "v3_e2e_ctc.yaml").read_text(encoding="utf-8"))
    featurizer = Featurizer.create(
        FeatureParams.from_config(cfg["preprocessor"]), MODEL / "preprocessor.npz"
    )
    with wave.open(str(EXAMPLE)) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    audio = pcm.astype(np.float32) / 32768.0
    with np.load(MODEL / "features_ref.npz") as ref:
        expected = ref["example_features"]
        length = int(ref["example_len"])
    features = featurizer(audio)
    assert features.shape == expected.shape and features.shape[1] == length
    assert float(np.abs(features - expected).max()) < TOLERANCE
