from __future__ import annotations

import pathlib

import pytest

from presentation_designer.cli.main import main


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        main(["--version"])
    assert info.value.code == 0
    assert "contracts 1.9" in capsys.readouterr().out


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


def test_cli_edit_slide_rejects_missing_inputs_and_bad_slide(tmp_path: pathlib.Path) -> None:
    args = [
        "edit-slide",
        str(tmp_path / "plan.json"),
        "--story",
        str(tmp_path / "story.json"),
        "--profile",
        str(tmp_path / "profile.json"),
        "--content",
        str(tmp_path / "package.json"),
        "--instruction",
        "короче",
        "--out",
        str(tmp_path / "out"),
    ]
    assert main([*args, "--slide", "1"]) == 2
    for name in ("plan", "story", "profile", "package"):
        (tmp_path / f"{name}.json").write_text("{}", encoding="utf-8")
    assert main([*args, "--slide", "0"]) == 2


def test_cli_patch_slides_on_examples(tmp_path: pathlib.Path) -> None:
    """Пример патча из контрактов применяется к примеру плана: новый план с overrides и
    порядком, отчёт со сводкой; отсутствующий вход — код 2, негодный патч — код 1."""
    import json

    root = pathlib.Path(__file__).resolve().parents[1] / "contracts" / "examples"
    plan = root / "slide_plan.example.json"
    args = [
        "patch-slides",
        str(plan),
        "--patch",
        str(root / "slide_patch.example.json"),
        "--profile",
        str(root / "template_profile.example.json"),
        "--content",
        str(root / "content_package.example.json"),
        "--deck",
        str(root / "composed_deck.example.json"),
        "--out",
        str(tmp_path / "out"),
        "--report",
    ]
    assert main(args) == 0
    result = json.loads((tmp_path / "out" / "plan.json").read_text(encoding="utf-8"))
    report = json.loads((tmp_path / "out" / "report.json").read_text(encoding="utf-8"))
    by_id = {s["slide_id"]: s for s in result["slides"]}
    assert len(by_id["s2"]["overrides"]) == 5 and "overrides" not in by_id["s5"]
    assert [s["slide_id"] for s in sorted(result["slides"], key=lambda s: s["order"])][6:8] == [
        "s3b",
        "s3",
    ]
    summary = report["summary"]
    assert summary.startswith("Слайд 3:") and "порядок слайдов изменён" in summary
    assert main([*args[:2], "--patch", str(tmp_path / "nope.json"), *args[4:]]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"slides": [{"slide_id": "s404", "overrides": []}]}))
    assert main([*args[:2], "--patch", str(bad), *args[4:]]) == 1
