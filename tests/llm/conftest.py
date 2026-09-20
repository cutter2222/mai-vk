"""Фикстуры адаптера моделей: настройки с временными каталогами кэша, конфиг моделей
с образцовыми адресами, клиент на заглушке транспорта. Сеть в тестах не вызывается."""

from __future__ import annotations

import os
import pathlib
from collections.abc import Callable
from typing import Any

import pytest
import redis

from presentation_designer.llm.cache import ResponseCache
from presentation_designer.llm.client import LlmClient
from presentation_designer.llm.limiter import LocalLimiter, Quota
from presentation_designer.llm.retry import RetryPolicy
from presentation_designer.llm.stub import StubTransport
from presentation_designer.llm.usage import UsageRecorder
from presentation_designer.shared import settings as s

TEST_VALKEY_URL = os.environ.get("PD_TEST_VALKEY_URL", "redis://localhost:6399/0")


def valkey_available() -> bool:
    try:
        return bool(redis.Redis.from_url(TEST_VALKEY_URL, socket_timeout=0.5).ping())
    except Exception:
        return False


@pytest.fixture
def settings(tmp_path: pathlib.Path) -> s.Settings:
    cfg = s.Settings()
    cfg.llm.cache_dir = tmp_path / "llm-cache"
    cfg.llm.fixtures_dir = tmp_path / "fixtures"
    cfg.llm.retry_base_s = 0.01
    cfg.llm.retry_max_s = 0.05
    cfg.timeouts.llm_call_s = 5
    return cfg


@pytest.fixture
def models(monkeypatch: pytest.MonkeyPatch) -> s.ModelsConfig:
    # Адрес и ключ подменяются заглушкой, чтобы обычный запуск тестов не мог уйти в сеть.
    # Исключение — разовая перезапись записанных ответов (PD_TEST_LLM_MODE=record): там
    # нужен настоящий провайдер из окружения.
    if os.environ.get("PD_TEST_LLM_MODE") != "record":
        monkeypatch.setenv("PD_QWEN_BASE_URL", "https://example.invalid/v1")
        monkeypatch.setenv("PD_QWEN_API_KEY", "replace-me")
    s.reset_cache()
    cfg = s.get_models_config()
    s.reset_cache()
    return cfg


@pytest.fixture
def stub() -> StubTransport:
    return StubTransport()


@pytest.fixture
def make_client(settings: s.Settings, models: s.ModelsConfig) -> Callable[..., LlmClient]:
    def make(
        transport: Any = None,
        *,
        cache_mode: str = "off",
        quota: Quota | None = None,
        limiter: Any = None,
        recorder: UsageRecorder | None = None,
        max_retries: int = 3,
    ) -> LlmClient:
        cache = ResponseCache(cache_mode, settings.llm_cache_dir, settings.llm_fixtures_dir)
        return LlmClient(
            settings=settings,
            models=models,
            transport=transport or StubTransport(),
            limiter=limiter or LocalLimiter(quota or Quota(4, 600, 1_000_000), max_wait_s=5),
            cache=cache,
            recorder=recorder,
            retry_policy=RetryPolicy(max_retries=max_retries, base_s=0.01, max_s=0.05),
        )

    return make
