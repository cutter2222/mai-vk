"""Рабочий пакет результата: макеты, плейсхолдеры, номера слайдов, очистка.

Рабочая основа варианта — сам исходный PPTX, открытый python-pptx: темы, мастера, макеты и
ресурсы остаются, новые слайды добавляются клонированием образцов, образцы удаляются в конце.
При сохранении python-pptx записывает только части, достижимые от корня пакета, поэтому
ресурсы удалённых образцов в файл не попадают, а общие части (макеты, мастера, шрифты) —
остаются. Удаление неиспользуемых макетов — отдельная настройка: оно уменьшает файл, но
меняет набор макетов, доступный пользователю в PowerPoint.
"""

from __future__ import annotations

from typing import Any

from presentation_designer.layout.ooxml import NS_P
from presentation_designer.layout.text import set_field_text


def layout_stem(layout: Any) -> str:
    """`ppt/slideLayouts/slideLayout3.xml` → `slideLayout3` (идентификатор макета в профиле)."""
    return str(layout.part.partname).rsplit("/", 1)[-1].rsplit(".", 1)[0]


def layout_by_id(prs: Any, layout_id: str) -> Any | None:
    for master in prs.slide_masters:
        for layout in master.slide_layouts:
            if layout_stem(layout) == layout_id:
                return layout
    return None


def ensure_placeholder(slide: Any, layout: Any, layout_shape_id: str) -> Any | None:
    """Плейсхолдер слайда, соответствующий плейсхолдеру макета с данным id: по `idx`.
    Если на слайде его нет, клонируется из макета (как делает python-pptx при добавлении
    слайда)."""
    layout_ph = next(
        (p for p in layout.placeholders if str(p.shape_id) == str(layout_shape_id)), None
    )
    if layout_ph is None:
        return None
    idx = layout_ph.placeholder_format.idx
    for ph in slide.placeholders:
        if ph.placeholder_format.idx == idx:
            return ph._element
    slide.shapes.clone_placeholder(layout_ph)
    for ph in slide.placeholders:
        if ph.placeholder_format.idx == idx:
            return ph._element
    return None


def update_slide_numbers(prs: Any) -> int:
    """Подставляет актуальный номер в поля `a:fld type="slidenum"` на слайдах."""
    updated = 0
    for index, slide in enumerate(prs.slides, start=1):
        updated += set_field_text(slide.shapes._spTree, "slidenum", str(index))
    return updated


def used_layout_partnames(prs: Any) -> set[str]:
    return {str(slide.slide_layout.part.partname) for slide in prs.slides}


def prune_unused_layouts(prs: Any) -> int:
    """Удаляет макеты, которые не использует ни один слайд; у мастера остаётся хотя бы один
    макет. Возвращает число удалённых."""
    used = used_layout_partnames(prs)
    removed = 0
    for master in prs.slide_masters:
        layouts = list(master.slide_layouts)
        keep_one = layouts[0] if layouts else None
        for layout in layouts:
            if str(layout.part.partname) in used:
                continue
            if layout is keep_one and not any(str(lay.part.partname) in used for lay in layouts):
                continue
            master.slide_layouts.remove(layout)
            removed += 1
    return removed


def slide_part_name(slide: Any) -> str:
    return str(slide.part.partname).lstrip("/")


def section_list(prs: Any) -> Any | None:
    return prs.part._element.find(f".//{{{NS_P}}}sectionLst")


__all__ = [
    "ensure_placeholder",
    "layout_by_id",
    "layout_stem",
    "prune_unused_layouts",
    "slide_part_name",
    "update_slide_numbers",
    "used_layout_partnames",
]


def drop_template_logos(prs: Any, profile: dict[str, Any]) -> int:
    """Снимает знак шаблона со всех макетов, мастеров и слайдов колоды.

    Логотип шаблона живёт не на слайде, а на макете: в колоде объекта с ним нет, и правкой
    слайда его не убрать. Другому подразделению чужой знак делает шаблон непригодным, поэтому
    снимается он целиком по профилю (`fixed_elements` вида `logo`): часть пакета и id объекта
    известны, остальное — обычное удаление фигуры со снятием осиротевших связей.
    """
    from presentation_designer.layout.shapes import remove_shape, shape_element

    by_part: dict[str, set[str]] = {}
    for item in profile.get("fixed_elements") or []:
        if str(item.get("kind")) != "logo" or not item.get("element_ref"):
            continue
        by_part.setdefault(str(item.get("source_part") or ""), set()).add(str(item["element_ref"]))
    if not by_part:
        return 0
    everywhere = {ref for refs in by_part.values() for ref in refs}
    removed = 0

    def strip(container: Any, refs: set[str]) -> None:
        nonlocal removed
        for ref in sorted(refs):
            element = shape_element(container, ref)
            if element is None:
                continue
            removed += len(remove_shape(container, element))

    for master in prs.slide_masters:
        strip(master, by_part.get(str(master.part.partname).lstrip("/"), set()))
        for layout in master.slide_layouts:
            strip(layout, by_part.get(str(layout.part.partname).lstrip("/"), set()))
    # Клон образца сохраняет id объектов, поэтому логотип, попавший на слайд, снимается тоже.
    for slide in prs.slides:
        strip(slide, everywhere)
    return removed
