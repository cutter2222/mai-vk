"""Реестр фактов: числа, проценты, деньги, даты с контекстом; производные показатели;
наборы данных из таблиц; уточнение контекста моделью с проверкой опоры на фрагмент."""

from __future__ import annotations

from typing import Any

from presentation_designer.llm.types import ProviderError
from presentation_designer.parsing.content.datasets import build_dataset
from presentation_designer.parsing.content.facts import (
    TextUnit,
    extract_dataset_facts,
    extract_text_facts,
    refine_facts_with_model,
)
from presentation_designer.parsing.content.numbers import find_numbers, parse_number_text
from presentation_designer.parsing.content.parsers import parse_file
from tests.parsing.content.conftest import CONTENT


def test_numbers_russian_formats() -> None:
    text = (
        "Выручка за 2025 год выросла на 25 % год к году и достигла 1 200 млн руб.; "
        "DAU 2,4 млн, +13 п. п. к плану, $3.5M ARR, 10–12 слайдов, запуск 16.09.2026, "
        "3 квартал 2026, рост x2, 15 000 клиентов"
    )
    found = {m.raw: m for m in find_numbers(text)}
    assert found["25 %"].kind == "percent" and found["25 %"].value == 25
    assert found["1 200 млн руб."].kind == "money" and found["1 200 млн руб."].value == 1.2e9
    assert found["1 200 млн руб."].unit == "₽"
    assert found["2,4 млн"].value == 2.4e6
    assert found["+13 п. п."].unit == "п. п." and found["+13 п. п."].sign == "+"
    assert found["$3.5M"].kind == "money" and found["$3.5M"].value == 3.5e6
    assert found["10–12 слайдов"].kind == "range" and found["10–12 слайдов"].value_to == 12
    assert found["16.09.2026"].value == "2026-09-16"
    assert found["3 квартал 2026"].value == "2026-Q3"
    assert found["2025 год"].kind == "date"
    assert found["x2"].kind == "ratio"
    assert found["15 000 клиентов"].value == 15000 and found["15 000 клиентов"].unit == "клиентов"
    assert parse_number_text("12 500,5") == 12500.5 and parse_number_text("1,234") == 1.234


def test_text_facts_context_and_derived() -> None:
    units = [
        TextUnit(
            "b6",
            "src_1",
            "Экономия составила 12,5 млн ₽ за год при росте открываемости с 31 % до 44 %.",
            heading="Результат пилота",
        ),
        TextUnit(
            "b7",
            "src_1",
            "Выручка компании «Ромашка» за 2025 год выросла на 25 % год к году и достигла "
            "1 200 млн руб.",
        ),
    ]
    facts = {f.fact_id: f for f in extract_text_facts(units)}
    economy = facts["f1"]
    assert economy.kind == "money" and economy.value == 12_500_000 and economy.must_keep
    assert economy.context["metric"] == "Экономия" and economy.context["period"] == "за год"
    assert economy.source_location["fragment"].startswith("Экономия составила")
    assert economy.uncertainty["extracted_by"] == "regex"
    first, second = facts["f2"], facts["f3"]
    assert (first.value, second.value) == (31, 44)
    assert first.context["metric"] == "росте открываемости"
    assert second.context["comparison"] == "с 31 % до 44 %"
    derived = facts["f4"]
    assert derived.derived == {"formula": "44 - 31", "inputs": ["f2", "f3"]}
    assert derived.value == 13 and derived.unit == "п. п." and not derived.must_keep
    assert derived.uncertainty["level"] == "inferred"
    revenue = facts["f5"]
    assert revenue.context["metric"] == "Выручка компании"
    assert revenue.context["subject"] == "Ромашка"
    assert revenue.context["period"] == "за 2025 год"
    assert revenue.context["comparison"] == "год к году"
    total = facts["f6"]
    assert total.value == 1.2e9 and total.context["metric"] == "Выручка компании"
    assert total.ambiguous  # показатель унаследован от соседнего факта — уточнит модель


