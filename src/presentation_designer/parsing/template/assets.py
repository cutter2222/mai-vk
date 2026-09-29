"""Индекс ресурсов шаблона: иконки, логотипы, фото, скриншоты, фон, QR.

Каждая картинка мастера, макета или слайда попадает в индекс один раз (по sha256 байтов);
признаки считаются по размеру на слайде, пропорциям, числу цветов и прозрачности через Pillow:
маленькая почти квадратная картинка с одним-двумя цветами и прозрачностью — иконка; большая
многоцветная — фото; во всю площадь — фон; на мастере/макете или повторяющаяся малая — логотип.
Изображения из каталогов ресурсов сохраняются как переиспользуемые, но не становятся паттернами.
Теги по назначению добираются пакетами через VLM: несколько иконок на одном листе с номерами.

Фоновый декор (29.09.2026): вырезанная графика позади текста — в макете, мастере или под
текстом слайда, с прозрачностью, крупнее значка, — кандидат в мотив шаблона. Что из
кандидатов фирменный декор, а что иллюстрация со смыслом (фото, скриншот, схема), решает VLM
по листу с номерами; выбранное помечается тегом `decor` и оценкой `decor:<0–10>` (узор, который
можно резать краем слайда, — ещё `decor-crop`), и вёрстка
ставит этот мотив бледно на пустоты своих слайдов (`library/motif.py`).
"""

from __future__ import annotations

import collections
import io
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from presentation_designer.parsing.template.classify import Classification
from presentation_designer.parsing.template.geometry import ShapeInfo, normalize_text
from presentation_designer.parsing.template.package import TemplatePackage
from presentation_designer.parsing.template.tone import relative_luminance

log = logging.getLogger(__name__)

ICON_MAX_AREA = 0.012
LOGO_MAX_AREA = 0.06
# Кандидат в фоновый декор: не меньше такой доли слайда и с такой долей прозрачных пикселей
# (вырезанная графика, а не прямоугольное фото и не почти пустой слой).
DECOR_MIN_AREA = 0.03
DECOR_ALPHA = (0.15, 0.97)
DECOR_TAG = "decor"


@dataclass
class Asset:
    asset_id: str
    kind: str
    media_path: str
    sha256: str
    width_px: int | None = None
    height_px: int | None = None
    source_slide_index: int | None = None
    bbox_on_source: dict[str, float] | None = None
    tags: list[str] = field(default_factory=list)
    reusable: bool = True
    monochrome: bool | None = None
    transparent: bool | None = None
    mean_luminance: float | None = None
    color_bins: int | None = None
    occurrences: int = 1
    # Лежит позади текста: в мастере или макете либо на слайде ниже всех текстовых объектов.
    behind: bool = False
    blob: bytes | None = field(default=None, repr=False)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "asset_id": self.asset_id,
            "kind": self.kind,
            "media_path": self.media_path,
            "sha256": self.sha256,
            "reusable": self.reusable,
            "tags": self.tags,
        }
        if self.width_px:
            out["width_px"] = self.width_px
        if self.height_px:
            out["height_px"] = self.height_px
        if self.source_slide_index is not None:
            out["source_slide_index"] = self.source_slide_index
        if self.bbox_on_source:
            out["bbox_on_source"] = self.bbox_on_source
        return out


@dataclass
class ImageProps:
    """Признаки картинки по уменьшенной копии: размер, монохромность (≤ 2 значимых цвета),
    прозрачность, средняя относительная яркость непрозрачных пикселей и число цветовых корзин
    (5 бит на канал) — по ним анализатор отличает пиктограмму от фото и оценивает тон фона."""

    width: int | None = None
    height: int | None = None
    monochrome: bool | None = None
    transparent: bool | None = None
    mean_luminance: float | None = None
    color_bins: int | None = None


