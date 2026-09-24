"""Превращение композиций библиотеки в паттерны профиля.

Собственная композиция попадает в профиль обычным паттерном с `source.kind = "builtin"`, и
дальше её видят все, кто читает профиль: отбор кандидатов, дайджест для модели, лестница
ёмкости, вёрстка и интерфейс. Отдельной ветки «а если композиция наша» в планировщике нет —
есть один пул, в котором паттерны шаблона идут первыми.

Ёмкость слотов считается теми же метриками шрифтов рендерера, что и у паттернов шаблона, но
по кеглям дизайн-кода: одна и та же композиция в шаблоне с крупной типографикой вмещает
меньше, и планировщик обязан это видеть до вёрстки, а не узнавать при сборке.
"""

from __future__ import annotations

from typing import Any

from presentation_designer.library.spec import Composition, load_families
from presentation_designer.library.tokens import DesignCode, content_tone
from presentation_designer.parsing.template.layouts import layout_family
from presentation_designer.parsing.template.tone import LIGHT_THRESHOLD, relative_luminance
from presentation_designer.shared import text_metrics

JsonDict = dict[str, Any]

# Уверенность собственной композиции ниже, чем у образца шаблона: при прочих равных
# планировщик выбирает то, что автор нарисовал сам.
BUILTIN_CONFIDENCE = 0.55
# Внутренние поля рамки как в PowerPoint.
INSETS_EMU = (91440, 45720, 91440, 45720)


def builtin_patterns(
    profile: JsonDict,
    *,
    code: DesignCode | None = None,
    slide_size: tuple[int, int] | None = None,
    layout_id: str | None = None,
) -> list[JsonDict]:
    """Паттерны из всех включённых композиций под дизайн-код этого шаблона."""
    design = code or DesignCode.from_profile(profile)
    width_emu, height_emu = slide_size or _slide_size(profile)
    layout = layout_id or _base_layout(profile)
    if not layout:
        return []
    layout_name = next(
        (
            str(item.get("name") or "")
            for item in profile.get("layouts") or []
            if str(item.get("layout_id")) == layout
        ),
        "",
    )
    tone = content_tone(profile)
    out: list[JsonDict] = []
    for family in load_families():
        for composition in family.expand():
            out.append(
                _as_pattern(composition, design, width_emu, height_emu, layout, layout_name, tone)
            )
    return out


def _slide_size(profile: JsonDict) -> tuple[int, int]:
    size = profile.get("slide_size") or {}
    return int(size.get("width_emu") or 12192000), int(size.get("height_emu") or 6858000)


def _base_layout(profile: JsonDict) -> str:
    """Макет, на котором строятся собственные композиции.

    Берётся самый пустой макет шаблона: у него меньше чужих рамок и декора, а фон, логотип и
    колонтитул наследуются. Если разметки нет вовсе — первый доступный. Макеты обложки,
    разделителя и финала не годятся, даже когда плейсхолдеров у всех поровну (VK WorkSpace:
    у каждого макета один заголовок, и первым шёл титульный с крупным декором сверху и
    снизу — текст композиций ложился на него): отсекаются макеты, на которых автор ставил
    только служебные слайды, и макеты с заголовком посередине высоты.
    """
    layouts = profile.get("layouts") or []
    if not layouts:
        return ""
    roles: dict[str, set[str]] = {}
    for pattern in profile.get("patterns") or []:
        source = pattern.get("source") or {}
        if source.get("kind") == "sample_slide":
            roles.setdefault(str(source.get("layout_id")), set()).add(str(pattern.get("role")))

    def service_only(item: JsonDict) -> int:
        used = roles.get(str(item.get("layout_id")))
        return 1 if used and used <= SERVICE_ROLES else 0

    def centered_title(item: JsonDict) -> int:
        titles = [
            p for p in item.get("placeholders") or [] if p.get("type") in ("title", "ctrTitle")
        ]
        return 1 if titles and float((titles[0].get("bbox") or {}).get("y") or 0) > 0.2 else 0

    ranked = sorted(
        layouts,
        key=lambda item: (
            service_only(item),
            centered_title(item),
            len(item.get("placeholders") or []),
            0 if "blank" in str(item.get("name") or "").lower() else 1,
        ),
    )
    return str(ranked[0].get("layout_id") or "")


SERVICE_ROLES = {"title", "thanks", "section_divider", "qr", "agenda", "speaker"}


