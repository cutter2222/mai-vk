"""Пороги слоя `design`: сколько содержания считать достаточным.

Пороги свои у каждого варианта вёрстки (ТЗ п.2.5). Плотный вариант обязан быть
плотнее не потому, что мы добавили ему текста, а потому что под него выбран
паттерн с большей вместимостью — иначе «три варианта» отличались бы только
числом слайдов.

Значения живут в `config/app.yaml`, а не в коде: ТЗ п.4 требует запуска
конфиг-файлом, и подгонка порогов под известные шаблоны недопустима.
"""

from __future__ import annotations

from dataclasses import dataclass

# Виды слотов, которые несут содержание. Иконки и подложки к делу не относятся:
# их заполненность от плана не зависит.
TEXT_KINDS = frozenset({"title", "body", "label", "number", "caption", "quote"})

# Виды, которые нельзя добирать из истории: заголовок пишет планировщик, а
# число обязано прийти из фактов исходных материалов (Приложение 1, вопрос 4).
NOT_ENRICHABLE = frozenset({"title", "number"})


@dataclass(frozen=True)
class Thresholds:
    """Границы заполненности слайда для одного варианта вёрстки."""

    min_fill: float          # ниже — слайд полупустой, композицию надо менять
    max_fill: float          # выше — слайд перегружен (этим занят capacity.py)
    max_slots_ratio: float   # во сколько раз слотов может быть больше блоков

    def underfilled(self, fill: float) -> bool:
        return fill < self.min_fill


# Умолчания на случай, когда в конфиге секции нет. Числа из раздела
# «Плотность» Приложения 1: заполненность меньше четверти и больше трёх
# четвертей считается дефектом; вариантам даны свои рамки внутри этих границ.
DEFAULTS: dict[str, Thresholds] = {
    "compact": Thresholds(min_fill=0.25, max_fill=0.65, max_slots_ratio=1.6),
    "balanced": Thresholds(min_fill=0.35, max_fill=0.72, max_slots_ratio=1.5),
    "detailed": Thresholds(min_fill=0.45, max_fill=0.75, max_slots_ratio=1.4),
}


def thresholds_for(variant: str, config: dict | None = None) -> Thresholds:
    """Пороги варианта: из конфига, иначе умолчание.

    Неизвестный вариант получает рамки `balanced`: это не ошибка, а разумная
    середина, и ронять генерацию из-за незнакомого имени не за что.
    """
    section = (config or {}).get("design") or {}
    raw = (section.get("variants") or {}).get(variant)
    base = DEFAULTS.get(variant, DEFAULTS["balanced"])
    if not isinstance(raw, dict):
        return base
    return Thresholds(
        min_fill=float(raw.get("min_fill", base.min_fill)),
        max_fill=float(raw.get("max_fill", base.max_fill)),
        max_slots_ratio=float(raw.get("max_slots_ratio", base.max_slots_ratio)),
    )
