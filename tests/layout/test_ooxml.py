"""Операции над пакетом PPTX на собственной фикстуре: клонирование, диаграммы, текст, удаление."""

from __future__ import annotations

import io
import pathlib
import zipfile

import pytest
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Pt

from presentation_designer.layout import ooxml

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "pptx" / "mini_template.pptx"


def _roundtrip(prs: object) -> object:
    buf = io.BytesIO()
    prs.save(buf)  # type: ignore[attr-defined]
    buf.seek(0)
    return Presentation(buf)


def _save(prs: object, path: pathlib.Path) -> pathlib.Path:
    prs.save(str(path))  # type: ignore[attr-defined]
    return path


def test_check_package_on_fixture() -> None:
    report = ooxml.check_package(FIXTURE)
    assert report.ok, report.errors
    assert report.slides == 4
    assert report.parts > 10


def test_check_package_detects_broken_rel(tmp_path: pathlib.Path) -> None:
    broken = tmp_path / "broken.pptx"
    with zipfile.ZipFile(FIXTURE) as zin, zipfile.ZipFile(broken, "w") as zout:
        for item in zin.infolist():
            if item.filename.startswith("ppt/charts/chart"):
                continue  # часть диаграммы пропала, связь со слайда осталась
            zout.writestr(item, zin.read(item.filename))
    report = ooxml.check_package(broken)
    assert not report.ok
    assert any("отсутствующую" in e for e in report.errors)


def test_roundtrip_is_reported_as_equivalent(tmp_path: pathlib.Path) -> None:
    out = _save(Presentation(str(FIXTURE)), tmp_path / "rt.pptx")
    diff = ooxml.compare_packages(FIXTURE, out)
    assert diff["only_in_a"] == [] and diff["only_in_b"] == []
    assert diff["changed_binary"] == []
    assert ooxml.check_package(out).ok


def test_clone_slide_shares_layout_and_copies_shapes(tmp_path: pathlib.Path) -> None:
    prs = Presentation(str(FIXTURE))
    source = prs.slides[1]
    clone = ooxml.clone_slide(prs, source, index=2)
    assert len(prs.slides) == 5
    assert prs.slides[2].part is clone.part
    assert clone.slide_layout.part is source.slide_layout.part
    assert [s.name for s in clone.shapes] == [s.name for s in source.shapes]
    assert clone.shapes[0].text_frame.paragraphs[0].runs[0].font.size == Pt(32)
    reopened = _roundtrip(prs)
    assert len(reopened.slides) == 5  # type: ignore[attr-defined]
    out = _save(prs, tmp_path / "clone.pptx")
    assert ooxml.check_package(out).ok


def test_clone_chart_slide_gives_independent_copies(tmp_path: pathlib.Path) -> None:
    prs = Presentation(str(FIXTURE))
    source = prs.slides[2]
    clone_a = ooxml.clone_slide(prs, source)
    clone_b = ooxml.clone_slide(prs, source)
    charts = {}
    for name, slide in (("src", source), ("a", clone_a), ("b", clone_b)):
        chart_shape = next(s for s in slide.shapes if s.has_chart)
        charts[name] = chart_shape.chart
    parts = {n: c.part for n, c in charts.items()}
    assert len({id(p) for p in parts.values()}) == 3
    assert len({p.partname for p in parts.values()}) == 3
    workbooks = {n: p.chart_workbook.xlsx_part for n, p in parts.items()}
    assert len({w.partname for w in workbooks.values()}) == 3

    data = CategoryChartData()
    data.categories = ["Q1", "Q2"]
    data.add_series("Копия А", (1, 2))
    charts["a"].replace_data(data)
    data = CategoryChartData()
    data.categories = ["Янв", "Фев", "Мар", "Апр"]
    data.add_series("Копия Б", (10, 20, 30, 40))
    charts["b"].replace_data(data)

    out = _save(prs, tmp_path / "charts.pptx")
    assert ooxml.check_package(out).ok
    reopened = Presentation(str(out))
    titles = []
    for slide in reopened.slides:
        for shape in slide.shapes:
            if shape.has_chart:
                plot = shape.chart.plots[0]
                titles.append((plot.series[0].name, list(plot.categories)))
    assert titles == [
        ("Открываемость", ["Май", "Июнь", "Июль"]),
        ("Копия А", ["Q1", "Q2"]),
        ("Копия Б", ["Янв", "Фев", "Мар", "Апр"]),
    ]
    with zipfile.ZipFile(out) as zf:
        assert len([n for n in zf.namelist() if n.startswith("ppt/embeddings/")]) == 3


