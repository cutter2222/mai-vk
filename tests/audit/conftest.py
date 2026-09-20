"""Фикстуры аудита: клиент моделей на заглушке транспорта (сеть в тестах не вызывается)."""

from tests.llm.conftest import (  # noqa: F401  # фикстуры клиента моделей
    make_client,
    models,
    settings,
    stub,
)
