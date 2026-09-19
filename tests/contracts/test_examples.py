"""Примеры проходят схему, модель и валидаторы и не теряют данных при обходе."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from collections.abc import Callable

import pytest

from presentation_designer.contracts import models as m
from presentation_designer.contracts import validators as v

ROOT = pathlib.Path(__file__).resolve().parents[2]
EXAMPLES = sorted((ROOT / "contracts" / "examples").glob("*.example.json"))
MODELS: dict[str, type] = {
    "template_profile": m.TemplateProfile,
    "content_package": m.ContentPackage,
    "story_plan": m.StoryPlan,
    "slide_plan": m.SlidePlan,
    "slide_patch": m.SlidePatch,
    "composed_deck": m.ComposedDeck,
    "audit_report": m.AuditReport,
    "generation_request": m.GenerationRequest,
    "generation_result": m.GenerationResult,
    "job_status": m.JobStatus,
    "skill_manifest": m.SkillManifest,
    "project": m.Project,
    "project_file": m.ProjectFile,
    "brief_extract": m.BriefExtract,
}


def test_schema_validator_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "contracts" / "validate.py")], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.name for p in EXAMPLES])
def test_example_loads_and_round_trips(path: pathlib.Path) -> None:
    key = path.name.split(".")[0]
    model = MODELS[key]
    raw = json.loads(path.read_text())
    obj = model.model_validate(raw)
    dumped = json.loads(obj.model_dump_json(exclude_none=True, by_alias=True))
    assert model.model_validate(dumped) == obj


def test_examples_are_mutually_consistent(example: Callable[[str], dict[str, object]]) -> None:
    profile = m.TemplateProfile.model_validate(example("template_profile"))
    package = m.ContentPackage.model_validate(example("content_package"))
    story = m.StoryPlan.model_validate(example("story_plan"))
    plan = m.SlidePlan.model_validate(example("slide_plan"))
    deck = m.ComposedDeck.model_validate(example("composed_deck"))
    report = m.AuditReport.model_validate(example("audit_report"))
    request = m.GenerationRequest.model_validate(example("generation_request"))
    result = m.GenerationResult.model_validate(example("generation_result"))
    partial = m.GenerationResult.model_validate(example("generation_result.partial"))

    assert v.check_generation_request(request) == []
    assert v.check_content_package(package) == []
    assert v.check_template_profile(profile) == []
    assert v.check_story_plan(story, package) == []
    assert v.check_slide_plan(plan, profile, package, story) == []
    assert v.check_audit_report(report, deck) == []
    assert v.check_generation_result(result) == []
    assert v.check_generation_result(partial) == []


def test_generated_models_have_no_drift() -> None:
    """Повторная генерация моделей не меняет файл."""
    target = ROOT / "src" / "presentation_designer" / "contracts" / "models.py"
    before = target.read_text()
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_contracts.py")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    after = target.read_text()
    if before != after:
        target.write_text(before)
        pytest.fail("models.py устарел: запустите make gen-contracts и закоммитьте результат")
