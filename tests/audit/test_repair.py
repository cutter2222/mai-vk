"""Исправления по находкам: что код чинит сам, а где честно отступает.

Проверяется договор слоя: на входе отчёт аудита и выбранные находки, на выходе — правки в том
же виде, что у ручного редактора (`overrides` слайда), и судьба каждой находки. Там, где
правильных ответов много (переставить композицию, переписать текст), правка не выдумывается:
находка остаётся с причиной, иначе «исправлено» означало бы «сделано наугад».
"""

from __future__ import annotations

from typing import Any

from presentation_designer.audit.repair import build_repair

JsonDict = dict[str, Any]


def _profile(**tokens: Any) -> JsonDict:
    return {
        "design_tokens": {
            "colors": {"palette": [{"hex": "#1B1B1B"}, {"hex": "#E4002B"}], "theme": {}},
            "typography": {
                "fonts": [{"family": "Montserrat"}, {"family": "Golos Text"}],
                "scale": [{"size_pt": 40}, {"size_pt": 24}, {"size_pt": 18}, {"size_pt": 14}],
            },
            "spacing": {"margins": {"left": 0.05, "top": 0.05, "right": 0.05, "bottom": 0.05}},
            **tokens,
        },
        "layouts": [],
        "fixed_elements": [],
    }


def _obj(object_id: str, bbox: tuple[float, float, float, float], **extra: Any) -> JsonDict:
    x, y, w, h = bbox
    font = extra.pop("font", None)
    obj: JsonDict = {
        "object_id": object_id,
        "source_object_id": object_id,
        "kind": "text",
        "role": "content",
        "bbox": {"x": x, "y": y, "width": w, "height": h},
        **extra,
    }
    if font is not None:
        obj["text"] = {"plain": "Текст", "computed_style": {"font": font}}
    return obj


def _deck(objects: list[JsonDict]) -> JsonDict:
    return {"slides": [{"slide_id": "s1", "index": 0, "objects": objects}]}


def _report(*issues: JsonDict) -> JsonDict:
    return {"revision": 1, "issues": list(issues)}


def _issue(issue_id: str, check_id: str, element: str, **extra: Any) -> JsonDict:
    return {
        "issue_id": issue_id,
        "check_id": check_id,
        "slide_index": 0,
        "element_ids": [element],
        "severity": "error",
        "fix": {"available": True},
        **extra,
    }


def test_object_outside_the_canvas_returns_to_it() -> None:
    deck = _deck([_obj("10", (0.92, 0.3, 0.2, 0.1))])
    plan = build_repair(
        _report(_issue("iss_1", "layout.out_of_bounds", "10")), ["iss_1"], deck, _profile()
    )

    assert plan.applied == ["iss_1"]
    (slide,) = plan.patch["slides"]
    (override,) = slide["overrides"]
    assert override["op"] == "geometry"
    assert override["target"] == {"object_id": "10", "source_object_id": "10"}
    assert override["geometry"]["bbox"] == {"x": 0.8, "y": 0.3, "width": 0.2, "height": 0.1}


def test_text_in_the_margin_moves_inside() -> None:
    deck = _deck([_obj("11", (0.01, 0.5, 0.3, 0.1))])
    plan = build_repair(
        _report(_issue("iss_1", "layout.in_margins", "11")), ["iss_1"], deck, _profile()
    )

    bbox = plan.patch["slides"][0]["overrides"][0]["geometry"]["bbox"]
    assert bbox["x"] == 0.05, "объект вернулся в поле шаблона"


def test_template_only_repair_preserves_geometry() -> None:
    deck = _deck([_obj("10", (0.92, 0.3, 0.2, 0.1))])
    result = build_repair(
        _report(_issue("iss_1", "layout.out_of_bounds", "10")),
        ["iss_1"],
        deck,
        _profile(),
        settings={"design_mode": "template_only"},
    )
    assert result.applied == []
    assert "Разделите слайд" in result.fixes[0].note
    assert deck["slides"][0]["objects"][0]["bbox"]["x"] == 0.92


def test_size_snaps_to_the_template_scale() -> None:
    deck = _deck([_obj("12", (0.1, 0.1, 0.5, 0.2), font={"size_pt": 31, "family": "Montserrat"})])
    issue = _issue(
        "iss_1",
        "template.size_not_in_scale",
        "12",
        evidence={"measured": 31, "threshold": [40, 24, 18]},
    )
    plan = build_repair(_report(issue), ["iss_1"], deck, _profile())

    override = plan.patch["slides"][0]["overrides"][0]
    assert override["op"] == "style"
    assert override["style"]["font"] == {"size_pt": 24.0}, "ближайшая ступень шкалы, а не любая"


