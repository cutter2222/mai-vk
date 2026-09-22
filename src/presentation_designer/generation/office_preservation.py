"""Strict acceptance check for text-only edits of a saved PPTX selection.

This is deliberately stricter than an editor round-trip: all ZIP members except
selected slide XML must be byte-identical. On those slides only selected a:t
contents may differ. Passing is not a visual overflow or translation-quality check.
"""

from __future__ import annotations

import io
from zipfile import ZipFile

from lxml import etree

from presentation_designer.generation.office_edit import NS, slides
from presentation_designer.generation.office_objects import ObjectTarget, selected, xml


def verify_selected_text_edit(
    before: bytes, after: bytes, targets: list[ObjectTarget]
) -> dict[str, list[str] | int]:
    """Raise ValueError on any change outside selected text, including formatting."""
    keys = {(target.slide, target.shape_id) for target in targets}
    if not targets or len(keys) != len(targets):
        raise ValueError("Пустой или повторяющийся выбор объектов")
    chosen = [selected(before, target) for target in targets]
    with ZipFile(io.BytesIO(before)) as old, ZipFile(io.BytesIO(after)) as new:
        names = old.namelist()
        if (
            len(names) != len(set(names))
            or len(new.namelist()) != len(set(new.namelist()))
            or set(names) != set(new.namelist())
            or old.comment != new.comment
        ):
            raise ValueError("Изменён состав PPTX или найдены повторяющиеся ZIP-части")
        order = slides(old)
        allowed: dict[str, set[int]] = {}
        for obj in chosen:
            allowed.setdefault(order[obj.slide - 1], set()).update(obj.runs)
        changed_parts = []
        changed_runs = 0
        for name in names:
            original, updated = old.read(name), new.read(name)
            if original == updated:
                continue
            if name not in allowed:
                raise ValueError(f"Изменена защищённая часть PPTX: {name}")
            left, right = xml(original), xml(updated)
            left_runs, right_runs = left.findall(".//a:t", NS), right.findall(".//a:t", NS)
            if len(left_runs) != len(right_runs):
                raise ValueError(f"Изменена структура текстовых runs: {name}")
            for index in allowed[name]:
                if left_runs[index].text != right_runs[index].text:
                    changed_runs += 1
                right_runs[index].text = left_runs[index].text
            if etree.tostring(left, method="c14n") != etree.tostring(right, method="c14n"):
                raise ValueError(f"Изменено оформление или содержимое вне выбора: {name}")
            changed_parts.append(name)
        return {
            "changed_slide_parts": changed_parts,
            "changed_text_runs": changed_runs,
            "unchanged_parts": len(names) - len(changed_parts),
            "slides": len(order),
        }
