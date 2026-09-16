"""Лимитер: одновременность, RPM, TPM, замена резерва на usage, истечение аренды, deadline.

Один набор проверок для лимитера процесса и для Valkey; без Valkey второй вариант пропускается
(`PD_TEST_VALKEY_URL`, по умолчанию redis://localhost:6399/0 — отдельный контейнер для тестов).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

from presentation_designer.llm import limiter as lim
from presentation_designer.llm.types import Deadline, LlmError, QuotaTimeoutError
from tests.llm.conftest import TEST_VALKEY_URL, valkey_available


@pytest.fixture(params=["local", "valkey"])
async def make_limiter(request: pytest.FixtureRequest) -> AsyncIterator[Any]:
    kind = request.param
    if kind == "valkey" and not valkey_available():
        pytest.skip(f"Valkey для тестов недоступен по {TEST_VALKEY_URL}")
    created: list[Any] = []

    def make(quota: lim.Quota, *, lease_ttl_s: float = 30, max_wait_s: float = 5) -> Any:
        if kind == "local":
            limiter = lim.LocalLimiter(quota, lease_ttl_s=lease_ttl_s, max_wait_s=max_wait_s)
        else:
            limiter = lim.ValkeyLimiter(
                TEST_VALKEY_URL,
                quota,
                lease_ttl_s=lease_ttl_s,
                max_wait_s=max_wait_s,
                prefix=f"pd:test:{uuid.uuid4().hex[:8]}",
            )
        created.append(limiter)
        return limiter

    yield make
    for limiter in created:
        if hasattr(limiter, "aclose"):
            await limiter.aclose()


async def test_concurrency_limit(make_limiter: Any) -> None:
    limiter = make_limiter(lim.Quota(concurrency=2, rpm=1000, tpm=1_000_000))
    key = "p:m"
    active = 0
    peak = 0

    async def job() -> None:
        nonlocal active, peak
        lease = await limiter.acquire(key, 100)
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        await limiter.release(lease, actual_tokens=50)

    await asyncio.gather(*(job() for _ in range(6)))
    assert peak == 2
    snap = await limiter.snapshot(key)
    assert snap["active"] == 0
    assert snap["requests_in_window"] == 6
    assert snap["tokens_in_window"] == 6 * 50, "резерв заменён фактическим usage"


async def test_rpm_limit_waits_and_reports(make_limiter: Any) -> None:
    limiter = make_limiter(lim.Quota(concurrency=10, rpm=2, tpm=1_000_000), max_wait_s=0.5)
    key = "p:m"
    for _ in range(2):
        lease = await limiter.acquire(key, 10)
        await limiter.release(lease)
    started = time.monotonic()
    with pytest.raises(QuotaTimeoutError, match="rpm"):
        await limiter.acquire(key, 10, deadline=Deadline.after(0.3))
    waited = time.monotonic() - started
    assert 0.25 <= waited < 0.6, "ожидание ограничено deadline, а не окном RPM в минуту"


async def test_tpm_limit_and_oversized_request(make_limiter: Any) -> None:
    limiter = make_limiter(lim.Quota(concurrency=10, rpm=1000, tpm=1000), max_wait_s=0.3)
    key = "p:m"
    lease = await limiter.acquire(key, 700)
    with pytest.raises(QuotaTimeoutError, match="tpm"):
        await limiter.acquire(key, 400)
    # После ответа usage меньше резерва — место освобождается.
    await limiter.release(lease, actual_tokens=100)
    ok = await limiter.acquire(key, 400)
    assert ok.wait_ms >= 0
    with pytest.raises(LlmError, match="никогда"):
        await limiter.acquire(key, 5000)


async def test_lease_expires_after_crash(make_limiter: Any) -> None:
    limiter = make_limiter(lim.Quota(concurrency=1, rpm=1000, tpm=1_000_000), lease_ttl_s=0.2)
    key = "p:m"
    await limiter.acquire(key, 10)  # «упавший» воркер аренду не освободил
    started = time.monotonic()
    lease = await limiter.acquire(key, 10, deadline=Deadline.after(2))
    assert 0.15 <= time.monotonic() - started < 1.0, "аренда истекла сама по сроку"
    await limiter.release(lease)


async def test_wait_time_is_reported(make_limiter: Any) -> None:
    limiter = make_limiter(lim.Quota(concurrency=1, rpm=1000, tpm=1_000_000))
    key = "p:m"
    first = await limiter.acquire(key, 10)

    async def release_later() -> None:
        await asyncio.sleep(0.3)
        await limiter.release(first)

    asyncio.get_running_loop().create_task(release_later())
    second = await limiter.acquire(key, 10)
    assert second.wait_ms >= 250
    await limiter.release(second)


def test_quota_must_be_positive() -> None:
    with pytest.raises(LlmError):
        lim.Quota(0, 1, 1)


def test_window_logic_direct() -> None:
    """Чистая логика окна — та же, что в Lua."""
    w = lim._Window()
    q = lim.Quota(1, 2, 100)
    assert lim.try_acquire_window(w, q, 0.0, 50, 10, "a")[0]
    assert lim.try_acquire_window(w, q, 0.1, 10, 10, "b") == (False, "concurrency", 200)
    lim.release_window(w, "a", 30)
    ok, _, _ = lim.try_acquire_window(w, q, 0.2, 60, 10, "b")
    assert ok, "после замены резерва 50 → 30 место под 60 есть"
    lim.release_window(w, "b", None)
    ok, reason, hint = lim.try_acquire_window(w, q, 0.3, 1, 10, "c")
    assert (ok, reason) == (False, "rpm") and 59_000 < hint <= 60_000
    ok, _, _ = lim.try_acquire_window(w, q, 61.0, 1, 10, "c")
    assert ok, "окно минуты сдвинулось"


def test_valkey_limiter_survives_multiple_event_loops() -> None:
    """Воркер и CLI запускают asyncio.run несколько раз за процесс: клиент Valkey должен
    переподключаться к текущему циклу, иначе аренды не освобождаются (утечка на сервере)."""
    if not valkey_available():
        pytest.skip(f"Valkey для тестов недоступен по {TEST_VALKEY_URL}")
    limiter = lim.ValkeyLimiter(
        TEST_VALKEY_URL, lim.Quota(1, 100, 100_000), prefix=f"pd:test:{uuid.uuid4().hex[:8]}"
    )

    async def phase() -> dict[str, Any]:
        lease = await limiter.acquire("k", 10)
        await limiter.release(lease, actual_tokens=5)
        return await limiter.snapshot("k")

    first = asyncio.run(phase())
    second = asyncio.run(phase())
    assert first["active"] == 0 and second["active"] == 0
    assert second["requests_in_window"] == 2
    asyncio.run(limiter.reset("k"))
    asyncio.run(limiter.aclose())
