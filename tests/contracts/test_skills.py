"""Каждый скилл имеет валидный манифест, а его промпты существуют с указанной версией."""

from __future__ import annotations

import pathlib

import pytest
import yaml

from presentation_designer.contracts import SkillManifest

ROOT = pathlib.Path(__file__).resolve().parents[2]
MANIFESTS = sorted((ROOT / "skills").glob("*/skill.yaml"))


@pytest.mark.parametrize("path", MANIFESTS, ids=[p.parent.name for p in MANIFESTS])
def test_skill_manifest_valid(path: pathlib.Path) -> None:
    manifest = SkillManifest.model_validate(yaml.safe_load(path.read_text()))
    assert manifest.name == path.parent.name
    for prompt in manifest.prompts:
        prompt_path = path.parent / prompt.path
        assert prompt_path.exists(), f"нет файла промпта {prompt_path}"
        assert f"version: {prompt.version}" in prompt_path.read_text().split("---")[1]
