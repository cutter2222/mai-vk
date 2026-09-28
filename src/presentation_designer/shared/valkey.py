"""Подключение к Valkey с повторами при обрыве связи.

Зачем. Задание, стартовавшее в первые секунды после перезапуска контейнеров, падало с
«Error -3 connecting to valkey:6379. Temporary failure in name resolution» — DNS Docker ещё
не отдавал имя, а клиент без повторов ронял этап (`plan_error`, `export_error`, 28.09.2026).
Все клиенты проекта берутся отсюда: пять попыток с растущей паузой (до ≈6 с суммарно) на
ошибки соединения и тайм-ауты, потом — обычное исключение.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from redis.exceptions import RedisError

ATTEMPTS = 5
BACKOFF_BASE_S = 0.25
BACKOFF_CAP_S = 3.0


def _retry() -> Any:
    from redis.backoff import ExponentialBackoff
    from redis.retry import Retry

    return Retry(ExponentialBackoff(cap=BACKOFF_CAP_S, base=BACKOFF_BASE_S), ATTEMPTS)


def _errors() -> list[type[RedisError]]:
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import TimeoutError as RedisTimeoutError

    return [RedisConnectionError, RedisTimeoutError]


def connect(url: str, **kwargs: Any) -> Any:
    """Синхронный клиент `redis.Redis` с повторами; `kwargs` — как у `Redis.from_url`."""
    import redis

    kwargs.setdefault("socket_connect_timeout", 5)
    return redis.Redis.from_url(url, retry=_retry(), retry_on_error=_errors(), **kwargs)


def connect_async(url: str, **kwargs: Any) -> Any:
    """Асинхронный клиент `redis.asyncio.Redis` с теми же повторами."""
    import redis.asyncio as aioredis

    kwargs.setdefault("socket_connect_timeout", 5)
    return aioredis.Redis.from_url(url, retry=_retry(), retry_on_error=_errors(), **kwargs)
