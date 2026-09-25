"""Подпись к числу не повторяет само число.

Планировщик часто пишет подпись показателя целиком: над подписью «{fact:f13} на остановку»
крупно стоит то же «420 тыс. руб.», и на слайде читается «420 тыс. руб. / 420 тыс. руб. на
остановку». Здесь повтор в начале подписи снимается: остаётся «на остановку». Подпись,
которая целиком повторяет число, убирается.

Пары «число — подпись» — слоты `<x>_value`/`<x>_label` (ряд показателей) и
`value`/`value_label` (главное число).
"""

from __future__ import annotations

import copy
import re
from typing import Any

JsonDict = dict[str, Any]

_TRIM = "  —–-:,.;"


def _value_slot(label_slot: str) -> str | None:
    if label_slot == "value_label":
        return "value"
    if label_slot.endswith("_label"):
        return label_slot[: -len("_label")] + "_value"
    return None


def _prefixes(value: JsonDict) -> list[str]:
    """Как число может стоять в начале подписи: ссылкой на его факт или его текстом."""
    out: list[str] = []
    fact = str((value.get("number") or {}).get("fact_id") or "")
    refs = [fact] if fact else [str(r) for r in value.get("fact_refs") or []]
    out += [f"{{fact:{r}}}" for r in refs if r]
    text = str(value.get("text") or "").strip()
    if text:
        out.append(text)
    return out


def trim_repeated_values(plan: JsonDict, locked: set[str] | None = None) -> tuple[JsonDict, int]:
    """План без повторов числа в подписях и число правок; слайды с ручными правками
    (`locked`) не трогаются."""
    out = copy.deepcopy(plan)
    changed = 0
    for slide in out.get("slides") or []:
        if str(slide.get("slide_id")) in (locked or set()) or slide.get("overrides"):
            continue
        blocks = slide.get("blocks") or []
        by_slot = {str(b.get("slot_id")): b for b in blocks}
        keep: list[JsonDict] = []
        for block in blocks:
            value_slot = _value_slot(str(block.get("slot_id") or ""))
            value = by_slot.get(value_slot or "")
            text = str(block.get("text") or "")
            if value is None or value.get("kind") != "number" or not text:
                keep.append(block)
                continue
            rest = None
            for prefix in _prefixes(value):
                if text.lower().startswith(prefix.lower()):
                    rest = text[len(prefix) :].lstrip(_TRIM)
                    break
            if rest is None:
                keep.append(block)
                continue
            changed += 1
            if not re.search(r"\w", rest):
                continue  # подпись целиком повторяла число
            block["text"] = rest
            block.pop("fit", None)
            refs = [r for r in block.get("fact_refs") or [] if f"{{fact:{r}}}" in rest]
            if refs:
                block["fact_refs"] = refs
            else:
                block.pop("fact_refs", None)
            keep.append(block)
        slide["blocks"] = keep
    return out, changed


__all__ = ["trim_repeated_values"]
