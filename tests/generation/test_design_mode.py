"""Composition policy is independent from content density and fails closed."""

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from presentation_designer.contracts import GenerationRequest
from presentation_designer.generation.design_mode import (
    LABELS,
    parse_design_mode,
    profile_for_mode,
    validate_plan_mode,
)
from presentation_designer.pipeline.run import StageError


@pytest.fixture
def profile() -> dict[str, Any]:
    return {
        "design_tokens": {"colors": ["#112233"]},
        "fixed_elements": [{"object_id": "logo"}],
        "patterns": [
            {"pattern_id": kind, "source": {"kind": kind, "layout_id": "layout"}}
            for kind in ("slide", "layout", "builtin")
        ],
    }


@pytest.mark.parametrize("mode,label", LABELS.items())
def test_parse_labels(mode: str, label: str) -> None:
    assert parse_design_mode(f"  {label.upper()}! ") == mode
    assert parse_design_mode(f"Расскажи про {label}") is None


@pytest.mark.parametrize("mode", [None, *LABELS])
@pytest.mark.parametrize("density", ["compact", "balanced", "detailed"])
def test_pool_is_immutable_and_independent_of_density(
    profile: dict[str, Any], mode: str | None, density: str
) -> None:
    before = deepcopy(profile)
    result = profile_for_mode(profile, {"design_mode": mode, "variants": [density]})
    expected = {
        "template_only": {"slide", "layout"},
        "all_new": {"builtin"},
    }.get(mode, {"slide", "layout", "builtin"})
    assert {p["pattern_id"] for p in result["patterns"]} == expected
    assert result["design_tokens"] == profile["design_tokens"]
    assert result["fixed_elements"] == profile["fixed_elements"]
    result["design_tokens"]["colors"].append("#ffffff")
    assert profile == before


@pytest.mark.parametrize("mode,forbidden", [("template_only", "builtin"), ("all_new", "slide")])
def test_validation_filters_even_an_unfiltered_profile(
    profile: dict[str, Any], mode: str, forbidden: str
) -> None:
    plan = {"slides": [{"pattern_id": forbidden, "layout_id": "layout"}]}
    with pytest.raises(StageError) as exc:
        validate_plan_mode(plan, profile, {"design_mode": mode})
    assert exc.value.code == "design_mode_violation"


@pytest.mark.parametrize("op", ["geometry", "add_text", "delete"])
def test_template_only_rejects_composition_overrides(profile: dict[str, Any], op: str) -> None:
    plan = {
        "slides": [
            {
                "pattern_id": "slide",
                "layout_id": "layout",
                "overrides": [{"op": op}],
            }
        ]
    }
    with pytest.raises(StageError, match="Разделите слайд"):
        validate_plan_mode(plan, profile, {"design_mode": "template_only"})
    validate_plan_mode(plan, profile, {"design_mode": "mixed"})


def test_request_defaults_and_rejects_unknown_modes() -> None:
    request = {"schema_version": "1.2", "template_id": "tpl", "package_id": "pkg"}
    model = GenerationRequest.model_validate({**request, "settings": {}})
    assert model.settings is not None and model.settings.design_mode == "mixed"
    with pytest.raises(ValidationError):
        GenerationRequest.model_validate({**request, "settings": {"design_mode": "unknown"}})


