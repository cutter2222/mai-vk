"""Детерминированные проверки: опознание постоянных элементов и подложка под текстом.

Проверки работают по ComposedDeck и профилю шаблона, поэтому в тестах — минимальные колоды
из одного слайда: так видно, что именно решает исход, без фикстур на десятки мегабайт.
"""

from __future__ import annotations

from typing import Any

from presentation_designer.audit.deterministic import Context, check_contrast, check_fixed_elements

JsonDict = dict[str, Any]


def _profile(**tokens: Any) -> JsonDict:
    """Профиль с колонтитулом и номером страницы внизу слайда."""
    return {
        "design_tokens": {
            "colors": {"palette": [{"hex": "#FFFFFF", "role": "background"}], "theme": {}},
            "typography": {"fonts": [], "scale": []},
            **tokens,
        },
        "layouts": [],
        "fixed_elements": [
            {
                "element_id": "fixed_1",
                "kind": "footer",
                "element_ref": "5",
                "bbox": {"x": 0.025, "y": 0.927, "width": 0.086, "height": 0.053},
            },
            {
                "element_id": "fixed_2",
                "kind": "page_number",
                "element_ref": "10",
                "bbox": {"x": 0.936, "y": 0.927, "width": 0.046, "height": 0.053},
            },
        ],
    }


def _slide(objects: list[JsonDict], background: JsonDict | None = None) -> JsonDict:
    return {
        "slide_id": "sld_1",
        "index": 0,
        "objects": objects,
        **({"background": background} if background else {}),
    }


def _obj(object_id: str, bbox: tuple[float, float, float, float], **extra: Any) -> JsonDict:
    x, y, w, h = bbox
    return {
        "object_id": object_id,
        "source_object_id": object_id,
        "kind": "text",
        "role": "fixed",
        "content_source": "template",
        "bbox": {"x": x, "y": y, "width": w, "height": h},
        **extra,
    }


def test_card_with_same_number_as_master_shape_is_not_a_footer() -> None:
    """Номер фигуры на слайде и в мастере совпадают случайно: это разные части пакета.

    Карточка композиции размером 0,19 × 0,27 не может быть колонтитулом размером 0,09 × 0,05,
    и в отчёт она попадать не должна.
    """
    slide = _slide([_obj("10", (0.32, 0.262, 0.194, 0.274), name="Скругленный прямоугольник 9")])
    issues = check_fixed_elements(slide, Context(deck={"slides": [slide]}, profile=_profile()))
    assert issues == []


def test_moved_page_number_is_reported() -> None:
    slide = _slide([_obj("7", (0.5, 0.5, 0.046, 0.053), name="Номер слайда")])
    issues = check_fixed_elements(slide, Context(deck={"slides": [slide]}, profile=_profile()))
    assert [i.check_id for i in issues] == ["template.fixed_element_moved"]
    assert "Номер страницы" in issues[0].message


def test_page_number_in_place_passes() -> None:
    slide = _slide([_obj("7", (0.936, 0.927, 0.046, 0.053), name="Номер слайда")])
    assert check_fixed_elements(slide, Context(deck={"slides": [slide]}, profile=_profile())) == []


def test_row_of_equal_shapes_is_not_a_moved_element() -> None:
    """Ряд одинаковых иконок: какая из них постоянный элемент шаблона — определить нечем."""
    objects = [
        _obj(str(n), (0.1 + n * 0.05, 0.4, 0.046, 0.053), name=f"Иконка {n}") for n in range(4)
    ]
    slide = _slide(objects)
    assert check_fixed_elements(slide, Context(deck={"slides": [slide]}, profile=_profile())) == []


def _text(
    object_id: str, color: str, z: int, bbox: tuple[float, float, float, float], **extra: Any
) -> JsonDict:
    x, y, w, h = bbox
    return {
        "object_id": object_id,
        "kind": "text",
        "role": "content",
        "content_source": "sample",
        "z_order": z,
        "bbox": {"x": x, "y": y, "width": w, "height": h},
        "text": {
            "plain": "Коротко о решении",
            "paragraphs": [
                {
                    "text": "Коротко о решении",
                    "style": {"font": {"family": "Montserrat", "size_pt": 20.0, "color": color}},
                }
            ],
        },
        **extra,
    }


def test_title_on_branded_plate_has_contrast() -> None:
    """Плашкой под заголовком бывает фигура с текстом: её заливку и надо брать за фон.

    Пока такие фигуры пропускались, белый заголовок сверялся с белым фоном слайда.
    """
    plate = {
        "object_id": "2",
        "kind": "text",
        "role": "fixed",
        "content_source": "template",
        "z_order": 1,
        "bbox": {"x": 0.07, "y": 0.05, "width": 0.6, "height": 0.09},
        "fill": {"kind": "solid", "color": "#520977"},
    }
    slide = _slide([plate, _text("3", "#FFFFFF", 2, (0.1, 0.066, 0.596, 0.066))])
    assert check_contrast(slide, Context(deck={"slides": [slide]}, profile=_profile())) == []


def test_contrast_is_not_judged_over_background_image() -> None:
    """Фон-картинка одним цветом не описывается: это работа контекстной части аудита."""
    slide = _slide(
        [_text("3", "#F2F2F2", 2, (0.04, 0.07, 0.81, 0.05))],
        background={"kind": "image", "asset_id": "media_1"},
    )
    assert check_contrast(slide, Context(deck={"slides": [slide]}, profile=_profile())) == []


def test_low_contrast_on_solid_background_is_reported() -> None:
    slide = _slide(
        [_text("3", "#EEEEEE", 2, (0.04, 0.07, 0.81, 0.05))],
        background={"kind": "solid", "color": "#FFFFFF"},
    )
    issues = check_contrast(slide, Context(deck={"slides": [slide]}, profile=_profile()))
    assert [i.check_id for i in issues] == ["template.contrast"]
