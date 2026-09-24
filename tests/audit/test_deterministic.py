"""Детерминированные проверки: опознание постоянных элементов и подложка под текстом.

Проверки работают по ComposedDeck и профилю шаблона, поэтому в тестах — минимальные колоды
из одного слайда: так видно, что именно решает исход, без фикстур на десятки мегабайт.
"""

from __future__ import annotations

from typing import Any

from presentation_designer.audit.deterministic import (
    Context,
    check_contrast,
    check_empty_slide,
    check_fill_ratio,
    check_fixed_elements,
)

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
    # Место из шаблона едет в улике: по нему исправление вернёт элемент, не угадывая.
    assert issues[0].evidence["place"] == {"x": 0.936, "y": 0.927, "width": 0.046, "height": 0.053}


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


def test_chart_is_never_a_moved_decoration() -> None:
    """Диаграмма готовой презентации — «статика образца», но не логотип и не декор макета,
    даже если её рамка того же размера, что декор на другом месте."""
    profile = _profile()
    profile["fixed_elements"].append(
        {
            "element_id": "fixed_3",
            "kind": "decoration",
            "element_ref": "40",
            "bbox": {"x": 0.42, "y": 0.03, "width": 0.52, "height": 0.93},
        }
    )
    chart = _obj("2380", (0.44, 0.1, 0.52, 0.93), kind="chart", name="Диаграмма (из фигур)")
    slide = _slide([chart])
    assert check_fixed_elements(slide, Context(deck={"slides": [slide]}, profile=profile)) == []


def test_slide_with_a_template_chart_is_neither_empty_nor_underfilled() -> None:
    """У готовой презентации весь слайд — статика образца, и диаграмма из фигур слайда тоже:
    слайд с ней не «пустой» и не «заполнен меньше четверти»."""
    title = _obj("2", (0.02, 0.05, 0.7, 0.14), name="Заголовок")
    chart = _obj("2380", (0.44, 0.1, 0.52, 0.8), kind="chart", name="Диаграмма (из фигур)")
    slide = _slide([title, chart])
    ctx = Context(deck={"slides": [slide]}, profile=_profile())
    found = {i.check_id for i in check_empty_slide(slide, ctx) + check_fill_ratio(slide, ctx)}
    assert "integrity.empty_slide" not in found
    assert "density.fill_ratio" not in found


def test_slide_without_content_is_empty() -> None:
    slide = _slide([_obj("2", (0.02, 0.05, 0.7, 0.14), name="Заголовок")])
    issues = check_empty_slide(slide, Context(deck={"slides": [slide]}, profile=_profile()))
    assert [(i.check_id, i.message) for i in issues] == [("integrity.empty_slide", "Слайд пустой")]


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


def _plain_slide(objects: list[JsonDict]) -> JsonDict:
    return {"slide_id": "sld_1", "index": 0, "objects": objects}


def test_moved_slot_object_is_checked_against_margins() -> None:
    """Объект слота стоит там, где его поставил автор шаблона, пока пользователь не сдвинул."""
    from presentation_designer.audit.deterministic import check_margins

    margins = {"left": 0.05, "right": 0.05, "top": 0.05, "bottom": 0.05}
    profile = _profile(spacing={"margins": margins})
    ctx = Context(deck={"slides": []}, profile=profile)
    box = {"x": 0.0, "y": 0.3, "width": 0.4, "height": 0.2}
    base = {"object_id": "7", "kind": "text", "role": "content", "bbox": box}
    inherited = {**base, "content_source": "plan", "source_object_id": "7"}
    assert check_margins(_plain_slide([inherited]), ctx) == []
    moved = {
        **inherited,
        "user_overrides": [
            {"op": "geometry", "target": {"object_id": "7"}, "geometry": {"bbox": box}}
        ],
    }
    assert [i.check_id for i in check_margins(_plain_slide([moved]), ctx)] == ["layout.margins"]
    added = {
        **base,
        "object_id": "usr_1",
        "content_source": "user",
        "user_overrides": [{"op": "add_text"}],
    }
    assert [i.check_id for i in check_margins(_plain_slide([added]), ctx)] == ["layout.margins"]


