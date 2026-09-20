"""Диаграмма, нарисованная руками: столбики из картинок и фигур вместо нативного графика.

Зачем. В шаблонах датасета нативных диаграмм нет ни одной: и у VK Tech, и у VK WorkSpace, и у
VK Education «график» — это ряд одинаковых прямоугольников или картинок разной высоты с
подписями значений поверх. Заполнять такой образец как набор чисел нельзя: высоты столбиков
нарисованы заранее и с новыми числами не сойдутся — на слайде окажется «91 %» над столбиком в
треть высоты соседа. Поэтому ряд распознаётся целиком, паттерн получает роль `chart` и один
слот `chart` на всю область ряда, а вёрстка строит на этом месте нативную диаграмму и убирает
нарисованные части (`chart_parts` паттерна).

Как отличить ряд столбиков от ряда карточек. Карточки в образце одинаковой высоты, столбики —
разной: у диаграммы высоты (у линейчатой — длины) обязаны различаться, иначе показывать нечего.
Плюс общая база: у столбцов совпадает нижний край, у полос — левый. Требуется не меньше четырёх
частей — три равных прямоугольника чаще оказываются карточками, а не графиком.

Мягкий режим (`relaxed`) включается только после того, как зрение модели уже сказало, что на
образце график: тогда пороги ослабляются, потому что вопрос «график ли это» решён, а код
отвечает лишь на «из каких объектов он состоит и где его область». Сам по себе мягкий режим
не применяется: на глаз и ряд карточек сойдёт за диаграмму.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from presentation_designer.parsing.template.geometry import ShapeInfo

# Столбик: не крошечный декор и не половина слайда.
MIN_PART_AREA = 0.0015
MAX_PART_AREA = 0.2
MIN_PARTS = 4
# Общая база: нижние (или левые) края совпадают с этой точностью в долях слайда.
BASE_TOL = 0.02
# Ширины столбиков одного ряда близки, высоты — обязаны различаться.
WIDTH_TOL = 0.45
MIN_SPAN_RATIO = 1.3
# Ряд занимает заметную часть слайда, иначе это иконки или маркеры.
MIN_CLUSTER_SPAN = 0.25
# Самый длинный столбик заметно длиннее иконки и вытянут вдоль своей оси: ряд одинаковых
# квадратиков с подписями — это карточки или иконки, а не график.
MIN_LONGEST = 0.15
MIN_ELONGATION = 1.2
# Подписи под рядом (категории) и над ним (легенда) входят в область диаграммы.
LABEL_GAP = 0.09
# Мелкий декор внутри области: кружки легенды (в образцах Google Slides это пустые надписи),
# засечки осей. У нативной диаграммы своя легенда, поэтому образцовая уходит.
MAX_DECOR_AREA = 0.01


@dataclass(frozen=True)
class Thresholds:
    """Пороги поиска ряда: строгие по умолчанию, мягкие — по подсказке зрения."""

    parts: int = MIN_PARTS
    span_ratio: float = MIN_SPAN_RATIO
    longest: float = MIN_LONGEST
    cluster_span: float = MIN_CLUSTER_SPAN
    elongation: float = MIN_ELONGATION


STRICT = Thresholds()
RELAXED = Thresholds(parts=3, span_ratio=1.12, longest=0.1, cluster_span=0.18, elongation=1.0)


@dataclass
class DrawnChart:
    """Найденный ряд: область целиком, части ряда и подписи внутри неё."""

    kind: str  # column | bar
    bbox: dict[str, float]
    parts: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    decor: list[str] = field(default_factory=list)

    @property
    def element_ids(self) -> set[str]:
        return {*self.parts, *self.labels, *self.decor}


def _is_part(shape: ShapeInfo) -> bool:
    """Часть ряда: видимый прямоугольник без своего текста."""
    if shape.kind not in ("picture", "shape"):
        return False
    if shape.text.strip():
        return False
    if shape.is_hidden or shape.group_path:
        return False
    if not (MIN_PART_AREA <= shape.area <= MAX_PART_AREA):
        return False
    if shape.kind == "shape" and shape.fill_kind in (None, "none"):
        return False
    return True


def _cluster(parts: list[ShapeInfo], *, kind: str, th: Thresholds) -> list[ShapeInfo] | None:
    """Части с общей базой: у столбцов — нижний край, у полос — левый."""
    if len(parts) < th.parts:
        return None
    base = statistics.median([(p.y + p.height) if kind == "column" else p.x for p in parts])
    row = [
        p for p in parts if abs(((p.y + p.height) if kind == "column" else p.x) - base) <= BASE_TOL
    ]
    if len(row) < th.parts:
        return None
    # Толщина столбиков одного ряда близка: median ± WIDTH_TOL.
    thickness = [p.width if kind == "column" else p.height for p in row]
    typical = statistics.median(thickness)
    if typical <= 0:
        return None
    row = [
        p for p, t in zip(row, thickness, strict=True) if abs(t - typical) / typical <= WIDTH_TOL
    ]
    if len(row) < th.parts:
        return None
    # Длины различаются: одинаковые прямоугольники — это карточки, а не график.
    lengths = [p.height if kind == "column" else p.width for p in row]
    if min(lengths) <= 0 or max(lengths) / min(lengths) < th.span_ratio:
        return None
    if max(lengths) < th.longest:
        return None
    longest = max(row, key=lambda p: p.height if kind == "column" else p.width)
    thin = longest.width if kind == "column" else longest.height
    if thin <= 0 or (max(lengths) / thin) < th.elongation:
        return None
    span = (
        max(p.x + p.width for p in row) - min(p.x for p in row)
        if kind == "column"
        else max(p.y + p.height for p in row) - min(p.y for p in row)
    )
    if span < th.cluster_span:
        return None
    return row


def find_drawn_chart(shapes: list[ShapeInfo], *, relaxed: bool = False) -> DrawnChart | None:
    """Ряд столбиков на слайде, если он есть: сначала столбцы, потом полосы.

    `relaxed` — только после подсказки зрения модели, что на образце график.
    """
    th = RELAXED if relaxed else STRICT
    parts = [s for s in shapes if _is_part(s)]
    for kind in ("column", "bar"):
        row = _cluster(parts, kind=kind, th=th)
        if row is None:
            continue
        x0 = min(p.x for p in row)
        y0 = min(p.y for p in row)
        x1 = max(p.x + p.width for p in row)
        y1 = max(p.y + p.height for p in row)
        ids = {p.element_id for p in row}
        # Подписи значений и категорий: текст внутри области или вплотную под ней.
        labels = [
            s
            for s in shapes
            if s.element_id not in ids
            and s.text.strip()
            and s.kind in ("text", "shape")
            and not s.group_path
            and x0 - 0.02 <= s.x + s.width / 2 <= x1 + 0.02
            and y0 - 0.02 <= s.y + s.height / 2 <= y1 + LABEL_GAP
        ]
        low = max((s.y + s.height for s in labels), default=y1)
        bottom = max(y1, low)
        taken = ids | {s.element_id for s in labels}
        # Кружки легенды и засечки внутри области: у нативной диаграммы своя легенда.
        decor = [
            s
            for s in shapes
            if s.element_id not in taken
            and not s.text.strip()
            and s.kind in ("picture", "shape", "connector", "text")
            and not s.group_path
            and s.area <= MAX_DECOR_AREA
            and s.x >= x0 - 0.02
            and s.y >= y0 - 0.02
            and s.x + s.width <= x1 + 0.02
            and s.y + s.height <= bottom + 0.02
        ]
        return DrawnChart(
            kind=kind,
            bbox={
                "x": round(x0, 4),
                "y": round(y0, 4),
                "width": round(x1 - x0, 4),
                "height": round(bottom - y0, 4),
            },
            parts=[p.element_id for p in row],
            labels=[s.element_id for s in labels],
            decor=[s.element_id for s in decor],
        )
    return None


__all__ = ["DrawnChart", "find_drawn_chart"]