def test_foreign_font_becomes_the_template_one() -> None:
    deck = _deck(
        [
            _obj(
                "13",
                (0.1, 0.1, 0.5, 0.2),
                slot_kind="title",
                font={"family": "Arial", "size_pt": 40},
            ),
            _obj("14", (0.1, 0.4, 0.5, 0.2), font={"family": "Arial", "size_pt": 18}),
        ]
    )
    report = _report(
        _issue("iss_1", "template.font_not_in_template", "13"),
        _issue("iss_2", "template.font_not_in_template", "14"),
    )
    plan = build_repair(report, ["iss_1", "iss_2"], deck, _profile())

    families = [o["style"]["font"]["family"] for o in plan.patch["slides"][0]["overrides"]]
    assert families == ["Montserrat", "Golos Text"], (
        "заголовку заголовочная гарнитура, тексту текстовая"
    )


def test_colour_snaps_to_the_nearest_palette_entry() -> None:
    deck = _deck([_obj("15", (0.1, 0.1, 0.5, 0.2), font={"color": "#FF0040", "size_pt": 18})])
    plan = build_repair(
        _report(_issue("iss_1", "template.color_not_in_palette", "15")), ["iss_1"], deck, _profile()
    )

    assert plan.patch["slides"][0]["overrides"][0]["style"]["font"] == {"color": "#E4002B"}


def test_overflow_steps_the_size_down_but_not_into_unreadable() -> None:
    deck = _deck([_obj("16", (0.1, 0.1, 0.5, 0.2), font={"size_pt": 24, "family": "Montserrat"})])
    plan = build_repair(
        _report(_issue("iss_1", "layout.text_overflow", "16")), ["iss_1"], deck, _profile()
    )
    assert plan.patch["slides"][0]["overrides"][0]["style"]["font"] == {"size_pt": 18.0}

    # У самой мелкой ступени шкалы отступать некуда: находка остаётся.
    small = _deck([_obj("17", (0.1, 0.1, 0.5, 0.2), font={"size_pt": 14, "family": "Montserrat"})])
    nothing = build_repair(
        _report(_issue("iss_1", "layout.text_overflow", "17")), ["iss_1"], small, _profile()
    )
    assert nothing.applied == [] and nothing.patch["slides"] == []


def test_ambiguous_findings_are_left_alone_with_a_reason() -> None:
    """Перекладка композиции, переписывание текста и смысловые находки кодом не решаются."""
    deck = _deck([_obj("18", (0.1, 0.1, 0.5, 0.2), font={"size_pt": 18})])
    report = _report(
        _issue("iss_1", "density.fill_ratio", "18"),
        _issue("iss_2", "content.facts_grounded", "18"),
        _issue("iss_3", "layout.overlap", "18"),
    )
    plan = build_repair(report, ["iss_1", "iss_2", "iss_3"], deck, _profile())

    assert plan.applied == [] and plan.patch["slides"] == []
    notes = {f.issue_id: f.note for f in plan.fixes}
    assert "композиция" in notes["iss_1"]
    assert "моделью" in notes["iss_2"]
    assert "перекладк" in notes["iss_3"]


def test_manual_edits_of_the_slide_survive_the_repair() -> None:
    """Правки пользователя на слайде не должны исчезнуть вместе с исправлением находки."""
    deck = _deck([_obj("19", (0.92, 0.3, 0.2, 0.1))])
    mine = {"op": "text", "target": {"object_id": "19"}, "text": {"plain": "Моя правка"}}
    plan = build_repair(
        _report(_issue("iss_1", "layout.out_of_bounds", "19")),
        ["iss_1"],
        deck,
        _profile(),
        {"slides": [{"slide_id": "s1", "overrides": [mine]}]},
    )

    overrides = plan.patch["slides"][0]["overrides"]
    assert overrides[0] == mine and overrides[1]["op"] == "geometry"


def test_unknown_issue_is_reported_not_ignored() -> None:
    plan = build_repair(_report(), ["iss_404"], _deck([]), _profile())
    assert plan.applied == []
    assert plan.fixes[0].note == "находки нет в отчёте базовой ревизии"


def test_moved_fixed_element_returns_to_the_template_place() -> None:
    """Знак шаблона возвращается на место из профиля, а не «примерно туда»."""
    deck = _deck([_obj("20", (0.5, 0.5, 0.086, 0.053), role="fixed")])
    issue = _issue(
        "iss_1",
        "template.fixed_element_moved",
        "20",
        evidence={
            "measured": 0.4,
            "threshold": 0.01,
            "place": {"x": 0.025, "y": 0.927, "width": 0.086, "height": 0.053},
        },
    )
    plan = build_repair(_report(issue), ["iss_1"], deck, _profile())

    bbox = plan.patch["slides"][0]["overrides"][0]["geometry"]["bbox"]
    assert bbox == {"x": 0.025, "y": 0.927, "width": 0.086, "height": 0.053}
