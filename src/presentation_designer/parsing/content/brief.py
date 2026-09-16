"""Извлечение брифа из свободного сообщения чата детерминированными правилами.

Слой `brief`: пока эвристика (`source: heuristic`), скилл `brief_extractor` на модели
подключается на этапе импорта содержания. Поля, которых нет в тексте, не заполняются;
`understood` перечисляет найденные. Правила повторяют заглушку интерфейса, чтобы
режим заглушек и рабочий режим понимали одни и те же фразы.
"""

from __future__ import annotations

import re
from typing import Any

PURPOSES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"отч[её]т", re.I), "report"),
    (re.compile(r"инициатив", re.I), "initiative"),
    (re.compile(r"фич[аиеу]", re.I), "feature"),
    (re.compile(r"продукт|сервис", re.I), "product"),
    (re.compile(r"проект", re.I), "project"),
]

GENERATE_RE = re.compile(r"сгенерир|запусти|собери|сделай|построй|начина", re.I)
EDIT_RE = re.compile(r"поменя[йть]|перестав|местами|удали|убери|добавь слайд|переимен", re.I)

VARIANT_RE = {
    "compact": re.compile(r"компактн", re.I),
    "balanced": re.compile(r"сбалансир", re.I),
    "detailed": re.compile(r"подробн", re.I),
}


def _clean(value: str) -> str:
    return re.sub(r"^[\s,:—–-]+|[\s,.;:!?—–-]+$", "", value).strip()


def _split_list(value: str) -> list[str]:
    return [item for item in (_clean(p) for p in re.split(r",| и ", value)) if item]


def extract_brief(text: str, current_brief: dict[str, Any] | None = None) -> dict[str, Any]:
    """Возвращает документ BriefExtract (без schema_version: её добавляет API)."""
    understood: list[str] = []
    brief: dict[str, Any] = {}
    slide_count: dict[str, int] | None = None
    variants: list[str] | None = None
    t = re.sub(r"\s+", " ", text or "").strip()
    intent = "none"
    if not t:
        return {"brief": brief, "understood": understood, "intent": intent, "source": "heuristic"}

    if EDIT_RE.search(t):
        intent = "edit"
    elif GENERATE_RE.search(t):
        intent = "generate"

    purpose = next((name for pattern, name in PURPOSES if pattern.search(t)), None)
    if purpose:
        brief["purpose"] = purpose
        understood.append("purpose")

    quoted = re.search(r"«([^»]{3,80})»|\"([^\"]{3,80})\"", t)
    about = re.search(
        r"(?:презентаци[юяи]|питч|отч[её]т|доклад|слайды)\s+(?:про|о|об|обо|на тему|по)\s+(.+?)(?=,| для | чтобы | цель| на \d| в \d|\.|$)",  # noqa: E501
        t,
        re.I,
    )
    title = (quoted.group(1) or quoted.group(2)) if quoted else (about.group(1) if about else None)
    if title:
        cleaned = _clean(title)
        brief["title"] = cleaned[:1].upper() + cleaned[1:]
        understood.append("title")

    audience = re.search(r"(?:^|\s)для\s+(.+?)(?=,| чтобы| цель| на \d| в \d|\.|$)", t, re.I)
    if audience and not re.search(r"слайд", audience.group(1), re.I):
        brief["audience"] = _clean(audience.group(1))
        understood.append("audience")

    goal = re.search(r"(?:чтобы|цель[:\s—–-]+)\s*(.+?)(?=\.|,\s*(?:тон|язык|слайд)|$)", t, re.I)
    if goal:
        brief["goal"] = _clean(goal.group(1))
        understood.append("goal")

    tone = re.search(r"тон[:\s—–-]+(.+?)(?=\.|,|$)", t, re.I)
    if tone:
        brief["tone"] = _clean(tone.group(1))
        understood.append("tone")

    if re.search(r"english|англий", t, re.I):
        brief["language"] = "en"
        understood.append("language")

    must = re.search(
        r"обязательно\s+(?:включи(?:ть)?|добав(?:ь|ить)|нужн[ыоа]|упомян(?:и|уть))\s+(.+?)(?=\.|$)",
        t,
        re.I,
    )
    if must:
        brief["must_include"] = _split_list(must.group(1))
        understood.append("must_include")
    avoid = re.search(r"(?:без|не\s+(?:надо|нужно)|избега(?:й|ть))\s+(.+?)(?=\.|,|$)", t, re.I)
    if avoid and not re.search(r"вариант", avoid.group(1), re.I):
        brief["avoid"] = _split_list(avoid.group(1))
        understood.append("avoid")

    rng = re.search(r"(\d{1,2})\s*[-–—]\s*(\d{1,2})\s*слайд", t, re.I)
    exact = re.search(r"(?:ровно\s+)?(\d{1,2})\s*слайд", t, re.I)
    if rng:
        slide_count = {"min": int(rng.group(1)), "max": int(rng.group(2))}
        understood.append("slide_count")
    elif exact:
        slide_count = {"exact": int(exact.group(1))}
        understood.append("slide_count")

    wanted = [name for name, pattern in VARIANT_RE.items() if pattern.search(t)]
    if re.search(r"без\s+(компактн|сбалансир|подробн)", t, re.I):
        variants = [name for name in VARIANT_RE if name not in wanted]
        understood.append("variants")
    elif re.search(r"только\s+(компактн|сбалансир|подробн)", t, re.I) and wanted:
        variants = wanted
        understood.append("variants")

    out: dict[str, Any] = {
        "brief": brief,
        "understood": understood,
        "intent": intent,
        "source": "heuristic",
    }
    if slide_count:
        out["slide_count"] = slide_count
    if variants:
        out["variants"] = variants
    return out
