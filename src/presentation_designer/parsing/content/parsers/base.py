"""Общая модель разбора одного файла: блоки, таблицы, изображения, предупреждения.

Парсер формата превращает файл в `ParsedDocument` — плоский список блоков в порядке чтения
(заголовки, абзацы, списки, цитаты, код, ссылки на таблицы и изображения), таблицы с сырыми
ячейками и изображения байтами. Идентификаторы здесь локальные (`t0`, `i0`); сквозные
идентификаторы пакета назначает сборка в `importer.py`, поэтому разбор файла кэшируется
независимо от того, каким по счёту он оказался в пакете.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

JsonDict = dict[str, Any]

# Виды блоков контракта content_package.blocks[].kind
BLOCK_KINDS = ("heading", "paragraph", "bullets", "table", "figure", "quote", "kpi", "code")


class ParserError(ValueError):
    """Файл не разобран: код для предупреждения источника."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Location:
    """Место блока в источнике: страница, лист, слайд, диапазон ячеек, смещение."""

    page: int | None = None
    sheet: str | None = None
    slide: int | None = None
    cell_range: str | None = None
    char_offset: int | None = None
    notes: bool | None = None

    def as_dict(self) -> JsonDict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class ParsedBlock:
    kind: str
    text: str = ""
    items: list[str] = field(default_factory=list)
    level: int | None = None
    table_ref: int | None = None  # индекс в ParsedDocument.tables
    image_ref: int | None = None  # индекс в ParsedDocument.images
    caption: str | None = None
    location: Location = field(default_factory=Location)
    tags: list[str] = field(default_factory=list)

    def as_dict(self) -> JsonDict:
        out = asdict(self)
        out["location"] = self.location.as_dict()
        return out

    @classmethod
    def from_dict(cls, data: JsonDict) -> ParsedBlock:
        loc = data.get("location") or {}
        return cls(
            kind=data["kind"],
            text=data.get("text", ""),
            items=list(data.get("items") or []),
            level=data.get("level"),
            table_ref=data.get("table_ref"),
            image_ref=data.get("image_ref"),
            caption=data.get("caption"),
            location=Location(**loc),
            tags=list(data.get("tags") or []),
        )


