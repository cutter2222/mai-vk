"""Контракт операций чата (этап 36): схема, адреса по снимку, overrides плана — подмножество."""

from __future__ import annotations

import copy
import json
import pathlib
from typing import Any

import pytest

from presentation_designer.generation import chat_ops
from presentation_designer.generation.snapshot import deck_snapshot
from tests.generation.test_snapshot import build_deck

ROOT = pathlib.Path(__file__).resolve().parents[2]
TARGET = {"object_id": "10", "source_object_id": "10", "slot_id": "title"}
# По правке каждого вида и их сочетания, как их пишет визуальный редактор.
OVERRIDES: list[dict[str, Any]] = [
    {"op": "text", "target": TARGET, "text": "Выручка {fact:revenue_2024} за год"},
    {
        "op": "style",
        "target": TARGET,
        "style": {
            "font": {
                "family": "Play",
                "size_pt": 28,
                "bold": True,
                "italic": False,
                "color": "#0077FF",
            },
            "align": "center",
        },
    },
    {"op": "style", "target": TARGET, "style": {"align": "right"}},
    {
        "op": "geometry",
        "target": {"object_id": "11"},
        "geometry": {"bbox": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.1}},
    },
    {
        "op": "picture",
        "target": {"object_id": "5", "source_object_id": "5"},
        "picture": {
            "source": {"kind": "template", "asset_id": "icon_rocket"},
            "fit": "contain",
            "color": "#0077FF",
        },
    },
    {
        "op": "picture",
        "target": {"object_id": "6"},
        "picture": {"source": {"kind": "file", "file_id": "file_1", "name": "фото.png"}},
    },
    {"op": "background", "background": {"kind": "solid", "color": "#F5F7FA"}},
    {
        "op": "background",
        "background": {
            "kind": "image",
            "source": {"kind": "package", "asset_id": "img_1"},
            "fit": "cover",
        },
    },
    {"op": "background", "background": {"kind": "inherited"}},
    {"op": "delete", "target": {"object_id": "12", "slot_id": "body_3"}},
    {
        "op": "add_text",
        "target": {"object_id": "new_caption"},
        "text": "Источник: отчёт 2024",
        "geometry": {"bbox": {"x": 0.05, "y": 0.9, "width": 0.5, "height": 0.05}},
        "style": {"font": {"size_pt": 10}},
    },
    {"op": "style", "target": {"object_id": "new_caption"}, "style": {"font": {"italic": True}}},
]


def document(ops: list[dict[str, Any]]) -> dict[str, Any]:
    base = {"kind": "variant", "job_id": "job_1", "variant_id": "balanced", "revision": 2}
    return {"schema_version": "1.0", "base": base, "ops": ops}


def test_example_is_valid() -> None:
    chat_ops.validate(json.loads((ROOT / "contracts/examples/chat_ops.example.json").read_text()))


def test_every_override_becomes_valid_operations_and_back_without_loss() -> None:
    slide = {"slide_id": "s2"}
    ops = [op for override in OVERRIDES for op in chat_ops.ops_from_override(override, slide)]
    chat_ops.validate(document(ops))
    assert chat_ops.overrides_from_ops(ops) == OVERRIDES
    for override in OVERRIDES:
        single = chat_ops.ops_from_override(override, slide)
        assert chat_ops.overrides_from_ops(single) == [override]
    sources = {op["op"]: op["source"] for op in ops}
    assert sources["text.set"] == "package"  # текст со ссылкой на факт пакета
    assert sources["style.size"] == "template"
    assert chat_ops.ops_from_override(OVERRIDES[5], slide)[0]["source"] == "attachment"


def test_slide_patch_example_is_expressed_by_operations() -> None:
    patch = json.loads((ROOT / "contracts/examples/slide_patch.example.json").read_text())
    ops = chat_ops.ops_from_patch(patch)
    chat_ops.validate(document(ops))
    names = [op["op"] for op in ops]
    assert names[-1] == "deck.move" and ops[-1]["args"]["order"] == patch["order"]
    for item in patch["slides"]:
        mine = [op for op in ops if op["address"].get("slide") == {"slide_id": item["slide_id"]}]
        assert chat_ops.overrides_from_ops(mine) == item["overrides"]
    logo = chat_ops.ops_from_patch({**patch, "template_logo": "drop"})[-1]
    assert logo["op"] == "deck.logo" and logo["args"] == {"action": "drop"}


