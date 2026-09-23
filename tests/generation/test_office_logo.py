"""Знак шаблона в офисной копии: замена и удаление на всех слайдах одним действием, по
картинкам из профиля; чужие логотипы на слайдах не трогаются."""

from __future__ import annotations

import hashlib
import io

import pytest
from PIL import Image
from pptx import Presentation
from pptx.oxml.shapes.picture import CT_Picture
from pptx.util import Emu, Inches

from presentation_designer.generation.office_logo import logo_hashes, replace_logo


def _png(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


BRAND = _png((400, 100), (0, 119, 255))
PARTNER = _png((200, 200), (255, 56, 133))
NEW = _png((300, 150), (255, 90, 0))


def _deck() -> bytes:
    """Знак шаблона слева сверху на макете, логотип партнёра — картинкой на слайде."""
    prs = Presentation()
    layout = prs.slide_layouts[6]
    _, rid = layout.part.get_or_add_image_part(io.BytesIO(BRAND))
    pic = CT_Picture.new_pic(90, "Logo", "", rid, Inches(0.5), Inches(0.4), Inches(2), Inches(0.5))
    layout.shapes._spTree.append(pic)
    slide = prs.slides.add_slide(layout)
    slide.shapes.add_picture(io.BytesIO(PARTNER), Inches(6), Inches(3), Inches(1), Inches(1))
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


def _pictures(data: bytes) -> dict[str, list[tuple[str, Emu, Emu, Emu, Emu]]]:
    prs = Presentation(io.BytesIO(data))
    out: dict[str, list[tuple[str, Emu, Emu, Emu, Emu]]] = {"layout": [], "slide": []}
    for where, shapes in (("layout", prs.slide_layouts[6].shapes), ("slide", prs.slides[0].shapes)):
        for sh in shapes:
            if sh.shape_type == 13:
                sha = hashlib.sha256(sh.image.blob).hexdigest()
                out[where].append((sha, sh.left, sh.top, sh.width, sh.height))
    return out


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_brand_mark_is_replaced_on_the_layout_and_keeps_its_place() -> None:
    data, n = replace_logo(_deck(), {sha(BRAND)}, NEW)
    assert n == 1
    pics = _pictures(data)
    ((new_sha, left, top, width, height),) = pics["layout"]
    assert new_sha == sha(NEW)
    # Та же высота и левый край, ширина — по пропорции новой картинки (2:1).
    assert (left, top, height) == (Inches(0.5), Inches(0.4), Inches(0.5))
    assert width == Inches(1)
    assert [p[0] for p in pics["slide"]] == [sha(PARTNER)]
    assert replace_logo(data, {sha(BRAND)}, NEW)[1] == 0


def test_brand_mark_is_removed_and_partner_logos_stay() -> None:
    data, n = replace_logo(_deck(), {sha(BRAND)}, None)
    assert n == 1
    pics = _pictures(data)
    assert pics["layout"] == []
    assert [p[0] for p in pics["slide"]] == [sha(PARTNER)]


def test_only_pinned_template_logos_count_and_vector_images_are_refused() -> None:
    profile = {
        "fixed_elements": [{"kind": "logo", "asset_id": "a1"}],
        "assets": [
            {"asset_id": "a1", "kind": "logo", "sha256": "brand"},
            {"asset_id": "a2", "kind": "logo", "sha256": "partner"},
        ],
    }
    assert logo_hashes(profile) == {"brand"}
    assert logo_hashes(None) == set()
    with pytest.raises(ValueError, match="PNG или JPG"):
        replace_logo(_deck(), {sha(BRAND)}, b"<svg xmlns='http://www.w3.org/2000/svg'/>")


def test_mark_of_two_pictures_becomes_one_logo_in_their_frame() -> None:
    # Как у VK Tech: квадрат VK и надпись tech — две картинки вплотную на одном макете.
    icon, word = _png((100, 100), (0, 119, 255)), _png((300, 100), (20, 20, 20))
    prs = Presentation()
    layout = prs.slide_layouts[6]
    for n, (data, left, width) in enumerate(((icon, 0.5, 0.5), (word, 1.05, 1.5))):
        _, rid = layout.part.get_or_add_image_part(io.BytesIO(data))
        layout.shapes._spTree.append(
            CT_Picture.new_pic(
                80 + n, f"Logo {n}", "", rid, Inches(left), Inches(0.4), Inches(width), Inches(0.5)
            )
        )
    prs.slides.add_slide(layout)
    buf = io.BytesIO()
    prs.save(buf)
    data, n = replace_logo(buf.getvalue(), {sha(icon), sha(word)}, NEW)
    assert n == 1
    ((new_sha, left, top, width, height),) = _pictures(data)["layout"]
    assert new_sha == sha(NEW)
    # Общая рамка двух частей: левый край первой, высота прежняя, ширина — по пропорции.
    assert (left, top, height) == (Inches(0.5), Inches(0.4), Inches(0.5))
    assert width == Inches(1)
