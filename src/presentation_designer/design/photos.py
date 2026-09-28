"""Фото к текстовым слайдам: часть слайдов «список» и «одна мысль» получает фотографию.

Презентация из одних текстовых слайдов выглядит как документ. Если в материалах нет
картинок, слой design берёт до трети содержательных текстовых слайдов (не подряд), просит
модель перевести тему каждого в короткий английский запрос к фотобанку (скилл
`visual_picker`), ищет фото (`parsing/content/stock.py`, CC0-фотобанки через Openverse) и
переводит найденные слайды на собственную композицию «картинка и текст» (`image_split`) —
тот же заголовок, тот же текст, фото рядом. Сторона фото чередуется: справа, слева.

Слайды с ручными правками, слайды образцов шаблона и слайды с данными не трогаются.
Всё, что не удалось (модель, сеть, текст не влезет), оставляет слайд как был.
"""

from __future__ import annotations

import concurrent.futures
import copy
import logging
import time
from collections.abc import Callable
from typing import Any

from presentation_designer.library.register import pattern_id_for

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]

# Композиция-источник → (семейство «картинка и текст» с параметром bullets, слоты текста;
# пункты двух колонок списка сливаются в один столбец рядом с фото).
SOURCES: dict[str, tuple[bool, tuple[str, ...]]] = {
    "bullets_pane@cols=1": (True, ("bullets_1",)),
    "bullets_pane@cols=2": (True, ("bullets_1", "bullets_2")),
    "statement": (False, ("statement",)),
}
# Доля содержательных слайдов с фото по варианту: сжатый — самый визуальный.
SHARE = {"compact": 0.45, "balanced": 0.4, "detailed": 0.3}
MAX_PHOTOS = 4
# Текст слайда длиннее этого не переводится на половину ширины: он бы ужался до мелкого.
# Список из четырёх пунктов по предложению — около 600 знаков — в половину ширины встаёт
# в 14 пт (замер 28.09.2026); при 420 фото получал один слайд из десяти.
MAX_CHARS = {True: 650, False: 520}