@pytest.mark.parametrize("mode", LABELS)
def test_pipeline_propagates_policy_and_fit_warning(
    profile: dict[str, Any], tmp_path: Path, mode: str
) -> None:
    from presentation_designer.pipeline.artifacts import Staging
    from presentation_designer.pipeline.run import (
        ComposeOutput,
        ExportOutput,
        VariantContext,
        run_variant,
    )

    warning = {"code": "template_fit_review", "message": "Разделите слайд"}
    expected = profile_for_mode(profile, {"design_mode": mode})

    class Layers:
        def plan(self, inp: Any) -> dict[str, Any]:
            assert inp.template_profile == expected
            assert inp.settings["design_mode"] == mode
            return {
                "slides": [
                    {
                        "pattern_id": expected["patterns"][0]["pattern_id"],
                        "layout_id": "layout",
                    }
                ]
            }

        def compose(self, inp: Any) -> ComposeOutput:
            assert inp.template_profile == expected
            assert inp.settings["design_mode"] == mode
            return ComposeOutput(1, ["Title"], {}, warnings=[warning])

        def export(self, inp: Any) -> ExportOutput:
            return ExportOutput(thumbnails=[])

        def audit(self, inp: Any) -> dict[str, Any]:
            return {"coverage": {"complete": True}, "summary": {"issues_total": 0}}

    ctx = VariantContext(
        "job",
        "balanced",
        profile,
        None,
        {},
        {},
        {"design_mode": mode},
        Staging(tmp_path, tmp_path, "balanced/r1/"),
    )
    outcome = run_variant(Layers(), ctx, lambda *_: None)
    assert outcome.error is None
    assert outcome.status == "needs_review"
    assert outcome.warnings == [warning]
    assert len(profile["patterns"]) == 3


@pytest.mark.parametrize(
    "mode,pattern,overrides",
    [
        ("template_only", "builtin", []),
        ("all_new", "slide", []),
        ("template_only", "slide", [{"op": "geometry"}]),
    ],
)
@pytest.mark.parametrize("manual", [False, True])
def test_edit_cannot_bypass_mode(
    profile: dict[str, Any],
    tmp_path: Path,
    mode: str,
    pattern: str,
    overrides: list[dict[str, Any]],
    manual: bool,
) -> None:
    from presentation_designer.pipeline.artifacts import Staging
    from presentation_designer.pipeline.run import EditContext, EditOutput, run_edit

    class Layers:
        def edit(self, inp: Any) -> EditOutput:
            assert inp.template_profile == profile_for_mode(profile, {"design_mode": mode})
            return EditOutput(
                {
                    "slides": [
                        {
                            "pattern_id": pattern,
                            "overrides": overrides,
                        }
                    ]
                },
                True,
                "s1",
            )

        def compose(self, inp: Any) -> None:
            pytest.fail("Forbidden edit reached assembly")

    ctx = EditContext(
        "job",
        "balanced",
        2,
        1,
        {},
        0,
        "Change slide",
        profile,
        None,
        {},
        {},
        {"design_mode": mode},
        Staging(tmp_path, tmp_path, "balanced/r2/"),
        patch={} if manual else None,
    )
    result = run_edit(Layers(), ctx, lambda *_: None)
    assert result.status == "failed"
    assert result.error is not None and result.error["code"] == "design_mode_violation"


@pytest.mark.parametrize("kind,warns", [("overflow", True), ("shrunk", True), ("hole", False)])
def test_real_compose_reports_capacity_not_empty_slots(
    profile: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    warns: bool,
) -> None:
    from types import SimpleNamespace

    from presentation_designer.design import feedback
    from presentation_designer.pipeline import real
    from presentation_designer.pipeline.artifacts import Staging
    from presentation_designer.pipeline.run import ComposeInput
    from presentation_designer.shared.settings import get_settings

    template = tmp_path / "template.pptx"
    template.touch()
    plan = {"slides": [{"pattern_id": "slide"}]}
    layers = real.RealLayers(get_settings())
    monkeypatch.setattr(layers, "polish_plan", lambda inp: inp.plan)
    monkeypatch.setattr(
        real,
        "compose_deck",
        lambda *a, **kw: SimpleNamespace(
            deck={"stats": {}},
            slide_titles=["Title"],
            warnings=[],
            report={"file_size_bytes": 0, "timings_ms": {"total": 0}},
        ),
    )
    monkeypatch.setattr(
        feedback,
        "read",
        lambda *a: feedback.Facts(
            items=[feedback.Fact("s1", 0, "body", kind)],
        ),
    )
    result = layers.compose(
        ComposeInput(
            "job",
            "balanced",
            1,
            plan,
            profile,
            template,
            {},
            {},
            Staging(tmp_path, tmp_path, "balanced/r1/"),
            settings={"design_mode": "template_only"},
        )
    )
    assert bool(result.warnings) == warns
    assert bool(plan.get("warnings")) == warns
