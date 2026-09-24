"""Извлечение брифа из сообщения чата: эвристика на наборе русских формулировок,
модель на записанных ответах (replay), проверка опоры значений на текст, резерв при
ошибке и тайм-ауте, слой через API."""

from __future__ import annotations

import asyncio
import json
import pathlib
from typing import Any

import pytest

from presentation_designer.contracts import models as m
from presentation_designer.llm.skills import get_skill
from presentation_designer.llm.types import ProviderError
from presentation_designer.parsing.content.brief import (
    extract_brief,
    extract_brief_with_model,
    validate_model_answer,
)
from tests.parsing.content.conftest import LLM_FIXTURES

PHRASES = {
    p["id"]: p["text"] for p in json.loads((LLM_FIXTURES / "brief_phrases.json").read_text())
}


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        (
            "generate_full",
            {
                "intent": "generate",
                "purpose": "product",
                "title": "Запуск сервиса умных уведомлений",
                "audience": "руководителей",
                "slide_count": {"min": 10, "max": 12},
            },
        ),
        ("report_exact", {"purpose": "report", "tone": "деловой", "slide_count": {"exact": 8}}),
        ("no_purpose", {"intent": "generate", "purpose": None, "title": "Итоги квартала"}),
        ("greeting", {"intent": "none", "understood": []}),
        ("edit_swap", {"intent": "edit", "understood": []}),
        (
            "variants_only",
            {"purpose": "project", "title": "Северный мост", "variants": ["compact", "balanced"]},
        ),
        ("must_avoid", {"purpose": "initiative", "avoid": ["технических деталей"]}),
        ("english", {"language": "en"}),
        ("question_only", {"intent": "none", "understood": []}),
        ("feature_generate", {"intent": "generate", "purpose": "feature", "title": "Тихие часы"}),
    ],
)
def test_heuristic_phrases(phrase: str, expected: dict[str, Any]) -> None:
    doc = extract_brief(PHRASES[phrase])
    m.BriefExtract.model_validate({"schema_version": "1.2", **doc})
    assert doc["source"] == "heuristic"
    _check(doc, expected)


def _stems(values: Any) -> set[str]:
    """Первые буквы слов: сравнение без окончаний: модель отвечает то «технические детали»,
    то «технических деталей» — это один и тот же ответ."""
    out: set[str] = set()
    for item in values or []:
        out |= {w[:5].lower() for w in str(item).split() if len(w) > 2}
    return out


def _check(doc: dict[str, Any], expected: dict[str, Any]) -> None:
    for key, value in expected.items():
        if key in ("intent", "understood", "slide_count", "variants"):
            assert doc.get(key) == value, (key, doc)
        elif key in ("must_include", "avoid") and value is not None:
            assert _stems(doc["brief"].get(key)) == _stems(value), (key, doc)
            assert key in doc["understood"]
        else:
            assert doc["brief"].get(key) == value, (key, doc)
            if value is not None:
                assert key in doc["understood"]


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        (
            "generate_full",
            {
                "intent": "generate",
                "purpose": "product",
                "title": "Запуск сервиса умных уведомлений",
                "audience": "руководителей",
                "goal": "одобрили расширение пилота",
                "slide_count": {"min": 10, "max": 12},
            },
        ),
        (
            "report_exact",
            {"purpose": "report", "title": "Итоги третьего квартала", "slide_count": {"exact": 8}},
        ),
        # «Сделай слайды про итоги квартала для команды»: жанр прямо не назван.
        # qwen3.8-27b его и не выводит — оставляет поле пустым, и это верно:
        # додумывать назначение колоды за автора незачем.
        ("no_purpose", {"purpose": None, "title": "Итоги квартала", "audience": "команды"}),
        ("greeting", {"intent": "none", "understood": []}),
        ("edit_swap", {"intent": "edit", "understood": []}),
        (
            "variants_only",
            {"purpose": "project", "title": "Северный мост", "variants": ["compact", "balanced"]},
        ),
        (
            "must_avoid",
            {
                "purpose": "initiative",
                "must_include": ["бюджет", "сроки"],
                "avoid": ["технические детали"],
            },
        ),
        (
            "english",
            {"language": "en", "slide_count": {"min": 12, "max": 15}, "purpose": "product"},
        ),
        ("question_only", {"intent": "none", "understood": []}),
        (
            "feature_generate",
            {"purpose": "feature", "title": "Тихие часы", "goal": "согласовать релиз в октябре"},
        ),
    ],
)
def test_model_phrases_on_replay(replay_client: Any, phrase: str, expected: dict[str, Any]) -> None:
    doc = asyncio.run(
        extract_brief_with_model(
            PHRASES[phrase], None, client=replay_client, skill=get_skill("brief_extractor")
        )
    )
    m.BriefExtract.model_validate({"schema_version": "1.2", **doc})
    from presentation_designer.shared.settings import get_models_config

    assert doc["source"] == "model"
    assert doc["model"]["name"] == get_models_config().role("llm").model
    assert doc["model"]["reasoning_mode"] == "off"
    _check(doc, expected)


