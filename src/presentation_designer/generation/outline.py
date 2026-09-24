"""Раскладка пользователя по слайдам: разделы «Слайд 1: …, Слайд 2: …» в материалах.

Пользователь, который сам расписал презентацию по слайдам (сообщением в чате — оно приходит
файлом «Текст из чата.md», — или документом), ждёт ровно эти слайды в этом порядке. Сюжет
получает правило «раздел — один слайд», план — по пакету на раздел без разделителей,
оглавления и финала «Спасибо»; титульный раздел становится обложкой с заголовком и
подзаголовком пользователя.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

JsonDict = dict[str, Any]

_MARK = re.compile(r"(?i)^(?:слайд|slide)\s*№?\s*(\d{1,2})\s*[:.)—–-]?\s*(.*)$")
_COVER = re.compile(r"(?i)титул|обложк|\btitle\b|\bcover\b")
_FIELD = re.compile(r"^([^:]{2,40}):\s+(.+)$")


@dataclass
class OutlineSlide:
    number: int
    title: str
    block_ids: list[str] = field(default_factory=list)
    # «Заголовок: …», «Подзаголовок: …» из абзацев раздела: ключ — подпись в нижнем регистре.
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def cover(self) -> bool:
        """Титульный раздел: назван так или несёт заголовок с подзаголовком, а не тезисы."""
        return self.number == 1 and bool(
            _COVER.search(self.title) or {"заголовок", "подзаголовок"} & set(self.fields)
        )


def user_outline(package: JsonDict) -> list[OutlineSlide]:
    """Разделы «Слайд N» пакета по порядку: не меньше двух, номера растут с 1. Раздел —
    его заголовок и блоки до следующего заголовка того же или более высокого уровня."""
    blocks = package.get("blocks") or []
    slides: list[OutlineSlide] = []
    level: int | None = None
    current: OutlineSlide | None = None
    for b in blocks:
        if b.get("kind") == "heading":
            m = _MARK.match(" ".join(str(b.get("text") or "").split()))
            b_level = int(b.get("level") or 1)
            if m and (level is None or b_level == level):
                level = b_level
                current = OutlineSlide(int(m.group(1)), m.group(2).strip(" .:—–-"))
                current.block_ids.append(str(b["block_id"]))
                slides.append(current)
                continue
            if level is not None and b_level <= level:
                current = None
        if current is None:
            continue
        current.block_ids.append(str(b["block_id"]))
        if b.get("kind") == "paragraph":
            f = _FIELD.match(" ".join(str(b.get("text") or "").split()))
            if f:
                current.fields.setdefault(f.group(1).strip().lower(), f.group(2).strip())
    numbers = [s.number for s in slides]
    if len(slides) < 2 or numbers[0] != 1:
        return []
    if any(b <= a for a, b in pairwise(numbers)):
        return []
    return slides


def outline_policy(slides: list[OutlineSlide]) -> str:
    """Правило для сюжета: раскладку пользователя сохранить слайд в слайд."""
    cover = slides[0].cover
    content = slides[1:] if cover else slides
    return (
        f"Раскладка пользователя: материал уже разложен по слайдам — {len(slides)} разделов "
        "«Слайд N». Сохрани её: каждый раздел — один слайд в том же порядке. "
        + (
            "Раздел «Слайд 1» — обложка, его покрывает титульный слайд; тезисов по нему не делай. "
            if cover
            else ""
        )
        + f"Для каждого из {len(content)} остальных разделов дай ровно один тезис (claim, "
        "evidence или context) с source_refs на блоки раздела: statement — главная мысль "
        "раздела по его заголовку и ключевому тезису, explanation — его пункты и цифры "
        "(ссылками {fact:…}). Тезисы-разделы (kind=section), вывод и призыв сверх раскладки "
        "не добавляй. Указания оформителю («Визуальный акцент») — не текст слайда."
    )


def slide_of(slides: list[OutlineSlide], block_ids: list[str]) -> int | None:
    """Номер раздела раскладки (индекс в списке), к которому относятся блоки тезиса."""
    where = {b: i for i, s in enumerate(slides) for b in s.block_ids}
    return next((where[b] for b in block_ids if b in where), None)


def thesis_slides(slides: list[OutlineSlide], thesis: JsonDict, facts: JsonDict) -> int | None:
    """Раздел тезиса сюжета: по его блокам, затем по блокам его фактов."""
    refs = list(thesis.get("source_refs") or [])
    refs += [
        str(facts[f]["block_id"])
        for f in thesis.get("fact_refs") or []
        if f in facts and facts[f].get("block_id")
    ]
    return slide_of(slides, refs)


# Указания оформителю — не содержание слайда.
_DESIGN_NOTE = re.compile(r"(?i)^(?:визуальн|оформлени|дизайн|картинк|изображени)")


def outline_thesis(slide: OutlineSlide, package: JsonDict) -> JsonDict:
    """Тезис сюжета из раздела раскладки, который модель пропустила: заголовок раздела —
    формулировка, его абзацы — пояснение, его блоки и факты — ссылки."""
    blocks = {str(b["block_id"]): b for b in package.get("blocks") or []}
    own = [blocks[b] for b in slide.block_ids[1:] if b in blocks]
    text = " ".join(
        " ".join(str(b.get("text") or "").split())
        for b in own
        if b.get("kind") == "paragraph" and not _DESIGN_NOTE.match(str(b.get("text") or ""))
    )
    items = [str(i) for b in own if b.get("kind") == "bullets" for i in b.get("items") or []]
    ids = set(slide.block_ids)
    thesis: JsonDict = {
        "thesis_id": "",
        "order": 0,
        "kind": "claim",
        "statement": (slide.title or f"Слайд {slide.number}")[:400],
        "required": True,
        "source_refs": [str(b["block_id"]) for b in own],
        "suggested_visual": "bullets" if items else "text",
    }
    explanation = " ".join([text, *items]).strip()
    if explanation:
        thesis["explanation"] = explanation[:800]
    fact_refs = [
        str(f["fact_id"]) for f in package.get("facts") or [] if str(f.get("block_id")) in ids
    ]
    if fact_refs:
        thesis["fact_refs"] = fact_refs
    return thesis


__all__ = [
    "OutlineSlide",
    "outline_policy",
    "outline_thesis",
    "slide_of",
    "thesis_slides",
    "user_outline",
]