@dataclass
class ParsedTable:
    """Таблица как прямоугольник строк; ячейки — строки, числа или None."""

    rows: list[list[Any]]
    title: str | None = None
    location: Location = field(default_factory=Location)
    # Числовые форматы ячеек по колонкам (xlsx): percent, money, date, number
    column_formats: list[str | None] = field(default_factory=list)

    def as_dict(self) -> JsonDict:
        return {
            "rows": self.rows,
            "title": self.title,
            "location": self.location.as_dict(),
            "column_formats": self.column_formats,
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> ParsedTable:
        return cls(
            rows=[list(r) for r in data.get("rows") or []],
            title=data.get("title"),
            location=Location(**(data.get("location") or {})),
            column_formats=list(data.get("column_formats") or []),
        )


@dataclass
class ParsedImage:
    """Изображение из файла; байты живут отдельно от JSON-описания (кэш пишет их файлами)."""

    data: bytes
    mime: str
    name: str
    width_px: int
    height_px: int
    caption: str | None = None
    location: Location = field(default_factory=Location)
    kind: str = "image"  # image | logo | screenshot | chart_image | diagram_image | photo

    def as_dict(self) -> JsonDict:
        return {
            "mime": self.mime,
            "name": self.name,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "caption": self.caption,
            "location": self.location.as_dict(),
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, data: JsonDict, blob: bytes) -> ParsedImage:
        return cls(
            data=blob,
            mime=data["mime"],
            name=data["name"],
            width_px=int(data["width_px"]),
            height_px=int(data["height_px"]),
            caption=data.get("caption"),
            location=Location(**(data.get("location") or {})),
            kind=data.get("kind", "image"),
        )


@dataclass
class ParsedDocument:
    kind: str  # вид источника контракта: docx, xlsx, csv, pdf, markdown, text, pptx, image
    blocks: list[ParsedBlock] = field(default_factory=list)
    tables: list[ParsedTable] = field(default_factory=list)
    images: list[ParsedImage] = field(default_factory=list)
    warnings: list[JsonDict] = field(default_factory=list)
    extracted: bool = True
    units: dict[str, int] = field(default_factory=dict)

    def warn(self, code: str, message: str) -> None:
        if not any(w["code"] == code and w["message"] == message for w in self.warnings):
            self.warnings.append({"code": code, "message": message})

    @property
    def text_chars(self) -> int:
        return sum(len(b.text) + sum(len(i) for i in b.items) for b in self.blocks)

    def as_dict(self) -> JsonDict:
        return {
            "kind": self.kind,
            "blocks": [b.as_dict() for b in self.blocks],
            "tables": [t.as_dict() for t in self.tables],
            "images": [i.as_dict() for i in self.images],
            "warnings": self.warnings,
            "extracted": self.extracted,
            "units": self.units,
        }

    @classmethod
    def from_dict(cls, data: JsonDict, blobs: list[bytes]) -> ParsedDocument:
        images = [
            ParsedImage.from_dict(i, blob)
            for i, blob in zip(data.get("images") or [], blobs, strict=False)
        ]
        return cls(
            kind=data["kind"],
            blocks=[ParsedBlock.from_dict(b) for b in data.get("blocks") or []],
            tables=[ParsedTable.from_dict(t) for t in data.get("tables") or []],
            images=images,
            warnings=list(data.get("warnings") or []),
            extracted=bool(data.get("extracted", True)),
            units=dict(data.get("units") or {}),
        )


# ---------- общие помощники текста ----------

_WS = re.compile(r"[ \t ]+")
_BULLET = re.compile(r"^\s*(?:[-•*–—▪●◦]|\d{1,2}[.)])\s+")


def clean_text(value: str | None) -> str:
    """Схлопывает пробелы внутри строки, убирает обрамляющие пробелы; переводы строк сохраняет."""
    if not value:
        return ""
    lines = [_WS.sub(" ", line).strip() for line in value.replace("\r", "").split("\n")]
    return "\n".join(line for line in lines if line).strip()


def is_bullet_line(line: str) -> bool:
    return bool(_BULLET.match(line))


def strip_bullet(line: str) -> str:
    return _BULLET.sub("", line, count=1).strip()


def split_paragraphs(text: str) -> list[str]:
    """Абзацы по пустым строкам; одиночные переводы строк внутри абзаца остаются."""
    chunks = re.split(r"\n\s*\n", text.replace("\r", ""))
    return [clean_text(c) for c in chunks if clean_text(c)]


def looks_like_heading(line: str, *, max_len: int = 90) -> bool:
    """Короткая строка без завершающей точки — заголовок в txt и заметках."""
    s = line.strip()
    if not s or len(s) > max_len or "\n" in s:
        return False
    if s[-1] in ".;:,!?…" and not s.endswith("?"):
        return False
    words = s.split()
    return len(words) <= 12


def blocks_from_text(
    text: str,
    *,
    location: Location | None = None,
    max_block_chars: int = 2000,
    allow_headings: bool = True,
) -> list[ParsedBlock]:
    """Абзацы и списки из простого текста: маркированные строки собираются в bullets,
    короткая строка без точки — заголовок (если разрешено)."""
    out: list[ParsedBlock] = []
    offset = 0
    base = location or Location()
    for para in split_paragraphs(text):
        loc = Location(**{**base.as_dict(), "char_offset": offset})
        lines = para.split("\n")
        if all(is_bullet_line(line) for line in lines):
            out.append(
                ParsedBlock("bullets", items=[strip_bullet(line) for line in lines], location=loc)
            )
        elif allow_headings and len(lines) == 1 and looks_like_heading(lines[0]):
            out.append(ParsedBlock("heading", text=lines[0], level=2, location=loc))
        else:
            head: list[str] = []
            bullets: list[str] = []
            for line in lines:
                if is_bullet_line(line):
                    bullets.append(strip_bullet(line))
                else:
                    if bullets:
                        out.append(ParsedBlock("bullets", items=bullets, location=loc))
                        bullets = []
                    head.append(line)
            joined = " ".join(head).strip()
            for piece in split_long(joined, max_block_chars):
                out.append(ParsedBlock("paragraph", text=piece, location=loc))
            if bullets:
                out.append(ParsedBlock("bullets", items=bullets, location=loc))
        offset += len(para) + 2
    return out


def split_long(text: str, max_chars: int) -> list[str]:
    """Режет длинный абзац по границам предложений, не превышая предел."""
    if len(text) <= max_chars:
        return [text] if text else []
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > max_chars:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def decode_text(raw: bytes) -> tuple[str, str]:
    """Текст и имя кодировки: UTF-8 (с BOM или без), иначе cp1251."""
    for encoding in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(encoding), "utf-8"
        except UnicodeDecodeError:
            continue
    return raw.decode("cp1251", errors="replace"), "cp1251"


def image_dimensions(data: bytes) -> tuple[int, int, str]:
    """Размер и MIME изображения через Pillow; неизвестный формат — ошибка ParserError."""
    import io

    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = (img.format or "").lower()
            width, height = img.size
    except Exception as e:
        raise ParserError("image_unreadable", f"изображение не читается: {e}") from e
    mime = {
        "png": "image/png",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "bmp": "image/bmp",
        "webp": "image/webp",
        "tiff": "image/tiff",
        "emf": "image/emf",
        "wmf": "image/wmf",
    }.get(fmt, f"image/{fmt or 'unknown'}")
    return width, height, mime


def guess_image_kind(data: bytes, width: int, height: int, mime: str) -> str:
    """Грубый вид картинки для пакета: фото (JPEG), логотип (маленький PNG с прозрачностью),
    скриншот (большой PNG с почти прямоугольной палитрой), иначе image."""
    if mime == "image/jpeg":
        return "photo"
    if mime != "image/png":
        return "image"
    import io

    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as img:
            has_alpha = img.mode in ("RGBA", "LA") or "transparency" in img.info
            small = img.convert("RGB").resize((32, 32))
            colors = len(set(small.tobytes()[i : i + 3] for i in range(0, len(small.tobytes()), 3)))
    except Exception:
        return "image"
    if max(width, height) <= 512 and (has_alpha or colors <= 24):
        return "logo"
    if width >= 800 and colors >= 200:
        return "screenshot"
    return "image"