def test_model_answer_grounding_drops_invented_fields() -> None:
    text = "Сделай презентацию про запуск сервиса для руководителей, 10-12 слайдов"
    answer = {
        "purpose": "product",
        "title": "Запуск сервиса",
        "audience": "руководителей",
        "goal": "получить финансирование",  # в тексте нет
        "tone": "деловой",  # в тексте нет
        "must_include": ["финансовые показатели"],  # в тексте нет
        "slide_count": {"min": 10, "max": 12},
        "variants": ["compact"],  # варианты не упоминались
        "language": "en",  # язык не назван
        "intent": "generate",
    }
    doc = validate_model_answer(answer, text)
    assert doc["brief"] == {
        "purpose": "product",
        "title": "Запуск сервиса",
        "audience": "руководителей",
    }
    assert doc["slide_count"] == {"min": 10, "max": 12} and "variants" not in doc
    assert set(doc["understood"]) == {"purpose", "title", "audience", "slide_count"}
    bad = validate_model_answer(
        {"purpose": "sales", "slide_count": {"exact": 99}, "intent": "x"}, "99 слайдов"
    )
    assert bad == {"brief": {}, "understood": [], "intent": "none", "source": "model"}


FLAT_OUTLINE = (
    pathlib.Path(__file__).resolve().parents[2] / "fixtures" / "content" / "chat_outline_flat.txt"
)


def test_outline_message_sets_slide_count_and_keeps_markers_out_of_must_include(
    make_client: Any, stub: Any
) -> None:
    """Сообщение 25.09 (5964 знака, «Слайд 1…10» одной строкой): модель получает выдержку
    с началом и разделами, число слайдов — по раскладке, «Слайд N: …» не обязательные
    пункты брифа (их содержание приходит материалом)."""
    text = FLAT_OUTLINE.read_text(encoding="utf-8")
    stub.answer(
        {
            "purpose": None,
            "title": "Будущее Flutter в 2026 году: от кроссплатформы к автономным интерфейсам",
            "audience": None,
            "goal": None,
            "tone": None,
            "language": None,
            "must_include": ["Слайд 1: Титульный", "Слайд 2: Индустрия в цифрах", "Impeller"],
            "avoid": None,
            "slide_count": {"exact": 5, "min": None, "max": None},
            "variants": None,
            "intent": "generate",
        }
    )
    client = make_client(stub)
    doc = asyncio.run(
        extract_brief_with_model(text, None, client=client, skill=get_skill("brief_extractor"))
    )
    assert doc["source"] == "model"
    assert doc["slide_count"] == {"exact": 10}
    assert doc["brief"]["must_include"] == ["Impeller"]
    sent = "\n".join(m.text for m in stub.calls[0].messages)
    assert "Слайд 10: Резюме" in sent and "Финальный посыл" not in sent
    heuristic = extract_brief(text)
    assert heuristic["slide_count"] == {"exact": 10} and "slide_count" in heuristic["understood"]
    # Число, названное словами, важнее раскладки.
    named = extract_brief("Сделай на 7 слайдов. Слайд 1: Проблема. Слайд 2: Решение.")
    assert named["slide_count"] == {"exact": 7}


