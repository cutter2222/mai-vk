from __future__ import annotations

import pytest

from presentation_designer.cli.main import main


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert "contracts 1.1" in capsys.readouterr().out


def test_cli_unimplemented_command_exits_2() -> None:
    assert main(["analyze"]) == 2
