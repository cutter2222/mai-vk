"""Общий смысловой план: сборка из ответа модели с проверкой ссылок и покрытия, ключ
кэша по содержанию и настройкам, план на записанных ответах (replay) для пакета из
examples/content, повтор при негодном ответе, переиспользование плана в pipeline."""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from presentation_designer.contracts import ContentPackage, SlidePlan, StoryPlan
from presentation_designer.contracts.validators import check_story_plan
from presentation_designer.generation import story as st
from presentation_designer.llm.skills import get_skill
from presentation_designer.parsing.content.importer import import_content
from presentation_designer.parsing.content.parsers import ParseCache
from presentation_designer.shared.settings import Settings
from tests.parsing.content.conftest import EXAMPLES

EXAMPLE_FILES = ("overview.docx", "metrics.xlsx", "notes.md", "openrate_chart.png", "logo.png")


@pytest.fixture
def example_package(materials: Any, cache: ParseCache, import_settings: Settings) -> dict[str, Any]:
    files = materials(*EXAMPLE_FILES, root=EXAMPLES)
    brief = json.loads((EXAMPLES / "brief.json").read_text())
    return import_content(
        files,
        brief,
        package_id="pkg_example",
        settings=import_settings,
        cache=cache,
        use_model=False,
    ).package


def _answer_for(package: dict[str, Any]) -> dict[str, Any]:
    """Ответ модели с намеренными огрехами: чужой идентификатор, пропущенный обязательный
    факт, пункт брифа без тезиса, сокращение с неизвестным тезисом."""
    facts = [f["fact_id"] for f in package["facts"] if f["must_keep"]]
    return {
        "key_takeaway": "Пилот доказал пользу умных уведомлений",
        "theses": [
            {
                "id": "a",
                "kind": "section",
                "statement": "Проблема",
                "required": True,
                "source_refs": ["b1", "b999"],
            },
            {
                "id": "b",
                "kind": "claim",
                "parent": "a",
                "statement": f"Пользователи теряют {{fact:{facts[0]}}} важных уведомлений",
                "required": True,
                "fact_refs": [facts[0]],
                "visual": "number",
                "covers": ["метрики пилота"],
            },
            {
                "id": "c",
                "kind": "conclusion",
                "statement": "Просим одобрить {fact:f404}",
                "required": True,
                "parent": "zzz",
            },
        ],
        "allowed_reductions": [
            {"thesis_id": "b", "reduction": "shorten_wording", "note": "короче"},
            {"thesis_id": "nope", "reduction": "drop_entirely"},
            {"thesis_id": "b", "reduction": "merge_with"},
        ],
        "assumptions": ["план на квартал взят из заметок"],
    }


def test_assemble_repairs_refs_and_covers_required_content(example_package: dict[str, Any]) -> None:
    brief = st.effective_brief(example_package, {"language": "ru"})
    story, fixes = st.assemble_story(
        _answer_for(example_package),
        example_package,
        brief,
        content_hash="sha256:test",
        story_id="story_t",
        generation_meta={"skills": [], "models": []},
    )
    doc = StoryPlan.model_validate(story)
    assert "Обязательный пункт брифа" not in json.dumps(story, ensure_ascii=False)
    assert not check_story_plan(doc, ContentPackage.model_validate(example_package))
    ids = [t["thesis_id"] for t in story["theses"]]
    assert ids == [f"t{i}" for i in range(1, len(ids) + 1)]
    assert story["theses"][1]["parent_id"] == "t1" and "parent_id" not in story["theses"][2]
    assert story["theses"][0]["source_refs"] == ["b1"]
    assert "{fact:f404}" not in story["theses"][2]["statement"]
    codes = {f["code"] for f in fixes}
    assert codes >= {
        "ref_unknown",
        "fact_placeholder_unknown",
        "parent_unknown",
        "must_keep_added",
        "must_include_added",
    }
    coverage = story["coverage"]
    must_total = sum(1 for f in example_package["facts"] if f["must_keep"])
    assert coverage["must_keep_facts"] == {"total": must_total, "covered": must_total}
    items = {c["item"]: c["thesis_ids"] for c in coverage["must_include"]}
    assert items["метрики пилота"] and items["план на квартал"]
    added = [t for t in story["theses"] if t["statement"].startswith("Ключевые показатели")]
    assert len(added) == 1 and added[0]["kind"] == "evidence" and added[0]["required"]
    assert story["allowed_reductions"] == [
        {"thesis_id": "t2", "reduction": "shorten_wording", "note": "короче"}
    ]
    assert story["assumptions"][0] == "план на квартал взят из заметок"
    assert story["effective_brief"]["slide_count"] == {"min": 10, "max": 15}
    assert story["effective_brief"]["language"] == "ru"


