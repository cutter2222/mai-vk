"""Шрифты колоды прикладываются к ревизии: холст редактора рисует тем же файлом, что и вёрстка.

Ради этого тест и написан: в браузере пользователя гарнитуры шаблона обычно нет, браузер
подставляет свою, и нажатие «Редактировать» меняло вид слайда. Резолвер здесь подменён: тест
проверяет договор (какие файлы попадают в ревизию и что записано в описание колоды), а не то,
какие шрифты стоят на машине.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from presentation_designer.pipeline.artifacts import Staging
from presentation_designer.pipeline.real import attach_deck_fonts


class _Face:
    def __init__(self, path: pathlib.Path) -> None:
        self.file = str(path)


class _Resolved:
    def __init__(self, path: pathlib.Path | None, substituted: bool) -> None:
        self.file = str(path) if path else None
        self.substituted = substituted


def _staging(tmp_path: pathlib.Path) -> Staging:
    target = tmp_path / "r1"
    target.mkdir()
    return Staging(dir=target, final=target, prefix="balanced/r1/")


def test_fonts_of_the_deck_land_in_the_revision(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    regular = tmp_path / "Montserrat-Regular.ttf"
    bold = tmp_path / "Montserrat-Bold.ttf"
    regular.write_bytes(b"regular-bytes")
    bold.write_bytes(b"bold-bytes")

    bold_file = bold

    def resolve(family: str | None, *, bold: bool = False, italic: bool = False) -> Any:
        _ = italic
        if str(family) == "Montserrat":
            return _Resolved(bold_file if bold else regular, False)
        # Гарнитуры шаблона нет: рендерер подменил её, прикладывать нечего.
        return _Resolved(regular, True)

    from presentation_designer.shared import text_metrics

    monkeypatch.setattr(text_metrics, "resolve_font", resolve)
    staging = _staging(tmp_path)
    deck: dict[str, Any] = {"fonts": [{"family": "Montserrat"}, {"family": "Play"}]}

    attach_deck_fonts(deck, staging)

    montserrat, play = deck["fonts"]
    assert [f["weight"] for f in montserrat["files"]] == [400, 700]
    assert montserrat["files"][0]["artifact"] == "balanced/r1/fonts/montserrat-400.ttf"
    assert montserrat["files"][0]["format"] == "truetype"
    assert (staging.dir / "fonts" / "montserrat-400.ttf").read_bytes() == b"regular-bytes"
    assert (staging.dir / "fonts" / "montserrat-700.ttf").read_bytes() == b"bold-bytes"
    assert "files" not in play, "подменённая гарнитура за шрифт шаблона не выдаётся"


def test_same_file_for_both_weights_is_stored_once(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Одно начертание на оба веса (переменный шрифт): файл кладётся один раз."""
    single = tmp_path / "Golos.ttf"
    single.write_bytes(b"variable")

    from presentation_designer.shared import text_metrics

    monkeypatch.setattr(
        text_metrics,
        "resolve_font",
        lambda family, *, bold=False, italic=False: _Resolved(single, False),
    )
    staging = _staging(tmp_path)
    deck: dict[str, Any] = {"fonts": [{"family": "Golos Text"}]}

    attach_deck_fonts(deck, staging)

    files = deck["fonts"][0]["files"]
    assert [f["artifact"] for f in files] == [
        "balanced/r1/fonts/golos-text-400.ttf",
        "balanced/r1/fonts/golos-text-400.ttf",
    ], "оба веса ссылаются на один приложенный файл"
    assert sorted(p.name for p in (staging.dir / "fonts").iterdir()) == ["golos-text-400.ttf"]
