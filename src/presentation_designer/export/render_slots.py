"""Слоты рендера: ограничение одновременных конвертаций LibreOffice на сервер.

Число слотов — `render.slots` (на сервере из `PD_RENDER_SLOTS`, по замерам 0A не больше двух
на 3,9 ГБ). Общий для всех воркеров вариант — ZSET аренд в Valkey с истечением: упавший воркер
не держит слот дольше `ttl_s`. Без Valkey (встроенный исполнитель, тесты) — семафор процесса.
Ожидание слота ограничено `timeout_s`; анализ шаблона и экспорт результатов проходят через
одну и ту же функцию `acquire`, чтобы конвертации шаблонов и колод считались вместе.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)

POLL_S = 0.25


class RenderSlotTimeoutError(TimeoutError):
    """Свободный слот рендера не появился за отведённое время."""


@dataclass
class SlotLease:
    lease_id: str
    wait_ms: int
    backend: str


class RenderSlots(Protocol):
    slots: int

    @contextlib.contextmanager
    def acquire(self, *, timeout_s: float, ttl_s: float) -> Iterator[SlotLease]: ...

    def active(self) -> int: ...


class LocalRenderSlots:
    """Семафор одного процесса."""

    backend = "local"

    def __init__(self, slots: int) -> None:
        self.slots = max(1, slots)
        self._sem = threading.BoundedSemaphore(self.slots)
        self._active = 0
        self._lock = threading.Lock()

    @contextlib.contextmanager
    def acquire(self, *, timeout_s: float = 300.0, ttl_s: float = 600.0) -> Iterator[SlotLease]:
        started = time.monotonic()
        if not self._sem.acquire(timeout=timeout_s):
            raise RenderSlotTimeoutError(f"слот рендера не освободился за {int(timeout_s)} с")
        with self._lock:
            self._active += 1
        try:
            yield SlotLease(
                uuid.uuid4().hex, int((time.monotonic() - started) * 1000), self.backend
            )
        finally:
            with self._lock:
                self._active -= 1
            self._sem.release()

    def active(self) -> int:
        return self._active


ACQUIRE_LUA = """
local key, ttl, slots, id = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), ARGV[3]
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
if redis.call('ZCARD', key) >= slots then return 0 end
redis.call('ZADD', key, now + ttl, id)
redis.call('EXPIRE', key, math.ceil(ttl) + 5)
return 1
"""


class ValkeyRenderSlots:
    """Общие слоты всех воркеров: ключ `pd:render:slots`, аренды с истечением."""

    backend = "valkey"

    def __init__(self, url: str, slots: int, *, key: str = "pd:render:slots") -> None:
        import redis

        self.slots = max(1, slots)
        self.redis = redis.Redis.from_url(url, socket_timeout=5)
        self.key = key
        self._acquire = self.redis.register_script(ACQUIRE_LUA)

    @contextlib.contextmanager
    def acquire(self, *, timeout_s: float = 300.0, ttl_s: float = 600.0) -> Iterator[SlotLease]:
        started = time.monotonic()
        lease_id = uuid.uuid4().hex
        while True:
            if int(self._acquire(keys=[self.key], args=[ttl_s, self.slots, lease_id])):
                break
            if time.monotonic() - started > timeout_s:
                raise RenderSlotTimeoutError(f"слот рендера не освободился за {int(timeout_s)} с")
            time.sleep(POLL_S)
        try:
            yield SlotLease(lease_id, int((time.monotonic() - started) * 1000), self.backend)
        finally:
            try:
                self.redis.zrem(self.key, lease_id)
            except Exception:  # аренда истечёт сама по ttl
                log.warning("не удалось освободить слот рендера %s", lease_id)

    def active(self) -> int:
        now = time.time()
        return int(self.redis.zcount(self.key, now, "+inf"))


def render_slots_from_env(slots: int, url: str | None = None) -> Any:
    """Valkey, если он задан и очередь не встроенная; иначе семафор процесса."""
    url = url or os.environ.get("PD_VALKEY_URL")
    if url and os.environ.get("PD_QUEUE_MODE", "rq") != "inline":
        return ValkeyRenderSlots(url, slots)
    return LocalRenderSlots(slots)
