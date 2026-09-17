"""Семейства макетов: имя макета без числового префикса и вариантных суффиксов.

В шаблонах датасета макеты одного семейства различаются номером и фоном («1_Титульный»,
«2_Титульный», «Разделитель тёмный»), поэтому ключ стиля образца строится по семейству, а не по
точному имени. Функцию переиспользует этап 16 при поиске макетов-братьев с другим фоном.
"""

from __future__ import annotations

import re

from presentation_designer.parsing.template.geometry import normalize_text

_PREFIX = re.compile(r"^\s*\d+[\s_.\-–—]*")
_SUFFIX = re.compile(r"[\s_\-–—]*(\(\d+\)|\d+|копия|copy)\s*$", re.IGNORECASE)


def layout_family(name: str | None) -> str:
    """«2_Титульный слайд» → «титульный слайд»; пустое имя → «layout»."""
    text = _PREFIX.sub("", name or "")
    text = _SUFFIX.sub("", text)
    text = normalize_text(text)
    return text or "layout"


__all__ = ["layout_family"]
