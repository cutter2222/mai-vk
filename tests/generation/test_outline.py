"""Раскладка пользователя «Слайд N» из текста чата: сюжет держит раздел на слайд и
достраивает пропущенный моделью раздел, план — обложка из титульного раздела и ровно по
слайду на раздел, без финала «Спасибо», разделителей и оглавления (колода 25.09: пятый
слайд «Главный тренд 2026» вытеснялся финалом, текст пользователя терялся в брифе)."""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from presentation_designer.generation import variants as vr
from presentation_designer.generation.outline import user_outline
from presentation_designer.generation.story import build_story
from presentation_designer.llm.skills import get_skill
from presentation_designer.parsing.content import chat_text
from presentation_designer.parsing.content.importer import import_content
from presentation_designer.parsing.content.parsers import ParseCache
from presentation_designer.shared.settings import Settings
from tests.fixtures.rich_template import build_rich_template
from tests.generation.test_variants import _assert_valid, own_profile
from tests.parsing.content.conftest import material

TEXT = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "content" / "chat_outline_5.txt"


@pytest.fixture(scope="module")
def rich_profile(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    return own_profile(
        build_rich_template(tmp_path_factory.mktemp("tpl") / "rich_template.pptx"), "tpl_rich"
    )


@pytest.fixture
def outline_package(
    tmp_path: pathlib.Path, cache: ParseCache, import_settings: Settings
) -> dict[str, Any]:
    path = tmp_path / "Текст из чата.md"
    path.write_text(chat_text.to_markdown(TEXT.read_text(encoding="utf-8")), encoding="utf-8")
    brief = {"title": "Будущее Flutter в 2026 году", "language": "ru", "slide_count": {"exact": 5}}
    return import_content(
        [material(path)],
        brief,
        package_id="pkg_outline",
        settings=import_settings,
        cache=cache,
        use_model=False,
    ).package


def _answer(package: dict[str, Any]) -> dict[str, Any]:
    """Ответ сюжета, как у модели: раздел-обёртка, тезисы слайдов 2–4, вывод; слайда 5 нет."""
    slides = user_outline(package)
    theses: list[dict[str, Any]] = [
        {"id": "s0", "kind": "section", "statement": "Flutter в 2026 году", "required": True}
    ]
    for n, slide in enumerate(slides[1:4], start=2):
        theses.append(
            {
                "id": f"t{n}",
                "kind": "claim",
                "statement": slide.title,
                "explanation": "Ключевые пункты раздела.",
                "required": True,
                "source_refs": slide.block_ids[1:3],
            }
        )
    theses.append(
        {
            "id": "tz",
            "kind": "conclusion",
            "statement": "Flutter — выбор 2026 года",
            "required": True,
        }
    )
    return {"key_takeaway": "Flutter — стандарт", "theses": theses, "assumptions": []}


def test_outline_story_and_plans_follow_user_slides(
    outline_package: dict[str, Any], rich_profile: dict[str, Any], make_client: Any, stub: Any
) -> None:
    slides = user_outline(outline_package)
    assert [s.number for s in slides] == [1, 2, 3, 4, 5] and slides[0].cover
    assert slides[0].fields["заголовок"].startswith("Flutter 2026: Эволюция стандарта")
    stub.answer(_answer(outline_package))
    story = build_story(
        outline_package,
        {"language": "ru", "slide_count": {"exact": 5}},
        client=make_client(stub),
        skill=get_skill("story_planner"),
    ).story
    sent = "\n".join(m.text for m in stub.calls[0].messages)
    assert "Раскладка пользователя" in sent and "5 разделов" in sent
    # Пропущенный моделью «Слайд 5» достроен из раздела пользователя.
    added = [w for w in story["warnings"] if w["code"] == "outline_slide_added"]
    assert [w["message"] for w in added] == ["Главный тренд 2026 — GenUI и Agentic Applications"]
    # По тезису на раздел: вывод модели без ссылок влит в последний слайд, а не отдельным.
    content = [t for t in story["theses"] if t["kind"] != "section"]
    assert len(content) == 4
    assert any(w["code"] == "outline_thesis_merged" for w in story["warnings"])
    for variant_id in vr.VARIANTS:
        result = vr.build_variant_plan(
            story,
            rich_profile,
            outline_package,
            variant_id,
            {"language": "ru", "slide_count": {"exact": 5}},
            slide_count=5,
            use_model=False,
        )
        plan = result.plan
        _assert_valid(plan, rich_profile, outline_package, story)
        assert len(plan["slides"]) == 5, variant_id
        structure = result.report["structure"]
        assert structure["final_pattern"] is None and structure["dividers"] == []
        cover = plan["slides"][0]
        texts = " ".join(str(b.get("text") or "") for b in cover["blocks"])
        assert "Flutter 2026: Эволюция стандарта" in texts
        assert "Метрики доминирования" in texts
        # Слайды идут в порядке раскладки: 2, 3, 4, 5.
        order = [
            next((i for i, s in enumerate(slides) if set(s.block_ids) & set(slide_refs(p))), None)
            for p in plan["slides"][1:]
        ]
        assert order == [1, 2, 3, 4], (variant_id, order)


def slide_refs(slide: dict[str, Any]) -> list[str]:
    return [str(r) for b in slide["blocks"] for r in b.get("source_refs") or []] + [
        str(r) for r in slide.get("source_refs") or []
    ]
