"""Late capacity repair must keep data and qualifications visible, not in notes."""

import copy

import pytest

from presentation_designer.generation import variants as vr
from presentation_designer.shared.settings import get_settings
from tests.generation.test_variants import MINI_TEMPLATE, own_profile


@pytest.fixture(scope="module")
def profile():
    return own_profile(MINI_TEMPLATE, "tpl_split")


def context(profile):
    package = {
        "facts": [{"fact_id": "f1", "raw": "12 млн ₽", "value": 12, "label": "Оценка"}],
        "datasets": [
            {
                "dataset_id": "ds1",
                "title": "Экономика",
                "columns": [
                    {"name": "Статья", "type": "string"},
                    {"name": "Сумма", "type": "number"},
                ],
                "rows": [["Пилот", 12]],
            }
        ],
    }
    ctx = vr.build_context({}, profile, package, "balanced", {}, get_settings(), None)
    pattern = next(p for p in ctx.patterns if p.single("table") and not p.builtin)
    draft = vr.Draft(
        kind="content",
        theses=["t1"],
        pattern=pattern,
        title="Экономика пилота",
        visual="table",
        dataset="ds1",
        columns=["Статья", "Сумма"],
        text="Ожидаемый эффект не является уже полученным результатом.",
        items=[
            {"text": "Оценка {fact:f1}", "fact_refs": ["f1"]},
            {"text": "Масштабирование только после проверки рисков"},
        ],
        facts=["f1"],
        notes="Сверить бюджет",
        source_refs=["b1"],
        overflow=[{"kind": "body", "slot_id": "body_1"}],
    )
    return ctx, draft


def test_split_preserves_data_conditions_and_source_without_mutating_original(profile):
    ctx, draft = context(profile)
    before = copy.deepcopy(draft)
    result = vr._split_visual_explanations(ctx, [draft])
    assert len(result) == 2
    assert draft == before
    assert vr.retry_content_loss([before], result) is None
    assert all(not d.overflow and not d.unplaced_text for d in result)
    assert result[0].dataset == "ds1" and result[0].columns == before.columns
    assert any(b["kind"] == "table" for b in result[0].blocks)
    assert all(d.source_refs == ["b1"] for d in result)
    visible = vr._visible_text(result[1])
    assert before.text in visible
    assert all(item["text"] in visible for item in before.items)


def test_image_without_caption_keeps_explanation_on_following_page(profile):
    ctx, draft = context(profile)
    ctx.assets["img1"] = {"asset_id": "img1", "caption": "Интерфейс"}
    draft.visual, draft.dataset, draft.image = "image", None, "img1"
    draft.pattern = next(
        p for p in ctx.patterns if p.pattern_id == "pat_builtin_image_full_captionFalse"
    )
    before = copy.deepcopy(draft)
    result = vr._split_visual_explanations(ctx, [draft])
    assert len(result) == 2
    assert result[0].image == "img1" and result[1].image is None
    assert any(b["kind"] == "image" for b in result[0].blocks)
    assert next(b["image"] for b in result[0].blocks if b["kind"] == "image")["fit"] == "contain"
    assert all(not d.overflow and not d.unplaced_text for d in result)
    assert before.text in vr._visible_text(result[1])
    assert vr.retry_content_loss([before], result) is None


def test_commentary_splits_facts_added_by_block_builder(profile):
    ctx, draft = context(profile)
    draft.items = []
    draft.facts = [f"f{i}" for i in range(8)]
    ctx.facts = {
        fid: {
            "fact_id": fid,
            "raw": f"{i + 10} млн ₽",
            "value": i + 10,
            "label": f"Ожидаемая экономия после проверки этапа {i}",
        }
        for i, fid in enumerate(draft.facts)
    }
    before = copy.deepcopy(draft)
    result = vr._split_visual_explanations(ctx, [draft])
    # Introductory text can now share a list slot; do not demand a needless page.
    assert 2 <= len(result) <= 3
    assert draft == before
    assert vr.retry_content_loss([before], result) is None
    assert all(not d.overflow and not d.unplaced_text for d in result)
    visible = " ".join(vr._visible_text(d) for d in result)
    assert all(f"{{fact:{fid}}}" in visible for fid in draft.facts)


