"""Фото к текстовым слайдам: выбор слайдов, замена композиции, отбор снимков. Без сети."""

from __future__ import annotations

import itertools
import json
import pathlib
from typing import Any

from presentation_designer.design import photos
from presentation_designer.library.register import pattern_id_for
from presentation_designer.parsing.content import stock


def _profile() -> dict[str, Any]:
    ids = [
        "statement",
        "bullets_pane@cols=1",
        "bullets_pane@cols=2",
        "cards_grid@cols=3,numbered=False,rows=1",
        "image_split@bullets=True,side=right",
        "image_split@bullets=True,side=left",
        "image_split@bullets=False,side=right",
        "image_split@bullets=False,side=left",
    ]
    return {
        "patterns": [
            {"pattern_id": pattern_id_for(c), "source": {"kind": "builtin", "composition_id": c}}
            for c in ids
        ]
    }


def _statement(sid: str, text: str) -> dict[str, Any]:
    return {
        "slide_id": sid,
        "role": "text",
        "title": f"Слайд {sid}",
        "pattern_id": pattern_id_for("statement"),
        "blocks": [
            {"slot_id": "title", "kind": "title", "text": f"Слайд {sid}"},
            {"slot_id": "statement", "kind": "body", "text": text, "fact_refs": ["f1"]},
            {"slot_id": "support", "kind": "caption", "text": "Подпись", "fact_refs": ["f2"]},
        ],
    }


def _plan(count: int) -> dict[str, Any]:
    slides = [{"slide_id": "s0", "role": "title", "title": "Умные остановки", "blocks": []}]
    slides += [_statement(f"s{i}", "Короткая мысль слайда") for i in range(1, count + 1)]
    return {"slides": slides}


def test_candidates_are_limited_and_not_three_in_a_row() -> None:
    plan = _plan(9)
    chosen = photos.candidates(plan, _profile(), "balanced")
    assert len(chosen) == 4, "две пятых содержательных слайдов"
    for i in chosen:
        assert not {i - 1, i - 2} <= set(chosen)


def test_long_text_and_foreign_patterns_are_not_candidates() -> None:
    plan = _plan(2)
    plan["slides"][1]["blocks"][1]["text"] = "очень длинный текст " * 40
    plan["slides"][2]["pattern_id"] = pattern_id_for("cards_grid@cols=3,numbered=False,rows=1")
    assert photos.candidates(plan, _profile(), "balanced") == []


def test_swap_keeps_title_text_and_references() -> None:
    slide = _statement("s1", "Мысль")
    out = photos.swap_to_photo(slide, "statement", "stock_abc", "left")
    assert out["pattern_id"] == pattern_id_for("image_split@bullets=False,side=left")
    by_slot = {b["slot_id"]: b for b in out["blocks"]}
    assert by_slot["title"]["text"] == "Слайд s1"
    assert by_slot["image"]["image"]["asset_id"] == "stock_abc"
    assert by_slot["body"]["text"] == "Мысль\nПодпись", "подпись не теряется"
    assert by_slot["body"]["fact_refs"] == ["f1", "f2"]


def test_two_bullet_columns_merge_into_one_list() -> None:
    slide = {
        "slide_id": "s1",
        "title": "Итоги",
        "pattern_id": pattern_id_for("bullets_pane@cols=2"),
        "blocks": [
            {"slot_id": "title", "kind": "title", "text": "Итоги"},
            {"slot_id": "bullets_1", "kind": "bullets", "items": [{"text": "А"}, {"text": "Б"}]},
            {"slot_id": "bullets_2", "kind": "bullets", "items": [{"text": "В"}]},
        ],
    }
    out = photos.swap_to_photo(slide, "bullets_pane@cols=2", "stock_x", "right")
    body = next(b for b in out["blocks"] if b["slot_id"] == "body")
    assert body["kind"] == "bullets"
    assert [i["text"] for i in body["items"]] == ["А", "Б", "В"]


class _Photo:
    def __init__(self, asset_id: str) -> None:
        self.asset_id = asset_id


def test_attach_places_photos_alternating_sides_without_repeats() -> None:
    plan = _plan(9)
    asked: list[str] = []

    def ask(user: str) -> str:
        asked.append(user)
        ids = [
            line.split("slide_id: ")[1].split(";")[0]
            for line in user.splitlines()
            if "slide_id" in line
        ]
        return json.dumps({"photos": [{"slide_id": i, "query": "bus stop"} for i in ids]})

    returned = iter(["stock_1", "stock_1", "stock_2"])

    def find(query: str, exclude: set[str]) -> Any:
        return _Photo(next(returned))

    out, report = photos.attach_photos(plan, _profile(), variant="balanced", ask=ask, find=find)
    assert "Умные остановки" in asked[0], "модель знает тему презентации"
    placed = [p["slide"] for p in report["placed"]]
    assert report["candidates"] == 4
    assert len(placed) == 2, "одно фото дважды не ставится"
    sides = [
        s["pattern_id"].rsplit("side", 1)[1]
        for s in out["slides"]
        if s.get("pattern_id", "").startswith("pat_builtin_image_split")
    ]
    assert sides[0] == "right" and all(a != b for a, b in itertools.pairwise(sides))


def test_attach_survives_model_failure() -> None:
    plan = _plan(3)

    def ask(user: str) -> str:
        raise RuntimeError("модель недоступна")

    out, report = photos.attach_photos(
        plan, _profile(), variant="balanced", ask=ask, find=lambda q, e: None
    )
    assert out is plan and report["placed"] == [] and "error" in report


def test_relevance_needs_most_query_words() -> None:
    tagged = {"title": "Table setting with flowers", "tags": [{"name": "dinner"}]}
    assert not stock._relevant(tagged, {"smartphone", "notifications", "settings"})
    phone = {"title": "Smartphone notifications", "tags": [{"name": "phone"}]}
    assert stock._relevant(phone, {"smartphone", "notifications", "settings"})


def test_loaded_stock_assets_are_package_assets(tmp_path: pathlib.Path) -> None:
    meta = tmp_path / "stock" / "assets"
    meta.mkdir(parents=True)
    asset = {"asset_id": "stock_1", "kind": "photo", "path": "stock/a.jpg", "sha256": "0" * 64}
    (meta / "stock_1.json").write_text(json.dumps(asset))
    (meta / "broken.json").write_text("{")
    assert stock.load_assets(tmp_path) == {"stock_1": asset}
    assert stock.load_assets(None) == {}
