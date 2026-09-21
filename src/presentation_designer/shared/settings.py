"""Загрузка настроек из config/app.yaml, config/models.yaml и окружения.

Один и тот же загрузчик используют CLI, API и воркеры. Порядок приоритета:
переменные окружения с префиксом PD_ (вложенность через двойное подчёркивание)
переопределяют YAML; YAML переопределяет умолчания моделей.
"""

from __future__ import annotations

import os
import pathlib
from functools import lru_cache
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

ENV_PREFIX = "PD_"
ROOT = pathlib.Path(__file__).resolve().parents[3]
CONFIG_DIR = pathlib.Path(os.environ.get("PD_CONFIG_DIR", ROOT / "config"))


class SlideCountDefault(BaseModel):
    min: int = 10
    max: int = 15


class Paths(BaseModel):
    data_dir: pathlib.Path = pathlib.Path("data")
    artifacts_dir: pathlib.Path = pathlib.Path("artifacts")
    runs_dir: pathlib.Path = pathlib.Path("runs")
    backups_dir: pathlib.Path = pathlib.Path("backups")
    organizers_dir: pathlib.Path = pathlib.Path("data/organizers")


class Limits(BaseModel):
    max_upload_mb: int = 100
    max_unzipped_mb: int = 1024
    max_content_files: int = 20
    max_project_files: int = 50
    max_project_mb: int = 1024
    slide_count_default: SlideCountDefault = Field(default_factory=SlideCountDefault)
    slide_count_max: int = 60
    variants_default: list[str] = Field(default_factory=lambda: ["compact", "balanced", "detailed"])


class Timeouts(BaseModel):
    job_total_s: int = 600
    stage_analyze_s: int = 180
    stage_story_s: int = 120
    stage_plan_s: int = 120
    stage_compose_s: int = 60
    stage_export_s: int = 120
    stage_audit_s: int = 180
    render_convert_s: int = 90
    llm_call_s: int = 90
    brief_s: int = 8


class Budget(BaseModel):
    target_total_s: int = 240
    hard_limit_s: int = 300


class Queue(BaseModel):
    analysis_queue: str = "analysis"
    repair_queue: str = "repair"
    generation_queue: str = "generation"
    generation_workers: int = 3
    result_ttl_s: int = 86400
    failure_ttl_s: int = 604800
    heartbeat_ttl_s: int = 90
    reconcile_interval_s: int = 60


class Execution(BaseModel):
    mode: str = "stub"
    stub_stage_delay_ms: int = 400


class ContentImport(BaseModel):
    """Импорт содержания: кэш разбора файлов и пределы пакета (parsing/content)."""

    cache_dir: pathlib.Path = pathlib.Path("data/import-cache")
    max_block_chars: int = 2000
    max_dataset_rows: int = 200
    max_facts: int = 300
    max_assets: int = 40
    min_image_px: int = 64
    fact_context_model: bool = True
    fact_context_budget_s: int = 20


class Plan(BaseModel):
    """Планы вариантов (generation/variants): кэш готовых планов, пакеты тезисов, лестница
    ёмкости (минимальный кегль по роли текста и допустимое уменьшение), политика стилей
    служебных слайдов (`per_variant` — единый стиль внутри колоды и разные у вариантов,
    `first` — первый паттерн пула роли)."""

    cache_dir: pathlib.Path = pathlib.Path("data/plan-cache")
    packet_theses: int = 5
    candidates_per_thesis: int = 3
    max_candidates: int = 16
    min_font_ratio: float = 0.75
    min_body_pt: float = 12.0
    min_title_pt: float = 20.0
    table_max_rows: int = 7
    margin_ratio: float = 0.92
    style_policy: Literal["per_variant", "first"] = "per_variant"
    photo_dividers_for_image_sections: bool = True


class Layout(BaseModel):
    """Вёрстка (layout): удалять ли неиспользуемые макеты из результата."""

    prune_unused_layouts: bool = False


class Render(BaseModel):
    slots: int = 2
    thumbnail_width_px: int = 1280
    vlm_image_max_px: int = 1024
    mode: Literal["onlyoffice"] = "onlyoffice"