def test_lone_move_takes_the_size_from_the_snapshot() -> None:
    snapshot = deck_snapshot(build_deck())
    slide = snapshot["slides"][0]
    obj = next(o for o in slide["objects"] if (o.get("text") or {}).get("plain") == "Повёрнут")
    ref = {"index": 1, "sld_id": slide["sld_id"]}
    move = {
        "op": "object.move",
        "address": {
            "scope": "object",
            "slide": ref,
            "object": {"object_id": obj["address"]["object_id"]},
        },
        "source": "snapshot",
        "confirm": False,
        "args": {"x": 0.5, "y": 0.4},
    }
    [override] = chat_ops.overrides_from_ops([move], snapshot)
    assert override["geometry"]["bbox"] == {
        "x": 0.5,
        "y": 0.4,
        "width": obj["bbox"]["width"],
        "height": obj["bbox"]["height"],
    }


@pytest.mark.parametrize(
    "op",
    [
        {"op": "object.move", "args": {"place": "right"}},
        {"op": "style.size", "args": {"step": "larger"}},
        {"op": "table.add_row", "args": {}},
        {"op": "chart.set_type", "args": {"chart_type": "line"}},
    ],
)
def test_operations_outside_the_plan_are_named_not_guessed(op: dict[str, Any]) -> None:
    full = {
        **op,
        "address": {"scope": "object", "slide": {"slide_id": "s1"}, "object": {"object_id": "3"}},
        "source": "snapshot",
        "confirm": False,
    }
    with pytest.raises(chat_ops.NotPlanExpressibleError):
        chat_ops.overrides_from_ops([full])


def test_addresses_are_checked_against_the_snapshot() -> None:
    snapshot = deck_snapshot(build_deck())
    first, second = snapshot["slides"]
    nested = next(o for o in first["objects"] if (o.get("text") or {}).get("plain") == "В группе")
    table = next(o for o in second["objects"] if o["kind"] == "table")
    good = [
        {
            "op": "text.set",
            "address": {
                "scope": "object",
                "slide": {"index": 1, "sld_id": first["sld_id"]},
                "object": nested["address"],
            },
            "source": "chat",
            "confirm": False,
            "args": {"text": "Новый текст"},
        },
        {
            "op": "table.set_cell",
            "address": {
                "scope": "cell",
                "slide": {"index": 2},
                "object": table["address"],
                "cell": {"row": 2, "col": 3},
            },
            "source": "chat",
            "confirm": False,
            "args": {"value": "+6%"},
        },
    ]
    doc = document(good)
    chat_ops.validate(doc)
    assert chat_ops.check_addresses(doc, snapshot) == []
    bad = copy.deepcopy(good)
    bad[0]["address"]["object"] = {**nested["address"], "group_path": []}
    bad[1]["address"]["cell"] = {"row": 4, "col": 1}
    bad.append(
        {**good[0], "address": {"scope": "slide", "slide": {"index": 7}}, "op": "text.set_notes"}
    )
    problems = chat_ops.check_addresses(document(bad), snapshot)
    assert len(problems) == 3
    assert "нет объекта" in problems[0] and "нет ячейки 4×1" in problems[1]
    assert "нет слайда 7" in problems[2]


def test_schema_rejects_wrong_address_and_arguments() -> None:
    op = {
        "op": "text.set",
        "address": {
            "scope": "cell",
            "slide": {"index": 1},
            "object": {"object_id": "3"},
            "cell": {"row": 1, "col": 1},
        },
        "source": "snapshot",
        "confirm": False,
        "args": {"text": "x"},
    }
    with pytest.raises(ValueError, match="Операции не по схеме"):
        chat_ops.validate(document([op]))
    with pytest.raises(ValueError):
        chat_ops.validate(
            document(
                [
                    {
                        **op,
                        "address": {
                            "scope": "object",
                            "slide": {"index": 1},
                            "object": {"object_id": "3"},
                        },
                        "args": {},
                    }
                ]
            )
        )
    with pytest.raises(ValueError):
        chat_ops.validate(document([{**op, "op": "slide.explode"}]))
