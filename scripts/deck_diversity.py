"""Мера разнообразия колоды по планам вариантов (SlidePlan) и ComposedDeck.

Читает только JSON, ядро не импортирует, поэтому одинаково считает «до» и «после» любых
правок анализатора, планировщика и композера. Файлы различаются по содержимому: план — по
`variant.variant_id` и `slides[].pattern_id`, ComposedDeck — по `slides[].objects`; отчёты,
manifest и comparison пропускаются. Показатели на вариант:

- content_slides — слайды ролей вне title/agenda/section_divider/thanks/qr;
- distinct_compositions, distinct_ratio — различные композиции содержательных слайдов
  (ключ — pattern_id и вид визуализации) и их доля;
- top_pattern_share — доля самого частого паттерна среди содержательных слайдов;
- max_run — самая длинная серия одного паттерна подряд по всей колоде;
- divider_styles — различных паттернов среди разделителей; service_styles — паттерны
  титула, разделителей и финала;
- icons_from_plan — объекты `slot_kind: icon` с `content_source: plan` к общему числу
  иконок в ComposedDeck (если он передан).

Между вариантами: variant_distance — доля позиций с разным ключом композиции (по большей
длине; позиции за концом короткого варианта считаются разными), число стилей разделителей,
титулов и финалов по всем вариантам.

    uv run python scripts/deck_diversity.py runs/plan-vkedu/*.json
    uv run python scripts/deck_diversity.py runs/diversity-baseline/vkedu/*.json --markdown
    … --json runs/diversity-baseline/vkedu/diversity.json
"""

from __future__ import annotations

import argparse
import collections
import itertools
import json
import pathlib
import sys
from typing import Any

JsonDict = dict[str, Any]
SERVICE_ROLES = {"title", "agenda", "section_divider", "thanks", "qr"}
VARIANT_ORDER = ("compact", "balanced", "detailed")
VISUAL_BLOCKS = ("chart", "table", "number", "diagram", "image", "bullets")


def load(path: pathlib.Path) -> JsonDict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"{path}: не прочитан ({e})", file=sys.stderr)
        return None
    return data if isinstance(data, dict) else None


def classify(doc: JsonDict) -> str | None:
    slides = doc.get("slides")
    if not isinstance(slides, list) or not slides:
        return None
    first = slides[0]
    if isinstance(first, dict) and "objects" in first and doc.get("variant_id"):
        return "composed"
    if isinstance(first, dict) and "pattern_id" in first and isinstance(doc.get("variant"), dict):
        return "plan"
    return None


def visual_kind(slide: JsonDict, hint: str | None) -> str:
    """Вид визуализации слайда: из comparison.visual_kinds плана, иначе по блокам."""
    if hint:
        return hint
    kinds = {str(b.get("kind")) for b in slide.get("blocks") or []}
    for k in VISUAL_BLOCKS:
        if k in kinds:
            return k
    return "text"


def composition_keys(plan: JsonDict) -> list[tuple[str, str, str]]:
    """(роль, pattern_id, вид визуализации) по слайдам плана в порядке колоды."""
    hints = (plan.get("comparison") or {}).get("visual_kinds") or []
    out: list[tuple[str, str, str]] = []
    for i, s in enumerate(plan["slides"]):
        hint = str(hints[i]) if i < len(hints) else None
        out.append((str(s.get("role", "")), str(s.get("pattern_id", "")), visual_kind(s, hint)))
    return out


def max_run(sequence: list[str]) -> int:
    best = 0
    for _, group in itertools.groupby(sequence):
        best = max(best, len(list(group)))
    return best


def plan_metrics(plan: JsonDict) -> JsonDict:
    keys = composition_keys(plan)
    content = [(pid, vis) for role, pid, vis in keys if role not in SERVICE_ROLES]
    patterns = collections.Counter(pid for pid, _ in content)
    dividers = [pid for role, pid, _ in keys if role == "section_divider"]
    titles = [pid for role, pid, _ in keys if role == "title"]
    finals = [pid for role, pid, _ in keys if role in ("thanks", "qr")]
    n = len(content)
    distinct = len(set(content))
    return {
        "slides": len(keys),
        "content_slides": n,
        "distinct_compositions": distinct,
        "distinct_ratio": round(distinct / n, 3) if n else 0.0,
        "top_pattern": patterns.most_common(1)[0][0] if patterns else None,
        "top_pattern_share": round(patterns.most_common(1)[0][1] / n, 3) if n else 0.0,
        "max_run": max_run([pid for _, pid, _ in keys]),
        "dividers": len(dividers),
        "divider_styles": len(set(dividers)),
        "service_styles": {
            "title": titles[0] if titles else None,
            "divider": sorted(set(dividers)),
            "final": finals[-1] if finals else None,
        },
        "keys": [f"{pid}|{vis}" for _, pid, vis in keys],
    }


def composed_metrics(deck: JsonDict) -> JsonDict:
    icons = [
        o
        for s in deck.get("slides", [])
        for o in s.get("objects", [])
        if o.get("slot_kind") == "icon"
    ]
    from_plan = sum(1 for o in icons if o.get("content_source") == "plan")
    return {
        "icons_total": len(icons),
        "icons_from_plan": from_plan,
        "icons_from_plan_ratio": round(from_plan / len(icons), 3) if icons else None,
        "composed_slides": len(deck.get("slides", [])),
    }


