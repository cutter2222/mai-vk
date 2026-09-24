"""Подбор композиций (паттернов) кодом: структура слотов, вместимость, кандидаты по типу содержания.

Профиль шаблона описывает паттерны как список слотов; для планирования удобнее «структура»:
заголовок, одиночные слоты по видам, повторяющиеся группы карточек (слот каждого вида на
карточку), какие визуализации паттерн умеет показывать и сколько текста вмещает. По ней код
отбирает кандидатов для каждого тезиса: роль паттерна, поддерживаемая визуализация (таблица
только при слоте таблицы, диаграмма — при слоте диаграммы или картинке диаграммы в образце
роли chart), число элементов, ёмкость текста, уверенность анализа и предпочтения варианта.
Титульный, разделители, содержание и финальный слайд подбираются по ролям. При генерации
композиция выбирается после ответа модели; геометрию и ёмкость определяет код.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.shared.text import plural

JsonDict = dict[str, Any]

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
# Одиночные обязательные слоты, которые код умеет заполнить из любого тезиса.
FILLABLE_REQUIRED = {"title", "subtitle", "body", "bullets", "label", "caption", "date"}
# Роли, не подходящие для содержательных слайдов (подбираются по ролям отдельно).
FIXED_ROLES = {"title", "thanks", "qr", "agenda", "section_divider"}
SKIPPED_ROLES = {"speaker", "team", "pricing", "code"}
CONTENT_VISUALS = (
    "text",
    "bullets",
    "number",
    "chart",
    "table",
    "diagram",
    "image",
    "quote",
    "comparison",
    "timeline",
    "cards",
)

# Роли паттернов, подходящие каждой подаче, с весом.
ROLE_AFFINITY: dict[str, dict[str, float]] = {
    "number": {"kpi": 3.0, "numbers": 3.0, "cards": 1.4, "comparison": 1.0, "chart": 0.8},
    "chart": {"chart": 3.0},
    "table": {"table": 3.0},
    "bullets": {"bullets": 3.0, "cards": 2.2, "text": 1.6, "two_column": 1.6, "process": 0.8},
    "text": {"text": 3.0, "bullets": 2.0, "two_column": 1.6, "cards": 1.2, "quote": 0.6},
    "timeline": {"timeline": 3.0, "process": 2.6, "cards": 1.4, "bullets": 0.8},
    "diagram": {"process": 3.0, "timeline": 2.2, "cards": 1.6, "bullets": 0.8},
    "comparison": {"comparison": 3.0, "two_column": 2.6, "cards": 1.6, "bullets": 0.8},
    "image": {"image_full": 3.0, "screenshot": 2.6, "mockup": 2.2, "text": 1.4, "cards": 1.0},
    "quote": {"quote": 3.0, "text": 1.6, "section_divider": 0.9, "bullets": 0.6},
    "cards": {"cards": 3.0, "bullets": 1.6, "numbers": 1.2, "two_column": 1.2},
}
# Штраф собственной композиции в отборе: паттерны загруженного файла идут первыми, своя
# композиция побеждает только с заметным перевесом по пригодности.
BUILTIN_PENALTY = 0.7
# Насколько своя композиция должна быть пригоднее лучшего паттерна шаблона, чтобы встать
# перед ним: без перевеса она добирает список после паттернов автора.
BUILTIN_LEAD = 1.25
# Предпочтения оси плотности: compact тянется к цифрам и коротким спискам, detailed —
# к карточкам, таблицам и двум колонкам.
VARIANT_ROLE_BIAS: dict[str, dict[str, float]] = {
    "compact": {
        "kpi": 1.3,
        "numbers": 1.3,
        "bullets": 1.2,
        "chart": 1.1,
        "cards": 0.9,
        "table": 0.8,
    },
    "balanced": {"text": 1.1, "chart": 1.15, "cards": 1.05},
    "detailed": {"cards": 1.25, "table": 1.3, "two_column": 1.2, "process": 1.1, "text": 1.05},
}


@dataclass
class SlotInfo:
    slot_id: str
    kind: str
    required: bool
    group: str | None
    bbox: tuple[float, float, float, float]
    family: str | None
    size_pt: float | None
    bold: bool
    italic: bool
    line_spacing: float
    space_before_pt: float
    space_after_pt: float
    insets: dict[str, float] | None
    max_chars: int
    max_lines: int
    max_items: int
    autofit: str | None
    sample_text: str
    indent_emu: int
    bullet: bool
    is_ordinal: bool = False
    clear_height: float | None = None
    clear_width: float | None = None

    @property
    def is_text(self) -> bool:
        return self.kind in TEXT_KINDS


@dataclass
class CardGroup:
    group_id: str
    count: int
    by_kind: dict[str, list[SlotInfo]]

    @property
    def text_kinds(self) -> list[str]:
        """Текстовые виды карточки в порядке заполнения: подпись/заголовок карточки, затем
        текст. Дополнительные слоты title (не первый заголовок слайда) — заголовки карточек."""
        return [
            k
            for k in ("label", "title", "subtitle", "body", "caption", "bullets")
            if k in self.by_kind
        ]

    def card(self, index: int) -> dict[str, SlotInfo]:
        """Слоты карточки index: по одному слоту каждого вида в порядке чтения."""
        out: dict[str, SlotInfo] = {}
        for kind, slots in self.by_kind.items():
            if index < len(slots):
                out[kind] = slots[index]
        return out


@dataclass
class PatternInfo:
    pattern_id: str
    role: str
    name: str
    confidence: float
    title: SlotInfo | None
    singles: dict[str, list[SlotInfo]]
    groups: list[CardGroup]
    supports: set[str]
    max_consecutive: int
    typical_position: str
    slots: dict[str, SlotInfo] = field(default_factory=dict)
    group_id: str = ""
    tone: str = "unknown"
    style_key: str = ""
    slide_index: int = 0
    # Композиция собственной библиотеки, а не образец шаблона: в отборе идёт после паттернов
    # автора и выигрывает только там, где шаблон не покрывает подачу или не вмещает содержание.
    builtin: bool = False
    ordinal_slot_ids: set[str] = field(default_factory=set)

    # ----- состав -----

    def single(self, kind: str) -> list[SlotInfo]:
        return self.singles.get(kind, [])

    @property
    def cards(self) -> CardGroup | None:
        """Главная группа карточек: с текстом и наибольшим числом карточек."""
        best: CardGroup | None = None
        for g in self.groups:
            if not g.text_kinds and "number" not in g.by_kind:
                continue
            if best is None or g.count > best.count:
                best = g
        return best

    @property
    def largest_text_slot(self) -> int:
        """Ёмкость самого вместительного текстового слота под содержание (без заголовка)."""
        kinds = ("body", "bullets", "subtitle", "caption", "label")
        return max(
            (
                s.max_chars
                for s in self.slots.values()
                if s.kind in kinds and s is not self.title and s.max_chars
            ),
            default=0,
        )

    @property
    def irregular_cards(self) -> bool:
        """Карточки распознаны не полностью: рядом с группой стоит одиночный слот того же
        вида и того же размера — карточка, выпавшая из ряда (ЛЦТ «Цифры × 4»: у второй
        карточки иная геометрия). План пишет пункты не в те рамки, и тексты ложатся друг
        на друга."""
        for group in self.groups:
            for kind, members in group.by_kind.items():
                # Выпавшая подпись карточки заполняется одиночным слотом без вреда; выпавший
                # текст карточки — нет: в него уходит абзац, рассчитанный на весь слайд.
                if kind not in ("body", "bullets", "caption") or not members:
                    continue
                w = sum(m.bbox[2] for m in members) / len(members)
                h = sum(m.bbox[3] for m in members) / len(members)
                for single in self.single(kind):
                    if single.group or single is self.title:
                        continue
                    if abs(single.bbox[2] - w) <= 0.12 * w and abs(single.bbox[3] - h) <= 0.12 * h:
                        return True
        return False

    @property
    def required_singles(self) -> list[SlotInfo]:
        return [s for slots in self.singles.values() for s in slots if s.required]

    def required_table_or_chart(self) -> bool:
        return any(s.kind in ("table", "chart") for s in self.required_singles)

    @property
    def service_safe(self) -> bool:
        """Годится для служебного слайда: нет обязательных слотов внутри повторяющихся групп
        (заголовки карточек), которые титул, разделитель, оглавление и финал не заполняют."""
        return not any(s.required and s.group for s in self.slots.values())

    def fillable(
        self,
        *,
        has_datasets: bool,
        has_code: bool = False,
        has_image: bool = False,
        has_diagram: bool = False,
    ) -> bool:
        """Все обязательные одиночные слоты заполняются из тезиса: текстовые всегда, таблица и
        диаграмма — при наборе данных, код — при блоке кода; имя/должность/QR — нет."""
        for s in self.required_singles:
            if s.kind in FILLABLE_REQUIRED:
                continue
            if s.kind in ("table", "chart") and has_datasets:
                continue
            if s.kind == "diagram" and has_diagram:
                continue
            if s.kind == "image" and (has_image or (self.role == "chart" and has_datasets)):
                continue
            if s.kind == "code" and has_code:
                continue
            return False
        return True

    # ----- вместимость и визуализация -----

    @property
    def has_table(self) -> bool:
        return bool(self.single("table"))

    @property
    def has_chart(self) -> bool:
        return bool(self.single("chart")) or (self.role == "chart" and bool(self.single("image")))

    @property
    def chart_slot(self) -> SlotInfo | None:
        if self.single("chart"):
            return self.single("chart")[0]
        if self.role == "chart" and self.single("image"):
            return self.single("image")[0]
        return None

    @property
    def number_capacity(self) -> int:
        return sum(
            s.kind == "number" and s.slot_id not in self.ordinal_slot_ids
            for s in self.slots.values()
        )

    @property
    def item_capacity(self) -> int:
        """Сколько элементов списка/карточек паттерн показывает: буллеты, карточки или
        колонки списков (число колонок × пунктов в колонке)."""
        bullets = self.single("bullets")
        cap = max((s.max_items for s in bullets), default=0)
        cards = self.cards
        if cards is not None:
            if "bullets" in cards.by_kind:
                per_column = max((s.max_items for s in cards.by_kind["bullets"]), default=1)
                cap = max(cap, cards.count * max(per_column, 1))
            else:
                cap = max(cap, cards.count)
        return cap

    @property
    def list_columns(self) -> int:
        """Колонки списков в главной группе карточек (две колонки буллитов); 0 — их нет."""
        cards = self.cards
        if cards is None or "bullets" not in cards.by_kind:
            return 0
        return len(cards.by_kind["bullets"])

    @property
    def text_capacity(self) -> int:
        chars = sum(s.max_chars for k in ("body", "bullets", "subtitle") for s in self.single(k))
        cards = self.cards
        if cards is not None:
            for kind in cards.text_kinds:
                chars += sum(s.max_chars for s in cards.by_kind[kind])
        return chars

    @property
    def has_image_slot(self) -> bool:
        return bool(self.single("image")) and self.role != "chart"

    def visuals(self) -> set[str]:
        out: set[str] = set()
        if self.single("diagram"):
            out.update({"diagram", "timeline"})
        if self.has_table:
            out.add("table")
        if self.has_chart:
            out.add("chart")
        if self.number_capacity:
            out.add("number")
        if self.item_capacity >= 2:
            out.add("bullets")
            out.add("cards")
        if self.role in ("timeline", "process") and self.item_capacity >= 2:
            out.update({"timeline", "diagram"})
        if self.role in ("comparison", "two_column") and self.item_capacity >= 2:
            out.add("comparison")
        if self.single("body") or self.single("bullets") or self.single("subtitle"):
            out.add("text")
        if self.has_image_slot:
            out.add("image")
        if self.role == "quote":
            out.add("quote")
        return out

    def summary(self) -> str:
        """Строка для запроса модели: роль, имя и что вмещает."""
        parts: list[str] = []
        if self.title is not None:
            parts.append(f"заголовок ≤{self.title.max_chars}")
        for kind, label in (("subtitle", "подзаголовок"), ("body", "текст"), ("bullets", "список")):
            for s in self.single(kind):
                if 0 < s.max_chars < 12:
                    continue  # крошечные слоты (единицы, номера) модели не предлагаются
                if kind == "bullets":
                    per_item = max(1, s.max_chars // max(1, s.max_items))
                    parts.append(f"список до {s.max_items} пунктов по ≤{per_item}")
                else:
                    parts.append(f"{label} ≤{s.max_chars}")
        nums = sum(s.slot_id not in self.ordinal_slot_ids for s in self.single("number"))
        if nums:
            parts.append(f"{nums} {plural(nums, 'показатель', 'показателя', 'показателей')}")
        cards = self.cards
        if cards is not None:
            inner = []
            if "number" in cards.by_kind:
                inner.append(
                    "номер шага"
                    if all(s.slot_id in self.ordinal_slot_ids for s in cards.by_kind["number"])
                    else "число"
                )
            for kind in cards.text_kinds:
                label = _KIND_LABEL.get(kind, "текст")
                inner.append(f"{label} ≤{cards.by_kind[kind][0].max_chars}")
            if "icon" in cards.by_kind:
                inner.append("иконка")
            word = plural(cards.count, "карточка", "карточки", "карточек")
            parts.append(f"{cards.count} {word} ({', '.join(inner)})")
        if self.has_table:
            parts.append("таблица")
        if self.has_chart:
            parts.append("диаграмма")
        if self.has_image_slot:
            parts.append("изображение")
        origin = "библиотека в дизайн-коде шаблона" if self.builtin else "образец шаблона"
        boxes = "; ".join(
            f"{s.slot_id} ({','.join(f'{v:.2f}' for v in s.bbox)})"
            for s in self.slots.values()
            if s.slot_id not in self.ordinal_slot_ids
        )
        return (
            f"{self.pattern_id} · {self.role} · {self.name} · {origin} · {self.tone} · "
            f"{' ; '.join(parts)} · слоты (x,y,w,h в долях слайда): {boxes}"
        )


_KIND_LABEL = {
    "title": "заголовок",
    "body": "текст",
    "label": "подпись",
    "subtitle": "подзаголовок",
    "caption": "подпись",
    "bullets": "список",
}


# ---------- разбор профиля ----------


def _slot_info(raw: JsonDict) -> SlotInfo:
    cap = raw.get("capacity") or {}
    font = raw.get("font") or {}
    params = raw.get("paragraph_params") or {}
    bbox = raw.get("bbox") or {}
    style = raw.get("computed_style") or {}
    bullet = (style.get("bullet") or {}).get("kind") not in (None, "none")
    return SlotInfo(
        slot_id=str(raw["slot_id"]),
        kind=str(raw["kind"]),
        required=bool(raw.get("required", False)),
        group=raw.get("repeat_group"),
        bbox=(
            float(bbox.get("x", 0)),
            float(bbox.get("y", 0)),
            float(bbox.get("width", 0)),
            float(bbox.get("height", 0)),
        ),
        family=font.get("family"),
        size_pt=float(font["size_pt"]) if font.get("size_pt") else None,
        bold=bool(font.get("bold", False)),
        italic=bool(font.get("italic", False)),
        line_spacing=float(font.get("line_spacing") or params.get("line_spacing") or 1.0),
        space_before_pt=float(params.get("space_before_pt") or 0.0),
        space_after_pt=float(params.get("space_after_pt") or 0.0),
        insets=params.get("insets"),
        max_chars=int(cap.get("max_chars") or 0),
        max_lines=int(cap.get("max_lines") or 0),
        max_items=int(cap.get("max_items") or 0),
        autofit=params.get("autofit"),
        sample_text=str(raw.get("sample_text") or ""),
        indent_emu=int(style.get("indent_emu") or 0),
        bullet=bullet,
        clear_height=float(raw["clear_height"]) if raw.get("clear_height") else None,
        clear_width=float(raw["clear_width"]) if raw.get("clear_width") else None,
    )


def ordinal_slots(raw: JsonDict) -> set[str]:
    """Recognize small repeated step badges, including in old stored profiles."""
    badges = sorted(
        (
            _slot_info(s)
            for s in raw.get("slots", [])
            if s.get("kind") == "number"
            and 0 < float((s.get("bbox") or {}).get("width", 0)) <= 0.12
        ),
        key=lambda s: (round(s.bbox[1], 2), s.bbox[0]),
    )
    if len(badges) >= 2 and all(
        re.fullmatch(r"0*" + str(i), s.sample_text.strip()) for i, s in enumerate(badges, 1)
    ):
        return {s.slot_id for s in badges}
    return set()


def pattern_info(raw: JsonDict) -> PatternInfo:
    slots = [_slot_info(s) for s in raw.get("slots", [])]
    title = next((s for s in slots if s.kind == "title"), None)
    # Профили старых анализаторов могли пометить заголовками несколько надписей (номера
    # карточек тем же кеглем); заголовок в паттерне один, остальные — подписи.
    for s in slots:
        if s.kind == "title" and s is not title:
            s.kind = "label"
    singles: dict[str, list[SlotInfo]] = collections.defaultdict(list)
    grouped: dict[str, list[SlotInfo]] = collections.defaultdict(list)
    for s in slots:
        if s is title:
            continue
        if s.group:
            grouped[s.group].append(s)
        else:
            singles[s.kind].append(s)
    groups: list[CardGroup] = []
    for gid, members in grouped.items():
        by_kind: dict[str, list[SlotInfo]] = collections.defaultdict(list)
        for s in members:
            by_kind[s.kind].append(s)
        count = max(len(v) for v in by_kind.values())
        groups.append(CardGroup(gid, count, dict(by_kind)))
    hints = raw.get("sequence_hints") or {}
    tone = raw.get("tone") or {}
    # A repeated sequence 1..N in small badges is navigation, not N KPI slots.
    # Require both the sequence and geometry; a large hero number remains a KPI.
    ordinals = ordinal_slots(raw)
    for slot in slots:
        slot.is_ordinal = slot.slot_id in ordinals
    return PatternInfo(
        pattern_id=str(raw["pattern_id"]),
        role=str(raw.get("role", "freeform")),
        name=str(raw.get("name") or raw["pattern_id"]),
        confidence=float(raw.get("confidence") or 0.5),
        title=title,
        singles=dict(singles),
        groups=groups,
        supports=set((raw.get("constraints") or {}).get("supports") or []),
        max_consecutive=int(hints.get("max_consecutive") or 3),
        typical_position=str(hints.get("typical_position") or "any"),
        slots={s.slot_id: s for s in slots},
        group_id=str(raw.get("group_id") or ""),
        tone=str(tone.get("background") or "unknown"),
        style_key=str(raw.get("style_key") or ""),
        slide_index=int((raw.get("source") or {}).get("slide_index") or 0),
        builtin=(raw.get("source") or {}).get("kind") == "builtin",
        ordinal_slot_ids=ordinals,
    )


def profile_patterns(profile: JsonDict) -> list[PatternInfo]:
    return [pattern_info(p) for p in profile.get("patterns", [])]


# ---------- фиксированные слайды по ролям ----------


# Слайд спикера — титул презентации-визитки (popov.pptx: портрет и имя на первом слайде);
# если и его нет, титулом становится любой образец с заголовком (см. fixed_pattern_pool).
FIXED_FALLBACK_ROLES: dict[str, tuple[str, ...]] = {
    "title": ("title", "section_divider", "text", "speaker"),
    "section_divider": ("section_divider",),
    "thanks": ("thanks", "qr"),
    "agenda": ("agenda",),
}
STYLE_POLICIES = ("per_variant", "first")
# Роли, чьи образцы шаблона берутся раньше своей композиции первой роли цепочки.
DECORATED_FALLBACK_ROLES: dict[str, tuple[str, ...]] = {
    "title": ("title", "section_divider"),
    "section_divider": ("section_divider",),
    # Шаблон без финального слайда закрывается своей обложкой, а не белой композицией.
    "thanks": ("thanks", "qr", "title"),
    "agenda": ("agenda",),
}
_COVER_HINTS = ("title slide", "cover", "обложк")


def cover_like(p: PatternInfo) -> bool:
    """Образец на титульном макете, а не на макете разделителя раздела."""
    family = [*p.style_key.split("|"), "", ""][1].lower()
    if any(h in family for h in _COVER_HINTS):
        return True
    return "титульн" in family and "раздел" not in family and "section" not in family


def fixed_pattern_pool(
    patterns: list[PatternInfo], role: str, *, has_datasets: bool = False
) -> list[PatternInfo]:
    """Весь пул роли для служебного слайда в порядке предпочтения: заполняемые паттерны первой
    роли из цепочки резервов без обязательных слотов в карточках (`service_safe`; если таких
    нет — все заполняемые), по возрастанию числа обязательных слотов (титульный слайд не
    должен требовать таблицу), по убыванию уверенности, затем по порядку образцов в шаблоне
    (`slide_index`, при равенстве — идентификатор)."""

    def ordered(pool: list[PatternInfo]) -> list[PatternInfo]:
        # Титул, разделитель, оглавление и финал — лицо колоды, и в шаблоне они почти всегда
        # нарисованы с декором. Своя композиция служебной роли берётся, только если в шаблоне
        # такой роли нет вовсе: иначе она попадала бы в чередование стилей по вариантам
        # наравне с нарисованными образцами.
        pool = [p for p in pool if not p.builtin] or pool
        pool = [p for p in pool if p.service_safe] or pool
        pool.sort(
            key=lambda p: (
                0 if role not in ("title", "thanks") or cover_like(p) else 1,
                len(p.required_singles),
                -p.confidence,
                p.slide_index,
                p.pattern_id,
            )
        )
        return pool

    # Нарисованный образец шаблона важнее своей композиции той же роли: анализатор нередко
    # относит обложку к разделителям (VK Education: четыре обложки на макете «Титульный
    # слайд» с ролью section_divider), и тогда белый титул библиотеки вытеснял их.
    for r in DECORATED_FALLBACK_ROLES.get(role, ()):
        pool = [
            p
            for p in patterns
            if p.role == r and not p.builtin and p.fillable(has_datasets=has_datasets)
        ]
        if pool:
            return ordered(pool)
    for r in FIXED_FALLBACK_ROLES.get(role, (role,)):
        pool = [p for p in patterns if p.role == r and p.fillable(has_datasets=has_datasets)]
        if pool:
            return ordered(pool)
    if role == "title":
        # Презентация без титульной композиции (обычный файл, отданный как шаблон): титулом
        # становится образец с заголовком и наименьшим числом обязательных слотов, а не отказ.
        pool = [
            p
            for p in patterns
            if p.title is not None
            and p.role not in ("thanks", "qr", "agenda")
            and p.fillable(has_datasets=has_datasets)
        ]
        if pool:
            return ordered(pool)
    return []


def fixed_pattern(
    patterns: list[PatternInfo], role: str, *, has_datasets: bool = False
) -> PatternInfo | None:
    """Лучший паттерн роли — первый элемент пула."""
    pool = fixed_pattern_pool(patterns, role, has_datasets=has_datasets)
    return pool[0] if pool else None


def siblings_of(patterns: list[PatternInfo], p: PatternInfo) -> list[PatternInfo]:
    """Члены той же группы взаимозаменяемых образцов (без самого паттерна) в порядке образцов."""
    if not p.group_id:
        return []
    return sorted(
        (q for q in patterns if q.group_id == p.group_id and q.pattern_id != p.pattern_id),
        key=lambda q: (q.slide_index, q.pattern_id),
    )


def pick_style(
    pool: list[PatternInfo], variant_id: str, policy: str = "per_variant"
) -> PatternInfo | None:
    """Паттерн служебного слайда для варианта. `per_variant`: различные `style_key` в порядке
    пула, вариант берёт стиль с индексом `VARIANTS.index(variant_id) % len(styles)`; когда
    стилей меньше, чем вариантов, варианты одного стиля берут разные образцы этого стиля
    (`index // len(styles)` по кругу), чтобы три финала не совпадали при двух тонах.
    `first` — прежнее поведение, первый элемент пула. Тон `unknown` остаётся стилем сам по
    себе: порядок задают `slide_index`, источник тона записан в профиле."""
    if not pool:
        return None
    # Чередование стилей — про образцы шаблона: у своей композиции стиля шаблона нет, и её
    # пустой style_key иначе становится ещё одним «стилем», который вариант выбирает наравне
    # с нарисованными титулами и разделителями.
    pool = [p for p in pool if not p.builtin] or pool
    if policy != "per_variant":
        return pool[0]
    styles: list[str] = []
    for p in pool:
        if p.style_key not in styles:
            styles.append(p.style_key)
    variants = ("compact", "balanced", "detailed")
    index = variants.index(variant_id) if variant_id in variants else 0
    members = [p for p in pool if p.style_key == styles[index % len(styles)]]
    return members[(index // len(styles)) % len(members)]


# ---------- кандидаты для содержательных слайдов ----------


@dataclass
class Need:
    """Что нужно показать: подача, число элементов, показателей, объём текста, есть ли данные."""

    visual: str
    items: int = 1
    numbers: int = 0
    text_chars: int = 0
    has_dataset: bool = False
    has_image: bool = False
    has_diagram: bool = False


def score_pattern(p: PatternInfo, need: Need, variant_id: str) -> float:
    if p.single("diagram") and not need.has_diagram:
        return 0.0
    if p.role in FIXED_ROLES or p.role in SKIPPED_ROLES:
        return 0.0
    if not need.has_image and p.role in ("image_full", "screenshot", "mockup"):
        return 0.0
    if not need.has_image and any(
        s.kind == "image" and s.bbox[2] * s.bbox[3] >= 0.18 for s in p.slots.values()
    ):
        # Optional photo slots still define the composition. Removing a half-slide
        # photo leaves a mostly empty slide even when the classifier called it text.
        if not (p.role == "chart" and need.has_dataset):
            return 0.0
    if not need.has_image and any(s.kind == "image" for s in p.required_singles):
        # A chart image can be replaced with a native chart from the dataset.
        if not (p.role == "chart" and need.has_dataset):
            return 0.0
    if (
        not any(p.single(k) for k in ("body", "bullets", "subtitle"))
        and p.cards is None
        and not any(s.max_chars >= 40 for s in p.single("label"))
        and p.number_capacity == 0
        and not p.single("diagram")
        and not ((p.has_chart or p.has_table) and need.has_dataset)
        and not (p.has_image_slot and need.has_image)
    ):
        # Кроме заголовка содержание показать нечем (VK Tech «Карточки продуктов»: подписи —
        # картинки образца): тезис потерялся бы, а на слайде остались бы чужие продукты.
        return 0.0
    if p.irregular_cards and not p.builtin:
        return 0.0
    if need.text_chars / max(need.items, 1) >= 90 and 0 < p.largest_text_slot < 60:
        # Образец из одних подписей (VK Tech «Два блока с иконками»: слоты по 26–41 знаку,
        # подписи 5,6 pt): абзацы в него не встают ни при каком кегле.
        return 0.0
    if p.role == "quote" and need.visual != "quote":
        # У образца цитаты место есть только под саму цитату и автора: утверждение с
        # пояснением теряет пояснение, на слайде остаётся одна строка.
        return 0.0
    if p.number_capacity >= 8 and p.number_capacity > 2 * max(need.numbers, 1) + 2:
        # Инфографика на 16 полос под четыре числа: пустые полосы без подписей выглядят
        # сломанной диаграммой (VK Tech «воронка», ЛЦТ «цифры × 16»).
        return 0.0
    affinity = ROLE_AFFINITY.get(need.visual, ROLE_AFFINITY["text"])
    base = affinity.get(p.role, 0.0)
    if p.role == "freeform":
        base = 0.3
    visuals = p.visuals()
    if base <= 0 and need.visual in visuals and need.visual in ("chart", "table", "number"):
        # Роль иная, но нужный слот есть (таблица в образце роли «текст» и т. п.).
        base = 1.5
    if base <= 0:
        return 0.0
    # Обязательные возможности: без них паттерн не кандидат.
    if need.visual == "chart" and "chart" not in visuals:
        return 0.0
    if need.visual == "table" and "table" not in visuals:
        return 0.0
    if need.visual == "number" and "number" not in visuals and "cards" not in visuals:
        return 0.0
    if need.visual == "image" and "image" not in visuals:
        return 0.0
    score = base
    if p.single("diagram") and need.has_diagram:
        # A native diagram expresses relationships, unlike a generic card grid.
        score += 2.0
    if need.visual in visuals:
        score += 0.6
    # Вместимость: элементы, показатели, текст.
    cap = p.item_capacity
    if need.items >= 2:
        if cap >= need.items:
            score += 0.5
            if cap > 2 * need.items:
                # Композиция на 6–7 элементов под три пункта выглядит пустой: подходит
                # ближайшая по вместимости.
                score -= 0.4
        elif cap >= 1 and p.single("bullets"):
            score += 0.2
        elif cap == 0 and p.text_capacity >= need.text_chars:
            score += 0.1
        else:
            score -= 0.8
    elif cap >= 3 and need.visual in ("text", "bullets", "cards"):
        # Одна мысль без пунктов: карточки и шаги под неё не нужны.
        score -= 0.6
    if need.numbers:
        if p.number_capacity >= need.numbers:
            score += 0.4
        elif p.number_capacity:
            score += 0.1
    if need.text_chars and p.text_capacity < need.text_chars * 0.6:
        score -= 0.5
    if need.has_image and p.has_image_slot:
        score += 0.3
    score += 0.5 * p.confidence
    # Обязательные слоты на несколько символов (единицы, номера) — признак составного образца.
    score -= 0.4 * sum(1 for s in p.required_singles if s.is_text and 0 < s.max_chars < 12)
    score *= VARIANT_ROLE_BIAS.get(variant_id, {}).get(p.role, 1.0)
    if p.builtin:
        # У образца шаблона картинка и схема уже нарисованы: под текстовый тезис он всё равно
        # выглядит цельно. У своей композиции на их месте пустое место, которое вёрстка
        # уберёт, — поэтому медиа-композиция берётся только под медиа-содержание.
        if p.has_image_slot and not (need.has_image or need.visual == "image"):
            return 0.0
        if "diagram" in p.supports and need.visual not in ("diagram", "timeline"):
            return 0.0
        # Сначала шаблон автора, свои композиции — когда его не хватает. Множитель подобран
        # так, чтобы годный паттерн шаблона обходил свою композицию той же роли, а заметно
        # менее пригодный (не та подача, не вмещает содержание) — нет.
        score *= BUILTIN_PENALTY
    return max(score, 0.0)


def candidates_for(
    patterns: list[PatternInfo],
    need: Need,
    variant_id: str,
    *,
    limit: int = 3,
    has_datasets: bool = False,
    include_library_alternative: bool = False,
) -> list[PatternInfo]:
    """Кандидаты по убыванию оценки; таблица и диаграмма только при наборе данных.

    Паттерны загруженного файла идут первыми. Своя композиция обгоняет лучший паттерн
    шаблона, только если заметно пригоднее (`BUILTIN_LEAD`) — то есть когда шаблон не даёт
    нужной подачи или не вмещает содержание; иначе она добирает список после него.
    """
    scored: list[tuple[float, PatternInfo]] = []
    for p in patterns:
        if not p.fillable(
            has_datasets=has_datasets, has_image=need.has_image, has_diagram=need.has_diagram
        ):
            continue
        if not has_datasets and (need.visual in ("chart", "table") or p.required_table_or_chart()):
            continue
        s = score_pattern(p, need, variant_id)
        if s > 0:
            scored.append((s, p))
    scored.sort(key=lambda t: (-t[0], t[1].pattern_id))
    best_template = max((s for s, p in scored if not p.builtin), default=0.0)
    ranked = sorted(
        scored,
        key=lambda t: (
            # 0 — паттерн шаблона или своя композиция с заметным перевесом, 1 — остальные свои.
            0 if not t[1].builtin or t[0] > best_template * BUILTIN_LEAD else 1,
            -t[0],
            t[1].pattern_id,
        ),
    )
    selected = [p for _, p in ranked[:limit]]
    if (
        include_library_alternative
        and limit >= 2
        and selected
        and not any(p.builtin for p in selected)
    ):
        alternative = next((p for _, p in scored if p.builtin and need.visual in p.visuals()), None)
        if alternative is not None:
            # Keep the preferred template first, but let the model see another composition.
            selected = [*selected[: limit - 1], alternative]
    return selected


def fallback_visual(visual: str, patterns: list[PatternInfo], *, has_datasets: bool) -> str:
    """Если шаблон не умеет запрошенную подачу, подбирается ближайшая: таблица → диаграмма →
    показатели → список; схема/таймлайн → карточки → список; изображение → текст."""
    available: set[str] = set()
    for p in patterns:
        if (
            p.role in FIXED_ROLES
            or p.role in SKIPPED_ROLES
            or not p.fillable(has_datasets=has_datasets)
        ):
            continue
        available |= p.visuals()
    chain = {
        "table": ("table", "chart", "number", "bullets", "text"),
        "chart": ("chart", "table", "number", "bullets", "text"),
        "number": ("number", "cards", "bullets", "text"),
        "timeline": ("timeline", "diagram", "cards", "bullets", "text"),
        "diagram": ("diagram", "timeline", "cards", "bullets", "text"),
        "comparison": ("comparison", "cards", "bullets", "text"),
        "image": ("image", "text", "bullets"),
        "quote": ("quote", "text", "bullets"),
        "cards": ("cards", "bullets", "text"),
        "bullets": ("bullets", "cards", "text"),
        "text": ("text", "bullets", "cards"),
    }.get(visual, ("text", "bullets"))
    for v in chain:
        if v in available and not (v in ("chart", "table") and not has_datasets):
            return v
    return "text"


def sequence_ok(sequence: list[str], pattern: PatternInfo) -> bool:
    """Можно ли поставить паттерн следующим: не длиннее max_consecutive подряд."""
    run = 0
    for pid in reversed(sequence):
        if pid != pattern.pattern_id:
            break
        run += 1
    return run < max(1, pattern.max_consecutive)


__all__ = [
    "CONTENT_VISUALS",
    "FIXED_ROLES",
    "CardGroup",
    "Need",
    "PatternInfo",
    "SlotInfo",
    "candidates_for",
    "cover_like",
    "fallback_visual",
    "fixed_pattern",
    "fixed_pattern_pool",
    "pattern_info",
    "pick_style",
    "profile_patterns",
    "score_pattern",
    "sequence_ok",
    "siblings_of",
]
