"""HTTP-приложение сервиса `asr`: `POST /transcribe` (тело — WAV PCM16 моно 16 кГц) и
`GET /health`. Сервис внутренний: снаружи его не видно, фразы пересылает API
(`POST /api/speech/transcribe`). Стандартный `http.server` вместо ASGI: запросы и так идут
по одному, а лишний фреймворк — лишняя память в контейнере с пределом 768 МБ."""

from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import numpy as np

from presentation_designer.speech.audio import AudioError, read_wav
from presentation_designer.speech.service import SpeechError, SpeechModel

log = logging.getLogger(__name__)


class SpeechServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self, address: tuple[str, int], model: SpeechModel, *, max_bytes: int, max_seconds: float
    ) -> None:
        super().__init__(address, _Handler)
        self.model = model
        self.max_bytes = max_bytes
        self.max_seconds = max_seconds


class _Handler(BaseHTTPRequestHandler):
    server: SpeechServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("%s %s", self.address_string(), format % args)

    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, code: str, message: str) -> None:
        self._send(status, {"error": {"code": code, "message": message}})

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/health":
            self._error(HTTPStatus.NOT_FOUND, "not_found", "Нет такого адреса")
            return
        self._send(HTTPStatus.OK, {"status": "ok", "speech": self.server.model.status()})

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/transcribe":
            self._error(HTTPStatus.NOT_FOUND, "not_found", "Нет такого адреса")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "audio_format", "Пустой запрос")
            return
        if length > self.server.max_bytes:
            self._error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "audio_too_long", "Запись больше предела"
            )
            return
        data = self.rfile.read(length)
        try:
            wav = read_wav(data, max_seconds=self.server.max_seconds)
        except AudioError as e:
            status = (
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE
                if e.code == "audio_too_long"
                else HTTPStatus.UNSUPPORTED_MEDIA_TYPE
            )
            self._error(status, e.code, str(e))
            return
        audio = np.frombuffer(wav.pcm, dtype="<i2").astype(np.float32) / 32768.0
        try:
            result = self.server.model.transcribe(audio)
        except SpeechError as e:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, "speech_unavailable", str(e))
            return
        except Exception:
            log.exception("распознавание не удалось")
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR, "speech_failed", "Распознавание не удалось"
            )
            return
        # В журнал — только длительности: ни аудио, ни текст не сохраняются.
        log.info("фраза %d мс распознана за %d мс", result.duration_ms, result.infer_ms)
        self._send(
            HTTPStatus.OK,
            {
                "text": result.text,
                "duration_ms": result.duration_ms,
                "infer_ms": result.infer_ms,
                "model": result.model,
            },
        )