def test_story_key_depends_on_content_brief_and_settings(example_package: dict[str, Any]) -> None:
    skill = get_skill("story_planner")
    base = st.story_key(
        example_package, {"language": "ru"}, skill=skill, model={"role": "llm", "name": "m"}
    )
    assert base == st.story_key(
        example_package, {"language": "ru"}, skill=skill, model={"role": "llm", "name": "m"}
    )
    other_id = {**example_package, "package_id": "pkg_other", "created_at": "2000-01-01T00:00:00Z"}
    assert (
        st.story_key(other_id, {"language": "ru"}, skill=skill, model={"role": "llm", "name": "m"})
        == base
    )
    assert (
        st.story_key(
            example_package, {"language": "en"}, skill=skill, model={"role": "llm", "name": "m"}
        )
        != base
    )
    assert (
        st.story_key(
            example_package,
            {"language": "ru", "seed": 7},
            skill=skill,
            model={"role": "llm", "name": "m"},
        )
        != base
    )
    changed_brief = {
        **example_package,
        "brief": {**example_package["brief"], "goal": "другая цель"},
    }
    assert (
        st.story_key(
            changed_brief, {"language": "ru"}, skill=skill, model={"role": "llm", "name": "m"}
        )
        != base
    )
    assert (
        st.story_key(
            example_package, {"language": "ru"}, skill=skill, model={"role": "llm", "name": "other"}
        )
        != base
    )
    assert (
        st.story_key(
            example_package,
            {"language": "ru"},
            skill=skill,
            model={"role": "llm", "name": "m"},
            nonce="x",
        )
        != base
    )
    facts_changed = {**example_package, "facts": example_package["facts"][:-1]}
    assert (
        st.story_key(
            facts_changed, {"language": "ru"}, skill=skill, model={"role": "llm", "name": "m"}
        )
        != base
    )


def test_build_story_on_replay(example_package: dict[str, Any], replay_client: Any) -> None:
    result = st.build_story(
        example_package, {"language": "ru"}, client=replay_client, skill=get_skill("story_planner")
    )
    story = result.story
    doc = StoryPlan.model_validate(story)
    assert not check_story_plan(doc, ContentPackage.model_validate(example_package))
    assert story["schema_version"] == "1.2" and story["purpose"] == "product"
    assert story["audience"] == "руководители продуктовых направлений"
    assert 8 <= len(story["theses"]) <= 20
    assert (
        story["coverage"]["must_keep_facts"]["covered"]
        == story["coverage"]["must_keep_facts"]["total"]
    )
    assert all(c["thesis_ids"] for c in story["coverage"]["must_include"])
    assert any("{fact:" in t["statement"] for t in story["theses"])
    kinds = {t["kind"] for t in story["theses"]}
    assert "section" in kinds and kinds & {"conclusion", "call_to_action"}
    meta = story["generation_meta"]
    assert meta["skills"] == [{"name": "story_planner", "version": "0.2.0"}]
    assert meta["prompts"] == [{"name": "story.outline", "version": "0.2.0"}]
    from presentation_designer.shared.settings import get_models_config

    role = get_models_config().role("llm")
    assert meta["models"][0]["name"] == role.model
    assert meta["models"][0]["reasoning_mode"] == role.reasoning.mode
    assert meta["prompt_tokens"] > 1000 and meta["completion_tokens"] > 500
    assert result.report["digest_truncated"] is False and result.report["fixes"] == []


def test_bad_answer_is_retried_with_hint(
    example_package: dict[str, Any], make_client: Any, stub: Any
) -> None:
    good = _answer_for(example_package)
    stub.answer({"key_takeaway": "", "theses": []}, times=1)
    stub.answer(good)
    client = make_client(stub, max_retries=2)
    result = st.build_story(example_package, {}, client=client, skill=get_skill("story_planner"))
    assert result.report["llm"]["attempts"] == 2
    assert len(stub.calls) == 2 and stub.calls[1].messages[-1].role == "user"
    assert "не прошёл проверку" in stub.calls[1].messages[-1].text
    assert result.story["generation_meta"]["cache_hit"] is False


def test_no_model_outline_is_marked(example_package: dict[str, Any]) -> None:
    result = st.build_story(example_package, {"language": "ru"}, use_model=False)
    story = result.story
    assert StoryPlan.model_validate(story)
    assert any(w["code"] == "story_without_model" for w in story["warnings"])
    assert story["generation_meta"]["models"] == []
    assert (
        story["coverage"]["must_keep_facts"]["covered"]
        == story["coverage"]["must_keep_facts"]["total"]
    )


def test_missing_model_is_explicit_error(example_package: dict[str, Any]) -> None:
    with pytest.raises(st.StoryError) as info:
        st.build_story(example_package, {}, client=None, skill=None)
    assert info.value.code == "story_llm_not_configured"


