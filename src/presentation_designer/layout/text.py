"""Подстановка текста в объекты образца с сохранением оформления.

Оформление абзаца (`a:pPr`: маркеры, выравнивание, интервалы) и первого фрагмента
(`a:rPr`: шрифт, цвет, начертание) остаются от образца; заменяется только текст. Кегль из
плана (`blocks[].fit.size_pt`) выставляется явно на каждом фрагменте, чтобы результат не
зависел от автоподбора PowerPoint. Строки текста ложатся на абзацы образца, если их
несколько (заголовок карточки крупнее описания), иначе на переносы `a:br` внутри одного
абзаца; пункты списка становятся абзацами по образцу первого маркированного абзаца.
Ссылки `{fact:<id>}` подставляются значением как в источнике до записи.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

from presentation_designer.layout.ooxml import NS_A, NS_P
from presentation_designer.layout.shapes import NS

FACT_REF = re.compile(r"\{fact:([A-Za-z0-9_.:-]+)\}")
A_P = f"{{{NS_A}}}p"
A_R = f"{{{NS_A}}}r"
A_BR = f"{{{NS_A}}}br"
A_FLD = f"{{{NS_A}}}fld"
A_T = f"{{{NS_A}}}t"
A_RPR = f"{{{NS_A}}}rPr"
A_PPR = f"{{{NS_A}}}pPr"
A_END = f"{{{NS_A}}}endParaRPr"
_RUN_LIKE = (A_R, A_BR, A_FLD)
_BULLET_TAGS = ("buChar", "buAutoNum", "buBlip")


@dataclass
class TextResult:
    plain: str
    paragraphs: int
    fact_refs: list[str] = field(default_factory=list)
    missing_facts: list[str] = field(default_factory=list)
    size_pt: float | None = None


# ---------- факты ----------


def fact_text(fact: dict[str, Any]) -> str:
    raw = str(fact.get("raw") or "").strip()
    if raw:
        return raw
    value = fact.get("value")
    if value is None:
        return ""
    unit = fact.get("unit")
    return f"{value} {unit}".strip() if unit else str(value)


def substitute_facts(
    text: str, facts: dict[str, dict[str, Any]]
) -> tuple[str, list[str], list[str]]:
    """Текст с подставленными значениями, использованные и ненайденные ссылки."""
    used: list[str] = []
    missing: list[str] = []

    def repl(m: re.Match[str]) -> str:
        fid = m.group(1)
        fact = facts.get(fid)
        if fact is None:
            missing.append(fid)
            return m.group(0)
        used.append(fid)
        return fact_text(fact)

    return FACT_REF.sub(repl, text), list(dict.fromkeys(used)), list(dict.fromkeys(missing))


# ---------- тело текста ----------


def text_body(element: Any) -> Any | None:
    """`p:txBody` фигуры или `a:txBody` ячейки таблицы."""
    body = element.find("p:txBody", NS)
    if body is None:
        body = element.find("a:txBody", NS)
    return body


def _paragraphs(body: Any) -> list[Any]:
    return list(body.findall(A_P))


def _template_rpr(paragraph: Any) -> Any | None:
    """Оформление первого фрагмента; для пустого абзаца — из endParaRPr."""
    for child in paragraph:
        if child.tag in (A_R, A_FLD):
            rpr = child.find(A_RPR)
            if rpr is not None:
                return copy.deepcopy(rpr)
            return None
    end = paragraph.find(A_END)
    if end is not None:
        rpr = copy.deepcopy(end)
        rpr.tag = A_RPR
        return rpr
    return None


def _set_size(rpr: Any | None, size_pt: float | None) -> None:
    if rpr is not None and size_pt:
        rpr.set("sz", str(round(size_pt * 100)))


def _write_paragraph(paragraph: Any, text: str, size_pt: float | None) -> None:
    """Текст абзаца: фрагменты образца заменяются одним фрагментом с оформлением первого,
    `\\n` становится `a:br` с тем же оформлением."""
    rpr = _template_rpr(paragraph)
    for child in list(paragraph):
        if child.tag in _RUN_LIKE:
            paragraph.remove(child)
    end = paragraph.find(A_END)
    if end is None:
        end = etree.SubElement(paragraph, A_END)
    _set_size(end, size_pt)
    insert_at = list(paragraph).index(end)
    for i, line in enumerate(text.split("\n")):
        if i > 0:
            br = etree.Element(A_BR)
            if rpr is not None:
                br.append(copy.deepcopy(rpr))
            paragraph.insert(insert_at, br)
            insert_at += 1
        run = etree.Element(A_R)
        if rpr is not None:
            run_rpr = copy.deepcopy(rpr)
            _set_size(run_rpr, size_pt)
            run.append(run_rpr)
        elif size_pt:
            run_rpr = etree.SubElement(run, A_RPR)
            _set_size(run_rpr, size_pt)
        t = etree.SubElement(run, A_T)
        t.text = line
        paragraph.insert(insert_at, run)
        insert_at += 1


def set_wrap(element: Any, wrap: bool) -> None:
    """Перенос строк в рамке: числа в крошечных слотах (кружки с номерами) не переносятся —
    LibreOffice считает текстовую область эллипса уже прямоугольника и рвёт «27» на две строки."""
    body = element.find(".//a:bodyPr", NS)
    if body is None:
        return
    if wrap:
        if "wrap" in body.attrib:
            del body.attrib["wrap"]
    else:
        body.set("wrap", "none")


def _reset_autofit(element: Any) -> None:
    """Масштаб автоподбора образца снимается: кегли заданы явно."""
    body = element.find(".//a:bodyPr", NS)
    if body is None:
        return
    norm = body.find("a:normAutofit", NS)
    if norm is not None:
        for attr in ("fontScale", "lnSpcReduction"):
            if attr in norm.attrib:
                del norm.attrib[attr]


def _has_bullet(paragraph: Any) -> bool:
    ppr = paragraph.find(A_PPR)
    if ppr is None:
        return False
    return any(ppr.find(f"a:{tag}", NS) is not None for tag in _BULLET_TAGS)


def _styled(paragraphs: list[Any]) -> list[Any]:
    """Абзацы с фрагментами (несут оформление образца); если таких нет — все."""
    with_runs = [p for p in paragraphs if any(c.tag in (A_R, A_FLD) for c in p)]
    return with_runs or paragraphs


def fill_text(
    element: Any,
    text: str,
    *,
    size_pt: float | None = None,
    facts: dict[str, dict[str, Any]] | None = None,
) -> TextResult:
    """Заменяет текст фигуры. Многострочный текст ложится на абзацы образца, если их
    несколько; иначе в один абзац с переносами строк."""
    substituted, used, missing = substitute_facts(text, facts or {})
    body = text_body(element)
    if body is None:
        body = etree.SubElement(element, f"{{{NS_P}}}txBody")
        etree.SubElement(body, f"{{{NS_A}}}bodyPr")
        etree.SubElement(body, f"{{{NS_A}}}lstStyle")
    paragraphs = _paragraphs(body)
    if not paragraphs:
        paragraphs = [etree.SubElement(body, A_P)]
    templates = _styled(paragraphs)
    lines = substituted.split("\n")
    if len(lines) >= 2 and len(templates) >= 2:
        new_paragraphs = []
        for i, line in enumerate(lines):
            tpl = templates[min(i, len(templates) - 1)]
            p = copy.deepcopy(tpl)
            _write_paragraph(p, line, size_pt)
            new_paragraphs.append(p)
    else:
        p = copy.deepcopy(templates[0])
        _write_paragraph(p, substituted, size_pt)
        new_paragraphs = [p]
    _replace_paragraphs(body, paragraphs, new_paragraphs)
    _reset_autofit(element)
    return TextResult(
        plain=substituted,
        paragraphs=len(new_paragraphs),
        fact_refs=used,
        missing_facts=missing,
        size_pt=size_pt,
    )


def fill_bullets(
    element: Any,
    items: list[str],
    *,
    size_pt: float | None = None,
    facts: dict[str, dict[str, Any]] | None = None,
) -> TextResult:
    """Пункты списка — абзацы по образцу первого маркированного абзаца (иначе первого
    с фрагментами); оформление маркера и отступов сохраняется."""
    body = text_body(element)
    if body is None:
        return fill_text(element, "\n".join(items), size_pt=size_pt, facts=facts)
    paragraphs = _paragraphs(body)
    if not paragraphs:
        paragraphs = [etree.SubElement(body, A_P)]
    bulleted = [p for p in _styled(paragraphs) if _has_bullet(p)]
    template = bulleted[0] if bulleted else _styled(paragraphs)[0]
    used: list[str] = []
    missing: list[str] = []
    plain: list[str] = []
    new_paragraphs = []
    for item in items:
        substituted, u, m = substitute_facts(item, facts or {})
        used.extend(u)
        missing.extend(m)
        plain.append(substituted)
        p = copy.deepcopy(template)
        _write_paragraph(p, substituted, size_pt)
        new_paragraphs.append(p)
    if not new_paragraphs:
        p = copy.deepcopy(template)
        _write_paragraph(p, "", size_pt)
        new_paragraphs.append(p)
    _replace_paragraphs(body, paragraphs, new_paragraphs)
    _reset_autofit(element)
    return TextResult(
        plain="\n".join(plain),
        paragraphs=len(new_paragraphs),
        fact_refs=list(dict.fromkeys(used)),
        missing_facts=list(dict.fromkeys(missing)),
        size_pt=size_pt,
    )


def clear_text(element: Any) -> None:
    """Пустой текст с оформлением первого абзаца (объект остаётся на месте)."""
    body = text_body(element)
    if body is None:
        return
    paragraphs = _paragraphs(body)
    if not paragraphs:
        return
    p = copy.deepcopy(_styled(paragraphs)[0])
    _write_paragraph(p, "", None)
    _replace_paragraphs(body, paragraphs, [p])


def _replace_paragraphs(body: Any, old: list[Any], new: list[Any]) -> None:
    first_index = list(body).index(old[0])
    for p in old:
        body.remove(p)
    for offset, p in enumerate(new):
        body.insert(first_index + offset, p)


def plain_text(element: Any) -> str:
    """Текст фигуры: абзацы через `\\n`, `a:br` тоже как перенос строки."""
    body = text_body(element)
    if body is None:
        return ""
    out: list[str] = []
    for p in _paragraphs(body):
        parts: list[str] = []
        for child in p:
            if child.tag in (A_R, A_FLD):
                t = child.find(A_T)
                parts.append(t.text or "" if t is not None else "")
            elif child.tag == A_BR:
                parts.append("\n")
        out.append("".join(parts))
    return "\n".join(out)


def set_field_text(element: Any, field_type: str, text: str) -> int:
    """Подставляет текст в поля `a:fld` данного типа (номер слайда); возвращает число полей."""
    count = 0
    for fld in element.iter(A_FLD):
        if fld.get("type") == field_type:
            t = fld.find(A_T)
            if t is None:
                t = etree.SubElement(fld, A_T)
            t.text = text
            count += 1
    return count


__all__ = [
    "FACT_REF",
    "TextResult",
    "clear_text",
    "fact_text",
    "fill_bullets",
    "fill_text",
    "plain_text",
    "set_field_text",
    "set_wrap",
    "substitute_facts",
    "text_body",
]
