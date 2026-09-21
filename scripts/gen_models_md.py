"""MODELS.md из config/models.yaml и манифестов скиллов.

ТЗ требует перечислить модели с ролями, ссылками на веса, лицензиями, числом параметров,
системными требованиями и режимами рассуждения. Всё это уже есть в конфиге и в манифестах —
документ собирается оттуда, чтобы не разойтись с кодом. Поля `verified` переносятся как есть:
непроверенный ориентир не выдаётся за подтверждённый API.

Запуск: uv run scripts/gen_models_md.py [--check]
"""

from __future__ import annotations

import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = ROOT / "MODELS.md"
MODELS = ROOT / "config" / "models.yaml"
SKILLS = ROOT / "skills"

ROLE_TITLES = {
    "llm": "текст (llm)",
    "vlm": "текст и изображения (vlm)",
    "text_to_image": "картинки по описанию (text_to_image)",
}
REASONING_TITLES = {"off": "выключено", "low": "короткое", "medium": "среднее", "high": "полное"}


def _yaml(path: pathlib.Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _skills() -> list[dict]:
    out = []
    for manifest in sorted(SKILLS.glob("*/skill.yaml")):
        data = _yaml(manifest)
        data["prompt_refs"] = ", ".join(
            f"`{p['id']}` {p['version']}" for p in data.get("prompts") or []
        )
        out.append(data)
    return out


def _memory_estimate(params_b: float | None) -> str:
    """Оценка памяти под веса, а не замер: параметры × байт на параметр.

    Сервис моделей не хостит — он ходит в OpenAI-совместимый endpoint. Цифры нужны тому, кто
    поднимает веса у себя, и честнее показать расчёт, чем выдать чужой бенчмарк за свой.
    """
    if not params_b:
        return "—"
    bf16 = params_b * 2
    int4 = params_b * 0.6
    return f"≈{bf16:.0f} ГБ в bf16, ≈{int4:.0f} ГБ при 4-битном квантовании (+ память под контекст)"


def build() -> str:
    cfg = _yaml(MODELS)
    providers = cfg.get("providers") or {}
    roles = cfg.get("roles") or {}
    active = cfg.get("active_provider")

    lines = [
        "# Модели",
        "",
        "Документ собран из кода: `uv run scripts/gen_models_md.py` по `config/models.yaml` и",
        "манифестам скиллов. Правки руками перезаписываются следующим запуском.",
        "",
        "Сервис не хостит веса: он ходит в OpenAI-совместимый endpoint провайдера, а роль",
        "решает, какая модель отвечает за какой этап. Ключи и адреса живут только в",
        "окружении — в конфиге записаны имена переменных.",
        "",
        "## Роли",
        "",
        "| роль | модель | веса | лицензия | параметров | рассуждение | температура | проверено |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for role, data in roles.items():
        if data.get("enabled") is False or not data.get("model"):
            continue
        reasoning = data.get("reasoning") or {}
        mode = REASONING_TITLES.get(str(reasoning.get("mode")), str(reasoning.get("mode") or "—"))
        budget = reasoning.get("max_output_tokens")
        verified = data.get("verified") or {}
        by, date = verified.get("by"), verified.get("date")
        proof = f"{by}, {date}" if by else "не проверено"
        hf = data.get("hf_url") or ""
        lines.append(
            f"| {ROLE_TITLES.get(role, role)} | `{data['model']}` | "
            f"{f'[{hf.rsplit("/", 1)[-1]}]({hf})' if hf else '—'} | {data.get('license') or '—'} | "
            f"{data.get('params_b') or '—'} млрд | {mode}"
            f"{f', до {budget} токенов ответа' if budget else ''} | "
            f"{data.get('temperature') if data.get('temperature') is not None else '—'} | {proof} |"
        )

    disabled = [r for r, d in roles.items() if d.get("enabled") is False or not d.get("model")]
    if disabled:
        lines += [
            "",
            "Выключенные роли: "
            + ", ".join(f"`{r}`" for r in disabled)
            + ". Они не вызываются и в результате не появляются.",
        ]

    lines += [
        "",
        "## Системные требования",
        "",
        "| роль | модель | память под веса |",
        "| --- | --- | --- |",
    ]
    for role, data in roles.items():
        if data.get("enabled") is False or not data.get("model"):
            continue
        lines.append(
            f"| {ROLE_TITLES.get(role, role)} | `{data['model']}` | "
            f"{_memory_estimate(data.get('params_b'))} |"
        )
    lines += [
        "",
        "Это расчёт по числу параметров (2 байта на параметр в bf16), а не замер: сервису",
        "хватает доступа к endpoint, и своей видеокарты он не требует. Машина, на которой",
        "работает сам сервис, считает вёрстку и рендер на CPU. ONLYOFFICE — отдельный сервис",
        "с бюджетом от 4 ГБ; для полного стека ориентир от 8 ГБ с нагрузочной проверкой.",
        "",
        "## Провайдеры",
        "",
    ]
    for name, data in providers.items():
        supports = data.get("supports") or {}
        limits = data.get("limits") or {}
        mark = " (активный)" if name == active else ""
        lines += [
            f"### `{name}`{mark}",
            "",
            f"* Вид: {data.get('kind')}; адрес и ключ — переменные `{data.get('env_base_url')}`,"
            f" `{data.get('env_url_key') or data.get('env_api_key')}`.",
            f"* Рассуждение: стиль `{data.get('reasoning_style')}`.",
            "* Умеет: "
            + ", ".join(
                f"{k} — {'да' if val is True else 'нет' if val is False else 'не проверено'}"
                for k, val in supports.items()
            )
            + ".",
            "* Квоты: "
            + (
                ", ".join(f"{k} = {val}" for k, val in limits.items() if val is not None)
                or "провайдером не сообщены, лимитер работает по умолчаниям `llm.*` из"
                " `config/app.yaml`"
            )
            + ".",
        ]
        if data.get("note"):
            lines.append(f"* {data['note']}")
        lines.append("")

    lines += [
        "## Кто из скиллов какую роль зовёт",
        "",
        "| скилл | этап | роль | промпты | рассуждение | формат ответа |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for skill in _skills():
        reasoning = skill.get("reasoning") or {}
        mode = REASONING_TITLES.get(str(reasoning.get("mode")), str(reasoning.get("mode") or "—"))
        budget = reasoning.get("max_output_tokens")
        lines.append(
            f"| `{skill['name']}` {skill.get('version')} | {skill.get('stage')} | "
            f"{skill.get('model_role')} | {skill['prompt_refs']} | {mode}"
            f"{f', до {budget}' if budget else ''} | {skill.get('response_format')} |"
        )

    lines += [
        "",
        "Версия скилла и версия промпта входят в ключ кэша ответов и в `GenerationResult`:",
        "по результату видно, каким промптом он получен.",
        "",
        "## Рассуждение",
        "",
        "У шлюза vLLM рассуждение выключается только `chat_template_kwargs.enable_thinking`",
        "(стиль `vllm_chat_template`); `reasoning_effort` в теле запроса принимается, но не",
        "действует. Там, где выключить его нечем, бюджет ответа поднят с запасом: рассуждение",
        "тратит те же `max_completion_tokens`, и при тесном лимите ответ приходит пустым с",
        "`finish_reason: length`. Подробности зондов — в `docs/llm-capabilities.md`.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    text = build()
    if "--check" in sys.argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != text:
            print("MODELS.md устарел: перезапустите uv run scripts/gen_models_md.py")
            return 1
        print("MODELS.md соответствует конфигу")
        return 0
    TARGET.write_text(text, encoding="utf-8")
    print(f"записан {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
