from __future__ import annotations

import pathlib

import pytest

from presentation_designer.cli.main import main


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert "contracts 1.5" in capsys.readouterr().out


def test_cli_unimplemented_command_exits_2() -> None:
    assert main(["export"]) == 2


def test_cli_compose_rejects_missing_inputs(tmp_path: pathlib.Path) -> None:
    assert (
        main(
            [
                "compose",
                str(tmp_path / "plan.json"),
                "--profile",
                str(tmp_path / "profile.json"),
                "--template",
                str(tmp_path / "t.pptx"),
                "--content",
                str(tmp_path / "package.json"),
                "--out",
                str(tmp_path / "out"),
            ]
        )
        == 2
    )


def test_cli_analyze_rejects_missing_file(tmp_path: pathlib.Path) -> None:
    assert (
        main(
            [
                "analyze",
                str(tmp_path / "нет.pptx"),
                "--out",
                str(tmp_path / "out"),
                "--no-render",
                "--no-vlm",
            ]
        )
        == 2
    )