def variant_distance(a: list[str], b: list[str]) -> JsonDict:
    length = max(len(a), len(b))
    if not length:
        return {"different": 0, "positions": 0, "ratio": 0.0}
    different = sum(1 for i in range(length) if i >= len(a) or i >= len(b) or a[i] != b[i])
    return {"different": different, "positions": length, "ratio": round(different / length, 3)}


def measure(paths: list[pathlib.Path]) -> JsonDict:
    plans: dict[str, JsonDict] = {}
    composed: dict[str, JsonDict] = {}
    for path in paths:
        doc = load(path)
        if doc is None:
            continue
        kind = classify(doc)
        if kind == "plan":
            plans[str(doc["variant"]["variant_id"])] = doc
        elif kind == "composed":
            composed[str(doc["variant_id"])] = doc
    variants = sorted(plans, key=lambda v: VARIANT_ORDER.index(v) if v in VARIANT_ORDER else 9)
    per_variant: dict[str, JsonDict] = {}
    for v in variants:
        metrics = plan_metrics(plans[v])
        if v in composed:
            metrics.update(composed_metrics(composed[v]))
        per_variant[v] = metrics
    for v, deck in composed.items():
        if v not in per_variant:
            per_variant[v] = composed_metrics(deck)
    distances: dict[str, JsonDict] = {}
    for a, b in itertools.combinations(variants, 2):
        distances[f"{a}/{b}"] = variant_distance(per_variant[a]["keys"], per_variant[b]["keys"])
    across = {
        "divider_styles": len(
            {p for v in variants for p in per_variant[v]["service_styles"]["divider"]}
        ),
        "title_styles": len({per_variant[v]["service_styles"]["title"] for v in variants} - {None}),
        "final_styles": len({per_variant[v]["service_styles"]["final"] for v in variants} - {None}),
    }
    return {
        "variants": per_variant,
        "variant_distance": distances,
        "across_variants": across,
        "inputs": {"plans": sorted(plans), "composed": sorted(composed)},
    }


def fmt_ratio(value: Any) -> str:
    return "—" if value is None else f"{value:.2f}".replace(".", ",")


def print_table(result: JsonDict, *, markdown: bool) -> None:
    rows = []
    for v, m in result["variants"].items():
        if "content_slides" not in m:
            continue
        icons = (
            f"{m['icons_from_plan']}/{m['icons_total']}"
            if m.get("icons_total") is not None
            else "—"
        )
        rows.append(
            [
                v,
                str(m["slides"]),
                str(m["content_slides"]),
                f"{m['distinct_compositions']} ({fmt_ratio(m['distinct_ratio'])})",
                f"{fmt_ratio(m['top_pattern_share'])} ({m['top_pattern'] or '—'})",
                str(m["max_run"]),
                f"{m['divider_styles']} из {m['dividers']}",
                icons,
            ]
        )
    head = [
        "Вариант",
        "Слайдов",
        "Содерж.",
        "Композиций (доля)",
        "Доля частого паттерна",
        "Серия",
        "Стили разделителей",
        "Иконки из плана",
    ]
    if markdown:
        print("| " + " | ".join(head) + " |")
        print("|" + "|".join(" --- " for _ in head) + "|")
        for r in rows:
            print("| " + " | ".join(r) + " |")
    else:
        widths = [max(len(x) for x in col) for col in zip(head, *rows, strict=False)]
        for r in (head, *rows):
            print("  ".join(x.ljust(w) for x, w in zip(r, widths, strict=False)))
    across = result["across_variants"]
    dist = ", ".join(
        f"{pair}: {d['different']}/{d['positions']} ({fmt_ratio(d['ratio'])})"
        for pair, d in result["variant_distance"].items()
    )
    print(
        f"\nПо вариантам: стилей разделителей {across['divider_styles']}, титулов "
        f"{across['title_styles']}, финалов {across['final_styles']}."
        + (f" Расстояние между вариантами: {dist}." if dist else "")
    )
    for v, m in result["variants"].items():
        styles = m.get("service_styles")
        if styles:
            print(
                f"{v}: титул {styles['title'] or '—'}, разделители "
                f"{', '.join(styles['divider']) or '—'}, финал {styles['final'] or '—'}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Мера разнообразия колоды по JSON вариантов")
    parser.add_argument("files", nargs="+", help="планы <variant>.json и composed.json")
    parser.add_argument("--json", default=None, help="записать результат в файл JSON")
    parser.add_argument("--markdown", action="store_true", help="таблица в формате Markdown")
    args = parser.parse_args(argv)
    paths = [pathlib.Path(f) for f in args.files]
    result = measure(paths)
    if not result["variants"]:
        print("среди файлов нет ни одного плана или ComposedDeck", file=sys.stderr)
        return 2
    print_table(result, markdown=args.markdown)
    if args.json:
        out = pathlib.Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nзаписано: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
