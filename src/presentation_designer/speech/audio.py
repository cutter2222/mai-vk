"""Фраза от браузера: WAV PCM16 моно 16 кГц не длиннее предела. Разбор — стандартным `wave`,
без numpy: этот модуль нужен и API, который аудио только проверяет и пересылает."""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass

SAMPLE_RATE = 16000


class AudioError(Exception):
    """`audio_format` — не WAV PCM16 моно 16 кГц; `audio_too_long` — длиннее предела."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WavInfo:
    frames: int
    seconds: float
    pcm: bytes


def read_wav(data: bytes, *, max_seconds: float) -> WavInfo:
    try:
        with wave.open(io.BytesIO(data)) as w:
            channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            frames, comptype = w.getnframes(), w.getcomptype()
            pcm = w.readframes(frames)
    except (wave.Error, EOFError) as e:
        raise AudioError("audio_format", "Нужен WAV PCM16 моно 16 кГц") from e
    if comptype != "NONE" or width != 2 or channels != 1 or rate != SAMPLE_RATE:
        raise AudioError("audio_format", "Нужен WAV PCM16 моно 16 кГц")
    frames = len(pcm) // 2
    if frames == 0:
        raise AudioError("audio_format", "Запись пустая")
    seconds = frames / SAMPLE_RATE
    if seconds > max_seconds:
        raise AudioError("audio_too_long", f"Фраза длиннее {max_seconds:g} с")
    return WavInfo(frames=frames, seconds=seconds, pcm=pcm)