def test_replace_paragraph_text_keeps_run_style() -> None:
    prs = Presentation(str(FIXTURE))
    paragraph = prs.slides[1].shapes[1].text_frame.paragraphs[0]
    before = ooxml.paragraph_run_styles(paragraph)
    ooxml.replace_paragraph_text(paragraph, "Новый заголовок\nвторая строка")
    after = ooxml.paragraph_run_styles(paragraph)
    assert paragraph.text == "Новый заголовок\vвторая строка"
    assert [a["attrs"] for a in after] == [before[0]["attrs"]] * 2
    assert paragraph.runs[0].font.size == Pt(20)


def test_set_run_texts_preserves_mixed_styles() -> None:
    prs = Presentation(str(FIXTURE))
    paragraph = prs.slides[0].shapes[0].text_frame.paragraphs[0]
    run = paragraph.runs[0]
    run.text = "Жирная часть"
    run.font.bold = True
    second = paragraph.add_run()
    second.text = " обычная часть"
    second.font.italic = True
    ooxml.set_run_texts(paragraph, ["Заменено", " и тут тоже"])
    styles = ooxml.paragraph_run_styles(paragraph)
    assert [s["text"] for s in styles] == ["Заменено", " и тут тоже"]
    assert styles[0]["attrs"].get("b") == "1" and styles[1]["attrs"].get("i") == "1"


def test_delete_slides_prunes_exclusive_parts(tmp_path: pathlib.Path) -> None:
    prs = Presentation(str(FIXTURE))
    keep = [prs.slides[0], prs.slides[3]]
    removed = ooxml.keep_only_slides(prs, keep)
    assert removed == 2 and len(prs.slides) == 2
    out = _save(prs, tmp_path / "pruned.pptx")
    report = ooxml.check_package(out)
    assert report.ok and report.slides == 2
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert not any(n.startswith("ppt/charts/") or n.startswith("ppt/embeddings/") for n in names)
    assert len([n for n in names if n.startswith("ppt/slides/slide")]) == 2
    with pytest.raises(ValueError, match="не принадлежит"):
        ooxml.delete_slide(prs, Presentation(str(FIXTURE)).slides[0])


