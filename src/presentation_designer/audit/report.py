"""Сборка AuditReport: что запускали, что нашли и что осталось непроверенным.

Отчёт честен в обе стороны. В `checks` перечислены все проверки реестра, включая те, что не
нашли ничего и те, что выполнить не удалось, — из этого списка собирается AUDIT.md. В
`results` записан исход каждой проверки для каждой области, поэтому «проверка прошла» и
«проверка не запускалась» различимы. В `coverage.missing_inputs` попадает причина, по которой
часть проверок пропущена: без модели контекстная часть невозможна, и отчёт этого не скрывает.
"""

from __future__ import annotations

import time
from typing import Any

import pathlib

from presentation_designer.audit.deterministic import Issue, check_package, run_slide_checks
from presentation_designer.audit.registry import ALL_CHECKS, BY_ID, Check

JsonDict = dict[str, Any]

CHECK_VERSION = "1.0"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _check_entry(check: Check, *, implemented: bool) -> JsonDict:
    entry = check.as_dict()
    entry["version"] = CHECK_VERSION
    # Все проверки взяты из Приложения 1 ТЗ: помечаем происхождение, чтобы в AUDIT.md было
    # видно, что список не выдуман, а покрывает ориентир заказчика.
    entry["origin"] = "appendix1"
    entry["implemented"] = implemented
    entry["inputs"] = _inputs_for(check)
    return entry


# Чем питается проверка. Список нужен в AUDIT.md: по нему видно, что детерминированная
# часть работает по файлу, а контекстная требует картинки слайда и исходных материалов.
_INPUTS_BY_CATEGORY = {
    "layout": ["composed_deck"],
    "template": ["composed_deck", "template_profile"],
    "density": ["composed_deck"],
    "integrity": ["composed_deck"],
    "content": ["render", "composed_deck"],
}
_INPUTS_BY_CHECK = {
    "layout.text_overflow": ["composed_deck", "font_metrics"],
    "integrity.package": ["xml"],
    "integrity.duplicate_slides": ["composed_deck", "whole_deck_text"],
    "content.facts_grounded": ["render", "composed_deck", "content_package"],
    "content.slides_connected": ["render", "neighbor_slides"],
    "content.one_language": ["whole_deck_text"],
    "content.table_works": ["render", "composed_deck", "content_package"],
}


def _inputs_for(check: Check) -> list[str]:
    return list(
        _INPUTS_BY_CHECK.get(
            check.check_id, _INPUTS_BY_CATEGORY.get(check.category, ["composed_deck"])
        )
    )


def _severity_of(check_id: str) -> str:
    check = BY_ID.get(check_id)
    return check.severity if check else "warning"


def _kind_of(check_id: str) -> str:
    check = BY_ID.get(check_id)
    return check.kind if check else "deterministic"


def _category_of(check_id: str) -> str:
    check = BY_ID.get(check_id)
    return check.category if check else "layout"


def issues_to_json(issues: list[Issue]) -> list[JsonDict]:
    out: list[JsonDict] = []
    for n, issue in enumerate(issues, start=1):
        check = BY_ID.get(issue.check_id)
        item: JsonDict = {
            "issue_id": f"iss_{n}",
            "check_id": issue.check_id,
            "severity": _severity_of(issue.check_id),
            "kind": _kind_of(issue.check_id),
            "message": issue.message,
            # Что можно сделать с находкой. Исправление не применяется само: пользователь
            # выбирает, что чинить, — этого требует ТЗ, и поэтому статус здесь «открыта».
            "fix": {
                "available": bool(check and check.fix_strategy != "none"),
                "strategy": check.fix_strategy if check else "none",
                "cost": check.fix_cost if check else "cheap",
                **({"description": check.fix_note} if check and check.fix_note else {}),
            },
            "status": "open",
        }
        if issue.slide_id:
            item["slide_id"] = issue.slide_id
        if issue.slide_index is not None:
            item["slide_index"] = issue.slide_index
        if issue.bbox:
            item["bbox"] = issue.bbox
        if issue.element_ids:
            item["element_ids"] = issue.element_ids
        if issue.evidence:
            item["evidence"] = issue.evidence
        out.append(item)
    return out


def _results(
    deck: JsonDict,
    issues: list[Issue],
    *,
    contextual: bool,
    not_checked: frozenset[str] = frozenset(),
) -> list[JsonDict]:
    """Исход каждой проверки по каждой области: пройдено, найдено или не запускалось."""
    failed: dict[tuple[str, int | None], int] = {}
    # Находка проверки уровня колоды может указывать на конкретный слайд (повтор слайда):
    # исход такой проверки считается по всей колоде, а не по ключу без слайда.
    failed_deck: dict[str, int] = {}
    for issue in issues:
        failed[(issue.check_id, issue.slide_index)] = (
            failed.get((issue.check_id, issue.slide_index), 0) + 1
        )
        failed_deck[issue.check_id] = failed_deck.get(issue.check_id, 0) + 1
    slides = deck.get("slides") or []
    out: list[JsonDict] = []
    for check in ALL_CHECKS:
        skipped = (check.kind == "contextual" and not contextual) or check.check_id in not_checked
        if check.scope == "deck":
            entry: JsonDict = {"check_id": check.check_id, "scope": "deck"}
            entry["outcome"] = (
                "not_checked"
                if skipped
                else ("failed" if failed_deck.get(check.check_id) else "passed")
            )
            out.append(entry)
            continue
        for slide in slides:
            index = int(slide.get("index", 0))
            entry = {
                "check_id": check.check_id,
                "scope": "slide",
                "slide_id": str(slide.get("slide_id") or ""),
                "slide_index": index,
            }
            entry["outcome"] = (
                "not_checked"
                if skipped
                else ("failed" if failed.get((check.check_id, index)) else "passed")
            )
            out.append(entry)
    return out


