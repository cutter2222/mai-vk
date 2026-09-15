"""Контракты между слоями: сгенерированные модели и валидаторы связей."""

from presentation_designer.contracts import validators
from presentation_designer.contracts.models import (
    AuditReport,
    ComposedDeck,
    ContentPackage,
    GenerationRequest,
    GenerationResult,
    JobStatus,
    SkillManifest,
    SlidePlan,
    StoryPlan,
    TemplateProfile,
)
from presentation_designer.contracts.validators import ContractError, Violation, raise_if

CONTRACTS_VERSION = "1.1"

__all__ = [
    "CONTRACTS_VERSION",
    "AuditReport",
    "ComposedDeck",
    "ContentPackage",
    "ContractError",
    "GenerationRequest",
    "GenerationResult",
    "JobStatus",
    "SkillManifest",
    "SlidePlan",
    "StoryPlan",
    "TemplateProfile",
    "Violation",
    "raise_if",
    "validators",
]
