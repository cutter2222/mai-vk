"""Иконка набора → векторная фигура DrawingML (`a:custGeom`).

Иконки Lucide — контуры на сетке 24 × 24 с обводкой 2: пути SVG, окружности, эллипсы,
прямоугольники со скруглением, линии и ломаные. Всё сводится к трём командам DrawingML —
`moveTo`, `lnTo`, `cubicBezTo` — и `close`: дуги SVG раскладываются на кубические кривые
по частям не больше четверти окружности, квадратичные кривые повышаются до кубических.

Иконка ставится одной фигурой: её перекрашивают и двигают в редакторе как обычный объект,
а толщина обводки задаётся от размера фигуры, как в исходной сетке.
"""

from __future__ import annotations

import math
import re
from typing import Any

GRID = 24.0
# Координаты путей DrawingML — целые: сетка 24 растягивается до 24000 без потери точности.
SCALE = 1000
KAPPA = 0.5522847498

Op = tuple[Any, ...]  # ("M", x, y) | ("L", x, y) | ("C", x1, y1, x2, y2, x, y) | ("Z",)

_NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


class _Reader:
    """Посимвольное чтение `d`: флаги дуги пишутся слитно («a1 1 0 01-1 1»)."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def _skip(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in " ,\t\r\n":
            self.pos += 1

    def command(self) -> str | None:
        self._skip()
        if self.pos < len(self.text) and self.text[self.pos].isalpha():
            self.pos += 1
            return self.text[self.pos - 1]
        return None

    def more(self) -> bool:
        self._skip()
        return self.pos < len(self.text) and not self.text[self.pos].isalpha()

    def number(self) -> float:
        self._skip()
        match = _NUMBER.match(self.text, self.pos)
        if match is None:
            raise ValueError(f"число ожидалось в позиции {self.pos}: {self.text!r}")
        self.pos = match.end()
        return float(match.group())

    def flag(self) -> bool:
        self._skip()
        char = self.text[self.pos]
        if char not in "01":
            raise ValueError(f"флаг дуги ожидался в позиции {self.pos}: {self.text!r}")
        self.pos += 1
        return char == "1"


def parse_path(d: str) -> list[Op]:
    """Путь SVG → абсолютные M/L/C/Z."""
    reader = _Reader(d)
    ops: list[Op] = []
    x = y = 0.0
    start = (0.0, 0.0)
    last_ctrl: tuple[float, float] | None = None
    last_quad: tuple[float, float] | None = None
    cmd: str | None = None
    while True:
        new = reader.command()
        if new is not None:
            cmd = new
        elif not reader.more():
            break
        if cmd is None:
            raise ValueError(f"путь без команды: {d!r}")
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if c == "Z":
            ops.append(("Z",))
            x, y = start
            last_ctrl = last_quad = None
            if not reader.more():
                continue
            cmd = "l" if rel else "L"
            continue
        if c == "M":
            x, y = ox + reader.number(), oy + reader.number()
            start = (x, y)
            ops.append(("M", x, y))
            cmd = "l" if rel else "L"  # следующие пары после M — отрезки
            last_ctrl = last_quad = None
            continue
        if c == "L":
            x, y = ox + reader.number(), oy + reader.number()
            ops.append(("L", x, y))
            last_ctrl = last_quad = None
        elif c == "H":
            x = ox + reader.number()
            ops.append(("L", x, y))
            last_ctrl = last_quad = None
        elif c == "V":
            y = oy + reader.number()
            ops.append(("L", x, y))
            last_ctrl = last_quad = None
        elif c == "C":
            x1, y1 = ox + reader.number(), oy + reader.number()
            x2, y2 = ox + reader.number(), oy + reader.number()
            x, y = ox + reader.number(), oy + reader.number()
            ops.append(("C", x1, y1, x2, y2, x, y))
            last_ctrl, last_quad = (x2, y2), None
        elif c == "S":
            x1, y1 = (2 * x - last_ctrl[0], 2 * y - last_ctrl[1]) if last_ctrl else (x, y)
            x2, y2 = ox + reader.number(), oy + reader.number()
            x, y = ox + reader.number(), oy + reader.number()
            ops.append(("C", x1, y1, x2, y2, x, y))
            last_ctrl, last_quad = (x2, y2), None
        elif c in ("Q", "T"):
            if c == "Q":
                qx, qy = ox + reader.number(), oy + reader.number()
            else:
                qx, qy = (2 * x - last_quad[0], 2 * y - last_quad[1]) if last_quad else (x, y)
            nx, ny = ox + reader.number(), oy + reader.number()
            ops.append(_quad(x, y, qx, qy, nx, ny))
            x, y = nx, ny
            last_quad, last_ctrl = (qx, qy), None
        elif c == "A":
            rx, ry, rot = reader.number(), reader.number(), reader.number()
            large, sweep = reader.flag(), reader.flag()
            nx, ny = ox + reader.number(), oy + reader.number()
            ops.extend(arc_to_cubic(x, y, rx, ry, rot, large, sweep, nx, ny))
            x, y = nx, ny
            last_ctrl = last_quad = None
        else:
            raise ValueError(f"команда {cmd} не поддерживается: {d!r}")
    return ops


def _quad(x0: float, y0: float, qx: float, qy: float, x: float, y: float) -> Op:
    return (
        "C",
        x0 + 2 / 3 * (qx - x0),
        y0 + 2 / 3 * (qy - y0),
        x + 2 / 3 * (qx - x),
        y + 2 / 3 * (qy - y),
        x,
        y,
    )


def arc_to_cubic(
    x1: float,
    y1: float,
    rx: float,
    ry: float,
    rotation: float,
    large: bool,
    sweep: bool,
    x2: float,
    y2: float,
) -> list[Op]:
    """Дуга SVG (конечные точки) → кубические кривые (SVG 1.1, приложение F.6.5)."""
    if (x1, y1) == (x2, y2):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [("L", x2, y2)]
    phi = math.radians(rotation % 360)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = cos_p * dx + sin_p * dy
    y1p = -sin_p * dx + cos_p * dy
    lam = (x1p / rx) ** 2 + (y1p / ry) ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        coef = -coef
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2

    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    theta = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    delta = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi
    parts = max(1, math.ceil(abs(delta) / (math.pi / 2) - 1e-9))
    step = delta / parts
    alpha = 4 / 3 * math.tan(step / 4)
    ops: list[Op] = []

    def point(t: float) -> tuple[float, float]:
        ex, ey = rx * math.cos(t), ry * math.sin(t)
        return cos_p * ex - sin_p * ey + cx, sin_p * ex + cos_p * ey + cy

    def tangent(t: float) -> tuple[float, float]:
        ex, ey = -rx * math.sin(t), ry * math.cos(t)
        return cos_p * ex - sin_p * ey, sin_p * ex + cos_p * ey

    t = theta
    px, py = x1, y1
    for index in range(parts):
        t2 = t + step
        qx, qy = point(t2) if index < parts - 1 else (x2, y2)
        d1, d2 = tangent(t), tangent(t2)
        ops.append(
            (
                "C",
                px + alpha * d1[0],
                py + alpha * d1[1],
                qx - alpha * d2[0],
                qy - alpha * d2[1],
                qx,
                qy,
            )
        )
        px, py, t = qx, qy, t2
    return ops


def _ellipse(cx: float, cy: float, rx: float, ry: float) -> list[Op]:
    kx, ky = rx * KAPPA, ry * KAPPA
    return [
        ("M", cx + rx, cy),
        ("C", cx + rx, cy + ky, cx + kx, cy + ry, cx, cy + ry),
        ("C", cx - kx, cy + ry, cx - rx, cy + ky, cx - rx, cy),
        ("C", cx - rx, cy - ky, cx - kx, cy - ry, cx, cy - ry),
        ("C", cx + kx, cy - ry, cx + rx, cy - ky, cx + rx, cy),
        ("Z",),
    ]


def _rect(x: float, y: float, w: float, h: float, rx: float, ry: float) -> list[Op]:
    rx, ry = min(rx, w / 2), min(ry, h / 2)
    if rx <= 0 or ry <= 0:
        return [("M", x, y), ("L", x + w, y), ("L", x + w, y + h), ("L", x, y + h), ("Z",)]
    kx, ky = rx * KAPPA, ry * KAPPA
    return [
        ("M", x + rx, y),
        ("L", x + w - rx, y),
        ("C", x + w - rx + kx, y, x + w, y + ry - ky, x + w, y + ry),
        ("L", x + w, y + h - ry),
        ("C", x + w, y + h - ry + ky, x + w - rx + kx, y + h, x + w - rx, y + h),
        ("L", x + rx, y + h),
        ("C", x + rx - kx, y + h, x, y + h - ry + ky, x, y + h - ry),
        ("L", x, y + ry),
        ("C", x, y + ry - ky, x + rx - kx, y, x + rx, y),
        ("Z",),
    ]


def _points(text: str) -> list[tuple[float, float]]:
    values = [float(v) for v in _NUMBER.findall(text)]
    return list(zip(values[::2], values[1::2], strict=False))


def element_ops(tag: str, attrs: dict[str, str]) -> list[Op]:
    """Элемент SVG иконки → команды пути."""

    def num(key: str, default: float = 0.0) -> float:
        value = attrs.get(key)
        return float(value) if value not in (None, "") else default

    if tag == "path":
        return parse_path(attrs.get("d") or "")
    if tag == "circle":
        return _ellipse(num("cx"), num("cy"), num("r"), num("r"))
    if tag == "ellipse":
        return _ellipse(num("cx"), num("cy"), num("rx"), num("ry"))
    if tag == "rect":
        rx = num("rx", num("ry"))
        ry = num("ry", rx)
        return _rect(num("x"), num("y"), num("width"), num("height"), rx, ry)
    if tag == "line":
        return [("M", num("x1"), num("y1")), ("L", num("x2"), num("y2"))]
    if tag in ("polyline", "polygon"):
        pts = _points(attrs.get("points") or "")
        if not pts:
            return []
        ops: list[Op] = [("M", *pts[0]), *(("L", *p) for p in pts[1:])]
        if tag == "polygon":
            ops.append(("Z",))
        return ops
    return []


def custgeom_xml(nodes: list[list[Any]]) -> str:
    """XML `a:custGeom` иконки: по пути на элемент; залитые элементы (`fill`) — со своей
    заливкой, остальные — только обводкой."""
    paths: list[str] = []
    for tag, attrs in nodes:
        ops = element_ops(str(tag), dict(attrs))
        if not ops:
            continue
        filled = str(attrs.get("fill") or "none") not in ("none", "")
        body: list[str] = []
        for op in ops:
            if op[0] == "M":
                body.append(f'<a:moveTo><a:pt x="{_c(op[1])}" y="{_c(op[2])}"/></a:moveTo>')
            elif op[0] == "L":
                body.append(f'<a:lnTo><a:pt x="{_c(op[1])}" y="{_c(op[2])}"/></a:lnTo>')
            elif op[0] == "C":
                pts = "".join(f'<a:pt x="{_c(op[i])}" y="{_c(op[i + 1])}"/>' for i in (1, 3, 5))
                body.append(f"<a:cubicBezTo>{pts}</a:cubicBezTo>")
            else:
                body.append("<a:close/>")
        size = int(GRID * SCALE)
        fill = "norm" if filled else "none"
        paths.append(f'<a:path w="{size}" h="{size}" fill="{fill}">{"".join(body)}</a:path>')
    return (
        '<a:custGeom xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>"
        '<a:rect l="0" t="0" r="r" b="b"/>'
        f"<a:pathLst>{''.join(paths)}</a:pathLst></a:custGeom>"
    )


def _c(value: float) -> int:
    return round(value * SCALE)
