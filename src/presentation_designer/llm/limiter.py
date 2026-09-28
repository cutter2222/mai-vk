"""Ограничитель запросов к провайдеру: одновременность, RPM и TPM на модель.

Общий для всех воркеров вариант живёт в Valkey (`ValkeyLimiter`), проверка и запись аренды
выполняются одним Lua-скриптом, время берётся у сервера — часы процессов не участвуют.
`LocalLimiter` повторяет ту же логику в памяти одного процесса для тестов без Valkey и для
встроенного исполнителя. Обе реализации проходят один набор тестов.

Аренда (lease) берётся до запроса на оценённые входные и допустимые выходные токены, после ответа
заменяется фактическим usage; без usage остаётся консервативная оценка. У аренды есть срок:
если воркер упал, не освободив её, она истекает сама. Время ожидания квоты возвращается
вызывающему и входит в общий deadline запроса — лимитер не ждёт дольше, чем ему разрешили.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from presentation_designer.llm.types import Deadline, LlmError, QuotaTimeoutError

WINDOW_S = 60.0
POLL_S = 0.2


@dataclass(frozen=True)
class Quota:
    concurrency: int
    rpm: int
    tpm: int

    def __post_init__(self) -> None:
        if min(self.concurrency, self.rpm, self.tpm) <= 0:
            raise LlmError("квота лимитера должна быть положительной")


@dataclass
class Lease:
    lease_id: str
    key: str
    tokens_reserved: int
    wait_ms: int
    acquired_at: float = field(default_factory=time.monotonic)
    released: bool = False


class Limiter(Protocol):
    async def acquire(
        self, key: str, tokens: int, *, deadline: Deadline | None = None
    ) -> Lease: ...

    async def release(self, lease: Lease, *, actual_tokens: int | None = None) -> None: ...

    async def snapshot(self, key: str) -> dict[str, Any]: ...


# ---------- общая часть: ожидание с учётом deadline ----------


async def _wait_for_slot(
    try_acquire: Any, key: str, tokens: int, deadline: Deadline | None, max_wait_s: float
) -> Lease:
    started = time.monotonic()
    hard_stop = started + max_wait_s
    while True:
        ok, reason, hint_ms, lease_id = await try_acquire()
        if ok:
            return Lease(lease_id, key, tokens, int((time.monotonic() - started) * 1000))
        now = time.monotonic()
        limit = hard_stop if deadline is None else min(hard_stop, deadline.at_monotonic)
        remaining = limit - now
        if remaining <= 0:
            raise QuotaTimeoutError(
                f"квота провайдера ({reason}) не освободилась за {int(now - started)} с для {key}"
            )
        # Одновременность освобождается без расписания — опрашиваем часто; окна RPM/TPM
        # подсказывают, когда освободится место, спим до него с небольшим разбросом.
        sleep_s = POLL_S if reason == "concurrency" else max(hint_ms / 1000.0, POLL_S)
        sleep_s = min(sleep_s * random.uniform(1.0, 1.15), remaining)
        await asyncio.sleep(sleep_s)


# ---------- в памяти процесса ----------


@dataclass
class _Window:
    leases: dict[str, tuple[float, int]] = field(
        default_factory=dict
    )  # lease_id → (истекает, токены)
    requests: list[tuple[float, str]] = field(default_factory=list)  # (время, lease_id)
    tokens: dict[str, tuple[float, int]] = field(default_factory=dict)  # lease_id → (время, токены)


def _prune(w: _Window, now: float) -> None:
    for lease_id, (expires, _) in list(w.leases.items()):
        if expires <= now:
            del w.leases[lease_id]
    w.requests = [(ts, lid) for ts, lid in w.requests if ts > now - WINDOW_S]
    for lease_id, (ts, _) in list(w.tokens.items()):
        if ts <= now - WINDOW_S:
            del w.tokens[lease_id]


def try_acquire_window(
    w: _Window, quota: Quota, now: float, tokens: int, ttl_s: float, lease_id: str
) -> tuple[bool, str, int]:
    """Одна попытка: (успех, причина отказа, подсказка ожидания в мс). Та же логика в Lua."""
    if tokens > quota.tpm:
        raise LlmError(f"запрос на {tokens} токенов больше TPM {quota.tpm}: он никогда не пройдёт")
    _prune(w, now)
    if len(w.leases) >= quota.concurrency:
        return False, "concurrency", int(POLL_S * 1000)
    if len(w.requests) >= quota.rpm:
        oldest = min(ts for ts, _ in w.requests)
        return False, "rpm", max(1, int((oldest + WINDOW_S - now) * 1000))
    used = sum(t for _, t in w.tokens.values())
    if used + tokens > quota.tpm:
        oldest = min((ts for ts, _ in w.tokens.values()), default=now)
        return False, "tpm", max(int(POLL_S * 1000), int((oldest + WINDOW_S - now) * 1000))
    w.leases[lease_id] = (now + ttl_s, tokens)
    w.requests.append((now, lease_id))
    w.tokens[lease_id] = (now, tokens)
    return True, "ok", 0


def release_window(w: _Window, lease_id: str, actual_tokens: int | None) -> None:
    w.leases.pop(lease_id, None)
    if actual_tokens is not None and lease_id in w.tokens:
        ts, _ = w.tokens[lease_id]
        w.tokens[lease_id] = (ts, actual_tokens)


class LocalLimiter:
    """Лимитер одного процесса; квота задаётся на ключ (провайдер/модель)."""

    name = "local"

    def __init__(
        self, quota_for: Any, *, lease_ttl_s: float = 180.0, max_wait_s: float = 120.0
    ) -> None:
        self._quota_for = quota_for if callable(quota_for) else (lambda _key: quota_for)
        self.lease_ttl_s = lease_ttl_s
        self.max_wait_s = max_wait_s
        self._windows: dict[str, _Window] = {}
        self._lock = asyncio.Lock()

    def _window(self, key: str) -> _Window:
        return self._windows.setdefault(key, _Window())

    async def acquire(self, key: str, tokens: int, *, deadline: Deadline | None = None) -> Lease:
        quota: Quota = self._quota_for(key)

        async def attempt() -> tuple[bool, str, int, str]:
            lease_id = uuid.uuid4().hex
            async with self._lock:
                ok, reason, hint = try_acquire_window(
                    self._window(key), quota, time.monotonic(), tokens, self.lease_ttl_s, lease_id
                )
            return ok, reason, hint, lease_id

        return await _wait_for_slot(attempt, key, tokens, deadline, self.max_wait_s)

    async def release(self, lease: Lease, *, actual_tokens: int | None = None) -> None:
        if lease.released:
            return
        lease.released = True
        async with self._lock:
            release_window(self._window(lease.key), lease.lease_id, actual_tokens)

    async def snapshot(self, key: str) -> dict[str, Any]:
        async with self._lock:
            w = self._window(key)
            _prune(w, time.monotonic())
            return {
                "active": len(w.leases),
                "requests_in_window": len(w.requests),
                "tokens_in_window": sum(t for _, t in w.tokens.values()),
            }


# ---------- Valkey ----------

# Возвращает {ok, reason, wait_ms}. Время — TIME сервера, одно для всех процессов.
ACQUIRE_LUA = """
local leases, req, tokts, tokh = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
local ttl, tokens = tonumber(ARGV[1]), tonumber(ARGV[2])
local conc, rpm, tpm = tonumber(ARGV[3]), tonumber(ARGV[4]), tonumber(ARGV[5])
local id, window, poll = ARGV[6], tonumber(ARGV[7]), tonumber(ARGV[8])
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', leases, '-inf', now)
redis.call('ZREMRANGEBYSCORE', req, '-inf', now - window)
local old = redis.call('ZRANGEBYSCORE', tokts, '-inf', now - window)
for _, m in ipairs(old) do redis.call('HDEL', tokh, m) end
redis.call('ZREMRANGEBYSCORE', tokts, '-inf', now - window)
if redis.call('ZCARD', leases) >= conc then return {0, 'concurrency', poll} end
if redis.call('ZCARD', req) >= rpm then
  local first = redis.call('ZRANGE', req, 0, 0, 'WITHSCORES')
  return {0, 'rpm', math.max(1, math.ceil((tonumber(first[2]) + window - now) * 1000))}