def test_image_distortion_reads_natural_size_of_deck() -> None:
    from presentation_designer.audit.deterministic import check_image_distorted

    deck = {"slide_size": {"width_emu": 12192000, "height_emu": 6858000}, "slides": []}
    ctx = Context(deck=deck, profile=_profile())
    square = {"natural_width_px": 800, "natural_height_px": 800, "fit": "as_is"}
    stretched = {
        "object_id": "3",
        "kind": "picture",
        "role": "content",
        "content_source": "plan",
        "bbox": {"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.2},
        "picture": square,
    }
    found = check_image_distorted(_plain_slide([stretched]), ctx)
    assert [i.check_id for i in found] == ["layout.image_distorted"]
    proper = {**stretched, "bbox": {"x": 0.1, "y": 0.1, "width": 0.225, "height": 0.4}}
    assert check_image_distorted(_plain_slide([proper]), ctx) == []


def test_chart_with_axis_titles_has_units() -> None:
    from presentation_designer.audit.deterministic import check_chart_labels

    ctx = Context(deck={"slides": []}, profile=_profile())
    chart = {"object_id": "5", "kind": "chart", "role": "content", "content_source": "generated"}
    bare = {**chart, "chart": {"type": "bar", "has_legend": False, "categories_count": 3}}
    assert [i.check_id for i in check_chart_labels(_plain_slide([bare]), ctx)] == [
        "integrity.chart_labels"
    ]
    titled = {**chart, "chart": {**bare["chart"], "has_axis_titles": True}}
    assert check_chart_labels(_plain_slide([titled]), ctx) == []


def test_package_check_finds_dangling_relationship(tmp_path: Any) -> None:
    """Проверка пакета: собранный python-pptx файл цел, файл без части слайда — нет."""
    import zipfile

    from pptx import Presentation

    from presentation_designer.audit.deterministic import check_package

    prs = Presentation()
    prs.slides.add_slide(prs.slide_layouts[5]).shapes.title.text = "Целый"
    good = tmp_path / "good.pptx"
    prs.save(good)
    assert check_package(good) == []

    broken = tmp_path / "broken.pptx"
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(broken, "w") as dst:
        for item in src.infolist():
            if item.filename != "ppt/slides/slide1.xml":
                dst.writestr(item, src.read(item.filename))
    found = check_package(broken)
    assert [i.check_id for i in found] == ["integrity.package"]
    assert "slide1.xml" in found[0].message
    assert check_package(tmp_path / "нет.pptx")[0].evidence["measured"] == "bad_zip"


def test_report_marks_deck_checks_and_package(tmp_path: Any) -> None:
    """Исход проверки уровня колоды считается по всем её находкам, а без файла проверка
    пакета помечается как не выполненная."""
    from pptx import Presentation

    from presentation_designer.audit.report import build_report

    text = {"plain": "Одно и то же", "paragraphs": []}
    obj = {
        "object_id": "2",
        "kind": "text",
        "role": "content",
        "content_source": "plan",
        "text": text,
    }
    deck = {
        "slide_size": {"width_emu": 12192000, "height_emu": 6858000},
        "slides": [
            {"slide_id": "sld_1", "index": 0, "pattern_id": "p1", "objects": [dict(obj)]},
            {"slide_id": "sld_2", "index": 1, "pattern_id": "p1", "objects": [dict(obj)]},
        ],
    }

    def outcome(report: JsonDict, check_id: str) -> str:
        return next(r["outcome"] for r in report["results"] if r["check_id"] == check_id)

    report = build_report(
        job_id="job_1",
        variant_id="v",
        revision=1,
        deck=deck,
        profile=_profile(),
        staging_prefix="v/r1/",
        contextual=False,
    )
    assert outcome(report, "integrity.duplicate_slides") == "failed"
    assert outcome(report, "integrity.package") == "not_checked"
    assert "pptx_file" in report["coverage"]["missing_inputs"]

    pptx = tmp_path / "deck.pptx"
    Presentation().save(pptx)
    report = build_report(
        job_id="job_1",
        variant_id="v",
        revision=1,
        deck=deck,
        profile=_profile(),
        staging_prefix="v/r1/",
        contextual=False,
        pptx_path=pptx,
    )
    assert outcome(report, "integrity.package") == "passed"
    assert "pptx_file" not in report["coverage"].get("missing_inputs", [])
