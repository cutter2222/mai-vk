"""Поиск стоковых фото: какие снимки годятся на слайд и как укорачивается запрос."""

from __future__ import annotations

import pytest

from presentation_designer.parsing.content import stock


def _result(
    title: str, width: int = 1800, height: int = 1200, creator: str = "", url: str = ""
) -> dict:
    return {"title": title, "width": width, "height": height, "creator": creator, "url": url}


@pytest.mark.parametrize(
    "result",
    [
        _result("Billboards Advertising Clutter Roadside, 06/1972"),
        _result("Street scene, bus", creator="U.S. National Archives"),
        _result("City bus collage element vector"),
        _result("Vintage bus stop illustration"),
        _result("Passengers waiting station 1941"),
        _result("Empty bus stop", width=711, height=1024),  # портрет обрежется до полосы
        _result("Bus stop", width=1200, height=1150),  # почти квадрат — тоже
        # Превью rawpixel с водяным знаком.
        _result("Technician", url="https://images.rawpixel.com/editor_1024/abc.jpg"),
        _result("Technician", url="https://images.rawpixel.com/image_1300/abc.jpg"),
    ],
)
def test_archival_graphic_and_portrait_photos_are_rejected(result: dict) -> None:
    assert not stock._usable(result)


@pytest.mark.parametrize(
    "result",
    [
        _result("Empty bus stop"),
        _result("Red city bus, public transportation", creator="Steven Lewis"),
        _result("Passengers at a modern tram station", width=2000, height=1300),
        _result("Technician", url="https://img.rawpixel.com/s3fs-private/x.jpg?w=1200"),
        _result("Office desk", url="https://cdn.stocksnap.io/img-thumbs/960w/x.jpg"),
    ],
)
def test_modern_landscape_photos_are_kept(result: dict) -> None:
    assert stock._usable(result)


def test_shortened_queries_keep_at_least_two_words() -> None:
    # Одно слово («shelter») находило хижины вместо остановки: без фото лучше.
    assert stock._attempts("smart bus shelter screen") == [
        "smart bus shelter screen",
        "shelter screen",
        "smart bus",
    ]
    assert stock._attempts("bus shelter") == ["bus shelter"]
    assert stock._attempts("harbor") == ["harbor"]