def _summary(issues_json: list[JsonDict], deck: JsonDict) -> JsonDict:
    by_severity: dict[str, int] = {"blocking": 0, "error": 0, "warning": 0, "info": 0}
    by_category: dict[str, int] = {}
    by_kind: dict[str, int] = {"deterministic": 0, "contextual": 0}
    slides: set[int] = set()
    for item in issues_json:
        by_severity[str(item["severity"])] = by_severity.get(str(item["severity"]), 0) + 1
        category = _category_of(str(item["check_id"]))
        by_category[category] = by_category.get(category, 0) + 1
        by_kind[str(item["kind"])] = by_kind.get(str(item["kind"]), 0) + 1
        if item.get("slide_index") is not None:
            slides.add(int(item["slide_index"]))
    slide_count = max(len(deck.get("slides") or []), 1)
    # Оценка: слайд без находок — целый балл, ошибка весит больше замечания. Нужна, чтобы
    # сравнивать варианты между собой, а не как самостоятельная метрика качества.
    penalty = (
        by_severity["blocking"] * 3 + by_severity["error"] * 1.5 + by_severity["warning"] * 0.5
    )
    score = max(0.0, 100.0 * (1 - penalty / (slide_count * 3)))
    return {
        "issues_total": len(issues_json),
        "by_severity": by_severity,
        "by_category": by_category,
        "by_kind": by_kind,
        "slides_with_issues": len(slides),
        "score": round(score, 1),
    }


def build_report(
    *,
    job_id: str,
    variant_id: str,
    revision: int,
    deck: JsonDict,
    profile: JsonDict,
    staging_prefix: str,
    contextual: bool,
    contextual_answers: list[JsonDict] | None = None,
    contextual_issues: list[Issue] | None = None,
    missing_inputs: list[str] | None = None,
    started: float | None = None,
    llm_metrics: JsonDict | None = None,
    pptx_path: pathlib.Path | None = None,
) -> JsonDict:
    """Отчёт аудита по собранной колоде. `pptx_path` — сам файл: по нему проверяется
    целостность пакета; без файла эта проверка честно помечается `not_checked`."""
    began = started if started is not None else time.perf_counter()
    issues = run_slide_checks(deck, profile)
    unchecked: set[str] = set()
    missing = list(missing_inputs or [])
    if pptx_path is not None and pptx_path.exists():
        issues += check_package(pptx_path)
    else:
        unchecked.add("integrity.package")
        missing.append("pptx_file")
    issues += list(contextual_issues or [])
    issues_json = issues_to_json(issues)
    answers = list(contextual_answers or [])

    checks = [
        _check_entry(c, implemented=c.kind == "deterministic" or contextual) for c in ALL_CHECKS
    ]
    results = _results(deck, issues, contextual=contextual, not_checked=frozenset(unchecked))
    not_checked = sum(1 for r in results if r["outcome"] == "not_checked")
    fonts = [
        {
            "requested": str(f.get("family") or ""),
            "actual": str(f.get("fallback") or f.get("family") or ""),
            **({"file": str(f["file"])} if f.get("file") else {}),
        }
        for f in deck.get("fonts") or []
    ]
    report: JsonDict = {
        "schema_version": "1.1",
        "report_id": f"audit_{job_id}_{variant_id}_r{revision}",
        "job_id": job_id,
        "variant_id": variant_id,
        "revision": revision,
        "created_at": _now(),
        "deck": {
            "pptx_artifact": f"{staging_prefix}deck.pptx",
            "composed_deck_artifact": f"{staging_prefix}composed.json",
            "slide_count": len(deck.get("slides") or []),
            **({"pptx_hash": str(deck["pptx_hash"])} if deck.get("pptx_hash") else {}),
            **({"fonts": fonts} if fonts else {}),
        },
        "checks": checks,
        "issues": issues_json,
        "contextual_answers": answers,
        "summary": _summary(issues_json, deck),
        "results": results,
        "coverage": {
            "complete": not_checked == 0,
            "checked": len(results) - not_checked,
            "not_checked": not_checked,
            "not_applicable": 0,
            **({"missing_inputs": missing} if missing else {}),
        },
        "metrics": {
            "duration_ms": int((time.perf_counter() - began) * 1000),
            **{k: v for k, v in (llm_metrics or {}).items()},
        },
    }
    return report