end
local used = 0
for _, v in ipairs(redis.call('HVALS', tokh)) do used = used + tonumber(v) end
if used + tokens > tpm then
  local first = redis.call('ZRANGE', tokts, 0, 0, 'WITHSCORES')
  local wait = poll
  if first[2] then wait = math.max(poll, math.ceil((tonumber(first[2]) + window - now) * 1000)) end
  return {0, 'tpm', wait}
end
redis.call('ZADD', leases, now + ttl, id)
redis.call('ZADD', req, now, id)
redis.call('ZADD', tokts, now, id)
redis.call('HSET', tokh, id, tokens)
local keep = math.ceil(ttl + window) + 5
redis.call('EXPIRE', leases, keep)
redis.call('EXPIRE', req, keep)
redis.call('EXPIRE', tokts, keep)
redis.call('EXPIRE', tokh, keep)
return {1, 'ok', 0}
"""

RELEASE_LUA = """
local leases, tokts, tokh = KEYS[1], KEYS[2], KEYS[3]
local id, actual = ARGV[1], ARGV[2]
redis.call('ZREM', leases, id)
if actual ~= '' and redis.call('ZSCORE', tokts, id) then redis.call('HSET', tokh, id, actual) end
return 1
"""

SNAPSHOT_LUA = """
local leases, req, tokts, tokh = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
local window = tonumber(ARGV[1])
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000
redis.call('ZREMRANGEBYSCORE', leases, '-inf', now)
redis.call('ZREMRANGEBYSCORE', req, '-inf', now - window)
local old = redis.call('ZRANGEBYSCORE', tokts, '-inf', now - window)
for _, m in ipairs(old) do redis.call('HDEL', tokh, m) end
redis.call('ZREMRANGEBYSCORE', tokts, '-inf', now - window)
local used = 0
for _, v in ipairs(redis.call('HVALS', tokh)) do used = used + tonumber(v) end
return {redis.call('ZCARD', leases), redis.call('ZCARD', req), used}
"""


class ValkeyLimiter:
    """Общий лимитер всех воркеров; ключи `pd:llm:<ключ>:{leases,req,tokts,tokh}`."""

    name = "valkey"

    def __init__(
        self,
        url: str,
        quota_for: Any,
        *,
        lease_ttl_s: float = 180.0,
        max_wait_s: float = 120.0,
        prefix: str = "pd:llm",
    ) -> None:
        self.url = url
        self._quota_for = quota_for if callable(quota_for) else (lambda _key: quota_for)
        self.lease_ttl_s = lease_ttl_s
        self.max_wait_s = max_wait_s
        self.prefix = prefix
        self._loop: Any = None
        self._redis: Any = None
        self._bind()

    def _bind(self) -> None:
        """Клиент redis.asyncio привязан к циклу событий. Синхронные вызывающие (воркер, CLI)
        запускают asyncio.run несколько раз за процесс — перед операцией клиент пересоздаётся
        под текущий цикл, иначе освобождение аренды падало и аренда «утекала» до истечения TTL."""
        from presentation_designer.shared.valkey import connect_async

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._redis is not None and loop is self._loop:
            return
        self._loop = loop
        self._redis = connect_async(self.url, socket_timeout=5)
        self._acquire = self._redis.register_script(ACQUIRE_LUA)
        self._release = self._redis.register_script(RELEASE_LUA)
        self._snapshot = self._redis.register_script(SNAPSHOT_LUA)

    @property
    def redis(self) -> Any:
        self._bind()
        return self._redis

    def keys(self, key: str) -> list[str]:
        base = f"{self.prefix}:{key}"
        return [f"{base}:leases", f"{base}:req", f"{base}:tokts", f"{base}:tokh"]

    async def acquire(self, key: str, tokens: int, *, deadline: Deadline | None = None) -> Lease:
        self._bind()
        quota: Quota = self._quota_for(key)
        if tokens > quota.tpm:
            raise LlmError(
                f"запрос на {tokens} токенов больше TPM {quota.tpm}: он никогда не пройдёт"
            )
        keys = self.keys(key)

        async def attempt() -> tuple[bool, str, int, str]:
            lease_id = uuid.uuid4().hex
            ok, reason, hint = await self._acquire(
                keys=keys,
                args=[
                    self.lease_ttl_s,
                    tokens,
                    quota.concurrency,
                    quota.rpm,
                    quota.tpm,
                    lease_id,
                    WINDOW_S,
                    int(POLL_S * 1000),
                ],
            )
            return (
                bool(ok),
                reason.decode() if isinstance(reason, bytes) else str(reason),
                int(hint),
                lease_id,
            )

        return await _wait_for_slot(attempt, key, tokens, deadline, self.max_wait_s)

    async def release(self, lease: Lease, *, actual_tokens: int | None = None) -> None:
        if lease.released:
            return
        lease.released = True
        self._bind()
        leases, _, tokts, tokh = self.keys(lease.key)
        await self._release(
            keys=[leases, tokts, tokh],
            args=[lease.lease_id, "" if actual_tokens is None else int(actual_tokens)],
        )

    async def snapshot(self, key: str) -> dict[str, Any]:
        self._bind()
        active, requests, used = await self._snapshot(keys=self.keys(key), args=[WINDOW_S])
        return {
            "active": int(active),
            "requests_in_window": int(requests),
            "tokens_in_window": int(used),
        }

    async def reset(self, key: str) -> None:
        await self.redis.delete(*self.keys(key))

    async def aclose(self) -> None:
        await self.redis.aclose()
