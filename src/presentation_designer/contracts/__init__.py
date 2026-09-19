"""Контракты между слоями: сгенерированные модели и валидаторы связей."""

from presentation_designer.contracts import validators
from presentation_designer.contracts.models import (
    AuditReport,
    BriefExtract,
    ComposedDeck,
    ContentPackage,
    GenerationRequest,
    GenerationResult,
    JobStatus,
    Project,
    ProjectFile,
    SkillManifest,
    SlidePatch,
    SlidePlan,
    StoryPlan,
    TemplateProfile,
)
from presentation_designer.contracts.validators import ContractError, Violation, raise_if

CONTRACTS_VERSION = "1.9"

__all__ = [
    "CONTRACTS_VERSION",
    "AuditReport",
    "BriefExtract",
    "ComposedDeck",
    "ContentPackage",
    "ContractError",
    "GenerationRequest",
    "GenerationResult",
    "JobStatus",
    "Project",
    "ProjectFile",
    "SkillManifest",
    "SlidePatch",
    "SlidePlan",
    "StoryPlan",
    "TemplateProfile",
    "Violation",
    "raise_if",
    "validators",
]
