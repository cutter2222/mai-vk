"""Адаптер моделей: единственная точка вызова LLM и VLM.

Слои импортируют отсюда `build_client`, `Request`, `Message`, `Image` и загрузчик скиллов;
детали протокола, лимитов, повторов и кэша остаются внутри пакета.
"""

from presentation_designer.llm.client import (
    LlmClient,
    build_client,
    describe_provider,
    model_refs,
    skill_model_ref,
)
from presentation_designer.llm.skills import Skill, get_skill, load_skill, version_refs
from presentation_designer.llm.types import (
    ConfigError,
    Deadline,
    DeadlineError,
    Image,
    LlmError,
    Message,
    ProviderError,
    QuotaTimeoutError,
    ReplayMissError,
    Request,
    Response,
    ResponseError,
    Usage,
)
from presentation_designer.llm.usage import UsageRecorder

__all__ = [
    "ConfigError",
    "Deadline",
    "DeadlineError",
    "Image",
    "LlmClient",
    "LlmError",
    "Message",
    "ProviderError",
    "QuotaTimeoutError",
    "ReplayMissError",
    "Request",
    "Response",
    "ResponseError",
    "Skill",
    "Usage",
    "UsageRecorder",
    "build_client",
    "describe_provider",
    "get_skill",
    "load_skill",
    "model_refs",
    "skill_model_ref",
    "version_refs",
]
