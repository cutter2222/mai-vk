"""One composition pool for planning, composition, editing and repair.

Never mutate the stored template: concurrent density variants share it. Missing settings
retain the historical mixed pool. The source design tokens and brand assets stay intact.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

JsonDict = dict[str, Any]

LABELS = {
    "template_only": "По шаблону",
    "mixed": "Смешанный",
    "all_new": "Все слайды новые",
}
DESCRIPTIONS = {
    "template_only": (
        "Сохраняю макеты и расположение блоков. Если текст не помещается, "
        "предложу разделить слайд или сократить текст."
    ),
    "mixed": (
        "Использую макеты шаблона, где они подходят, и добавляю новые композиции в том же стиле."
    ),
    "all_new": (
        "Все композиции будут новыми, включая титул и финал. "
        "Сохраняю цвета, типографику и фирменные элементы исходника."
    ),
}


def parse_design_mode(text: str) -> str | None:
    normalized = text.strip().casefold().rstrip(".! ")
    return next((key for key, label in LABELS.items() if normalized == label.casefold()), None)


def profile_for_mode(profile: JsonDict, settings: JsonDict) -> JsonDict:
    mode = settings.get("design_mode") or "mixed"
    if mode not in LABELS:
        raise ValueError(f"Unknown design mode: {mode}")
    result = deepcopy(profile)
    if mode != "mixed":
        result["patterns"] = [
            pattern
            for pattern in result.get("patterns", [])
            if ((pattern.get("source") or {}).get("kind") == "builtin") == (mode == "all_new")
        ]
    return result


def validate_plan_mode(plan: JsonDict, profile: JsonDict, settings: JsonDict) -> None:
    """Fail closed before assembly, even if a model/cache returns a forbidden pattern."""
    from presentation_designer.pipeline.run import StageError

    mode = settings.get("design_mode") or "mixed"
    if mode == "mixed":
        return
    allowed = {p["pattern_id"]: p for p in profile_for_mode(profile, settings).get("patterns", [])}
    for slide in plan.get("slides", []):
        pattern = allowed.get(slide.get("pattern_id"))
        # SlidePlan refers to a pattern, not a layout: the composer resolves its source.
        if pattern is None:
            raise StageError(
                "design_mode_violation", "Композиция не соответствует выбранному режиму"
            )
        if mode == "template_only" and any(
            op.get("op") in {"geometry", "add_text", "delete"}
            for op in slide.get("overrides") or []
        ):
            raise StageError(
                "design_mode_violation",
                "Режим «По шаблону» сохраняет расположение блоков. "
                "Разделите слайд или сократите текст.",
            )