def pattern_id_for(composition_id: str) -> str:
    """Идентификатор паттерна из идентификатора композиции: без символов схемы `@`, `,`, `=`."""
    tail = composition_id.replace("@", "_").replace(",", "_").replace("=", "")
    return f"pat_builtin_{tail}"


def _as_pattern(
    composition: Composition,
    code: DesignCode,
    width_emu: int,
    height_emu: int,
    layout_id: str,
    layout_name: str = "",
    content_tone: JsonDict | None = None,
) -> JsonDict:
    slots: list[JsonDict] = []
    for slot in composition.slots:
        raw = slot.as_profile_slot()
        if slot.kind in ("table", "chart", "image", "icon", "diagram"):
            slots.append(raw)
            continue
        size_pt = code.size_for(slot.text_role)
        family = code.font_for(slot.text_role)
        raw["font"] = {
            "family": family or "",
            "size_pt": round(size_pt, 1),
            "color": _slot_color(slot.color_role, code),
        }
        if slot.bold or (slot.text_role == "title" and code.title_bold):
            raw["font"]["bold"] = True
        raw["capacity"] = _capacity(
            slot.bbox,
            size_pt,
            family,
            width_emu,
            height_emu,
            slot.kind,
            bool(raw["font"].get("bold")),
        )
        slots.append(raw)
    # Тон и стиль — как у паттернов шаблона: по ним планировщик держит служебные слайды
    # (титул, разделители, финал) в одном стиле внутри колоды и разводит варианты. Фон
    # композиции — фон содержательных образцов (его ставит `library.skin`), без них — фон
    # дизайн-кода, под него подобраны цвета текста.
    luminance = relative_luminance(code.background)
    content = content_tone or {}
    if content.get("luminance") is not None:
        luminance = float(content["luminance"])
    tone = {
        "background": "light" if luminance >= LIGHT_THRESHOLD else "dark",
        "luminance": round(luminance, 3),
        "source": "design_code",
    }
    return {
        "pattern_id": pattern_id_for(composition.composition_id),
        "name": composition.name,
        "role": composition.role,
        "source": {
            "kind": "builtin",
            "layout_id": layout_id,
            "composition_id": composition.composition_id,
        },
        "slots": slots,
        "tone": tone,
        "style_key": f"{tone['background']}|{layout_family(layout_name)}|plain",
        "constraints": {
            "min_items": composition.min_items,
            "max_items": composition.max_items,
            **({"supports": list(composition.supports)} if composition.supports else {}),
        },
        "tags": list(composition.tags),
        "confidence": BUILTIN_CONFIDENCE,
        "role_source": "manual",
        "notes": composition.notes
        or "Собственная композиция: построена из дизайн-кода шаблона на его макете",
    }


def _slot_color(role: str, code: DesignCode) -> str:
    return {"accent": code.accent, "muted": code.muted_color}.get(role, code.text_color)


def _capacity(
    bbox: tuple[float, float, float, float],
    size_pt: float,
    family: str | None,
    width_emu: int,
    height_emu: int,
    kind: str,
    bold: bool = False,
) -> JsonDict:
    _, _, width, height = bbox
    font = text_metrics.resolve_font(family, bold=bold)
    cap = text_metrics.capacity(
        width_emu=int(width * width_emu),
        height_emu=int(height * height_emu),
        size_pt=size_pt,
        font=font,
        insets_emu=INSETS_EMU,
        bullet_indent_emu=182880 if kind == "bullets" else 0,
    )
    # Общая формула считает последнюю строку наполовину — у паттернов шаблона недобор
    # покрывает длина текста образца, которого у композиции нет. В однострочный слот
    # (заголовок, число, подпись) строка входит целиком, поэтому берётся полная ширина.
    lines = max(int(cap.max_lines), 1)
    max_chars = cap.chars_per_line if lines == 1 else int(cap.max_chars)
    out: JsonDict = {
        "max_chars": int(max(max_chars, cap.chars_per_line if lines else 0)),
        "max_lines": lines,
        "measured_with": {
            "method": cap.method,
            "margin_ratio": cap.margin_ratio,
            **({"font_file": cap.font_file} if cap.font_file else {}),
        },
        # Геометрия своя и точная, поэтому уверенность измерения выше, чем у разбора образца;
        # ниже единицы её держит только возможная подмена шрифта в рендерере.
        "confidence": 0.9 if cap.method == "font_metrics" and not cap.substituted else 0.6,
    }
    if kind == "bullets":
        out["max_items"] = max(1, min(int(cap.max_lines), 8))
        out["max_words_per_item"] = max(2, cap.chars_per_line // 7)
    return out
