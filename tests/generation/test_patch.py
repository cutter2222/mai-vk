"""Ручные правки редактора → план (этап 22): замена и сброс списков overrides, перестановка
слайдов, проверки против ComposedDeck и плана, сводка, сброс правок при правке из чата."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from presentation_designer.generation import edit as ed
from presentation_designer.generation import patch as pt
from tests.generation.test_edit import (  # noqa: F401
    _answer,
    _content_index,
    _edit,
    _valid,
    base_plan,
)
from tests.generation.test_variants import (  # noqa: F401
    example_package,
    example_story,
    mini_profile,
)

pytestmark = pytest.mark.usefixtures("mini_profile")


def _deck_for(plan: dict[str, Any]) -> dict[str, Any]:
    """Описание собранной колоды в объёме, нужном патчу: объекты по слайдам."""
    slides = []
    for i, s in enumerate(sorted(plan["slides"], key=lambda x: x["order"])):
        objects = [
            {
                "object_id": "2",
                "kind": "text",
                "bbox": {"x": 0.05, "y": 0.05, "width": 0.9, "height": 0.1},
                "z_order": 1,
                "slot_id": "title_1",
                "slot_kind": "title",
                "source_object_id": "2",
            },
            {
                "object_id": "7",
                "kind": "picture",
                "bbox": {"x": 0.7, "y": 0.5, "width": 0.2, "height": 0.3},
                "z_order": 2,
            },
            {
                "object_id": "9",
                "kind": "connector",
                "bbox": {"x": 0.1, "y": 0.5, "width": 0.4, "height": 0.0},
                "z_order": 3,
            },
        ]
        slides.append(
            {"slide_id": s["slide_id"], "index": i, "layout_id": "l1", "objects": objects}
        )
    return {"slides": slides}


def _patch(plan: dict[str, Any], slides: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "job_id": "job_test",
        "variant_id": plan["variant"]["variant_id"],
        "base_revision": 1,
        "slides": slides,
        **extra,
    }


def test_apply_replaces_and_resets_overrides(
    base_plan: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    example_story: dict[str, Any],  # noqa: F811
) -> None:
    ordered = sorted(base_plan["slides"], key=lambda s: s["order"])
    first, second = ordered[0]["slide_id"], ordered[1]["slide_id"]
    plan = copy.deepcopy(base_plan)
    for s in plan["slides"]:
        if s["slide_id"] == second:
            s["overrides"] = [
                {"op": "background", "background": {"kind": "solid", "color": "#000000"}}
            ]
    deck = _deck_for(plan)
    overrides = [
        {"op": "text", "target": {"object_id": "2", "slot_id": "title_1"}, "text": "Новый"},
        {
            "op": "style",
            "target": {"object_id": "2"},
            "style": {"font": {"size_pt": 28, "bold": True}},
        },
        {"op": "style", "target": {"object_id": "2"}, "style": {"font": {"size_pt": 30}}},
        {
            "op": "geometry",
            "target": {"object_id": "7"},
            "geometry": {"bbox": {"x": 0.6, "y": 0.5, "width": 0.3, "height": 0.3}},
        },
    ]
    result = pt.apply_patch(
        plan,
        _patch(
            plan,
            [{"slide_id": first, "overrides": overrides}, {"slide_id": second, "overrides": []}],
        ),
        deck,
        mini_profile,
        example_package,
        example_story,
    )
    assert result.changed_slide_ids == [first, second]
    by_id = {s["slide_id"]: s for s in result.plan["slides"]}
    applied = by_id[first]["overrides"]
    assert [o["op"] for o in applied] == ["text", "style", "geometry"], (
        "последний стиль объекта победил"
    )
    assert applied[1]["style"]["font"] == {"size_pt": 30}
    assert "overrides" not in by_id[second], "пустой список — сброс"
    assert by_id[first]["revision_note"].startswith(
        "Слайд 1: заголовок: текст; заголовок: кегль 30"
    )
    assert by_id[second]["revision_note"] == "Слайд 2: сброс правок"
    assert "Слайд 1" in result.summary and "Слайд 2: сброс правок" in result.summary
    # Остальное не тронуто.
    untouched = [s for s in result.plan["slides"] if s["slide_id"] not in (first, second)]
    original = {s["slide_id"]: s for s in base_plan["slides"]}
    assert all(s == original[s["slide_id"]] for s in untouched)
    assert result.plan["generation_meta"]["created_at"] != base_plan["generation_meta"].get(
        "created_at"
    )
    _valid(result.plan, mini_profile, example_package, example_story)
    assert result.report["patch_version"] == pt.PATCH_VERSION


def test_reorder_moves_slides(
    base_plan: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    example_story: dict[str, Any],  # noqa: F811
) -> None:
    ids = [s["slide_id"] for s in sorted(base_plan["slides"], key=lambda s: s["order"])]
    new_order = [ids[0], *ids[2:4], ids[1], *ids[4:]]
    result = pt.apply_patch(
        base_plan,
        _patch(base_plan, [], order=new_order),
        _deck_for(base_plan),
        mini_profile,
        example_package,
        example_story,
    )
    got = [s["slide_id"] for s in sorted(result.plan["slides"], key=lambda s: s["order"])]
    assert got == new_order
    assert [s["order"] for s in result.plan["slides"]] == list(range(1, len(ids) + 1))
    assert set(result.changed_slide_ids) == {ids[1], ids[2], ids[3]}
    assert result.plan["comparison"]["pattern_sequence"] == [
        next(s["pattern_id"] for s in base_plan["slides"] if s["slide_id"] == sid)
        for sid in new_order
    ]
    assert result.summary == "порядок слайдов изменён"
    _valid(result.plan, mini_profile, example_package, example_story)
    with pytest.raises(pt.PatchError) as err:
        pt.apply_patch(
            base_plan,
            _patch(base_plan, [], order=ids[:-1]),
            _deck_for(base_plan),
            mini_profile,
            example_package,
        )
    assert err.value.code == "patch_invalid" and "order_invalid" in str(err.value)


def test_validation_against_deck_and_plan(
    base_plan: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
) -> None:
    first = sorted(base_plan["slides"], key=lambda s: s["order"])[0]["slide_id"]
    deck = _deck_for(base_plan)
    cases = [
        ([{"slide_id": "s_nope", "overrides": []}], "slide_unknown"),
        (
            [
                {
                    "slide_id": first,
                    "overrides": [{"op": "text", "target": {"object_id": "404"}, "text": "x"}],
                }
            ],
            "object_unknown",
        ),
        (
            [
                {
                    "slide_id": first,
                    "overrides": [{"op": "text", "target": {"object_id": "7"}, "text": "x"}],
                }
            ],
            "override_unsupported",
        ),
        (
            [
                {
                    "slide_id": first,
                    "overrides": [
                        {
                            "op": "geometry",
                            "target": {"object_id": "9"},
                            "geometry": {"bbox": {"x": 0, "y": 0, "width": 0.1, "height": 0.1}},
                        }
                    ],
                }
            ],
            "override_unsupported",
        ),
        (
            [
                {
                    "slide_id": first,
                    "overrides": [
                        {
                            "op": "picture",
                            "target": {"object_id": "7"},
                            "picture": {"source": {"kind": "template", "asset_id": "asset_nope"}},
                        }
                    ],
                }
            ],
            "asset_missing",
        ),
        (
            [
                {
                    "slide_id": first,
                    "overrides": [
                        {
                            "op": "style",
                            "target": {"object_id": "2"},
                            "style": {"font": {"size_pt": 3}},
                        }
                    ],
                }
            ],
            "size_pt",
        ),
    ]
    for slides, code in cases:
        with pytest.raises(Exception) as err:
            pt.apply_patch(
                base_plan, _patch(base_plan, slides), deck, mini_profile, example_package
            )
        assert code in str(err.value), (code, str(err.value)[:200])
    with pytest.raises(pt.PatchError) as empty:
        pt.apply_patch(base_plan, _patch(base_plan, []), deck, mini_profile, example_package)
    assert empty.value.code == "patch_empty"
    # Без описания колоды объекты не проверяются, но план — да.
    result = pt.apply_patch(
        base_plan,
        _patch(
            base_plan,
            [
                {
                    "slide_id": first,
                    "overrides": [{"op": "text", "target": {"object_id": "404"}, "text": "x"}],
                }
            ],
        ),
        None,
        mini_profile,
        example_package,
    )
    assert result.changed_slide_ids == [first]


def test_delete_and_add_text(
    base_plan: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
) -> None:
    """Удаление объекта и своя надпись: адрес, обязательные поля, сводка."""
    first = sorted(base_plan["slides"], key=lambda s: s["order"])[0]["slide_id"]
    deck = _deck_for(base_plan)
    add = {
        "op": "add_text",
        "target": {"object_id": "usr_1"},
        "text": "Своя строка",
        "geometry": {"bbox": {"x": 0.2, "y": 0.4, "width": 0.4, "height": 0.1}},
    }
    ok = [
        {
            "slide_id": first,
            "overrides": [
                add,
                {"op": "text", "target": {"object_id": "usr_1"}, "text": "Правка своей строки"},
                {"op": "delete", "target": {"object_id": "9"}},
            ],
        }
    ]
    patch = {"job_id": "job_1", "variant_id": "compact", "base_revision": 1, "slides": ok}
    assert pt.validate_patch(patch, base_plan, deck, mini_profile, example_package) == []
    result = pt.apply_patch(base_plan, patch, deck, mini_profile, example_package)
    assert "новая надпись «Своя строка»" in result.summary
    assert "удалён" in result.summary
    changed = next(s for s in result.plan["slides"] if s["slide_id"] == first)
    assert [o["op"] for o in changed["overrides"]] == ["add_text", "text", "delete"]

    def codes(overrides: list[dict[str, Any]]) -> list[str]:
        bad = {"job_id": "job_1", "variant_id": "compact", "base_revision": 1}
        bad["slides"] = [{"slide_id": first, "overrides": overrides}]
        found = pt.validate_patch(bad, base_plan, deck, mini_profile, example_package)
        return [v.code for v in found]

    # своя надпись без текста и без рамки
    assert codes([{"op": "add_text", "target": {"object_id": "usr_2"}, "text": " "}]) == [
        "text_empty",
        "bbox_missing",
    ]
    # чужой адрес занимать нельзя
    assert "object_exists" in codes([{**add, "target": {"object_id": "2"}}])
    # картинка на свою надпись не ставится
    assert "override_unsupported" in codes(
        [
            add,
            {
                "op": "picture",
                "target": {"object_id": "usr_1"},
                "picture": {"source": {"kind": "template", "asset_id": "asset_logo"}},
            },
        ]
    )
    # удалять можно только существующий объект
    assert codes([{"op": "delete", "target": {"object_id": "404"}}]) == ["object_unknown"]
    # своя надпись без object_id не даёт другим правкам без адреса проскочить проверку
    assert codes(
        [
            {**add, "target": {}},
            {"op": "text", "target": {}, "text": "куда?"},
        ]
    ) == ["target_missing", "object_unknown"]


def test_template_logo_is_deck_wide(
    base_plan: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
) -> None:
    """Знак шаблона лежит на макетах, поэтому снимается флагом на всю колоду, а не правкой
    объекта: план получает template_logo, сводка это называет, повтор — уже ничего не меняет."""
    deck = _deck_for(base_plan)
    patch = {
        "job_id": "job_1",
        "variant_id": "compact",
        "base_revision": 1,
        "slides": [],
        "template_logo": "drop",
    }
    assert pt.validate_patch(patch, base_plan, deck, mini_profile, example_package) == []
    assert not pt.patch_is_noop(patch, base_plan)
    result = pt.apply_patch(base_plan, patch, deck, mini_profile, example_package)
    assert result.plan["template_logo"] == "drop"
    assert "логотип шаблона снят" in result.summary
    assert result.report["template_logo"] == "drop"
    # Повтор на новом плане ничего не меняет, а возврат знака — снова правка.
    assert pt.patch_is_noop(patch, result.plan)
    back = {**patch, "template_logo": "keep"}
    assert not pt.patch_is_noop(back, result.plan)
    assert (
        pt.apply_patch(result.plan, back, deck, mini_profile, example_package).plan["template_logo"]
        == "keep"
    )


def test_normalize_and_describe() -> None:
    raw = {
        "slides": [
            {
                "slide_id": "s1",
                "overrides": [
                    {"op": "text", "target": {"object_id": "2", "slot_id": None}, "text": "a"},
                    {"op": "text", "target": {"object_id": "2"}, "text": "b"},
                    {"op": "background", "background": {"kind": "inherited"}},
                    {
                        "op": "picture",
                        "target": {"object_id": "7"},
                        "picture": {
                            "source": {"kind": "file", "file_id": "f1", "name": "photo.png"},
                            "color": "#FF0000",
                        },
                    },
                ],
            },
            {"slide_id": "s1", "overrides": []},
            {
                "slide_id": "s2",
                "overrides": [
                    {
                        "op": "style",
                        "target": {"object_id": "2"},
                        "style": {"font": {"color": "#0077ff", "italic": True}, "align": "center"},
                    }
                ],
            },
        ],
        "order": ["s2", "s1"],
    }
    doc = pt.normalize_patch(raw)
    assert [e["slide_id"] for e in doc["slides"]] == ["s1", "s2"]
    assert doc["slides"][0]["overrides"] == [], "повторная запись слайда побеждает"
    plan = {"slides": [{"slide_id": "s1", "order": 1}, {"slide_id": "s2", "order": 2}]}
    deck = {
        "slides": [
            {
                "slide_id": "s1",
                "objects": [
                    {"object_id": "2", "kind": "text", "slot_kind": "title"},
                    {"object_id": "7", "kind": "picture"},
                ],
            },
            {"slide_id": "s2", "objects": [{"object_id": "2", "kind": "text", "name": "Подпись"}]},
        ]
    }
    text = pt.describe_patch(pt.normalize_patch({"slides": raw["slides"][:1]}), deck, plan)
    expected = (
        "Слайд 1: заголовок: текст; фон как в макете; "
        "картинка: картинка своя (photo.png), перекраска"
    )
    assert text == expected
    text2 = pt.describe_patch(doc, deck, plan)
    assert "Слайд 1: сброс правок" in text2 and "текст: курсив, цвет #0077FF, по центру" in text2
    assert text2.endswith("порядок слайдов изменён")


def test_chat_edit_drops_manual_overrides(
    base_plan: dict[str, Any],  # noqa: F811
    example_story: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
    make_client: Any,
    stub: Any,
) -> None:
    """Правка из чата пересобирает слайд моделью: его ручные правки сбрасываются с
    предупреждением, правки соседних слайдов остаются."""
    index = _content_index(base_plan)
    ordered = ed.ordered_slides(base_plan)
    target, neighbour = ordered[index], ordered[(index + 1) % len(ordered)]
    plan = copy.deepcopy(base_plan)
    bg = {"op": "background", "background": {"kind": "solid", "color": "#000000"}}
    for s in plan["slides"]:
        if s["slide_id"] in (target["slide_id"], neighbour["slide_id"]):
            s["overrides"] = [bg]
    stub.answer(_answer(target))
    result = _edit(
        plan,
        index,
        "сделай заголовок короче",
        make_client(stub),
        story=example_story,
        profile=mini_profile,
        package=example_package,
    )
    assert result.changed and result.plan is not None
    by_id = {s["slide_id"]: s for s in result.plan["slides"]}
    assert "overrides" not in by_id[target["slide_id"]]
    assert by_id[neighbour["slide_id"]]["overrides"] == [bg]
    dropped = [w for w in result.plan["warnings"] if w["code"] == "overrides_dropped"]
    assert len(dropped) == 1 and "1 ручных правок" in dropped[0]["message"]
    assert ed.EDIT_VERSION == "0.1.2"


def test_old_plan_schema_is_upgraded(
    base_plan: dict[str, Any],  # noqa: F811
    mini_profile: dict[str, Any],  # noqa: F811
    example_package: dict[str, Any],  # noqa: F811
) -> None:
    """План ревизии, собранной до контрактов 1.9 (schema 1.2), правится как текущий."""
    from presentation_designer.generation.variants import PLAN_SCHEMA_VERSION, upgrade_plan_schema

    old = {**copy.deepcopy(base_plan), "schema_version": "1.2"}
    assert upgrade_plan_schema(old)["schema_version"] == PLAN_SCHEMA_VERSION
    assert upgrade_plan_schema({**old, "schema_version": "0.9"})["schema_version"] == "0.9"
    first = sorted(old["slides"], key=lambda s: s["order"])[0]["slide_id"]
    result = pt.apply_patch(
        old,
        _patch(
            old,
            [
                {
                    "slide_id": first,
                    "overrides": [{"op": "background", "background": {"kind": "inherited"}}],
                }
            ],
        ),
        _deck_for(old),
        mini_profile,
        example_package,
    )
    assert result.plan["schema_version"] == PLAN_SCHEMA_VERSION