class AuditThresholds(BaseModel):
    max_bullets: int = 6
    max_words_per_bullet: int = 15
    table_max_rows: int = 7
    table_max_cols: int = 5
    chart_max_series: int = 5
    fill_ratio_min: float = 0.25
    fill_ratio_max: float = 0.75
    min_contrast: float = 4.5
    overlap_min_ratio: float = 0.02
    guide_tolerance: float = 0.005


class Retention(BaseModel):
    """Сроки сборки мусора; отсчёт идёт с момента, когда объект впервые найден без ссылок."""

    gc_interval_s: int = 3600
    upload_tmp_hours: int = 6
    unreferenced_uploads_hours: int = 24
    orphan_jobs_hours: int = 24


class Backup(BaseModel):
    keep: int = 7


class Audit(BaseModel):
    contextual_enabled: bool = True
    contextual_concurrency: int = 4
    thresholds: AuditThresholds = Field(default_factory=AuditThresholds)


class DesignVariant(BaseModel):
    """Рамки заполненности одного варианта вёрстки (design/rules.py)."""

    min_fill: float = 0.35
    max_fill: float = 0.72
    max_slots_ratio: float = 1.5


class DesignFeedback(BaseModel):
    """Правка плана по фактам собранного файла (design/feedback.py)."""

    enabled: bool = True
    refill: bool = True  # спрашивать модель; выключено — только детерминированные правки
    rounds: int = 2  # кругов дозапроса: дальше выигрыш не окупает вызова
    max_slots: int = 24
    pattern_swap: bool = False  # менять композицию по фактам: см. комментарий в app.yaml


class Design(BaseModel):
    variants: dict[str, DesignVariant] = Field(default_factory=dict)
    feedback: DesignFeedback = Field(default_factory=DesignFeedback)


class Llm(BaseModel):
    """Адаптер моделей: кэш, умолчания лимитера, повторы и оценка токенов (llm/)."""

    cache_mode: str = "read_write"
    cache_dir: pathlib.Path = pathlib.Path("data/llm-cache")
    fixtures_dir: pathlib.Path = pathlib.Path("tests/fixtures/llm")
    concurrency: int = 4
    rpm: int = 60
    tpm: int = 200_000
    max_retries: int = 3
    retry_base_s: float = 1.0
    retry_max_s: float = 30.0
    lease_ttl_s: int = 180
    quota_wait_max_s: int = 120
    chars_per_token: float = 3.0
    image_tokens: int = 1280


class App(BaseModel):
    name: str = "presentation-designer"
    contracts_version: str = "1.9"
    language_default: str = "ru"


class OnlyOffice(BaseModel):
    max_pdf_mb: int = Field(default=200, ge=1, le=1024)
    enabled: bool = False
    jwt_secret: str = ""
    public_url: str = "http://localhost:8080/onlyoffice"
    internal_url: str = "http://onlyoffice"
    storage_url: str = "http://api:8000"


class Settings(BaseModel):
    onlyoffice: OnlyOffice = Field(default_factory=OnlyOffice)
    app: App = Field(default_factory=App)
    paths: Paths = Field(default_factory=Paths)
    limits: Limits = Field(default_factory=Limits)
    timeouts: Timeouts = Field(default_factory=Timeouts)
    budget: Budget = Field(default_factory=Budget)
    queue: Queue = Field(default_factory=Queue)
    execution: Execution = Field(default_factory=Execution)
    content_import: ContentImport = Field(default_factory=ContentImport)
    plan: Plan = Field(default_factory=Plan)
    layout: Layout = Field(default_factory=Layout)
    render: Render = Field(default_factory=Render)
    retention: Retention = Field(default_factory=Retention)
    backup: Backup = Field(default_factory=Backup)
    audit: Audit = Field(default_factory=Audit)
    design: Design = Field(default_factory=Design)
    llm: Llm = Field(default_factory=Llm)

    def resolve(self, path: pathlib.Path) -> pathlib.Path:
        """Относительные пути из конфига считаются от корня репозитория."""
        return path if path.is_absolute() else ROOT / path

    @property
    def data_dir(self) -> pathlib.Path:
        return self.resolve(self.paths.data_dir)

    @property
    def artifacts_dir(self) -> pathlib.Path:
        return self.resolve(self.paths.artifacts_dir)

    @property
    def runs_dir(self) -> pathlib.Path:
        return self.resolve(self.paths.runs_dir)

    @property
    def backups_dir(self) -> pathlib.Path:
        return self.resolve(self.paths.backups_dir)

    @property
    def db_path(self) -> pathlib.Path:
        return self.data_dir / "state.sqlite3"

    @property
    def uploads_dir(self) -> pathlib.Path:
        return self.data_dir / "uploads"

    @property
    def import_cache_dir(self) -> pathlib.Path:
        return self.resolve(self.content_import.cache_dir)

    @property
    def plan_cache_dir(self) -> pathlib.Path:
        return self.resolve(self.plan.cache_dir)

    @property
    def llm_cache_dir(self) -> pathlib.Path:
        return self.resolve(self.llm.cache_dir)

    @property
    def llm_fixtures_dir(self) -> pathlib.Path:
        return self.resolve(self.llm.fixtures_dir)


