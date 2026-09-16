"""Загрузчик скиллов: манифест по схеме, промпты с версиями, запрос из манифеста."""

from __future__ import annotations

import pathlib

import pytest

from presentation_designer.llm.skills import (
    SkillError,
    get_skill,
    list_skills,
    load_skill,
    version_refs,
)


def test_all_repo_skills_load() -> None:
    names = list_skills()
    assert {
        "story_planner",
        "variant_planner",
        "auditor",
        "repairer",
        "template_analyzer",
        "brief_extractor",
        "content_importer",
    } <= set(names)
    for name in names:
        skill = load_skill(name)
        assert skill.name == name
        assert skill.prompts, name
        declared = {p.id: p.version for p in skill.manifest.prompts}
        for prompt in skill.prompts.values():
            assert prompt.version == declared[prompt.id]
            assert prompt.body, f"тело промпта {prompt.id} пустое"


def test_request_from_manifest() -> None:
    skill = get_skill("story_planner")
    req = skill.request("story.outline", "Материалы: ...", schema={"type": "object"})
    assert req.role == "llm"
    assert req.response_format == "json_schema"
    assert req.reasoning == "off"
    assert req.max_output_tokens == 3500
    assert req.temperature == 0.2
    assert req.prompt == ("story.outline", "0.2.0")
    assert req.skill == ("story_planner", "0.2.0")
    assert req.stage == "story"
    assert req.messages[0].role == "system" and req.messages[1].text == "Материалы: ..."
    with pytest.raises(SkillError):
        skill.request("story.outline", "x", unknown_field=1)
    with pytest.raises(SkillError):
        skill.prompt("нет такого")


def test_version_refs_match_contract_shape() -> None:
    skills, prompts = version_refs()
    assert all(set(x) == {"name", "version"} for x in skills + prompts)
    assert {"name": "story_planner", "version": "0.2.0"} in skills
    assert {"name": "story.outline", "version": "0.2.0"} in prompts


def test_prompt_version_mismatch_fails(tmp_path: pathlib.Path) -> None:
    base = tmp_path / "demo"
    (base / "prompts").mkdir(parents=True)
    (base / "skill.yaml").write_text(
        "schema_version: '1.1'\nname: demo\nversion: 0.1.0\nstage: story\ndescription: d\n"
        "model_role: llm\nprompts:\n- id: demo.p\n  path: prompts/p.md\n  version: 0.2.0\n"
    )
    (base / "prompts" / "p.md").write_text("---\nid: demo.p\nversion: 0.1.0\n---\nтело\n")
    with pytest.raises(SkillError, match="не совпадает"):
        load_skill("demo", tmp_path)
    (base / "prompts" / "p.md").write_text("---\nid: demo.p\nversion: 0.2.0\n---\nтело\n")
    assert load_skill("demo", tmp_path).prompt("demo.p").body == "тело"
