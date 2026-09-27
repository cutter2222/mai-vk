"""Only selected, labelled source cells can replace automatically added fact prose."""

import copy

import pytest

from presentation_designer.generation import variants as vr
from tests.generation.test_number_content import _context


def data_context():
    ctx = _context("balanced")
    ctx.datasets = {
        "ds": {
            "dataset_id": "ds",
            "source_id": "src",
            "block_id": "b",
            "source_location": {"sheet": "Метрики", "cell_range": "B3:D6"},
            "columns": [
                {"name": "Месяц", "type": "date"},
                {"name": "Охват", "type": "percent", "unit": "%"},
                {"name": "Отписки", "type": "percent", "unit": "%"},
            ],
            "rows": [["Май", 31, 4.1], ["Июнь", 38, 3.2], ["Июль", 44, 2.7]],
        }
    }
    ctx.facts = {
        fid: {
            "fact_id": fid,
            "source_id": "src",
            "block_id": "b",
            "value": value,
            "raw": f"{value} %",
            "unit": "%",
            "label": "Показатель",
            "source_location": {"sheet": "Метрики", "cell": cell},
        }
        for fid, value, cell in [("f1", 31, "C4"), ("f2", 44, "C6"), ("f3", 4.1, "D4")]
    }
    return ctx


@pytest.mark.parametrize(
    ("columns", "offset", "limit", "expected"),
    [
        (["Месяц", "Охват"], 0, 1, {"f1"}),
        (["Месяц", "Охват"], 2, 1, {"f2"}),
        (["Месяц", "Отписки"], 0, 3, {"f3"}),
        (["Месяц", "Охват", "Отписки"], 0, 3, {"f1", "f2", "f3"}),
        (["Охват"], 0, 3, set()),
    ],
)
def test_table_checks_selected_columns_and_row_window(columns, offset, limit, expected):
    block = {
        "kind": "table",
        "table": {
            "dataset_id": "ds",
            "columns": columns,
            "row_offset": offset,
            "max_rows": limit,
        },
    }
    assert vr._data_shown_facts(data_context(), [block]) == expected


@pytest.mark.parametrize("change", ["source", "sheet", "cell", "unit", "value", "derived"])
def test_same_number_without_matching_provenance_is_not_covered(change):
    ctx = data_context()
    fact = ctx.facts["f1"]
    if change in ("sheet", "cell"):
        fact["source_location"][change] = "other"
    else:
        fact[{"source": "source_id"}.get(change, change)] = "other"
    block = {"kind": "table", "table": {"dataset_id": "ds"}}
    assert vr._data_shown_facts(ctx, [block]) == {"f2", "f3"}


@pytest.mark.parametrize("kind, expected", [("column", {"f1", "f2", "f3"}), ("pie", {"f1", "f2"})])
def test_chart_uses_rendered_series_not_just_requested_series(kind, expected):
    block = {
        "kind": "chart",
        "chart": {
            "dataset_id": "ds",
            "type": kind,
            "series": ["Охват", "Отписки"],
            "category_column": "Месяц",
            "units": "%",
            "show_legend": True,
            "show_data_labels": True,
            "show_axis_labels": True,
        },
    }
    assert vr._data_shown_facts(data_context(), [block]) == expected
    block["chart"]["series"] = ["Отписки"]
    assert vr._data_shown_facts(data_context(), [block]) == {"f3"}
    block["chart"]["show_data_labels"] = False
    assert not vr._data_shown_facts(data_context(), [block])


def test_fill_keeps_qualifiers_but_does_not_inject_selected_cell_again():
    ctx = data_context()
    profile = copy.deepcopy(ctx.profile)
    profile["patterns"][0]["slots"][1]["kind"] = "table"
    ctx.patterns = vr.profile_patterns(profile)
    item = {"text": "Не включает отказавшихся пользователей", "fact_refs": ["f1"]}
    draft = vr.Draft(
        kind="content",
        theses=[],
        pattern=ctx.patterns[0],
        title="Метрики",
        visual="table",
        dataset="ds",
        columns=["Месяц", "Охват"],
        facts=["f1", "f3"],
        items=[item],
    )
    blocks = vr.fill_blocks(ctx, draft)
    items = [i for b in blocks for i in b.get("items", [])]
    assert items[0] == item
    assert any("{fact:f3}" in i["text"] for i in items)
    assert not any("{fact:f1}" in i["text"] for i in items)
    assert draft.items == [item]


def test_chart_scale_filter_does_not_cover_dropped_series():
    ctx = data_context()
    ds = ctx.datasets["ds"]
    ds["columns"][2] = {"name": "Пользователи", "type": "number"}
    ds["rows"][0][2] = 100000
    ctx.facts["f3"].update(value=100000, raw="100000", unit=None)
    draft = vr.Draft(
        kind="content",
        theses=[],
        pattern=ctx.patterns[0],
        title="Метрики",
        visual="chart",
        dataset="ds",
        columns=["Охват", "Пользователи"],
    )
    block = vr._chart_block(ctx, draft, next(iter(ctx.patterns[0].slots.values())))
    assert block["chart"]["series"] == ["Охват"]
    assert "f3" not in vr._data_shown_facts(ctx, [block])


def test_rounded_table_value_does_not_replace_precise_fact():
    ctx = data_context()
    ctx.datasets["ds"]["rows"][0][1] = 31.123
    ctx.facts["f1"]["value"] = 31.123
    assert vr._data_shown_facts(ctx, [{"kind": "table", "table": {"dataset_id": "ds"}}]) == {
        "f2",
        "f3",
    }


def test_deck_restoration_does_not_reinject_fact_in_selected_table_cell(monkeypatch):
    ctx = data_context()
    draft = vr.Draft(
        kind="content",
        theses=[],
        pattern=ctx.patterns[0],
        title="Метрики",
        blocks=[{"kind": "table", "table": {"dataset_id": "ds"}}],
    )
    assert vr._data_shown_facts(ctx, draft.blocks) == set(ctx.facts)
    # If not recognized, the owners branch would try to restore each mandatory fact.
    monkeypatch.setattr(ctx, "theses", [type("Thesis", (), {"id": "t", "fact_refs": ["f1"]})()])
    draft.theses = ["t"]
    monkeypatch.setattr(vr, "fit_draft", lambda *_: pytest.fail("unexpected fact injection"))
    assert vr._ensure_facts(ctx, [draft]) == [draft]
    assert not draft.items