def test_dataset_types_units_and_cell_facts() -> None:
    doc = parse_file(CONTENT / "metrics.xlsx", "xlsx")
    dataset = build_dataset(doc.tables[0], dataset_id="ds1", source_id="src_2", block_id="b4")
    assert dataset is not None
    assert [c.type for c in dataset.columns] == ["date", "percent", "percent"]
    assert dataset.columns[1].name == "Открываемость" and dataset.columns[1].unit == "%"
    assert dataset.rows == [["Май", 31, 4.1], ["Июнь", 38, 3.2], ["Июль", 44, 2.7]]
    assert dataset.as_dict()["source_location"] == {"sheet": "Метрики", "cell_range": "A1:C4"}
    facts = extract_dataset_facts(dataset, start_index=10)
    assert [f.fact_id for f in facts] == ["f10", "f11", "f12", "f13", "f14", "f15"]
    first = facts[0]
    assert first.raw == "31 %" and first.source_location["cell"] == "B2"
    assert first.context == {
        "metric": "Открываемость",
        "period": "Май",
        "subject": "Метрики",
        "unit": "%",
    }
    assert not first.must_keep and first.uncertainty["extracted_by"] == "parser"
    change = facts[2]
    assert change.derived == {"formula": "44 - 31", "inputs": ["f10", "f11"]}
    assert change.context["comparison"] == "Май → Июль"
    unsub_change = facts[5]
    assert unsub_change.value == -1.4 and unsub_change.raw == "−1.4 п. п."


def test_dataset_from_text_cells_with_units() -> None:
    doc = parse_file(CONTENT / "report_with_table.docx", "docx")
    dataset = build_dataset(doc.tables[0], dataset_id="ds1", source_id="s", block_id=None)
    assert dataset is not None
    assert [c.type for c in dataset.columns] == ["date", "money", "number"]
    assert dataset.columns[1].name == "Выручка" and dataset.columns[1].unit == "млн ₽"
    assert dataset.rows == [["1 кв.", 80, 1200], ["2 кв.", 95, 1450]]


def test_model_refinement_accepts_grounded_and_rejects_invented(
    make_client: Any, stub: Any
) -> None:
    from presentation_designer.llm.skills import get_skill

    units = [
        TextUnit(
            "b2",
            "src_1",
            "Пользователи пропускают до 40 % важных уведомлений, потому что получают их "
            "без приоритизации.",
        ),
        TextUnit("b3", "src_1", "Сумма — 3 400 ₽."),
    ]
    facts = extract_text_facts(units)
    ambiguous = [f for f in facts if f.ambiguous]
    assert [f.raw for f in ambiguous] == ["3 400 ₽"]
    # Делаем оба факта неоднозначными, чтобы проверить принятие и отказ.
    for f in facts:
        f.ambiguous = True
    stub.answer(
        {
            "items": [
                {
                    "fact_id": "f1",
                    "metric": "доля пропущенных важных уведомлений",
                    "period": "2025 год",  # в фрагменте нет — не принимается
                    "confidence": 0.9,
                },
                {"fact_id": "f2", "metric": "выручка компании", "confidence": 0.95},  # выдумано
            ]
        }
    )
    client = make_client(stub)
    summary = refine_facts_with_model(facts, client, get_skill("content_importer"), budget_s=5)
    assert summary["asked"] == 2 and summary["accepted"] == 1 and summary["rejected"] == 1
    first = facts[0]
    assert first.context["metric"] == "доля пропущенных важных уведомлений"
    assert "period" not in first.context
    assert first.uncertainty == {"level": "inferred", "extracted_by": "model", "confidence": 0.9}
    assert facts[1].context.get("metric") in (None, "Сумма")
    assert stub.calls[0].stage == "import" and stub.calls[0].reasoning == "off"


def test_model_refinement_survives_provider_error(make_client: Any, stub: Any) -> None:
    from presentation_designer.llm.skills import get_skill

    facts = extract_text_facts([TextUnit("b1", "s", "Всего — 500 ₽.")])
    for f in facts:
        f.ambiguous = True
    stub.fail(ProviderError("502", status=502, retryable=True), times=5)
    client = make_client(stub, max_retries=1)
    summary = refine_facts_with_model(facts, client, get_skill("content_importer"), budget_s=3)
    assert summary["accepted"] == 0 and summary["errors"]
    assert facts[0].uncertainty["extracted_by"] == "regex"
