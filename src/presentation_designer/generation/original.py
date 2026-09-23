"""Вариант original: загруженная презентация как готовый результат.

Пользователь отдал PPTX не как шаблон и не как материал, а как свою презентацию, которую
хочет дальше править из чата. Файл уже разобран анализатором (профиль: композиция каждого
слайда с текстами образцов) и импортёром (пакет: блоки и факты с номерами слайдов), поэтому
смысловой план и план слайдов строятся детерминированно, без модели:

- тезис смыслового плана — один на слайд: заголовок слайда, факты этого слайда;
- слайд плана — его же композиция, блоки — тексты образца как есть (`fit.action = as_is`);
- композер в режиме сохранения (`_Context.preserve`) не трогает объекты образца, поэтому
  первая ревизия совпадает с исходником; правка слайда из чата меняет только его блоки.
  Исключение — пустые текстовые плейсхолдеры: редактор подписывает их «Заголовок слайда»,
  и эта подсказка становится текстом, чтобы её было видно в превью и можно было править.

Все страницы, включая скрытые, пустые и нераспознанные, сохраняются в исходном порядке.
Композиции без слотов добавляются только для original, не для генерации по шаблону.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from presentation_designer.generation.story import STORY_SCHEMA_VERSION, now_iso
from presentation_designer.generation.variants import PLAN_SCHEMA_VERSION

JsonDict = dict[str, Any]

ORIGINAL_VERSION = "0.2.0"
VARIANT_ID = "original"
SERVICE_ROLES = ("title", "divider", "section", "agenda", "final", "thanks")
# Виды слотов, которые план описывает текстовым блоком. Числа (number) и QR блоком не
# описываются: у блока number по контракту обязателен факт, а у QR — адрес; их объекты
# остаются образцом и правке из чата пока не подлежат.
TEXT_KINDS = (
    "title",
    "subtitle",
    "body",
    "bullets",
    "label",
    "caption",
    "date",
    "name",
    "position",
    "code",
)
PURPOSES = ("feature", "product", "project", "initiative", "report", "other")


def deck_patterns(profile: JsonDict) -> list[JsonDict]:
    """Ровно одна композиция на исходную страницу, независимо от классификации."""
    patterns = {
        int(p["source"]["slide_index"]): p
        for p in profile.get("patterns") or []
        if (p.get("source") or {}).get("kind") == "sample_slide"
        and int((p.get("source") or {}).get("slide_index") or 0) >= 1
    }
    samples = {int(s["slide_index"]): s for s in profile.get("sample_slides") or []}
    total = int((profile.get("stats") or {}).get("slides") or 0)
    for index in sorted(set(range(1, total + 1)) | samples.keys()):
        if index in patterns:
            continue
        sample = samples.get(index, {})
        patterns[index] = {
            "pattern_id": f"original_slide_{index}",
            "role": "freeform",
            "source": {
                "kind": "sample_slide",
                "slide_index": index,
                "layout_id": sample.get("layout_id") or "original",
                "pptx_slide_part": sample.get("pptx_slide_part") or "",
            },
            "slots": [],
        }
    return [patterns[index] for index in sorted(patterns)]


def original_profile(profile: JsonDict) -> JsonDict:
    """Локальное дополнение профиля; общий каталог композиций не мутируется."""
    patterns = list(profile.get("patterns") or [])
    ids = {p["pattern_id"] for p in patterns}
    return {
        **profile,
        "patterns": patterns + [p for p in deck_patterns(profile) if p["pattern_id"] not in ids],
    }


def unchanged_slide(slide: JsonDict, pattern: JsonDict) -> bool:
    """Можно перенести XML страницы, не заполняя её заново."""
    blocks = [b for b in (_block_for(s) for s in pattern.get("slots") or []) if b]
    return (
        slide.get("blocks", []) == blocks and not slide.get("overrides") and not slide.get("notes")
    )


def skipped_slides(profile: JsonDict) -> list[int]:
    """Страницы, не представленные в original (включая резервные композиции)."""
    have = {int(p["source"]["slide_index"]) for p in deck_patterns(profile)}
    total = int((profile.get("stats") or {}).get("slides") or 0)
    listed = {
        int(s.get("slide_index") or 0)
        for s in profile.get("sample_slides") or []
        if s.get("classification") == "content_sample"
    }
    candidates = set(range(1, total + 1)) if total else listed | have
    return sorted(candidates - have)


def _slot_text(slot: JsonDict) -> str:
    return str(slot.get("sample_text") or "").strip()


def slide_title(pattern: JsonDict, index: int) -> str:
    """Заголовок слайда: первый непустой слот заголовка, иначе первый текст, иначе номер."""
    slots = pattern.get("slots") or []
    for kind in ("title", "subtitle"):
        for slot in slots:
            if slot.get("kind") == kind and _slot_text(slot):
                return " ".join(_slot_text(slot).split())[:120]
    for slot in slots:
        if slot.get("kind") in TEXT_KINDS and _slot_text(slot):
            return " ".join(_slot_text(slot).split())[:120]
    return f"Слайд {index}"


def _facts_by_slide(package: JsonDict) -> dict[int, list[str]]:
    slide_of_block = {
        str(b.get("block_id")): int((b.get("source_location") or {}).get("slide") or 0)
        for b in package.get("blocks") or []
    }
    out: dict[int, list[str]] = {}
    for fact in package.get("facts") or []:
        slide = slide_of_block.get(str(fact.get("block_id")), 0)
        if slide:
            out.setdefault(slide, []).append(str(fact["fact_id"]))
    return out


def original_story(package: JsonDict, profile: JsonDict, settings: JsonDict | None) -> JsonDict:
    """StoryPlan из самого файла: тезис на слайд, факты по слайдам, без модели."""
    settings = settings or {}
    brief = package.get("brief") or {}
    patterns = deck_patterns(profile)
    facts = _facts_by_slide(package)
    theses: list[JsonDict] = []
    for order, pattern in enumerate(patterns, start=1):
        index = int(pattern["source"]["slide_index"])
        role = str(pattern.get("role") or "")
        thesis: JsonDict = {
            "thesis_id": f"t{order}",
            "order": order,
            "kind": "context" if role in SERVICE_ROLES else "claim",
            "statement": slide_title(pattern, index),
            "required": True,
            "fact_refs": facts.get(index, []),
        }
        theses.append(thesis)
    purpose = str(brief.get("purpose") or "other")
    if purpose not in PURPOSES:
        purpose = "other"
    language = str(settings.get("language") or brief.get("language") or "ru")
    content_hash = hashlib.sha256(
        json.dumps(
            {
                "original": ORIGINAL_VERSION,
                "template_hash": profile.get("template_hash"),
                "package_id": package.get("package_id"),
                "language": language,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    total_facts = sum(1 for f in package.get("facts") or [] if f.get("must_keep", True))
    covered = sum(len(v) for v in facts.values())
    story: JsonDict = {
        "schema_version": STORY_SCHEMA_VERSION,
        "story_id": f"story_original_{content_hash[:12]}",
        "package_id": package.get("package_id"),
        "content_hash": content_hash,
        "language": language,
        "purpose": purpose,
        "key_takeaway": (
            str(brief.get("title") or "")
            or next((t["statement"] for t in theses if t["kind"] == "claim"), "")
            or (theses[0]["statement"] if theses else "Презентация")
        ),
        "theses": theses,
        "allowed_reductions": [],
        "assumptions": [],
        "coverage": {
            "must_keep_facts": {"total": total_facts, "covered": min(covered, total_facts)}
        },
        "generation_meta": {
            "skills": [{"name": "original_deck", "version": ORIGINAL_VERSION}],
            "prompts": [],
            "models": [],
            "cache_hit": False,
            "created_at": now_iso(),
        },
        "warnings": [],
    }
    if brief:
        story["effective_brief"] = {
            k: v
            for k, v in brief.items()
            if k in ("purpose", "title", "audience", "goal", "language", "must_include", "avoid")
        }
        if brief.get("audience"):
            story["audience"] = str(brief["audience"])
        if brief.get("goal"):
            story["goal"] = str(brief["goal"])
    return story


def _block_for(slot: JsonDict) -> JsonDict | None:
    kind = str(slot.get("kind") or "")
    text = _slot_text(slot)
    if kind not in TEXT_KINDS or not text:
        return None
    font = slot.get("font") or {}
    capacity = slot.get("capacity") or {}
    size = float(font.get("size_pt") or 0) or 12.0
    lines = text.count("\n") + 1
    block: JsonDict = {"slot_id": slot["slot_id"], "kind": kind}
    if kind == "bullets":
        block["items"] = [{"text": line.strip()} for line in text.split("\n") if line.strip()]
    else:
        block["text"] = text
    block["fit"] = {
        "size_pt": size,
        "slot_size_pt": size,
        "lines": lines,
        "max_lines": max(int(capacity.get("max_lines") or 0), lines),
        "chars": len(text),
        "action": "as_is",
    }
    return block


def original_plan(
    story: JsonDict, profile: JsonDict, package: JsonDict, *, plan_id: str
) -> JsonDict:
    """SlidePlan варианта original: слайд на каждую композицию файла, блоки — тексты образца."""
    patterns = deck_patterns(profile)
    theses = {int(t["order"]): t for t in story.get("theses") or []}
    slides: list[JsonDict] = []
    chars_total = 0
    for order, pattern in enumerate(patterns, start=1):
        index = int(pattern["source"]["slide_index"])
        blocks = [b for b in (_block_for(s) for s in pattern.get("slots") or []) if b]
        thesis = theses.get(order)
        title = slide_title(pattern, index)
        slide: JsonDict = {
            "slide_id": f"s{order}",
            "order": order,
            "role": str(pattern.get("role") or "content"),
            "pattern_id": str(pattern["pattern_id"]),
            "title": title,
            "key_message": title[:300],
            "blocks": blocks,
            "thesis_refs": [thesis["thesis_id"]] if thesis else [],
        }
        if thesis and thesis.get("fact_refs"):
            slide["fact_refs"] = list(thesis["fact_refs"])
        slides.append(slide)
        chars_total += sum(len(b.get("text") or "") for b in blocks)
    required = [t["thesis_id"] for t in story.get("theses") or [] if t.get("required")]
    covered = [
        {"thesis_id": tid, "slide_ids": [s["slide_id"] for s in slides if tid in s["thesis_refs"]]}
        for tid in required
    ]
    warnings: list[JsonDict] = []
    skipped = skipped_slides(profile)
    if skipped:
        warnings.append(
            {
                "code": "original_slides_skipped",
                "message": "слайды без композиции не перенесены (скрытые, пустые или служебные): "
                + ", ".join(str(i) for i in skipped[:20]),
            }
        )
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "plan_id": plan_id,
        "template_id": profile["template_id"],
        "package_id": package.get("package_id"),
        "story_id": story["story_id"],
        "language": story.get("language", "ru"),
        "variant": {
            "variant_id": VARIANT_ID,
            "axis": "custom",
            "value": VARIANT_ID,
            "rationale": "Исходная презентация как есть: слайды, тексты и оформление файла "
            "сохранены, правки применяются по слайдам из чата",
        },
        "story": {
            "purpose": str(story.get("purpose", "other")),
            "key_takeaway": str(story.get("key_takeaway", "")),
            "outline": [s["title"] for s in slides if s["role"] in SERVICE_ROLES][:20],
        },
        "slide_count": {"exact": len(slides), "target": len(slides)},
        "slides": slides,
        "coverage": {
            "required_thesis_ids": required,
            "covered": [c for c in covered if c["slide_ids"]],
            "missing": [c["thesis_id"] for c in covered if not c["slide_ids"]],
        },
        "comparison": {
            "pattern_sequence": [s["pattern_id"] for s in slides],
            "visual_kinds": [s["role"] for s in slides],
            "text_chars_total": chars_total,
        },
        "generation_meta": {
            "skills": [{"name": "original_deck", "version": ORIGINAL_VERSION}],
            "prompts": [],
            "models": [],
            "cache_hit": False,
            "created_at": now_iso(),
        },
        "warnings": warnings,
    }


__all__ = ["ORIGINAL_VERSION", "VARIANT_ID", "original_plan", "original_story", "skipped_slides"]