QUERY_SCHEMA: JsonDict = {
    "type": "object",
    "properties": {
        "photos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"slide_id": {"type": "string"}, "query": {"type": "string"}},
                "required": ["slide_id", "query"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["photos"],
    "additionalProperties": False,
}

AskQueries = Callable[[str], str]
FindPhoto = Callable[[str, set[str]], Any]


def _composition(pattern: JsonDict | None) -> str:
    source = (pattern or {}).get("source") or {}
    return str(source.get("composition_id") or "") if source.get("kind") == "builtin" else ""


def _text_of(slide: JsonDict, slot_id: str) -> str:
    for block in slide.get("blocks") or []:
        if block.get("slot_id") == slot_id:
            if block.get("items"):
                return "\n".join(str(i.get("text") or "") for i in block["items"])
            return str(block.get("text") or "")
    return ""


def candidates(
    plan: JsonDict, profile: JsonDict, variant: str, locked: set[str] | None = None
) -> list[int]:
    """Номера слайдов, которым можно дать фото: текстовая композиция, текст умещается
    в половину ширины, слайды не подряд; не больше доли варианта."""
    patterns = {str(p.get("pattern_id")): p for p in profile.get("patterns") or []}
    have = set(patterns)
    slides = plan.get("slides") or []
    content = [s for s in slides if str(s.get("role") or "") not in ("title", "closing", "section")]
    limit = min(MAX_PHOTOS, max(1, round(len(content) * SHARE.get(variant, 0.34))))
    scored: list[tuple[int, int]] = []
    for index, slide in enumerate(slides):
        if str(slide.get("slide_id")) in (locked or set()) or slide.get("overrides"):
            continue
        composition = _composition(patterns.get(str(slide.get("pattern_id"))))
        if composition not in SOURCES:
            continue
        bullets, slots = SOURCES[composition]
        if pattern_id_for(_target(bullets, "right")) not in have:
            continue
        chars = sum(len(_text_of(slide, slot)) for slot in slots)
        chars += len(_text_of(slide, "support"))
        if chars == 0 or chars > MAX_CHARS[bullets]:
            continue
        scored.append((chars, index))
    # Сначала слайды через один (фото не идут подряд), затем — соседние, но не три подряд.
    chosen: list[int] = []
    for spacing in (2, 1):
        for _chars, index in sorted(scored):
            if len(chosen) >= limit or index in chosen:
                continue
            if any(abs(index - other) < spacing for other in chosen):
                continue
            if {index - 1, index + 1} <= set(chosen) or (
                {index - 1, index - 2} <= set(chosen) or {index + 1, index + 2} <= set(chosen)
            ):
                continue
            chosen.append(index)
    return sorted(chosen)


def _target(bullets: bool, side: str) -> str:
    return f"image_split@bullets={bullets},side={side}"


def query_request(plan: JsonDict, indices: list[int]) -> str:
    """Текст запроса к `visual_picker`: тема презентации и слайды-кандидаты."""
    slides = plan.get("slides") or []
    story = plan.get("story") if isinstance(plan.get("story"), dict) else {}
    title = str((story or {}).get("title") or plan.get("title") or "")
    if not title and slides:
        # Тема — заголовок титульного слайда: у плана своего названия нет.
        title = str(slides[0].get("title") or "")
    outline = "; ".join(str(s.get("title") or "") for s in slides if s.get("title"))
    lines = [
        f"Тема презентации: {title}".strip(),
        f"Все слайды по порядку: {outline}",
        "",
        "Подбери фото к слайдам:",
    ]
    for index in indices:
        slide = plan["slides"][index]
        lines.append(
            f"- slide_id: {slide.get('slide_id')}; заголовок: {slide.get('title') or ''}; "
            f"суть: {slide.get('key_message') or ''}"
        )
    return "\n".join(lines)


def parse_queries(text: str) -> dict[str, str]:
    import json

    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return {}
    out: dict[str, str] = {}
    for item in (data or {}).get("photos") or []:
        if isinstance(item, dict) and item.get("slide_id") and item.get("query"):
            out[str(item["slide_id"])] = str(item["query"]).strip()
    return out


def swap_to_photo(slide: JsonDict, composition: str, asset_id: str, side: str) -> JsonDict:
    """Слайд на композиции «картинка и текст»: тот же заголовок и текст, фото рядом."""
    bullets, slots = SOURCES[composition]
    out = copy.deepcopy(slide)
    out["pattern_id"] = pattern_id_for(_target(bullets, side))
    blocks = out.get("blocks") or []
    title = next((b for b in blocks if b.get("slot_id") == "title"), None)
    mains = [b for slot in slots for b in blocks if b.get("slot_id") == slot]
    support = next((b for b in blocks if b.get("slot_id") == "support"), None)
    body: JsonDict = {"slot_id": "body", "kind": "bullets" if bullets else "body"}
    items: list[JsonDict] = []
    texts: list[str] = []
    refs: dict[str, set[str]] = {"source_refs": set(), "fact_refs": set()}
    for main in mains + ([support] if support is not None else []):
        items.extend(copy.deepcopy(main.get("items") or []))
        if main.get("text"):
            texts.append(str(main["text"]))
        for key in refs:
            refs[key].update(str(r) for r in main.get(key) or [])
    if items and bullets:
        body["items"] = items
        if texts:
            # Текст подписи к пунктам — последним пунктом, чтобы не потерять его.
            body["items"] = items + [{"text": t} for t in texts]
    elif items:
        texts = [str(i.get("text") or "") for i in items] + texts
    if texts and "items" not in body:
        body["text"] = "\n".join(t for t in texts if t).strip()
    for key, values in refs.items():
        if values:
            body[key] = sorted(values)
    new_blocks: list[JsonDict] = []
    if title is not None:
        new_blocks.append(title)
    new_blocks.append(
        {
            "slot_id": "image",
            "kind": "image",
            "image": {"asset_id": asset_id, "fit": "cover", "alt": str(slide.get("title") or "")},
        }
    )
    new_blocks.append(body)
    for block in new_blocks:
        block.pop("fit", None)
    out["blocks"] = new_blocks
    return out


def attach_photos(
    plan: JsonDict,
    profile: JsonDict,
    *,
    variant: str,
    ask: AskQueries,
    find: FindPhoto,
    locked: set[str] | None = None,
    budget_s: float = 20.0,
) -> tuple[JsonDict, JsonDict]:
    """План с фото на части текстовых слайдов и отчёт: какие слайды, какие запросы."""
    started = time.monotonic()
    report: JsonDict = {"candidates": 0, "placed": [], "skipped": []}
    indices = candidates(plan, profile, variant, locked)
    report["candidates"] = len(indices)
    if not indices:
        return plan, report
    try:
        queries = parse_queries(ask(query_request(plan, indices)))
    except Exception as e:  # модель недоступна — без фото
        log.info("фото: запросы не получены: %s", e)
        report["error"] = str(e)[:200]
        return plan, report
    slides = plan.get("slides") or []
    report["queries"] = queries
    wanted = [(i, queries.get(str(slides[i].get("slide_id")), "")) for i in indices]
    wanted = [(i, q) for i, q in wanted if q]
    if not wanted:
        return plan, report
    remaining = max(1.0, budget_s - (time.monotonic() - started))
    used: set[str] = set()
    found: dict[int, Any] = {}
    # Поиск параллельно, но выбор без повторов — по порядку слайдов.
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(wanted))) as pool:
        futures = {pool.submit(find, q, set()): (i, q) for i, q in wanted}
        try:
            for future in concurrent.futures.as_completed(futures, timeout=remaining):
                i, q = futures[future]
                try:
                    found[i] = future.result()
                except Exception as e:
                    report["skipped"].append({"slide": i + 1, "query": q, "reason": str(e)[:120]})
        except concurrent.futures.TimeoutError:
            report["skipped"].append({"reason": "время поиска фото вышло"})
    patterns = {str(p.get("pattern_id")): p for p in profile.get("patterns") or []}
    out = copy.deepcopy(plan)
    side = "right"
    for i, q in wanted:
        photo = found.get(i)
        if photo is None or photo.asset_id in used:
            reason = "повтор фото" if photo is not None else "фото не нашлось"
            report["skipped"].append({"slide": i + 1, "query": q, "reason": reason})
            continue
        used.add(photo.asset_id)
        composition = _composition(patterns.get(str(slides[i].get("pattern_id"))))
        out["slides"][i] = swap_to_photo(slides[i], composition, photo.asset_id, side)
        report["placed"].append({"slide": i + 1, "query": q, "asset_id": photo.asset_id})
        side = "left" if side == "right" else "right"
    report["seconds"] = round(time.monotonic() - started, 1)
    return out, report


__all__ = ["QUERY_SCHEMA", "attach_photos", "candidates", "swap_to_photo"]