def test_pipeline_reuses_story_by_content_hash(
    tmp_path: pathlib.Path,
    monkeypatch: Any,
    replay_client: Any,
) -> None:
    """Генерация с настоящими импортом и планом: план строится один раз на содержание,
    повтор с теми же настройками — попадание, смена языка — новый план (и промах replay)."""
    from fastapi.testclient import TestClient

    from presentation_designer.api.app import create_app
    from presentation_designer.pipeline.artifacts import ArtifactStore
    from presentation_designer.pipeline.files import FileStore
    from presentation_designer.pipeline.jobs import InlineExecutor, Orchestrator
    from presentation_designer.pipeline.real import RealLayers
    from presentation_designer.pipeline.state import State

    # This test isolates story caching, not the external rendering service.
    from presentation_designer.pipeline.stubs import StubLayers

    monkeypatch.setattr(RealLayers, "export", StubLayers.export)
    settings = Settings()
    settings.paths.data_dir = tmp_path / "data"
    settings.paths.artifacts_dir = tmp_path / "artifacts"
    settings.paths.runs_dir = tmp_path / "runs"
    settings.content_import.cache_dir = tmp_path / "import-cache"
    settings.content_import.fact_context_model = False
    settings.plan.cache_dir = tmp_path / "plan-cache"
    layers = RealLayers(settings)
    layers._llm = replay_client
    orch = Orchestrator(
        settings,
        State(settings.db_path),
        FileStore(settings.uploads_dir, max_upload_mb=4, max_unzipped_mb=64),
        ArtifactStore(settings.artifacts_dir),
        layers,
        InlineExecutor(),
    )
    pptx = (
        pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "pptx" / "mini_template.pptx"
    ).read_bytes()
    brief = json.loads((EXAMPLES / "brief.json").read_text())
    with TestClient(create_app(orch, reconcile=False)) as client:
        template_id = client.post(
            "/api/templates", files={"file": ("mini.pptx", pptx, "application/octet-stream")}
        ).json()["template_id"]
        project_id = client.post("/api/projects", json={"title": "story"}).json()["project_id"]
        file_ids = []
        for name in EXAMPLE_FILES:
            r = client.post(
                f"/api/projects/{project_id}/files",
                files=[
                    ("files", (name, (EXAMPLES / name).read_bytes(), "application/octet-stream"))
                ],
            )
            file_ids.append(r.json()[0]["file_id"])
        package_id = client.post(
            "/api/content", json={"file_ids": file_ids, "brief": brief}
        ).json()["package_id"]
        assert client.get(f"/api/content/{package_id}").json()["status"] == "succeeded"

        def generate(settings_doc: dict[str, Any], key: str) -> dict[str, Any]:
            job_id = client.post(
                "/api/generations",
                json={
                    "schema_version": "1.2",
                    "template_id": template_id,
                    "package_id": package_id,
                    "idempotency_key": key,
                    "settings": settings_doc,
                },
            ).json()["job_id"]
            return client.get(f"/api/generations/{job_id}").json()

        first = generate({"language": "ru"}, "k1")
        assert first["status"] in ("needs_review", "succeeded"), first.get("error")
        assert first["metrics"]["cache"]["story_hit"] is False
        story = client.get(f"/api/generations/{first['job_id']}/story").json()
        assert StoryPlan.model_validate(story) and story["language"] == "ru"
        # Три настоящих плана вариантов из одного смыслового плана: общее содержание не
        # генерируется повторно — вызовы стадии story один раз, планы ссылаются на story_id.
        assert first["execution_mode"]["layers"]["generation.plan"] == "real"
        plans: dict[str, dict[str, Any]] = {}
        for v in first["variants"]:
            assert v["status"] in ("ready", "needs_review"), v.get("error")
            plan = client.get(
                f"/api/generations/{first['job_id']}/artifacts/{v['plan_artifact']}"
            ).json()
            SlidePlan.model_validate(plan)
            assert plan["story_id"] == story["story_id"] and plan["coverage"]["missing"] == []
            assert plan["variant"]["value"] == v["variant_id"]
            plans[v["variant_id"]] = plan
            stages = {s["stage"]: s for s in v["stages"]}
            assert stages["plan"]["status"] == "done" and not stages["plan"].get("cache_hit")
        assert len({p["comparison"]["text_chars_total"] for p in plans.values()}) >= 2
        # Вызовы модели этапов записаны в метрики задания, а регистратор клиента опустошён.
        llm_calls = first["metrics"]["llm_calls"]
        story_calls = [c for c in llm_calls if c["stage"] == "story"]
        plan_calls = [c for c in llm_calls if c["stage"] == "plan"]
        assert len(story_calls) == 1 and len(plan_calls) >= 3
        assert first["metrics"]["totals"]["llm_calls"] == len(llm_calls) >= 4
        assert not replay_client.recorder.calls
        second = generate({"language": "ru"}, "k2")
        assert second["metrics"]["cache"]["story_hit"] is True
        assert second["execution_mode"]["layers"]["generation.story"] == "real"
        # Планы переиспользуются из кэша по ключу: стадия plan отмечена попаданием.
        for v in second["variants"]:
            stages = {s["stage"]: s for s in v["stages"]}
            assert stages["plan"].get("cache_hit") is True
        assert not [c for c in second["metrics"]["llm_calls"] if c["stage"] == "story"]
        # Другой язык — другой смысловой ключ: записи нет, план не строится, задание честно падает.
        third = generate({"language": "en"}, "k3")
        assert third["status"] == "failed" and third["error"]["stage"] == "story"
        assert third["error"]["code"] == "replay_miss"
