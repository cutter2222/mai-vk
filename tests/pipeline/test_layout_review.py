"""Проверка вёрстки по картинке в варианте: слайды с дефектами перестраиваются на других
композициях; остаётся вариант с меньшим числом дефектов, хуже — прежний каталог ревизии."""

from __future__ import annotations

import pathlib
from typing import Any

from presentation_designer.audit.visual import LayoutReview
from presentation_designer.pipeline import run as rn
from presentation_designer.pipeline.artifacts import Staging


class FakeLayers(rn.Layers):
    """План из двух слайдов; дефект второго слайда лечится запретом его композиции."""

    def __init__(self, after: int) -> None:
        self.modes = {}
        self.after = after
        self.plans: list[Any] = []
        self.reviews = 0

    def plan(self, inp: rn.PlanInput) -> dict[str, Any]:
        avoid = list(inp.settings.get("avoid_patterns") or [])
        self.plans.append(avoid)
        second = "pat_b2" if "pat_b" in avoid else "pat_b"
        return {"slides": [{"order": 1, "pattern_id": "pat_a"}, {"order": 2, "pattern_id": second}]}

    def compose(self, inp: rn.ComposeInput) -> rn.ComposeOutput:
        second = inp.plan["slides"][1]["pattern_id"]
        inp.staging.write_json("deck.json", {"second": second})
        return rn.ComposeOutput(2, ["a", "b"], {"slides": []})

    def export(self, inp: rn.ExportInput) -> rn.ExportOutput:
        return rn.ExportOutput(thumbnails=[])

    def review_layout(self, inp: rn.ReviewInput) -> LayoutReview:
        self.reviews += 1
        if self.reviews == 1:
            return LayoutReview(ran=True, findings={1: [{"code": "clipped", "where": "низ"}]})
        issues = [{"code": "overlap", "where": "центр"}] * self.after
        return LayoutReview(ran=True, findings={1: issues} if issues else {})


def _ctx(tmp_path: pathlib.Path) -> rn.VariantContext:
    staging = Staging(dir=tmp_path / "rev", final=tmp_path / "final", prefix="balanced/r1/")
    staging.dir.mkdir()
    return rn.VariantContext(
        job_id="job_1",
        variant_id="balanced",
        template_profile={"patterns": []},
        template_path=None,
        package={},
        story={},
        settings={"slide_count": {"min": 2, "max": 2}},
        staging=staging,
    )


def _run(layers: FakeLayers, ctx: rn.VariantContext) -> Any:
    plan = layers.plan(rn.PlanInput("job_1", "balanced", {}, {}, {}, ctx.settings, 2))
    composed = layers.compose(
        rn.ComposeInput("job_1", "balanced", 1, plan, {}, None, {}, {}, ctx.staging)
    )
    exported = layers.export(
        rn.ExportInput("job_1", "balanced", 1, ctx.staging, ["a", "b"], "Тема", {})
    )
    return rn._review_and_fix(layers, ctx, plan, composed, exported, "Тема")


def test_defective_slide_is_rebuilt_on_other_composition(tmp_path: pathlib.Path) -> None:
    layers = FakeLayers(after=0)
    ctx = _ctx(tmp_path)
    _composed, _exported, review = _run(layers, ctx)
    assert layers.plans[-1] == ["pat_b"]
    assert '"pat_b2"' in (ctx.staging.dir / "deck.json").read_text()
    assert review is not None and review["defects"] == 0
    assert "1 → 0" in review["message"]


def test_worse_rebuild_restores_previous_revision(tmp_path: pathlib.Path) -> None:
    layers = FakeLayers(after=2)
    ctx = _ctx(tmp_path)
    _composed, _exported, review = _run(layers, ctx)
    assert '"pat_b"' in (ctx.staging.dir / "deck.json").read_text()
    assert review is not None and review["defects"] == 1


def test_without_model_nothing_changes(tmp_path: pathlib.Path) -> None:
    class Plain(FakeLayers):
        def review_layout(self, inp: rn.ReviewInput) -> LayoutReview:
            return LayoutReview()

    layers = Plain(after=0)
    ctx = _ctx(tmp_path)
    _composed, _exported, review = _run(layers, ctx)
    assert review is None and len(layers.plans) == 1
