"""Метаданные модели в результате: карточка открытой модели не приписывается закрытой."""

from __future__ import annotations

from presentation_designer.llm.client import model_refs
from presentation_designer.shared.settings import get_models_config


def test_open_model_role_carries_its_card() -> None:
    models = get_models_config().model_copy(deep=True)
    models.roles["llm"].provider = "qwen-api"
    ref = next(r for r in model_refs(models) if r["role"] == "llm")
    assert ref["license"] == "Apache-2.0"
    assert "hf_url" in ref and "params_b" in ref


def test_closed_dev_provider_gets_no_open_model_card() -> None:
    # Роль переключена на мост разработки: ссылка на Qwen и лицензия Apache-2.0 были бы ложью.
    models = get_models_config().model_copy(deep=True)
    models.roles["llm"].provider = "dev-bridge"
    models.roles["llm"].model = "dev-model"
    ref = next(r for r in model_refs(models) if r["role"] == "llm")
    assert ref["name"] == "dev-model"
    assert ref["license"] == "proprietary"
    assert "hf_url" not in ref and "params_b" not in ref