def test_clone_keeps_references_when_skipped_rel_precedes_others(tmp_path: pathlib.Path) -> None:
    """Заметки идут в связях раньше диаграммы: пропуск заметок не должен трогать её ссылку."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.notes_slide.notes_text_frame.text = "заметка"  # rId2 — заметки
    data = CategoryChartData()
    data.categories = ["a", "b"]
    data.add_series("s", (1, 2))
    slide.shapes.add_chart(XL_CHART_TYPE.PIE, 0, 0, Pt(200), Pt(200), data)  # rId3 — диаграмма
    clone = ooxml.clone_slide(prs, slide)
    assert not clone.has_notes_slide
    assert ooxml.shape_tree_signature(clone) == ooxml.shape_tree_signature(slide)
    assert next(s for s in clone.shapes if s.has_chart).chart.plots[0].series[0].name == "s"
    out = _save(prs, tmp_path / "notes.pptx")
    assert ooxml.check_package(out).ok


def _link_to(slide: object, target: object, text: str) -> None:
    """Фигура с переходом на другой слайд, как кнопка «Back to overview page»."""
    from pptx.opc.constants import RELATIONSHIP_TYPE as RT

    box = slide.shapes.add_textbox(0, 0, Pt(100), Pt(20))  # type: ignore[attr-defined]
    run = box.text_frame.paragraphs[0].add_run()
    run.text = text
    rid = slide.part.relate_to(target.part, RT.SLIDE)  # type: ignore[attr-defined]
    rpr = run._r.get_or_add_rPr()
    link = rpr.makeelement(f"{{{ooxml.NS_A}}}hlinkClick", {})
    link.set(f"{{{ooxml.NS_R}}}id", rid)
    link.set("action", "ppaction://hlinksldjump")
    rpr.append(link)


def test_links_to_dropped_slides_do_not_keep_them(tmp_path: pathlib.Path) -> None:
    """Колода из копий образцов: переход на образец, чья копия есть в колоде, ведёт на
    копию; переход на оглавление, которого в колоде нет, снимается — иначе оглавление
    остаётся в файле лишним слайдом («в файле 6 слайдов, по плану 5»). Кнопка, весь текст
    которой — такой переход, убирается целиком; слово-ссылка в обычном тексте остаётся."""
    prs = Presentation()
    blank = prs.slide_layouts[6]
    cover, overview, content = (prs.slides.add_slide(blank) for _ in range(3))
    _link_to(content, overview, "Back to overview page")
    _link_to(content, cover, "На обложку")
    _link_to(content, overview, "см. оглавление")
    para = content.shapes[-1].text_frame.paragraphs[0]
    lead = para.add_run()
    lead.text = "Подробности: "
    para.runs[0]._r.addprevious(lead._r)
    cover_copy = ooxml.clone_slide(prs, cover)
    content_copy = ooxml.clone_slide(prs, content)
    ooxml.keep_only_slides(prs, [cover_copy, content_copy])
    origin = {cover.part: cover_copy.part, content.part: content_copy.part}
    assert ooxml.retarget_slide_links(prs, origin) == 2
    out = _save(prs, tmp_path / "links.pptx")
    report = ooxml.check_package(out)
    assert report.ok, report.errors
    assert report.slides == 2 and report.unreachable_parts == []
    saved = Presentation(str(out))
    runs = [
        r for p in saved.slides[1].shapes for para in p.text_frame.paragraphs for r in para.runs
    ]
    assert [r.text for r in runs] == ["На обложку", "Подробности: ", "см. оглавление"]
    assert runs[2]._r.find(f".//{{{ooxml.NS_A}}}hlinkClick") is None
    target = runs[0]._r.find(f".//{{{ooxml.NS_A}}}hlinkClick").get(f"{{{ooxml.NS_R}}}id")
    assert saved.slides[1].part.related_part(target) is saved.slides[0].part


def test_drop_template_logos_removes_marked_shapes() -> None:
    """Знак шаблона снимается с макета по профилю: объект уходит, остальное на месте.

    Логотип живёт на макете, а не на слайде, поэтому правкой слайда его не убрать — это
    отдельный проход по пакету (`template_logo: drop` в плане).
    """
    from presentation_designer.layout.package import drop_template_logos
    from tests.layout.conftest import MINI_TEMPLATE

    prs = Presentation(str(MINI_TEMPLATE))
    layout = prs.slide_layouts[0]
    victim = next(sh for sh in layout.shapes)
    part = str(layout.part.partname).lstrip("/")
    profile = {
        "fixed_elements": [
            {"kind": "logo", "element_ref": str(victim.shape_id), "source_part": part},
            # Чужая часть: тот же id, но другой макет — трогать нельзя.
            {"kind": "footer", "element_ref": str(victim.shape_id), "source_part": part},
        ]
    }
    before = len(list(layout.shapes))
    removed = drop_template_logos(prs, profile)
    assert removed >= 1
    after = [str(sh.shape_id) for sh in layout.shapes]
    assert str(victim.shape_id) not in after
    assert len(after) == before - 1, "остальные объекты макета на месте"
    # Профиль без логотипов ничего не трогает.
    assert drop_template_logos(prs, {"fixed_elements": []}) == 0
