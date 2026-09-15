from __future__ import annotations

import pathlib

import pytest
from pptx import Presentation

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def test_mini_template_opens() -> None:
    prs = Presentation(str(FIXTURES / "pptx" / "mini_template.pptx"))
    assert len(prs.slides) == 4
    assert prs.slide_width == 12192000


def test_content_fixtures_exist() -> None:
    for name in ("product_description.docx", "metrics.xlsx", "metrics.csv", "brief.md"):
        assert (FIXTURES / "content" / name).exists(), name


@pytest.mark.organizer_data
def test_organizer_manifest_lists_templates(organizer_dir: pathlib.Path) -> None:
    import json

    manifest = json.loads((organizer_dir / "manifest.json").read_text())
    assert len(manifest["templates"]) >= 3
    for entry in manifest["templates"]:
        assert (organizer_dir.parent.parent / entry["path"]).exists()
