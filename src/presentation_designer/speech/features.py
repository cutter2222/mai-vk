"""Лог-мел признаки GigaAM на numpy — без torch в рабочем образе.

Повторяет `gigaam.preprocess.FeatureExtractor`: `torchaudio.transforms.MelSpectrogram` с
умолчаниями torchaudio (периодическое окно Ханна, степень 2, `center=True` с отражением краёв,
мел-шкала HTK без нормировки, частоты от 0 до половины частоты дискретизации) и
`log(clamp(x, 1e-9, 1e9))`. Параметры и число мел-полос — из `preprocessor` в yaml экспорта;
совпадение с torch проверяет `tests/speech/test_features.py` по эталону экспорта.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np

LOG_FLOOR = 1e-9
LOG_CEIL = 1e9


@dataclass(frozen=True)
class FeatureParams:
    sample_rate: int = 16000
    n_mels: int = 64
    win_length: int = 400
    hop_length: int = 160
    n_fft: int = 400
    center: bool = True

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> FeatureParams:
        """`preprocessor` из yaml экспорта; пропуски — как в `FeatureExtractor`."""
        rate = int(cfg.get("sample_rate", 16000))
        return cls(
            sample_rate=rate,
            n_mels=int(cfg.get("features", cfg.get("n_mels", 64))),
            win_length=int(cfg.get("win_length", rate // 40)),
            hop_length=int(cfg.get("hop_length", rate // 100)),
            n_fft=int(cfg.get("n_fft", rate // 40)),
            center=bool(cfg.get("center", True)),
        )


def _hz_to_mel(freq: np.ndarray) -> np.ndarray:
    return 2595.0 * np.log10(1.0 + freq / 700.0)


def _mel_to_hz(mels: np.ndarray) -> np.ndarray:
    return 700.0 * (10.0 ** (mels / 2595.0) - 1.0)


@lru_cache(maxsize=4)
def mel_filterbank(params: FeatureParams) -> np.ndarray:
    """Треугольные фильтры (n_freqs × n_mels) как `torchaudio.functional.melscale_fbanks`
    с `norm=None`, `mel_scale="htk"`."""
    n_freqs = params.n_fft // 2 + 1
    all_freqs = np.linspace(0.0, params.sample_rate // 2, n_freqs)
    top = _hz_to_mel(np.array(params.sample_rate / 2))
    m_pts = np.linspace(_hz_to_mel(np.array(0.0)), top, params.n_mels + 2)
    f_pts = _mel_to_hz(m_pts)
    f_diff = f_pts[1:] - f_pts[:-1]
    slopes = f_pts[None, :] - all_freqs[:, None]
    down = -slopes[:, :-2] / f_diff[:-1]
    up = slopes[:, 2:] / f_diff[1:]
    fb: np.ndarray = np.maximum(0.0, np.minimum(down, up)).astype(np.float32)
    return fb


@lru_cache(maxsize=4)
def _window(params: FeatureParams) -> np.ndarray:
    """Периодическое окно Ханна длины win_length по центру кадра n_fft (как torch.stft)."""
    n = np.arange(params.win_length)
    window = 0.5 - 0.5 * np.cos(2.0 * np.pi * n / params.win_length)
    left = (params.n_fft - params.win_length) // 2
    padded = np.zeros(params.n_fft, dtype=np.float32)
    padded[left : left + params.win_length] = window
    return padded


@dataclass(frozen=True)
class Featurizer:
    """Параметры и буферы признаков одной модели. Окно и мел-фильтры берутся из весов
    (`preprocessor.npz` экспорта): в чекпойнте GigaAM они округлены, и признаки модели — это
    признаки с этими буферами. Без файла — вычисленные по параметрам (для тестов и других
    моделей)."""

    params: FeatureParams
    window: np.ndarray
    fb: np.ndarray

    @classmethod
    def create(cls, params: FeatureParams, buffers: pathlib.Path | None = None) -> Featurizer:
        window, fb = _window(params), mel_filterbank(params)
        if buffers is not None and buffers.is_file():
            with np.load(buffers) as data:
                raw_window = np.asarray(data["window"], dtype=np.float32)
                raw_fb = np.asarray(data["fb"], dtype=np.float32)
            if raw_window.shape != (params.win_length,) or raw_fb.shape != fb.shape:
                raise ValueError(f"{buffers}: окно или фильтры не совпадают с параметрами модели")
            left = (params.n_fft - params.win_length) // 2
            window = np.zeros(params.n_fft, dtype=np.float32)
            window[left : left + params.win_length] = raw_window
            fb = raw_fb
        return cls(params, window, fb)

    def __call__(self, audio: np.ndarray) -> np.ndarray:
        return log_mel(audio, self.params, window=self.window, fb=self.fb)


def out_len(samples: int, params: FeatureParams) -> int:
    """Число кадров признаков — `FeatureExtractor.out_len`."""
    if params.center:
        return samples // params.hop_length + 1
    return (samples - params.win_length) // params.hop_length + 1


def log_mel(
    audio: np.ndarray,
    params: FeatureParams,
    *,
    window: np.ndarray | None = None,
    fb: np.ndarray | None = None,
) -> np.ndarray:
    """float32 сигнал в [-1, 1] → лог-мел (n_mels × кадры), float32.

    Считается во float32, как у torch: в полосах почти без энергии точное значение float64
    на порядки меньше «шумового пола» float32, на котором модель обучена, и лог-шкала
    расходилась бы там на единицы."""
    x = np.asarray(audio, dtype=np.float32).reshape(-1)
    if params.center:
        pad = params.n_fft // 2
        if x.shape[0] <= pad:
            raise ValueError("запись слишком короткая для признаков")
        x = np.pad(x, (pad, pad), mode="reflect")
    frames = 1 + (x.shape[0] - params.n_fft) // params.hop_length
    if frames <= 0:
        raise ValueError("запись слишком короткая для признаков")
    strided = np.lib.stride_tricks.as_strided(
        x,
        shape=(frames, params.n_fft),
        strides=(x.strides[0] * params.hop_length, x.strides[0]),
        writeable=False,
    )
    spectrum = np.fft.rfft(
        strided * (_window(params) if window is None else window), n=params.n_fft, axis=1
    )
    power = (spectrum.real**2 + spectrum.imag**2).astype(np.float32)
    mel = power @ (mel_filterbank(params) if fb is None else fb)
    features: np.ndarray = np.log(np.clip(mel, LOG_FLOOR, LOG_CEIL)).T.astype(np.float32)
    return features
