"""AUDIT.md из реестра проверок: что сервис проверяет, чем и чем это подтверждено.

Документ собирается из кода, а не пишется руками: реестр (`audit/registry.py`) даёт список,
пороги и серьёзность, исходники проверок — признак реализации, тесты — доказательство.
Проверка, у которой нет ни реализации, ни теста, попадает в таблицу с пустой клеткой: ТЗ
требует документ об аудите, а не рекламу аудита.

Запуск: uv run scripts/gen_audit_md.py [--check]
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from presentation_designer.audit.contextual import QUESTIONS  # noqa: E402
from presentation_designer.audit.registry import ALL_CHECKS, Check  # noqa: E402
from presentation_designer.audit.report import CHECK_VERSION, inputs_for  # noqa: E402

TARGET = ROOT / "AUDIT.md"
SOURCES = ROOT / "src" / "presentation_designer" / "audit"
TESTS = ROOT / "tests"

CATEGORY_TITLES = {
    "layout": "Вёрстка",
    "template": "Шаблон",
    "density": "Плотность",
    "integrity": "Целостность",
    "content": "Содержание",
}
SEVERITY_TITLES = {
    "blocking": "блокирующая",
    "error": "ошибка",
    "warning": "замечание",
    "info": "справочно",
}
KIND_TITLES = {"deterministic": "по файлу", "contextual": "моделью"}
SCOPE_TITLES = {"slide": "слайд", "deck": "колода"}


def _sources() -> dict[str, str]:
    """Где живёт проверка: файл, в котором упомянут её идентификатор."""
    found: dict[str, str] = {}
    for path in sorted(SOURCES.glob("*.py")):
        if path.name in {"registry.py", "report.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        for check in ALL_CHECKS:
            if check.check_id in text and check.check_id not in found:
                found[check.check_id] = f"audit/{path.name}"
    for number, (check_id, _) in QUESTIONS.items():
        found.setdefault(check_id, f"audit/contextual.py (вопрос {number})")
    return found


def _tests() -> dict[str, list[str]]:
    """Чем подтверждено: тесты, в которых идентификатор проверки встречается явно."""
    hits: dict[str, list[str]] = {}
    for path in sorted(TESTS.rglob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        for check in ALL_CHECKS:
            if re.search(rf'["\']{re.escape(check.check_id)}["\']', text):
                hits.setdefault(check.check_id, []).append(
                    str(path.relative_to(ROOT)).replace("tests/", "")
                )
    return hits


def _threshold(check: Check) -> str:
    if not check.threshold:
        return "—"
    return ", ".join(f"`{k}` = {v}" for k, v in check.threshold.items())


def _row(check: Check, sources: dict[str, str], tests: dict[str, list[str]]) -> str:
    source = sources.get(check.check_id, "—")
    evidence = tests.get(check.check_id) or []
    shown = ", ".join(f"`{name}`" for name in evidence[:2])
    if len(evidence) > 2:
        shown += f" и ещё {len(evidence) - 2}"
    return (
        f"| `{check.check_id}` | {check.name} | {KIND_TITLES[check.kind]} | "
        f"{SCOPE_TITLES[check.scope]} | {SEVERITY_TITLES[check.severity]} | {_threshold(check)} | "
        f"{', '.join(inputs_for(check))} | `{source}` | {shown or '—'} |"
    )


def build() -> str:
    sources = _sources()
    tests = _tests()
    deterministic = [c for c in ALL_CHECKS if c.kind == "deterministic"]
    contextual = [c for c in ALL_CHECKS if c.kind == "contextual"]
    without_tests = [c.check_id for c in ALL_CHECKS if c.check_id not in tests]

    lines = [
        "# Аудит презентации",
        "",
        "Документ собран из кода: `uv run scripts/gen_audit_md.py`. Список проверок, пороги и",
        "серьёзность берутся из реестра `src/presentation_designer/audit/registry.py`, признак",
        "реализации — из исходников проверок, доказательства — из тестов. Правки руками",
        "перезаписываются следующим запуском.",
        "",
        "## Как устроен аудит",
        "",
        f"Проверок в реестре: **{len(ALL_CHECKS)}** — {len(deterministic)} считаются по файлу и",
        f"{len(contextual)} задаются модели. Все взяты из Приложения 1 ТЗ.",
        "",
        "* **По файлу** (`audit/deterministic.py`) — геометрия, шрифты, палитра, макет, плотность,",
        "  целостность пакета. Считаются по ComposedDeck и по самому PPTX, без модели и без",
        "  токенов, поэтому на одном и том же файле всегда дают один и тот же ответ.",
        "* **Моделью** (`audit/contextual.py`) — 11 вопросов о содержании. Модель получает",
        "  картинку слайда, его текст, состав объектов и список фактов из исходных материалов.",
        "  Девять вопросов задаются по каждому слайду одним вызовом, два (язык колоды и связность",
        "  соседей) — одним вызовом по тексту всей колоды.",
        "",
        "Отчёт (`audit_report`) перечисляет исход каждой проверки для каждой области:",
        "",
        "| исход | что значит |",
        "| --- | --- |",
        "| `passed` | проверка выполнена, нарушения нет |",
        "| `failed` | нарушение найдено, в отчёте есть находка со ссылкой на слайд и объект |",
        "| `not_applicable` | проверять нечего: вопрос про таблицу слайду без таблицы"
        " не задаётся |",
        "| `not_checked` | проверку выполнить не удалось: нет входных данных, модель не уверена"
        " или вызов не прошёл; причина записана рядом |",
        "",
        "`coverage.complete` становится `false`, как только появилась хоть одна непроверенная",
        "область, а `coverage.missing_inputs` называет причину (`vlm_unavailable`,",
        "`slide_render`, `pptx_file`). Неприменимые проверки покрытие не ломают.",
        "",
        "Находка несёт стратегию исправления и его цену: правка файла, пересборка или",
        "перегенерация текста моделью. Исправления применяются только по выбору пользователя.",
        "",
        "## Проверки",
        "",
    ]

    header = (
        "| проверка | что ищет | как | область | серьёзность | порог | входы | код | тесты |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    )
    for category, title in CATEGORY_TITLES.items():
        rows = [c for c in ALL_CHECKS if c.category == category]
        if not rows:
            continue
        lines += [f"### {title}", "", header]
        lines += [_row(c, sources, tests) for c in rows]
        lines.append("")

    lines += [
        "## Чего аудит не делает",
        "",
        "* **Исправления по находкам** (`repair`) пока заглушка: новая ревизия повторяет файлы",
        "  предыдущей. Стратегия и цена у находок уже проставлены, применение — следующий шаг.",
        "* **Ответ модели недетерминирован.** Контекстные проверки на повторном запуске могут",
        "  ответить иначе; поэтому их серьёзность — замечание, кроме вопросов о фактах, мусоре",
        "  и пустом слайде.",
        "* **Без модели контекстной части нет.** Отчёт в этом случае помечает все 11 проверок",
        "  `not_checked` с причиной, а не выдаёт их за пройденные.",
        "* **Без рендера** (миниатюр слайдов) вопрос о картинках остаётся непроверенным:",
        "  остальные вопросы модель отвечает по тексту и составу объектов.",
        "",
        f"Версия реестра проверок: {CHECK_VERSION}.",
        "",
    ]
    if without_tests:
        lines += [
            "Проверки без отдельного теста: "
            + ", ".join(f"`{cid}`" for cid in without_tests)
            + ". Они выполняются и попадают в отчёт, но доказательства в виде дефектной",
            "фикстуры у них пока нет.",
            "",
        ]
    return "\n".join(lines)


def main() -> int:
    text = build()
    if "--check" in sys.argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != text:
            print("AUDIT.md устарел: перезапустите uv run scripts/gen_audit_md.py")
            return 1
        print("AUDIT.md соответствует реестру")
        return 0
    TARGET.write_text(text, encoding="utf-8")
    print(f"записан {TARGET.relative_to(ROOT)}: {len(ALL_CHECKS)} проверок")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
