"""Извлечение брифа из свободного сообщения чата: модель с резервной эвристикой.

Слой `brief`. `extract_brief_with_model` — короткий вызов скилла `brief_extractor` без
рассуждения по схеме `brief_extract` с тайм-аутом в несколько секунд; ответ модели проходит
проверку: поля, которых нет в тексте сообщения, отбрасываются (слова значения должны
встречаться в сообщении), перечисления и числа проверяются по схеме. При ошибке,
тайм-ауте или ненастроенном провайдере отвечает та же детерминированная эвристика
`extract_brief` с `source: heuristic`, что и заглушка интерфейса, чтобы диалог не ждал очередь.
"""

from __future__ import annotations

import logging
import re
from typing import Any

log = logging.getLogger(__name__)

BRIEF_LAYER_VERSION = "0.1.0"
PURPOSE_VALUES = ("feature", "product", "project", "initiative", "report", "other")
VARIANT_VALUES = ("compact", "balanced", "detailed")
INTENT_VALUES = ("generate", "edit", "none")
UNDERSTOOD_FIELDS = (
    "purpose",
    "title",
    "audience",
    "goal",
    "tone",
    "language",
    "must_include",
    "avoid",
    "slide_count",
    "variants",
)

# Схема ответа модели: плоский документ, поля необязательны — отсутствие поля означает
# «в тексте этого нет».
BRIEF_MODEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "purpose": {"type": "string", "enum": list(PURPOSE_VALUES)},
        "title": {"type": "string"},
        "audience": {"type": "string"},
        "goal": {"type": "string"},
        "tone": {"type": "string"},
        "language": {"type": "string"},
        "must_include": {"type": "array", "items": {"type": "string"}},
        "avoid": {"type": "array", "items": {"type": "string"}},
        "slide_count": {
            "type": "object",
            "properties": {
                "exact": {"type": "integer"},
                "min": {"type": "integer"},
                "max": {"type": "integer"},
            },
        },
        "variants": {"type": "array", "items": {"type": "string", "enum": list(VARIANT_VALUES)}},
        "intent": {"type": "string", "enum": list(INTENT_VALUES)},
    },
    "required": ["intent"],
}

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


# ---------- модель ----------

_WORD = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё\-]*")


def _grounded(value: str, text_lower: str) -> bool:
    """Значение опирается на сообщение: не меньше половины значимых слов (по основе
    из 5 символов) есть в тексте. Так модель не добавляет тему или аудиторию от себя."""
    words = [w.lower() for w in _WORD.findall(value) if len(w) >= 3]
    if not words:
        return False
    hits = sum(1 for w in words if w[:5] in text_lower)
    return hits >= max(1, (len(words) + 1) // 2)


def _clean_model_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return _clean(re.sub(r"\s+", " ", value)).strip()


def validate_model_answer(answer: dict[str, Any], text: str) -> dict[str, Any]:
    """Документ BriefExtract (без schema_version и model) из ответа модели: поля без опоры
    в тексте и с недопустимыми значениями отбрасываются, `understood` строится по факту."""
    text_lower = re.sub(r"\s+", " ", text or "").lower()
    brief: dict[str, Any] = {}
    understood: list[str] = []
    purpose = answer.get("purpose")
    if isinstance(purpose, str) and purpose in PURPOSE_VALUES:
        brief["purpose"] = purpose
        understood.append("purpose")
    for key in ("title", "audience", "goal", "tone"):
        value = _clean_model_text(answer.get(key))
        if value and len(value) <= 200 and _grounded(value, text_lower):
            if key == "title":
                value = value[:1].upper() + value[1:]
            brief[key] = value
            understood.append(key)
    language = _clean_model_text(answer.get("language")).lower()
    if language in ("ru", "en") and (
        (language == "en" and re.search(r"english|англий|\ben\b", text_lower))
        or (language == "ru" and re.search(r"русск|\bru\b", text_lower))
    ):
        brief["language"] = language
        understood.append("language")
    for key in ("must_include", "avoid"):
        items = answer.get(key)
        if isinstance(items, list):
            cleaned = [
                _clean_model_text(i)
                for i in items
                if isinstance(i, str) and _grounded(_clean_model_text(i), text_lower)
            ]
            cleaned = [i for i in cleaned if i][:12]
            if cleaned:
                brief[key] = cleaned
                understood.append(key)
    slide_count: dict[str, int] | None = None
    sc = answer.get("slide_count")
    if isinstance(sc, dict) and re.search(r"слайд|slide|стран", text_lower):
        exact, lo, hi = (sc.get(k) for k in ("exact", "min", "max"))
        if isinstance(exact, int) and 1 <= exact <= 60 and str(exact) in text_lower:
            slide_count = {"exact": exact}
        elif (
            isinstance(lo, int)
            and isinstance(hi, int)
            and 1 <= lo <= hi <= 60
            and str(lo) in text_lower
            and str(hi) in text_lower
        ):
            slide_count = {"min": lo, "max": hi}
        if slide_count:
            understood.append("slide_count")
    variants: list[str] | None = None
    raw_variants = answer.get("variants")
    if isinstance(raw_variants, list) and re.search(
        r"вариант|компактн|сбалансир|подробн", text_lower
    ):
        chosen = [v for v in raw_variants if isinstance(v, str) and v in VARIANT_VALUES]
        chosen = list(dict.fromkeys(chosen))
        if chosen and len(chosen) < len(VARIANT_VALUES):
            variants = chosen
            understood.append("variants")
    intent = answer.get("intent")
    if intent not in INTENT_VALUES:
        intent = "none"
    out: dict[str, Any] = {
        "brief": brief,
        "understood": understood,
        "intent": intent,
        "source": "model",
    }
    if slide_count:
        out["slide_count"] = slide_count
    if variants:
        out["variants"] = variants
    return out


def _model_ref(client: Any, skill: Any) -> dict[str, Any] | None:
    try:
        from presentation_designer.llm import skill_model_ref

        return skill_model_ref(client, skill)
    except Exception:
        return None


async def extract_brief_with_model(
    text: str,
    current_brief: dict[str, Any] | None,
    *,
    client: Any,
    skill: Any,
    deadline_s: float | None = None,
) -> dict[str, Any]:
    """Бриф моделью; при любой ошибке — эвристика. Возвращает документ BriefExtract
    без schema_version (её добавляет API)."""
    from presentation_designer.llm.types import Deadline

    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return extract_brief(text, current_brief)
    params = skill.manifest.params or {}
    budget = float(deadline_s if deadline_s is not None else params.get("deadline_s", 8))
    req = skill.request("brief.extract", cleaned, schema=BRIEF_MODEL_SCHEMA, stage="brief")
    req.schema_name = "brief_extract"
    req.deadline = Deadline.after(budget)
    try:
        resp = await client.complete(req)
    except Exception as e:
        log.warning("бриф моделью не извлечён (%s): эвристика", f"{type(e).__name__}: {e}"[:200])
        return extract_brief(text, current_brief)
    answer = resp.parsed if isinstance(resp.parsed, dict) else {}
    out = validate_model_answer(answer, cleaned)
    ref = _model_ref(client, skill)
    if ref:
        out["model"] = ref
    return out