def test_table_without_text_slot_is_not_reported_as_fitting(profile, monkeypatch):
    ctx, draft = context(profile)
    draft.pattern = next(p for p in ctx.patterns if p.pattern_id.endswith("table_sheet_leadFalse"))
    draft.candidates = []
    monkeypatch.setattr(vr, "candidates_for", lambda *args, **kwargs: [])
    vr.fit_draft(ctx, draft)
    assert draft.unplaced_text
    assert draft.overflow == [{"kind": "body", "slot_id": "unplaced_text"}]
    assert "не размещена" in vr.overflow_hint([draft])
    monkeypatch.undo()
    parts = vr._split_visual_explanations(ctx, [draft])
    assert len(parts) >= 2
    assert all(not part.overflow and not part.unplaced_text for part in parts)
    assert draft.text in " ".join(vr._visible_text(part) for part in parts)
    assert vr.retry_content_loss([draft], parts) is None


@pytest.mark.parametrize("reason", ["budget", "fits", "no_content", "no_dataset", "service"])
def test_split_is_not_used_without_need_or_budget(profile, reason):
    ctx, draft = context(profile)
    if reason == "budget":
        ctx.spec.max = 1
    elif reason == "fits":
        draft.overflow = []
    elif reason == "no_content":
        draft.text, draft.items = "", []
    elif reason == "no_dataset":
        draft.dataset = None
    else:
        draft.kind = "summary"
    before = copy.deepcopy(draft)
    assert vr._split_visual_explanations(ctx, [draft]) == [before]
    assert draft == before


def test_failed_fit_is_atomic_and_does_not_leak_trial_fixes(profile, monkeypatch):
    ctx, draft = context(profile)
    before = copy.deepcopy(draft)
    fixes = copy.deepcopy(ctx.fixes)

    def fail(ctx, trial):
        ctx.fix("trial", "must not survive")
        trial.overflow = [{"kind": "body", "slot_id": "body"}]
        return trial

    monkeypatch.setattr(vr, "fit_draft", fail)
    assert vr._split_visual_explanations(ctx, [draft]) == [before]
    assert ctx.fixes == fixes


def test_split_budget_is_shared_across_slides(profile):
    ctx, first = context(profile)
    second = copy.deepcopy(first)
    second.title = "Вторая таблица"
    ctx.spec.max = 3
    result = vr._split_visual_explanations(ctx, [first, second])
    assert len(result) == 3
    assert result[-1] is second
    assert all(not d.overflow for d in result[:2])


def test_failed_commentary_fit_rolls_back_all_diagnostics(profile, monkeypatch):
    ctx, draft = context(profile)
    draft.dataset = None
    fixes = copy.deepcopy(ctx.fixes)
    before = copy.deepcopy(draft)

    def fail(ctx, trial):
        ctx.fix("trial", "must not survive")
        trial.overflow = [{"kind": "body", "slot_id": "body"}]
        return trial

    monkeypatch.setattr(vr, "fit_draft", fail)
    assert vr._split_commentary(ctx, draft) is None
    assert draft == before
    assert ctx.fixes == fixes


@pytest.mark.parametrize("extra_budget", [1, 2])
def test_long_commentary_needs_two_pages_and_respects_budget(profile, extra_budget):
    ctx, draft = context(profile)
    ctx.spec.max = 1 + extra_budget
    draft.text = (
        "Пилот улучшил ключевые метрики, а план задаёт последовательность расширения. "
        "При решении важно учитывать риски и ожидаемый характер экономии при масштабировании."
    )
    draft.items = [
        {
            "text": (
                f"Показатель {i}: оценка {{fact:f1}} только после проверки рисков "
                "и подтверждения согласия пользователей на изменение условий доставки."
            ),
            "fact_refs": ["f1"],
        }
        for i in range(14)
    ]
    before = copy.deepcopy(draft)
    fixes = copy.deepcopy(ctx.fixes)
    result = vr._split_visual_explanations(ctx, [draft])
    assert draft == before
    if extra_budget == 1:
        assert result == [before]
        assert ctx.fixes == fixes
        return
    assert len(result) == 3
    assert vr.retry_content_loss([before], result) is None
    assert all(not d.overflow and not d.unplaced_text for d in result)
    assert result[0].dataset == "ds1"
    assert all(d.dataset is None for d in result[1:])
    assert all(d.source_refs == before.source_refs and d.notes == before.notes for d in result)
    visible = " ".join(vr._visible_text(d) for d in result[1:])
    assert before.text in visible
    assert all(item["text"] in visible for item in before.items)
