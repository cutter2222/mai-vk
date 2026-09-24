"""Голосовой ввод в чате: фраза от браузера (WAV PCM16 моно 16 кГц) → текст.

API проверяет запись и пересылает её во внутренний сервис `asr` (GigaAM), на диск аудио не
пишет, в журнал — только длительности. В режиме заглушек сервис не нужен: ответ — фиксированная
фраза, чтобы интерфейс и сквозные тесты работали без модели."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request
from starlette.datastructures import UploadFile

from presentation_designer.api.deps import Orch
from presentation_designer.api.errors import ApiError
from presentation_designer.speech.audio import AudioError, read_wav

router = APIRouter(tags=["speech"])
log = logging.getLogger(__name__)

STUB_PHRASE = "Проверка голосового ввода."
# Запас на заголовки multipart поверх самой записи.
MULTIPART_OVERHEAD = 16 * 1024

_health: dict[str, Any] = {"at": 0.0, "url": None, "ok": False}


def speech_available(orch: Any) -> bool:
    """Есть ли голосовой ввод: заглушки — всегда; иначе включён и сервис отвечает, что модель
    на месте (загружена или ждёт первого запроса). Ответ сервиса кэшируется."""
    cfg = orch.settings.speech
    if not cfg.enabled:
        return False
    if orch.settings.execution.mode == "stub":
        return True
    now = time.monotonic()
    if _health["url"] == cfg.url and now - float(_health["at"]) < cfg.health_cache_s:
        return bool(_health["ok"])
    ok = False
    try:
        with httpx.Client(timeout=2.0, trust_env=False) as client:
            r = client.get(f"{cfg.url.rstrip('/')}/health")
        state = (r.json().get("speech") or {}).get("state") if r.status_code == 200 else None
        ok = state in {"loaded", "unloaded"}
    except (httpx.HTTPError, ValueError):
        ok = False
    _health.update(at=now, url=cfg.url, ok=ok)
    return ok


@router.post("/speech/transcribe")
async def transcribe(request: Request, orch: Orch) -> dict[str, Any]:
    cfg = orch.settings.speech
    declared = int(request.headers.get("content-length") or 0)
    if declared > cfg.max_bytes + MULTIPART_OVERHEAD:
        raise ApiError(413, "audio_too_long", f"Фраза длиннее {cfg.max_seconds:g} с")
    form = await request.form(max_files=1, max_fields=4)
    upload = form.get("audio")
    if not isinstance(upload, UploadFile):
        raise ApiError(422, "audio_required", "Нужна запись в поле audio")
    data = await upload.read(cfg.max_bytes + 1)
    await form.close()
    if len(data) > cfg.max_bytes:
        raise ApiError(413, "audio_too_long", f"Фраза длиннее {cfg.max_seconds:g} с")
    try:
        wav = read_wav(data, max_seconds=cfg.max_seconds)
    except AudioError as e:
        raise ApiError(413 if e.code == "audio_too_long" else 415, e.code, str(e)) from e
    duration_ms = round(wav.seconds * 1000)
    if not cfg.enabled:
        raise ApiError(503, "speech_unavailable", "Голосовой ввод выключен")
    if orch.settings.execution.mode == "stub":
        return {"text": STUB_PHRASE, "duration_ms": duration_ms, "infer_ms": 0, "model": "stub"}
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=cfg.timeout_s, trust_env=False) as client:
            r = await client.post(
                f"{cfg.url.rstrip('/')}/transcribe",
                content=data,
                headers={"content-type": "audio/wav"},
            )
    except httpx.HTTPError as e:
        log.warning("сервис распознавания не ответил: %s", e.__class__.__name__)
        raise ApiError(
            503, "speech_unavailable", "Распознавание речи сейчас недоступно, наберите текст"
        ) from e
    if r.status_code in (413, 415):
        error = (r.json().get("error") or {}) if r.content else {}
        raise ApiError(
            r.status_code,
            str(error.get("code") or "audio_format"),
            str(error.get("message") or "Запись не подходит"),
        )
    if r.status_code != 200:
        raise ApiError(
            503, "speech_unavailable", "Распознавание речи сейчас недоступно, наберите текст"
        )
    body = r.json()
    log.info(
        "фраза %d мс: распознавание %d мс, всего %d мс",
        duration_ms,
        int(body.get("infer_ms") or 0),
        int((time.perf_counter() - started) * 1000),
    )
    return {
        "text": str(body.get("text") or ""),
        "duration_ms": int(body.get("duration_ms") or duration_ms),
        "infer_ms": int(body.get("infer_ms") or 0),
        "model": str(body.get("model") or ""),
    }
