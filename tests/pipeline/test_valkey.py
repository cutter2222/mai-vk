"""Клиенты Valkey с повторами: обрыв связи в первые секунды после перезапуска контейнеров
(DNS ещё не отдал имя) не должен ронять этап задания."""

from __future__ import annotations

import time

import pytest
import redis

from presentation_designer.shared import valkey


def test_clients_retry_connection_errors() -> None:
    client = valkey.connect("redis://valkey.invalid:6379/0", socket_timeout=1)
    retry = client.get_retry()
    assert retry is not None and retry._retries == valkey.ATTEMPTS
    errors = client.connection_pool.connection_kwargs.get("retry_on_error") or []
    assert redis.exceptions.ConnectionError in errors and redis.exceptions.TimeoutError in errors
    assert client.connection_pool.connection_kwargs["socket_connect_timeout"] == 5
    aclient = valkey.connect_async("redis://valkey.invalid:6379/0")
    assert aclient.get_retry() is not None and aclient.get_retry()._retries == valkey.ATTEMPTS


def test_unresolvable_host_fails_after_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(valkey, "BACKOFF_CAP_S", 0.05)
    monkeypatch.setattr(valkey, "BACKOFF_BASE_S", 0.01)
    client = valkey.connect("redis://valkey.invalid:6379/0", socket_connect_timeout=0.5)
    started = time.monotonic()
    with pytest.raises(redis.exceptions.ConnectionError):
        client.ping()
    # Повторы были (пауза между ними), но не бесконечные.
    assert 0.02 < time.monotonic() - started < 15