class Verified(BaseModel):
    by: str | None = None
    date: str | None = None
    source: str | None = None


class Reasoning(BaseModel):
    mode: str = "provider_default"
    max_output_tokens: int | None = None


class ModelRole(BaseModel):
    enabled: bool = True
    provider: str | None = None
    model: str | None = None
    hf_url: str | None = None
    license: str | None = None
    params_b: float | None = None
    reasoning: Reasoning = Field(default_factory=Reasoning)
    temperature: float | None = None
    verified: Verified = Field(default_factory=Verified)


class ProviderSupports(BaseModel):
    json_schema: bool | None = None
    json_object: bool | None = None
    images: bool | None = None
    multi_image: bool | None = None
    usage: bool | None = None


class ProviderLimits(BaseModel):
    concurrency: int | None = None
    rpm: int | None = None
    tpm: int | None = None


class Provider(BaseModel):
    kind: str = "openai_compatible"
    env_base_url: str
    env_api_key: str
    note: str | None = None
    # Как endpoint принимает режим рассуждения: qwen_enable_thinking (extra_body с
    # enable_thinking / thinking_budget), openai_reasoning_effort (reasoning_effort) или none.
    reasoning_style: str = "none"
    # Ревизия или checkpoint по данным зонда; входит в ключ кэша, чтобы смена весов сбросила кэш.
    model_revision: str | None = None
    supports: ProviderSupports = Field(default_factory=ProviderSupports)
    limits: ProviderLimits = Field(default_factory=ProviderLimits)

    def base_url(self) -> str | None:
        return os.environ.get(self.env_base_url) or None

    def api_key(self) -> str | None:
        return os.environ.get(self.env_api_key) or None

    def configured(self) -> bool:
        """Есть адрес и ключ, и это не значения-образцы из .env.example."""
        url, key = self.base_url(), self.api_key()
        return bool(url and key) and "example.invalid" not in (url or "") and key != "replace-me"


class ModelsConfig(BaseModel):
    providers: dict[str, Provider]
    active_provider: str
    roles: dict[str, ModelRole]

    def role(self, name: str) -> ModelRole:
        return self.roles[name]

    def provider_for(self, role: str) -> Provider:
        r = self.roles[role]
        return self.providers[r.provider or self.active_provider]


def _coerce(value: str) -> Any:
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _apply_env(data: dict[str, Any], prefix: str = ENV_PREFIX) -> dict[str, Any]:
    """PD_SECTION__KEY=value переопределяет data['section']['key']."""
    for key, value in os.environ.items():
        if not key.startswith(prefix) or "__" not in key:
            continue
        path = key[len(prefix) :].lower().split("__")
        node = data
        for part in path[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                break
        else:
            node[path[-1]] = _coerce(value)
    return data


def _read_yaml(path: pathlib.Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text()) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: ожидался объект YAML")
    return loaded


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data = _read_yaml(CONFIG_DIR / "app.yaml")
    return Settings.model_validate(_apply_env(data))


@lru_cache(maxsize=1)
def get_models_config() -> ModelsConfig:
    return ModelsConfig.model_validate(_read_yaml(CONFIG_DIR / "models.yaml"))


def reset_cache() -> None:
    """Для тестов: сбрасывает кэш настроек после изменения окружения."""
    get_settings.cache_clear()
    get_models_config.cache_clear()