def test_model_failure_falls_back_to_heuristic(make_client: Any, stub: Any) -> None:
    stub.fail(ProviderError("boom", status=500, retryable=True), times=10)
    client = make_client(stub, max_retries=1)
    doc = asyncio.run(
        extract_brief_with_model(
            PHRASES["generate_full"], None, client=client, skill=get_skill("brief_extractor")
        )
    )
    assert doc["source"] == "heuristic" and doc["brief"]["purpose"] == "product"
    assert "model" not in doc


def test_slow_model_hits_deadline_then_heuristic(make_client: Any, stub: Any) -> None:
    stub.latency_ms = 800
    stub.answer({"intent": "generate", "purpose": "report"})
    client = make_client(stub, max_retries=0)
    doc = asyncio.run(
        extract_brief_with_model(
            "Сделай отчёт", None, client=client, skill=get_skill("brief_extractor"), deadline_s=0.3
        )
    )
    assert doc["source"] == "heuristic" and doc["brief"]["purpose"] == "report"


def test_empty_text_needs_no_model(make_client: Any, stub: Any) -> None:
    client = make_client(stub)
    doc = asyncio.run(
        extract_brief_with_model("   ", None, client=client, skill=get_skill("brief_extractor"))
    )
    assert doc == {"brief": {}, "understood": [], "intent": "none", "source": "heuristic"}
    assert stub.calls == []


def test_api_brief_uses_model_layer(tmp_path: Any, monkeypatch: Any, replay_client: Any) -> None:
    """POST /api/brief с настоящим слоем: ответ модели, `source: model`; при заглушечных
    слоях — эвристика."""
    from fastapi.testclient import TestClient

    from presentation_designer.api.app import create_app
    from presentation_designer.pipeline.artifacts import ArtifactStore
    from presentation_designer.pipeline.files import FileStore
    from presentation_designer.pipeline.jobs import InlineExecutor, Orchestrator
    from presentation_designer.pipeline.real import RealLayers
    from presentation_designer.pipeline.state import State
    from presentation_designer.shared.settings import Settings

    settings = Settings()
    settings.paths.data_dir = tmp_path / "data"
    settings.paths.artifacts_dir = tmp_path / "artifacts"
    settings.paths.runs_dir = tmp_path / "runs"
    layers = RealLayers(settings)
    layers._llm = replay_client  # клиент на записях вместо провайдера
    orch = Orchestrator(
        settings,
        State(settings.db_path),
        FileStore(settings.uploads_dir, max_upload_mb=2, max_unzipped_mb=64),
        ArtifactStore(settings.artifacts_dir),
        layers,
        InlineExecutor(),
    )
    with TestClient(create_app(orch, reconcile=False)) as client:
        r = client.post("/api/brief", json={"text": PHRASES["generate_full"]})
        assert r.status_code == 200, r.text
        doc = r.json()
        m.BriefExtract.model_validate(doc)
        assert doc["source"] == "model" and doc["intent"] == "generate"
        assert doc["brief"]["title"] == "Запуск сервиса умных уведомлений"
        assert doc["slide_count"] == {"min": 10, "max": 12}
        assert client.get("/api/capabilities").json()["execution_mode"]["layers"]["brief"] == "real"
        # Фраза без записи: replay падает, слой отвечает эвристикой, диалог не ждёт.
        r = client.post("/api/brief", json={"text": "Сделай отчёт про выручку для CFO"})
        assert r.json()["source"] == "heuristic" and r.json()["brief"]["purpose"] == "report"
