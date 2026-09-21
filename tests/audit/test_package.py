"""Целостность пакета: то, из-за чего PowerPoint предлагает «восстановить файл».

LibreOffice такие пакеты открывает молча, поэтому ни предпросмотр, ни PDF о поломке не
скажут — узнает только тот, кому колоду отдали. Проверки читают сам zip, без python-pptx.
"""

from __future__ import annotations

import pathlib
import shutil
import zipfile

from pptx import Presentation

from presentation_designer.audit.deterministic import check_package


def _deck(path: pathlib.Path) -> pathlib.Path:
    """Маленькая настоящая колода: один слайд с заголовком."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Итоги квартала"
    prs.save(path)
    return path


def _patch(src: pathlib.Path, dst: pathlib.Path, edits: dict[str, str | None]) -> pathlib.Path:
    """Копия пакета с подменённым (или удалённым) содержимым частей."""
    with zipfile.ZipFile(src) as source, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
        for item in source.infolist():
            if item.filename in edits:
                replacement = edits[item.filename]
                if replacement is None:
                    continue
                out.writestr(item, replacement)
            else:
                out.writestr(item, source.read(item.filename))
    return dst


def test_clean_deck_passes(tmp_path: pathlib.Path) -> None:
    assert check_package(_deck(tmp_path / "deck.pptx")) == []


def test_duplicate_shape_id_is_reported(tmp_path: pathlib.Path) -> None:
    """Два объекта с одним id на слайде — типичный след клонирования образца на уровне XML."""
    deck = _deck(tmp_path / "deck.pptx")
    with zipfile.ZipFile(deck) as archive:
        slide = archive.read("ppt/slides/slide1.xml").decode()
    # Все id на слайде становятся одинаковыми: достаточно одного повтора.
    broken = slide.replace('id="3"', 'id="2"').replace('id="4"', 'id="2"')
    assert broken != slide
    patched = _patch(deck, tmp_path / "broken.pptx", {"ppt/slides/slide1.xml": broken})
    issues = check_package(patched)
    assert [i.check_id for i in issues] == ["integrity.package"]
    assert "одним идентификатором" in issues[0].message


def test_part_without_content_type_is_reported(tmp_path: pathlib.Path) -> None:
    """Часть без типа содержимого: PowerPoint такой пакет чинит, LibreOffice молчит."""
    deck = _deck(tmp_path / "deck.pptx")
    with zipfile.ZipFile(deck) as archive:
        types = archive.read("[Content_Types].xml").decode()
    stripped = types.replace(
        '<Override PartName="/ppt/slides/slide1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>',
        "",
    )
    assert stripped != types
    patched = _patch(deck, tmp_path / "no-type.pptx", {"[Content_Types].xml": stripped})
    issues = check_package(patched)
    assert [i.check_id for i in issues] == ["integrity.package"]
    assert "типа содержимого" in issues[0].message
    assert "ppt/slides/slide1.xml" in issues[0].evidence["parts"]


def test_dangling_relationship_is_reported(tmp_path: pathlib.Path) -> None:
    deck = _deck(tmp_path / "deck.pptx")
    patched = _patch(deck, tmp_path / "dangling.pptx", {"ppt/slides/slide1.xml": None})
    issues = check_package(patched)
    assert [i.check_id for i in issues] == ["integrity.package"]


def test_not_a_zip_is_reported(tmp_path: pathlib.Path) -> None:
    broken = tmp_path / "text.pptx"
    broken.write_bytes(b"not a package at all")
    issues = check_package(broken)
    assert [i.check_id for i in issues] == ["integrity.package"]
    assert issues[0].evidence["measured"] == "bad_zip"


def test_real_generated_deck_passes(tmp_path: pathlib.Path) -> None:
    """Фикстура шаблона из датасета: пакет читается и проходит проверки целостности."""
    source = pathlib.Path("tests/fixtures/pptx/mini_template.pptx")
    copy = shutil.copy(source, tmp_path / "template.pptx")
    assert check_package(pathlib.Path(copy)) == []
