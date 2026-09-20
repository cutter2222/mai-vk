"""Описание композиций библиотеки и их разворачивание в конкретные варианты.

Композиция описана семейством: базовая схема плюс параметры. `kpi_row` с параметром
`count: [2, 3, 4]` — это три композиции, а не три файла; так библиотека растёт числом
вариантов, а не объёмом ручной работы.

Геометрия задана кодом семейства в долях холста и пересчитывается под поля шаблона, поэтому
одна композиция работает и на 16:9, и на 4:3. Ни одно видимое свойство не записано в
описании значением: цвет, шрифт и кегль называются ролью и берутся из `DesignCode`.
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import yaml

JsonDict = dict[str, Any]

LIBRARY_DIR = pathlib.Path(__file__).resolve().parent / "compositions"


@dataclass
class CompositionSlot:
    """Слот композиции: место под содержание с ролью текста и вместимостью.

    `bbox` — доли холста. `text_role` выбирает кегль и шрифт из дизайн-кода, `on_card`
    говорит, что текст лежит на заливке и его цвет считается от неё.
    """

    slot_id: str
    kind: str
    bbox: tuple[float, float, float, float]
    text_role: str = "body"
    align: str = "left"
    valign: str = "top"
    bold: bool = False
    required: bool = False
    repeat_group: str = ""
    color_role: str = "text"
    on_card: bool = False
    card_index: int = 0

    def as_profile_slot(self) -> JsonDict:
        x, y, w, h = self.bbox
        out: JsonDict = {
            "slot_id": self.slot_id,
            "kind": self.kind,
            "bbox": {
                "x": round(x, 4),
                "y": round(y, 4),
                "width": round(w, 4),
                "height": round(h, 4),
            },
            "z_order": 2 if self.on_card else 1,
            "align": self.align,
            "valign": self.valign,
        }
        if self.required:
            out["required"] = True
        if self.repeat_group:
            out["repeat_group"] = self.repeat_group
        return out


@dataclass
class CardSpec:
    """Плашка под группой слотов: рисуется заливкой акцента или обводкой по пластике шаблона."""

    bbox: tuple[float, float, float, float]
    index: int
    fill: str = "accent"  # accent | surface | none
    accent_index: int = 0


@dataclass
class Composition:
    """Развёрнутая композиция: готовый набор слотов и плашек под конкретные параметры."""

    composition_id: str
    family: str
    role: str
    name: str
    slots: list[CompositionSlot]
    cards: list[CardSpec] = field(default_factory=list)
    supports: tuple[str, ...] = ()
    min_items: int = 1
    max_items: int = 1
    tags: tuple[str, ...] = ()
    notes: str = ""


# Построитель семейства: параметры → композиция.
Builder = Callable[["Family", dict[str, Any]], Composition]


@dataclass
class Family:
    """Семейство композиций: роль, множества параметров и построитель геометрии."""

    name: str
    role: str
    title: str
    params: dict[str, Sequence[Any]]
    builder: Builder
    supports: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    enabled: bool = True

    def combinations(self) -> Iterator[dict[str, Any]]:
        keys = sorted(self.params)
        if not keys:
            yield {}
            return
        stack: list[dict[str, Any]] = [{}]
        for key in keys:
            stack = [dict(item, **{key: value}) for item in stack for value in self.params[key]]
        yield from stack

    def expand(self) -> list[Composition]:
        return [self.builder(self, params) for params in self.combinations()]


def composition_id(family: str, params: dict[str, Any]) -> str:
    if not params:
        return family
    tail = ",".join(f"{k}={params[k]}" for k in sorted(params))
    return f"{family}@{tail}"


def load_families(path: pathlib.Path | None = None) -> list[Family]:
    """Семейства из встроенного реестра, отфильтрованные настройками `compositions/*.yaml`.

    Геометрию строит код (`geometry.py`), а YAML включает и выключает семейства и задаёт
    наборы параметров — так набор композиций правится без правки кода, а сама раскладка
    остаётся проверяемой тестами.
    """
    from presentation_designer.library.geometry import REGISTRY

    settings = _load_settings(path or LIBRARY_DIR)
    families: list[Family] = []
    for family in REGISTRY:
        override = settings.get(family.name) or {}
        if override.get("enabled") is False:
            continue
        params = dict(family.params)
        for key, values in (override.get("params") or {}).items():
            if isinstance(values, list) and values:
                params[key] = values
        families.append(
            Family(
                name=family.name,
                role=str(override.get("role") or family.role),
                title=family.title,
                params=params,
                builder=family.builder,
                supports=family.supports,
                tags=family.tags,
            )
        )
    return families


def find_composition(composition_id: str) -> Composition | None:
    """Композиция по идентификатору из профиля; None, если семейство выключили."""
    family_name = composition_id.split("@", 1)[0]
    for family in load_families():
        if family.name != family_name:
            continue
        for composition in family.expand():
            if composition.composition_id == composition_id:
                return composition
    return None


def _load_settings(directory: pathlib.Path) -> dict[str, JsonDict]:
    out: dict[str, JsonDict] = {}
    if not directory.is_dir():
        return out
    for file in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        for name, value in (data.get("families") or {}).items():
            if isinstance(value, dict):
                out[str(name)] = value
    return out
