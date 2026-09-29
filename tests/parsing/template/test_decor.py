"""Фоновый декор шаблона: кандидатов отбирает устройство файла, декор среди них — модель."""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import Any

from PIL import Image, ImageDraw

from presentation_designer.llm.client import LlmClient
from presentation_designer.llm.skills import get_skill
from presentation_designer.llm.stub import StubTransport
from presentation_designer.llm.types import Request
from presentation_designer.parsing.template.assets import (
    Asset,
    decor_candidates,
    pick_decor_with_vlm,
)


def _png(*, cutout: bool, size: tuple[int, int] = (300, 400)) -> bytes:
    """Вырезанная фигура на прозрачном фоне или непрозрачный прямоугольник-«фото»."""
    img = Image.new("RGBA", size, (0, 0, 0, 0) if cutout else (90, 120, 160, 255))
    ImageDraw.Draw(img).ellipse((20, 20, size[0] - 20, size[1] - 20), fill=(0, 110, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _asset(
    n: int, *, kind: str = "image", behind: bool = True, cutout: bool = True, area: float = 0.25
) -> Asset:
    return Asset(
        asset_id=f"asset_{n}",
        kind=kind,
        media_path=f"ppt/media/image{n}.png",
        sha256=f"sha{n}",
        bbox_on_source={"x": 0.7, "y": 0.0, "width": area**0.5, "height": area**0.5},
        behind=behind,
        blob=_png(cutout=cutout),
    )


def test_candidates_are_cutouts_behind_text() -> None:
    motif = _asset(1)
    assets = [
        motif,
        _asset(2, cutout=False),  # прямоугольное фото без прозрачности
        _asset(3, behind=False),  # поверх текста — иллюстрация к содержанию
        _asset(4, area=0.005),  # размер значка
        _asset(5, kind="logo"),
    ]
    assert decor_candidates(assets) == [motif]


def test_model_decides_which_candidates_are_decor(
    make_client: Callable[..., LlmClient],
) -> None:
    assets = [_asset(1, area=0.4), _asset(2, area=0.2)]
    stub = StubTransport()

    def respond(req: Request, _attempt: int) -> Any:
        text = "\n".join(m.text for m in req.messages)
        assert "На листе 2 пронумерованных картинок" in text and "позади текста" in text
        return {
            "items": [
                {"n": 1, "decor": True, "crop": True, "score": 8},
                {"n": 2, "decor": False, "crop": False, "score": 2},
            ]
        }

    stub.on(lambda _r: True, respond)
    picked = pick_decor_with_vlm(assets, make_client(stub), get_skill("template_analyzer"))
    assert picked == 1
    assert assets[0].tags == ["decor", "decor-crop", "decor:8"]
    assert assets[1].tags == []
    assert all(m.images for r in stub.calls for m in r.messages if m.role == "user")


def test_without_model_nothing_is_decor() -> None:
    assets = [_asset(1)]
    assert pick_decor_with_vlm(assets, None, get_skill("template_analyzer")) == 0
    assert assets[0].tags == []
