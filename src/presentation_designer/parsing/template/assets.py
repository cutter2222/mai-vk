"""Индекс ресурсов шаблона: иконки, логотипы, фото, скриншоты, фон, QR.

Каждая картинка мастера, макета или слайда попадает в индекс один раз (по sha256 байтов);
признаки считаются по размеру на слайде, пропорциям, числу цветов и прозрачности через Pillow:
маленькая почти квадратная картинка с одним-двумя цветами и прозрачностью — иконка; большая
многоцветная — фото; во всю площадь — фон; на мастере/макете или повторяющаяся малая — логотип.
Изображения из каталогов ресурсов сохраняются как переиспользуемые, но не становятся паттернами.
Теги по назначению добираются пакетами через VLM: несколько иконок на одном листе с номерами.
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

log = logging.getLogger(__name__)

ICON_MAX_AREA = 0.012
LOGO_MAX_AREA = 0.06


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
    occurrences: int = 1
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


def _image_props(blob: bytes) -> tuple[int | None, int | None, bool | None, bool | None]:
    """Размер, монохромность (≤ 2 значимых цвета) и наличие прозрачности."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(blob)) as img:
            width, height = img.size
            small = img.convert("RGBA")
            small.thumbnail((48, 48))
            raw: Any = getattr(small, "get_flattened_data", small.getdata)()
            pixels = list(raw)
    except Exception:
        return None, None, None, None
    opaque = [(r // 32, g // 32, b // 32) for r, g, b, a in pixels if a > 40]
    transparent = any(a <= 40 for _, _, _, a in pixels)
    if not opaque:
        return width, height, None, transparent
    counter = collections.Counter(opaque)
    top = counter.most_common(3)
    share = sum(n for _, n in top[:2]) / len(opaque)
    return width, height, share >= 0.9, transparent


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
    ) -> None:
        if shape.kind != "picture" or not shape.media_sha256 or not shape.media_part:
            return
        sha = shape.media_sha256
        if sha in assets:
            assets[sha].occurrences += 1
            existing = assets[sha]
            if on_master and existing.kind == "icon":
                existing.kind = "logo"
            return
        blob = shape.media_blob
        width, height, mono, transparent = _image_props(blob) if blob else (None, None, None, None)
        kind = _kind_for(
            shape,
            (mono, transparent),
            on_master=on_master,
            catalog=catalog,
            ext=shape.media_ext,
            nearby_text=nearby_text,
            pixel_size=(width, height),
        )
        asset = Asset(
            asset_id=f"asset_{len(order) + 1}",
            kind=kind,
            media_path=shape.media_part.lstrip("/"),
            sha256=sha,
            width_px=width,
            height_px=height,
            source_slide_index=slide_index,
            bbox_on_source=shape.bbox,
            reusable=kind
            in ("icon", "logo", "photo", "image", "mockup", "background", "screenshot"),
            monochrome=mono,
            transparent=transparent,
            blob=blob,
        )
        if catalog:
            asset.tags.append("catalog")
        assets[sha] = asset
        order.append(sha)

    for master in pkg.masters:
        for shape in master.shapes:
            register(shape, slide_index=None, on_master=True, catalog=False, nearby_text="")
    for layout in pkg.layouts:
        for shape in layout.shapes:
            register(shape, slide_index=None, on_master=True, catalog=False, nearby_text="")
    for slide in pkg.slides:
        kind = by_kind.get(slide.index, "content_sample")
        if kind == "hidden":
            continue
        catalog = kind == "asset_catalog"
        text = normalize_text(slide.all_text)
        for shape in slide.shapes:
            register(
                shape,
                slide_index=slide.index,
                on_master=False,
                catalog=catalog,
                nearby_text=text if catalog else _nearby(slide.shapes, shape),
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


def assets_digest(assets: list[Asset]) -> str:
    counts = collections.Counter(a.kind for a in assets)
    return ", ".join(f"{k}: {n}" for k, n in counts.most_common()) or "нет"


def dumps_assets(assets: list[Asset]) -> str:
    return json.dumps([a.as_dict() for a in assets], ensure_ascii=False)