def _image_props(blob: bytes) -> ImageProps:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(blob)) as img:
            width, height = img.size
            small = img.convert("RGBA")
            small.thumbnail((48, 48))
            raw: Any = getattr(small, "get_flattened_data", small.getdata)()
            pixels = list(raw)
    except Exception:
        return ImageProps()
    opaque = [(r // 32, g // 32, b // 32) for r, g, b, a in pixels if a > 40]
    transparent = any(a <= 40 for _, _, _, a in pixels)
    if not opaque:
        return ImageProps(width, height, None, transparent, None, 0)
    counter = collections.Counter(opaque)
    top = counter.most_common(3)
    share = sum(n for _, n in top[:2]) / len(opaque)
    bins = len({(r // 8, g // 8, b // 8) for r, g, b, a in pixels if a > 40})
    luminance = sum(
        relative_luminance(f"#{r:02X}{g:02X}{b:02X}") for r, g, b, a in pixels if a > 40
    ) / len(opaque)
    return ImageProps(width, height, share >= 0.9, transparent, round(luminance, 3), bins)


def _kind_for(
    shape: ShapeInfo,
    props: tuple[bool | None, bool | None],
    *,
    on_master: bool,
    catalog: bool,
    ext: str | None,
    nearby_text: str,
    pixel_size: tuple[int | None, int | None] = (None, None),
) -> str:
    monochrome, transparent = props
    if shape.area >= 0.85:
        return "background"
    if "qr" in nearby_text:
        return "qr"
    if ext in ("svg", "emf", "wmf") and shape.area <= LOGO_MAX_AREA:
        return "icon" if catalog or monochrome else "logo"
    if shape.area <= ICON_MAX_AREA:
        if on_master:
            return "logo"
        if catalog and "логотип" in nearby_text:
            return "logo"
        if monochrome or transparent or catalog:
            return "icon"
        return "logo"
    if shape.area <= LOGO_MAX_AREA and (on_master or monochrome):
        return "logo"
    if "скриншот" in nearby_text or "мокап" in nearby_text or "экран" in nearby_text:
        return "screenshot"
    # Мокап телефона — вертикальная картинка сама по себе; ландшафтное фото, обрезанное
    # рамкой в полслайда (x 0.5, высота 1.0), мокапом не считается.
    px_w, px_h = pixel_size
    ratio = (
        (px_w / px_h) if px_w and px_h else (shape.width / shape.height if shape.height else 1.0)
    )
    if 0.4 <= ratio <= 0.6 and shape.area >= 0.08:
        return "mockup"
    if "график" in nearby_text or "диаграмм" in nearby_text:
        return "chart_image"
    return "photo" if not monochrome else "image"


def index_assets(
    pkg: TemplatePackage, classes: list[Classification]
) -> tuple[list[Asset], dict[str, str]]:
    by_kind = {c.slide_index: c.kind for c in classes}
    assets: dict[str, Asset] = {}
    order: list[str] = []

    def register(
        shape: ShapeInfo,
        *,
        slide_index: int | None,
        on_master: bool,
        catalog: bool,
        nearby_text: str,
        behind: bool = False,
    ) -> None:
        if shape.kind != "picture" or not shape.media_sha256 or not shape.media_part:
            return
        sha = shape.media_sha256
        if sha in assets:
            assets[sha].occurrences += 1
            existing = assets[sha]
            existing.behind = existing.behind or behind
            if on_master and existing.kind == "icon":
                existing.kind = "logo"
            return
        blob = shape.media_blob
        props = _image_props(blob) if blob else ImageProps()
        kind = _kind_for(
            shape,
            (props.monochrome, props.transparent),
            on_master=on_master,
            catalog=catalog,
            ext=shape.media_ext,
            nearby_text=nearby_text,
            pixel_size=(props.width, props.height),
        )
        asset = Asset(
            asset_id=f"asset_{len(order) + 1}",
            kind=kind,
            media_path=shape.media_part.lstrip("/"),
            sha256=sha,
            width_px=props.width,
            height_px=props.height,
            source_slide_index=slide_index,
            bbox_on_source=shape.bbox,
            reusable=kind
            in ("icon", "logo", "photo", "image", "mockup", "background", "screenshot"),
            monochrome=props.monochrome,
            transparent=props.transparent,
            mean_luminance=props.mean_luminance,
            color_bins=props.color_bins,
            behind=behind,
            blob=blob,
        )
        if catalog:
            asset.tags.append("catalog")
        assets[sha] = asset
        order.append(sha)

    for master in pkg.masters:
        for shape in master.shapes:
            register(
                shape, slide_index=None, on_master=True, catalog=False, nearby_text="", behind=True
            )
    for layout in pkg.layouts:
        for shape in layout.shapes:
            register(
                shape, slide_index=None, on_master=True, catalog=False, nearby_text="", behind=True
            )
    for slide in pkg.slides:
        kind = by_kind.get(slide.index, "content_sample")
        if kind == "hidden":
            continue
        catalog = kind == "asset_catalog"
        text = normalize_text(slide.all_text)
        # Слой: картинка ниже всех текстовых объектов слайда лежит под текстом, как фон.
        lowest_text = min(
            (s.z_order for s in slide.shapes if s.text.strip()), default=len(slide.shapes) + 1
        )
        for shape in slide.shapes:
            register(
                shape,
                slide_index=slide.index,
                on_master=False,
                catalog=catalog,
                nearby_text=text if catalog else _nearby(slide.shapes, shape),
                behind=shape.z_order < lowest_text and not shape.group_path,
            )
    ordered = [assets[s] for s in order]
    return ordered, {a.sha256: a.asset_id for a in ordered}


def _nearby(shapes: list[ShapeInfo], target: ShapeInfo) -> str:
    """Тексты объектов, пересекающих или касающихся картинки: подпись «QR», «скриншот»."""
    texts: list[str] = []
    for s in shapes:
        if s is target or not s.text:
            continue
        if (
            abs(s.center[0] - target.center[0]) <= (s.width + target.width) / 2 + 0.03
            and abs(s.center[1] - target.center[1]) <= (s.height + target.height) / 2 + 0.05
        ):
            texts.append(normalize_text(s.text)[:60])
    return " ".join(texts)


# ---------- теги через VLM пакетами ----------


def icon_sheet(assets: list[Asset], *, cell: int = 96, columns: int = 6) -> tuple[bytes, list[str]]:
    """Лист с пронумерованными иконками: один запрос VLM вместо десятков."""
    from PIL import Image, ImageDraw

    rows = (len(assets) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell, max(1, rows) * cell), (245, 245, 245))
    draw = ImageDraw.Draw(sheet)
    ids: list[str] = []
    for i, asset in enumerate(assets):
        x, y = (i % columns) * cell, (i // columns) * cell
        if asset.blob:
            try:
                with Image.open(io.BytesIO(asset.blob)) as img:
                    icon = img.convert("RGBA")
                    icon.thumbnail((cell - 28, cell - 28))
                    # Светлые пиктограммы (белые на прозрачном) не видны на белом: подложка
                    # выбирается по средней яркости непрозрачных пикселей.
                    data: Any = getattr(icon, "get_flattened_data", icon.getdata)()
                    opaque = [(r + g + b) / 3 for r, g, b, a in data if a > 40]
                    light = bool(opaque) and sum(opaque) / len(opaque) > 160
                    fill = (70, 70, 70, 255) if light else (255, 255, 255, 255)
                    bg = Image.new("RGBA", icon.size, fill)
                    bg.alpha_composite(icon)
                    sheet.paste(bg.convert("RGB"), (x + (cell - icon.width) // 2, y + 18))
            except Exception:
                pass
        draw.rectangle((x, y, x + cell - 1, y + cell - 1), outline=(200, 200, 200))
        draw.text((x + 4, y + 2), str(i + 1), fill=(60, 60, 60))
        ids.append(asset.asset_id)
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue(), ids


def tag_icons_with_vlm(
    assets: list[Asset],
    client: Any,
    skill: Any,
    *,
    batch: int = 24,
    limit: int = 240,
    deadline_s: float = 60.0,
    deadline: Any = None,
) -> int:
    """Теги назначения для иконок и логотипов через скилл template_analyzer (промпт tag_assets):
    листы по `batch` штук, не больше `limit` ресурсов, запросы параллельно. Возвращает число
    размеченных ресурсов; ошибки модели не роняют анализ."""
    import asyncio

    from presentation_designer.llm.types import Deadline, Image, LlmError, Message

    targets = [a for a in assets if a.kind in ("icon", "logo") and a.blob][:limit]
    if not targets or client is None or skill is None:
        return 0
    if deadline is not None and deadline.remaining() < 5:
        log.warning("теги ресурсов пропущены: бюджет времени VLM исчерпан")
        return 0
    chunks = [targets[i : i + batch] for i in range(0, len(targets), batch)]

    def make_request(chunk: list[Asset]) -> Any:
        sheet, ids = icon_sheet(chunk)
        req = skill.request(
            "analyze.tag_assets",
            f"На листе {len(ids)} пронумерованных пиктограмм. Для каждого номера дай 1–3 тега "
            "назначения по-русски (например: безопасность, деньги, время, пользователь) и признак "
            "logo, если это логотип бренда, а не пиктограмма.",
            schema=TAG_SCHEMA,
            stage="analyze",
        )
        req.messages[1] = Message("user", req.messages[1].text, (Image(sheet, "image/png"),))
        req.deadline = deadline or Deadline.after(deadline_s)
        req.schema_name = "asset_tags"
        return req

    async def run_all() -> list[Any]:
        return await asyncio.gather(
            *(client.complete(make_request(chunk)) for chunk in chunks), return_exceptions=True
        )

    tagged = 0
    for chunk, outcome in zip(chunks, asyncio.run(run_all()), strict=True):
        if isinstance(outcome, BaseException):
            if isinstance(outcome, LlmError):
                log.warning("теги ресурсов не получены: %s", outcome)
            else:
                log.exception("теги ресурсов: неожиданная ошибка", exc_info=outcome)
            continue
        items = outcome.parsed.get("items", []) if isinstance(outcome.parsed, dict) else []
        for item in items:
            try:
                idx = int(item.get("n", 0)) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(chunk):
                tags = [str(t).strip().lower() for t in item.get("tags", []) if str(t).strip()][:3]
                chunk[idx].tags = sorted(set(chunk[idx].tags) | set(tags))
                if item.get("logo") is True and chunk[idx].kind == "icon":
                    chunk[idx].kind = "logo"
                tagged += 1
    return tagged


TAG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "logo": {"type": "boolean"},
                },
                "required": ["n", "tags"],
            },
        }
    },
    "required": ["items"],
}


# ---------- фоновый декор через VLM ----------


def alpha_share(blob: bytes) -> float | None:
    """Доля прозрачных пикселей картинки (по уменьшенной копии); None — не читается."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(blob)) as img:
            if img.mode not in ("RGBA", "LA", "P", "PA"):
                return 0.0
            alpha = img.convert("RGBA").getchannel("A")
            alpha.thumbnail((96, 96))
            hist = alpha.histogram()
            return sum(hist[:16]) / max(1, alpha.width * alpha.height)
    except Exception:
        return None


def decor_candidates(assets: list[Asset], *, limit: int = 8) -> list[Asset]:
    """Кандидаты в фоновый декор — по устройству файла, без модели: картинка позади текста
    (макет, мастер или нижний слой слайда), крупнее значка, вырезанная (с прозрачностью),
    не логотип и не QR. Крупные и повторяющиеся — первыми."""

    def area(a: Asset) -> float:
        box = a.bbox_on_source or {}
        return float(box.get("width", 0)) * float(box.get("height", 0))

    out: list[Asset] = []
    for asset in assets:
        if not asset.blob or not asset.behind or "catalog" in asset.tags:
            continue
        if asset.kind in ("icon", "logo", "qr") or area(asset) < DECOR_MIN_AREA:
            continue
        share = alpha_share(asset.blob)
        if share is None or not DECOR_ALPHA[0] <= share <= DECOR_ALPHA[1]:
            continue
        out.append(asset)
    out.sort(key=lambda a: -area(a) * min(a.occurrences, 4))
    return out[:limit]


def _where(asset: Asset) -> str:
    box = asset.bbox_on_source or {}
    x, y = float(box.get("x", 0)), float(box.get("y", 0))
    w, h = float(box.get("width", 0)), float(box.get("height", 0))
    edges = [
        name
        for name, touch in (
            ("левого", x <= 0.02),
            ("правого", x + w >= 0.98),
            ("верхнего", y <= 0.02),
            ("нижнего", y + h >= 0.98),
        )
        if touch
    ]
    place = (
        "в макете" if asset.source_slide_index is None else f"на слайде {asset.source_slide_index}"
    )
    edge = f", у {' и '.join(edges)} края" if edges else ""
    return f"{place}, позади текста, {round(w * h * 100)} % площади{edge}"


def decor_sheet(assets: list[Asset], *, cell: int = 220, columns: int = 4) -> bytes:
    """Лист кандидатов на нейтральном сером: видны и белые, и тёмные вырезки."""
    from PIL import Image, ImageDraw

    rows = (len(assets) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell, max(1, rows) * cell), (150, 154, 160))
    draw = ImageDraw.Draw(sheet)
    for i, asset in enumerate(assets):
        x, y = (i % columns) * cell, (i // columns) * cell
        try:
            with Image.open(io.BytesIO(asset.blob or b"")) as img:
                pic = img.convert("RGBA")
                box = pic.getchannel("A").getbbox()
                if box:
                    pic = pic.crop(box)
                pic.thumbnail((cell - 24, cell - 34))
                sheet.paste(pic, (x + (cell - pic.width) // 2, y + 26), pic)
        except Exception:
            pass
        draw.rectangle((x, y, x + cell - 1, y + cell - 1), outline=(110, 114, 120))
        draw.rectangle((x, y, x + 28, y + 20), fill=(40, 40, 40))
        draw.text((x + 6, y + 4), str(i + 1), fill=(255, 255, 255))
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue()


def pick_decor_with_vlm(
    assets: list[Asset],
    client: Any,
    skill: Any,
    *,
    limit: int = 8,
    deadline_s: float = 40.0,
    deadline: Any = None,
) -> int:
    """Какие из кандидатов — фоновый декор шаблона (промпт pick_decor): один лист, один
    запрос. Выбранным ставится тег `decor` и оценка `decor:<0–10>`; возвращает их число.
    Без модели декор не выбирается: по устройству файла фирменный узор не отличить от
    иллюстрации со смыслом."""
    from presentation_designer.llm.types import Deadline, Image, LlmError, Message

    candidates = decor_candidates(assets, limit=limit)
    if not candidates or client is None or skill is None:
        return 0
    if deadline is not None and deadline.remaining() < 5:
        log.warning("выбор декора пропущен: бюджет времени VLM исчерпан")
        return 0
    lines = "\n".join(f"{i}. {_where(a)}" for i, a in enumerate(candidates, 1))
    req = skill.request(
        "analyze.pick_decor",
        f"На листе {len(candidates)} пронумерованных картинок шаблона, показаны на сером. "
        f"Где каждая стоит в шаблоне:\n{lines}",
        schema=DECOR_SCHEMA,
        stage="analyze",
    )
    req.messages[1] = Message(
        "user", req.messages[1].text, (Image(decor_sheet(candidates), "image/png"),)
    )
    req.deadline = deadline or Deadline.after(deadline_s)
    req.schema_name = "template_decor"
    import asyncio

    try:
        outcome = asyncio.run(client.complete(req))
    except LlmError as e:
        log.warning("декор шаблона не выбран: %s", e)
        return 0
    items = outcome.parsed.get("items", []) if isinstance(outcome.parsed, dict) else []
    picked = 0
    for item in items:
        try:
            idx, score = int(item.get("n", 0)) - 1, int(item.get("score", 0))
        except (TypeError, ValueError):
            continue
        if not 0 <= idx < len(candidates) or item.get("decor") is not True or score < 5:
            continue
        asset = candidates[idx]
        tags = {DECOR_TAG, f"{DECOR_TAG}:{min(score, 10)}"}
        if item.get("crop") is True:
            tags.add(f"{DECOR_TAG}-crop")
        asset.tags = sorted({t for t in asset.tags if not t.startswith(DECOR_TAG)} | tags)
        picked += 1
    return picked


DECOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "decor": {"type": "boolean"},
                    "crop": {"type": "boolean"},
                    "score": {"type": "integer", "minimum": 0, "maximum": 10},
                },
                "required": ["n", "decor", "crop", "score"],
            },
        }
    },
    "required": ["items"],
}


def assets_digest(assets: list[Asset]) -> str:
    counts = collections.Counter(a.kind for a in assets)
    return ", ".join(f"{k}: {n}" for k, n in counts.most_common()) or "нет"


def dumps_assets(assets: list[Asset]) -> str:
    return json.dumps([a.as_dict() for a in assets], ensure_ascii=False)
