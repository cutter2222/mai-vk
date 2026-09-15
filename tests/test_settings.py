from __future__ import annotations

import pytest

from presentation_designer.shared import settings as s


def test_settings_load_from_yaml() -> None:
    s.reset_cache()
    cfg = s.get_settings()
    assert cfg.app.contracts_version == "1.1"
    assert cfg.limits.max_upload_mb == 100
    assert cfg.budget.hard_limit_s == 300


def test_env_overrides_yaml(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PD_LIMITS__MAX_UPLOAD_MB", "200")
    monkeypatch.setenv("PD_AUDIT__CONTEXTUAL_ENABLED", "false")
    s.reset_cache()
    cfg = s.get_settings()
    assert cfg.limits.max_upload_mb == 200
    assert cfg.audit.contextual_enabled is False
    s.reset_cache()


def test_models_config_roles_and_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PD_QWEN_BASE_URL", "https://example.invalid/v1")
    s.reset_cache()
    models = s.get_models_config()
    assert models.role("llm").license == "Apache-2.0"
    assert models.role("llm").params_b is not None and models.role("llm").params_b <= 35
    assert models.role("llm").verified.by is None, "ориентир из ТЗ не выдаётся за проверенный"
    provider = models.provider_for("llm")
    assert provider.base_url() == "https://example.invalid/v1"
    assert models.role("text_to_image").enabled is False
    s.reset_cache()
