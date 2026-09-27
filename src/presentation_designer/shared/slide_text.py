"""Small, deliberately bounded presentation markup: **strong**, not arbitrary Markdown.

Unmatched markers, formulas and unsupported syntax stay literal. The same spans are
used by measurement and OOXML rendering, so markup is never measured as visible stars.
"""

from __future__ import annotations

import re

_STRONG = re.compile(r"(?<!\*)\*\*([^*\n]+)\*\*(?!\*)")


def spans(text: str) -> list[tuple[str, bool]]:
    result: list[tuple[str, bool]] = []
    at = 0
    for match in _STRONG.finditer(text):
        if match.start() > at:
            result.append((text[at : match.start()], False))
        result.append((match.group(1), True))
        at = match.end()
    if at < len(text):
        result.append((text[at:], False))
    return result or [("", False)]


def plain(text: str) -> str:
    return "".join(value for value, _ in spans(text))
