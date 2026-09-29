"""Фоновый мотив шаблона на пустотах содержательных слайдов (29.09.2026).

Содержательный слайд на светлом макете выходил белым листом с заголовком и текстом, хотя у
шаблона есть узнаваемая графика — буквенный узор, объёмные фигуры обложки. Мотив — вырезанная
графика шаблона с тегом `decor`: кандидатов по устройству файла (позади текста, с прозрачным
фоном, крупнее значка) отбирает анализ, декор среди них выбирает VLM и заодно говорит, можно
ли резать мотив краем слайда (узор — да, цельная фигура — нет; `parsing/template/assets.py`).
Место мотиву подсказывает сам шаблон — край, к которому он прижат на обложке или финале. На
слайде мотив встаёт у того же края (занят — у другого), бледно и в размер свободного места:

* свободное место считается по тексту, а не по рамкам: рамка списка тянется до низа, а текст
  занимает её верх; плашки, таблицы, графики, картинки и объекты макета (логотип,
  колонтитул) занимают место целиком, с полем;
* мотив выдвигается из-за края ровно настолько, чтобы не задеть содержание: узор может
  уйти за край больше чем наполовину, цельная фигура — не больше чем на пятую часть, и на
  слайде должна остаться почти вся её плотная часть (без свечения вокруг);
* заметность, а не прозрачность: мотив берётся лучший по оценке из заметных на этом фоне
  (белый узор на белом не ставится), непрозрачность подбирается по разнице светлоты с фоном;
* обрезка и прозрачность запекаются в PNG: слайд одинаково выглядит в ONLYOFFICE,
  PowerPoint, PDF и HTML;
* слайд, чей макет уже несёт свой декор (полосы WorkSpace), и слайд с фото мотива не
  получают: там пустоты нет или якорь уже есть;
* панели, нарисованные прямо в картинке-фоне макета, видны только на самой картинке: мотив
  встаёт лишь туда, где у фона нет границ.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

from presentation_designer.layout.ooxml import NS_A, NS_P, NS_R
from presentation_designer.parsing.template.tone import relative_luminance

JsonDict = dict[str, Any]
Box = tuple[float, float, float, float]  # x, y, ширина, высота — доли слайда

NAME = "Декор шаблона"
DECOR_TAG = "decor"
CROP_TAG = "decor-crop"
# Заметность — разница светлоты мотива и фона, умноженная на итоговую непрозрачность.
# Насыщенный синий на белом получает ≈12 % непрозрачности, уже бледная версия мотива —
# больше; мотив, почти не отличимый от фона, не ставится.
VISIBILITY = 0.075
MIN_CONTRAST = 0.1
OPACITY = (0.08, 0.25)
# Видимая часть мотива — от 5 до 40 % слайда; меньше — клочок, больше — спорит с текстом.
# Цельная фигура — не меньше 10 % и только у бокового края: мелкий предмет посреди нижнего
# края выглядит вставленной картинкой, а не оформлением.
MIN_SHARE = {True: 0.05, False: 0.1}
MAX_SHARE = 0.4
# Поле между мотивом и содержанием.
MARGIN = 0.04
# Размер мотива: высота у бокового края в долях высоты слайда (у верхнего и нижнего края —
# 0,6 от неё).
SCALES = (1.2, 1.0, 0.85, 0.7, 0.55, 0.42)
# Сколько мотива может уйти за край (доля по оси края) и сколько его плотной части должно
# остаться на слайде: узор режется смело, цельная фигура — едва.
BLEED = {True: (0.15, 0.7), False: (0.0, 0.2)}
KEPT = {True: 0.25, False: 0.85}
STEP = 0.05
# Видимая часть должна быть графикой, а не прозрачной пустотой картинки.
MIN_INK = 0.12
# Объект макета без текста крупнее этого — у макета свой декор; картинка слайда крупнее
# этого — фото или иллюстрация, якорь у слайда уже есть.
OWN_DECOR = 0.06
PHOTO = 0.12
MAX_PX = 1400
EMU_PT = 12700
# Карта границ картинок-фонов: панели и карточки, нарисованные прямо в фоне макета (VK Tech),
# структура файла не видит — мотив не встаёт туда, где у фона есть границы.
EDGE_MAP = (192, 108)
EDGE_LEVEL = 4
EDGE_SHARE = 0.002


@dataclass
class Motif:
    """Мотив: картинка без прозрачных полей, край, у которого она стоит в шаблоне, оценка
    модели, можно ли её резать, светлота и плотность нарисованного."""

    image: Any  # PIL.Image в RGBA
    edge: str  # right | left | top | bottom
    alpha: Any = None  # уменьшенная маска непрозрачности
    solid: Any = None  # уменьшенная маска плотной части (без свечения и теней)
    score: int = 0
    croppable: bool = False
    lightness: float = 0.5
    density: float = 1.0

    @property
    def aspect(self) -> float:
        return float(self.image.width) / float(max(1, self.image.height))

    def contrast(self, backdrop: float) -> float:
        """Заметность мотива без ослабления на фоне с яркостью `backdrop` (0–1)."""
        return abs(self.lightness - _lightness(backdrop)) * self.density

    def opacity(self, backdrop: float) -> float:
        contrast = max(self.contrast(backdrop), 1e-6)
        return min(max(VISIBILITY / contrast, OPACITY[0]), OPACITY[1])


def _lightness(luminance: float) -> float:
    """Светлота на глаз: яркость в пространстве, близком к восприятию."""
    return float(max(luminance, 0.0) ** 0.5)


def motifs_for(profile: JsonDict, prs: Any) -> list[Motif]:
    """Мотивы шаблона — ресурсы с тегом `decor`, от лучшей оценки модели; пусто — декора нет."""
    from PIL import Image, ImageStat

    decor = [a for a in profile.get("assets") or [] if DECOR_TAG in (a.get("tags") or [])]
    decor.sort(key=lambda a: -_score(a))
    parts = {str(p.partname).lstrip("/"): p for p in prs.part.package.iter_parts()}
    out: list[Motif] = []
    for asset in decor:
        part = parts.get(str(asset.get("media_path") or "").lstrip("/"))
        if part is None:
            continue
        try:
            with Image.open(io.BytesIO(part.blob)) as raw:
                image = raw.convert("RGBA")
        except Exception:
            continue
        box = image.getchannel("A").getbbox()
        if not box:
            continue
        image = image.crop(box)
        image.thumbnail((MAX_PX * 2, MAX_PX * 2))
        small = image.copy()
        small.thumbnail((96, 96))
        alpha = small.getchannel("A")
        drawn = alpha.point(lambda a: 255 if a > 16 else 0)
        if not drawn.getbbox():
            continue
        r, g, b, a = ImageStat.Stat(small, mask=drawn).mean
        color = f"#{round(r):02X}{round(g):02X}{round(b):02X}"
        out.append(
            Motif(
                image,
                _edge(asset.get("bbox_on_source") or {}),
                alpha,
                alpha.point(lambda v: 255 if v > 128 else 0),
                score=_score(asset),
                croppable=CROP_TAG in (asset.get("tags") or []),
                lightness=_lightness(relative_luminance(color)),
                density=a / 255,
            )
        )
    return out


def _score(asset: JsonDict) -> int:
    for tag in asset.get("tags") or []:
        if tag.startswith(f"{DECOR_TAG}:"):
            try:
                return int(tag.split(":", 1)[1])
            except ValueError:
                return 0
    return 0


def _edge(bbox: JsonDict) -> str:
    """Край, к которому мотив прижат в шаблоне: боковые важнее (полоса во всю высоту касается
    и верха, и низа, но стоит у бока)."""
    x, y = float(bbox.get("x", 0)), float(bbox.get("y", 0))
    w, h = float(bbox.get("width", 0)), float(bbox.get("height", 0))
    if x + w >= 0.98:
        return "right"
    if x <= 0.02:
        return "left"
    if y + h >= 0.98:
        return "bottom"
    if y <= 0.02:
        return "top"
    return "right"


# ---------- занятое место ----------


def occupied(slide: Any, width: int, height: int) -> list[Box] | None:
    """Места содержания и объектов макета на слайде; None — мотив слайду не нужен."""
    layout = slide.slide_layout
    inherited = list(layout.shapes)
    if layout._element.get("showMasterSp") != "0":
        inherited += list(layout.slide_master.shapes)
    boxes: list[Box] = []
    for shape in inherited:
        if getattr(shape, "is_placeholder", False):
            continue
        box = _box(shape, width, height)
        if box is None or box[2] * box[3] >= 0.85:
            continue
        if box[2] * box[3] >= OWN_DECOR and not _text(shape):
            return None
        boxes.append(box)
    for shape in slide.shapes:
        box = _box(shape, width, height)
        if box is None:
            continue
        if getattr(shape, "shape_type", None) == 13 and box[2] * box[3] >= PHOTO:
            return None
        if getattr(shape, "has_text_frame", False) and not _filled(shape):
            if _text(shape):
                boxes.append(_ink(shape, box, height))
            continue
        boxes.append(box)
    return boxes


def _box(shape: Any, width: int, height: int) -> Box | None:
    """Место объекта на слайде (часть за краем отрезана); None — объект целиком за краем:
    служебные линии макета над верхом и под низом слайда не запирают края."""
    try:
        left, top = int(shape.left), int(shape.top)
        w, h = int(shape.width), int(shape.height)
    except (TypeError, ValueError):
        return None
    if w <= 0 and h <= 0:
        return None
    # Линия нулевой толщины — всё же преграда.
    box = (left / width, top / height, max(w / width, 0.002), max(h / height, 0.002))
    return _clip(box)


def _text(shape: Any) -> str:
    if not getattr(shape, "has_text_frame", False):
        return ""
    return str(shape.text_frame.text or "").strip()


def _filled(shape: Any) -> bool:
    """Плашка с заливкой или контуром занимает место целиком, даже с коротким текстом."""
    sppr = shape._element.find(f"{{{NS_P}}}spPr")
    if sppr is None:
        return False
    for tag in ("solidFill", "gradFill", "blipFill", "pattFill"):
        if sppr.find(f"{{{NS_A}}}{tag}") is not None:
            return True
    line = sppr.find(f"{{{NS_A}}}ln")
    return line is not None and line.find(f"{{{NS_A}}}noFill") is None and len(line) > 0


def _ink(shape: Any, box: Box, height: int) -> Box:
    """Часть рамки, которую занимает текст: высота по строкам, место — по привязке рамки."""
    from presentation_designer.generation.capacity import wrap_lines
    from presentation_designer.shared import text_metrics

    x, y, w, h = box
    width_pt = int(shape.width) / EMU_PT * 0.92
    total = 0.0
    for paragraph in shape.text_frame.paragraphs:
        runs = [r for r in paragraph.runs if r.text.strip()]
        size = next((r.font.size.pt for r in runs if r.font.size is not None), None)
        if size is None and paragraph.font.size is not None:
            size = paragraph.font.size.pt
        size = float(size or 18.0)
        family = next((r.font.name for r in runs if r.font.name), None)
        font = text_metrics.resolve_font(family)
        text = paragraph.text.replace("\v", "\n")
        lines = wrap_lines(text, width_pt, font, size) if text.strip() else 1
        total += lines * text_metrics.line_metrics(font, size).line_height_pt * 1.15
    # Поля рамки сверху и снизу (по умолчанию 0,05 дюйма).
    ink = min(h, (total + 7.2) * EMU_PT / height)
    body = shape.text_frame._txBody.find(f"{{{NS_A}}}bodyPr")
    anchor = body.get("anchor") if body is not None else None
    if anchor == "ctr":
        return x, y + (h - ink) / 2, w, ink
    if anchor == "b":
        return x, y + h - ink, w, ink
    return x, y, w, ink


def background_edges(slide: Any, width: int, height: int) -> Any | None:
    """Карта границ картинок-фонов слайда (фон-заливка картинкой и картинки во весь слайд в
    макете, мастере и на слайде): белые точки — края панелей и форм. None — фон без картинок."""
    from PIL import Image, ImageChops, ImageFilter

    layout = slide.slide_layout
    parts = [slide, layout]
    if layout._element.get("showMasterSp") != "0":
        parts.append(layout.slide_master)
    blobs: list[bytes] = []
    for part in (slide, layout, layout.slide_master):
        bg = part._element.find(f"{{{NS_P}}}cSld/{{{NS_P}}}bg")
        if bg is None:
            continue
        blob = _blip_blob(part, bg)
        if blob is not None:
            blobs.append(blob)
        break
    for part in parts:
        for shape in part.shapes:
            box = _box(shape, width, height)
            if getattr(shape, "shape_type", None) == 13 and box and box[2] * box[3] >= 0.85:
                try:
                    blobs.append(bytes(shape.image.blob))
                except Exception:
                    continue
    edges: Any = None
    for blob in blobs:
        try:
            with Image.open(io.BytesIO(blob)) as raw:
                image = raw.convert("RGBA")
        except Exception:
            continue
        flat = Image.new("RGBA", image.size, (255, 255, 255, 255))
        flat.alpha_composite(image)
        gray = flat.convert("L").resize(EDGE_MAP).filter(ImageFilter.GaussianBlur(1))
        found = gray.filter(ImageFilter.FIND_EDGES).point(lambda v: 255 if v > EDGE_LEVEL else 0)
        # Рамка карты — след фильтра на краю картинки, а не граница на фоне.
        inner = Image.new("L", EDGE_MAP, 0)
        inner.paste(found.crop((2, 2, EDGE_MAP[0] - 2, EDGE_MAP[1] - 2)), (2, 2))
        edges = inner if edges is None else ImageChops.lighter(edges, inner)
    return edges


def _blip_blob(part: Any, element: Any) -> bytes | None:
    blip = element.find(f".//{{{NS_A}}}blip")
    if blip is None:
        return None
    rid = blip.get(f"{{{NS_R}}}embed")
    try:
        return bytes(part.part.related_part(rid).blob) if rid else None
    except (KeyError, AttributeError):
        return None


def _calm(edges: Any | None, visible: Box) -> bool:
    """Фон под видимой частью мотива без границ (с полем)."""
    if edges is None:
        return True
    w, h = EDGE_MAP
    pad = MARGIN / 2
    box = (
        max(0, int((visible[0] - pad) * w)),
        max(0, int((visible[1] - pad) * h)),
        min(w, round((visible[0] + visible[2] + pad) * w)),
        min(h, round((visible[1] + visible[3] + pad) * h)),
    )
    part = edges.crop(box)
    hits = int(part.histogram()[255])
    limit = max(2.0, EDGE_SHARE * float(part.width) * float(part.height))
    return hits <= limit


# ---------- место мотива ----------


def _clip(rect: Box) -> Box | None:
    x0, y0 = max(rect[0], 0.0), max(rect[1], 0.0)
    x1, y1 = min(rect[0] + rect[2], 1.0), min(rect[1] + rect[3], 1.0)
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    return x0, y0, x1 - x0, y1 - y0


def _hits(a: Box, b: Box, pad: float) -> bool:
    return (
        a[0] < b[0] + b[2] + pad
        and b[0] < a[0] + a[2] + pad
        and a[1] < b[1] + b[3] + pad
        and b[1] < a[1] + a[3] + pad
    )


def _slides(motif: Motif, ratio: float) -> list[tuple[str, list[Box]]]:
    """Для каждого края, размера и места вдоль края — положения мотива от наименьшего выхода
    за край к наибольшему. `ratio` — высота слайда к ширине: доли по осям разные."""
    low, high = BLEED[motif.croppable]
    bleeds = [low + STEP * i for i in range(round((high - low) / STEP) + 1)]
    out: list[tuple[str, list[Box]]] = []
    for s in SCALES:
        mh, mw = s, s * motif.aspect * ratio
        if mw <= 0.8:
            for y in (1 - mh * 0.9, (1 - mh) / 2, -mh * 0.1):
                out.append(("right", [(1 - mw * (1 - b), y, mw, mh) for b in bleeds]))
                out.append(("left", [(-mw * b, y, mw, mh) for b in bleeds]))
        mh = s * 0.6
        mw = mh * motif.aspect * ratio
        if motif.croppable and mw <= 1.0:
            for x in (1 - mw * 0.9, (1 - mw) / 2, -mw * 0.1):
                out.append(("bottom", [(x, 1 - mh * (1 - b), mw, mh) for b in bleeds]))
                out.append(("top", [(x, -mh * b, mw, mh) for b in bleeds]))
    return out


def _masked(mask: Any, rect: Box, visible: Box) -> tuple[float, float]:
    """Плотность видимой части маски и доля всей маски, оставшаяся на слайде."""
    from PIL import ImageStat

    u0 = (visible[0] - rect[0]) / rect[2]
    v0 = (visible[1] - rect[1]) / rect[3]
    u1 = u0 + visible[2] / rect[2]
    v1 = v0 + visible[3] / rect[3]
    box = (
        int(u0 * mask.width),
        int(v0 * mask.height),
        max(int(u0 * mask.width) + 1, round(u1 * mask.width)),
        max(int(v0 * mask.height) + 1, round(v1 * mask.height)),
    )
    part = mask.crop(box)
    density = float(ImageStat.Stat(part).mean[0]) / 255
    total = float(ImageStat.Stat(mask).sum[0])
    return density, (float(ImageStat.Stat(part).sum[0]) / total if total else 0.0)


def best_place(
    motif: Motif, boxes: list[Box], ratio: float, edges: Any | None = None
) -> tuple[Box, Box] | None:
    """Положение мотива (целиком и видимая часть) с наибольшей видимой графикой, не задевающее
    содержание; край мотива в шаблоне в приоритете. None — места нет."""
    solid = motif.solid if motif.solid is not None and motif.solid.getbbox() else motif.alpha
    best: tuple[float, Box, Box] | None = None
    for edge, positions in _slides(motif, ratio):
        for rect in positions:
            visible = _clip(rect)
            if visible is None or any(_hits(visible, box, MARGIN) for box in boxes):
                continue
            if not _calm(edges, visible):
                continue
            # Наименьший выход за край, при котором содержание не задето, — дальше мотив
            # только меньше виден.
            share = visible[2] * visible[3]
            ink, _ = _masked(motif.alpha, rect, visible)
            _, kept = _masked(solid, rect, visible)
            if (
                MIN_SHARE[motif.croppable] <= share <= MAX_SHARE
                and ink >= MIN_INK
                and kept >= KEPT[motif.croppable]
            ):
                score = share * min(ink * 2, 1.0) * (1.3 if edge == motif.edge else 1.0)
                if best is None or score > best[0]:
                    best = (score, rect, visible)
            break
    return (best[1], best[2]) if best else None


def place_motif(
    slide: Any, motifs: list[Motif], *, width: int, height: int, backdrop: float
) -> bool:
    """Ставит мотив на пустоту слайда под всё содержание: лучший по оценке из заметных на фоне
    яркости `backdrop`, которому нашлось место. False — места нет или мотив не нужен."""
    boxes = occupied(slide, width, height)
    if boxes is None:
        return False
    # При равной оценке — самая заметная расцветка: цельный узор, а не версия, где часть форм
    # белая и на белом от неё остаются клочки.
    ranked = sorted(motifs, key=lambda m: (-m.score, -m.contrast(backdrop)))
    edges = background_edges(slide, width, height)
    for motif in ranked:
        if motif.contrast(backdrop) < MIN_CONTRAST:
            continue
        found = best_place(motif, boxes, height / width, edges)
        if found is not None:
            _draw(slide, motif, *found, width=width, height=height, opacity=motif.opacity(backdrop))
            return True
    return False


def _draw(
    slide: Any, motif: Motif, rect: Box, visible: Box, *, width: int, height: int, opacity: float
) -> None:
    image = motif.image
    crop = image.crop(
        (
            round((visible[0] - rect[0]) / rect[2] * image.width),
            round((visible[1] - rect[1]) / rect[3] * image.height),
            round((visible[0] + visible[2] - rect[0]) / rect[2] * image.width),
            round((visible[1] + visible[3] - rect[1]) / rect[3] * image.height),
        )
    )
    crop.thumbnail((MAX_PX, MAX_PX))
    crop.putalpha(crop.getchannel("A").point(lambda a: round(a * opacity)))
    buf = io.BytesIO()
    crop.save(buf, format="PNG", compress_level=3)
    buf.seek(0)
    picture = slide.shapes.add_picture(
        buf,
        round(visible[0] * width),
        round(visible[1] * height),
        round(visible[2] * width),
        round(visible[3] * height),
    )
    element = picture._element
    cnvpr = element.find(f".//{{{NS_P}}}cNvPr")
    if cnvpr is not None:
        cnvpr.set("name", NAME)
    # Под всё содержание: сразу за служебными nvGrpSpPr и grpSpPr дерева фигур.
    tree = slide.shapes._spTree
    tree.remove(element)
    tree.insert(2, element)


__all__ = [
    "NAME",
    "Motif",
    "background_edges",
    "best_place",
    "motifs_for",
    "occupied",
    "place_motif",
]
