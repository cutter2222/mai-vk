"""Детерминированная проверка собранного пакета до рендера.

Поверх `ooxml.check_package` (типы содержимого, цели связей, ссылки `r:*`, достижимость
частей, `sldIdLst`, уникальность id фигур) проверяется соответствие плану: число слайдов,
отсутствие недостижимых частей, отсутствие ссылок на части удалённых образцов, цельность
диаграмм (у каждой части chart есть книга данных или встроенные данные). Ошибка — пакет
не выдаётся; предупреждение — попадает в ComposedDeck.
"""

from __future__ import annotations

import pathlib
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

from presentation_designer.layout.ooxml import check_package

_PARSER = etree.XMLParser(resolve_entities=False, huge_tree=True)
NS_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


@dataclass
class IntegrityReport:
    path: str
    slides: int = 0
    parts: int = 0
    charts: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unreachable_parts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ok": self.ok,
            "slides": self.slides,
            "parts": self.parts,
            "charts": self.charts,
            "errors": self.errors,
            "warnings": self.warnings,
            "unreachable_parts": self.unreachable_parts,
        }


def check_deck(path: pathlib.Path, *, expected_slides: int | None = None) -> IntegrityReport:
    base = check_package(path)
    report = IntegrityReport(
        path=str(path),
        slides=base.slides,
        parts=base.parts,
        errors=list(base.errors),
        warnings=list(base.warnings),
        unreachable_parts=list(base.unreachable_parts),
    )
    if base.unreachable_parts:
        report.warnings.append(
            f"недостижимые части: {', '.join(base.unreachable_parts[:5])}"
            + (" …" if len(base.unreachable_parts) > 5 else "")
        )
    if expected_slides is not None and base.slides != expected_slides:
        report.errors.append(f"в файле {base.slides} слайдов, по плану {expected_slides}")
    if not base.ok:
        return report
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        charts = [n for n in names if re.fullmatch(r"ppt/charts/chart\d+\.xml", n)]
        report.charts = len(charts)
        for name in sorted(charts):
            root = etree.fromstring(zf.read(name), _PARSER)
            rels_name = name.replace("ppt/charts/", "ppt/charts/_rels/") + ".rels"
            has_external = root.find(f".//{{{NS_C}}}externalData") is not None
            has_cache = root.find(f".//{{{NS_C}}}numCache") is not None
            has_package = False
            if rels_name in names:
                rels = etree.fromstring(zf.read(rels_name), _PARSER)
                has_package = any(
                    r.get("Type", "").endswith("/package")
                    for r in rels.iter(f"{{{NS_REL}}}Relationship")
                )
            if has_external and not has_package:
                report.errors.append(f"{name}: ссылка на книгу данных без части")
            if not has_cache and not has_package:
                report.warnings.append(f"{name}: диаграмма без данных")
        # Слайды не должны ссылаться на отсутствующие файлы медиа.
        for name in sorted(
            n for n in names if re.fullmatch(r"ppt/slides/_rels/slide\d+\.xml\.rels", n)
        ):
            rels = etree.fromstring(zf.read(name), _PARSER)
            for rel in rels.iter(f"{{{NS_REL}}}Relationship"):
                if rel.get("TargetMode") == "External":
                    continue
                target = rel.get("Target", "")
                resolved = _resolve("ppt/slides/x.xml", target)
                if resolved not in names:
                    report.errors.append(f"{name}: {rel.get('Id')} → {target} отсутствует")
    return report


def _resolve(source: str, target: str) -> str:
    import posixpath

    if target.startswith("/"):
        return target[1:]
    return posixpath.normpath(posixpath.join(posixpath.dirname(source), target))


__all__ = ["IntegrityReport", "check_deck"]
