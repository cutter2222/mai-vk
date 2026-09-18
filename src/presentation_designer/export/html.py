"""Нативный HTML-экспорт колоды: объекты слайда, а не его снимок.

ТЗ п.2.7 требует экспорт в html наравне с .pptx и .pdf, а слайд-картинку не
засчитывает. Прежний экспорт складывал в страницу миниатюры и текстовую
выжимку под ними: формально html, по сути — те же картинки. При этом всё для
настоящей страницы уже посчитано вёрсткой и лежит в `ComposedDeck`: у каждого
объекта рамка в долях холста, порядок слоёв, вычисленный стиль текста, врезки,
заливка, ссылка на ресурс.

Поэтому здесь ничего не измеряется и не угадывается — описание слайда
переводится в разметку один в один:

* рамка в долях холста → `left/top/width/height` в процентах;
* кегль в пунктах → `cqw` относительно ширины слайда, поэтому страница
  одинаково выглядит на любом размере окна;
* шрифт шаблона → `@font-face` с файлом, который нашёл тот же резолвер, каким
  меряется вместимость слотов;
* картинки → `data:` из самого .pptx, без внешних ссылок.

Страница получается автономной: ни одного обращения в сеть, текст выделяется и
ищется, вёрстка масштабируется. Растровых слайдов в ней нет.
"""

from __future__ import annotations

import base64
import html
import logging
import pathlib
import zipfile
from typing import Any

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]

EMU_PER_PT = 12700

# Видимая часть страницы: слайд занимает ширину колонки, высота — из его
# пропорций. Единица cqw равна проценту ширины слайда, поэтому кегли и отступы
# задаются один раз и дальше масштабируются сами.
_CSS = """
:root{color-scheme:light}
body{margin:0;padding:24px 16px 48px;background:#eef0f4;
 font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#1d1f25}
h1{max-width:1200px;margin:0 auto 20px;font-size:22px;font-weight:600}
.deck{max-width:1200px;margin:0 auto}
.slide{position:relative;container-type:inline-size;margin:0 0 28px;overflow:hidden;
 background:#fff;border:1px solid #dfe2e8;border-radius:10px;box-shadow:0 1px 2px #0000000d}
.o{position:absolute;box-sizing:border-box}
.o p{margin:0}
.o img{width:100%;height:100%;display:block}
.num{position:absolute;right:10px;bottom:6px;font-size:11px;color:#8c909c;
 font-family:system-ui,sans-serif}
"""


def build_html(
    title: str,
    deck: JsonDict,
    pptx_path: pathlib.Path | None = None,
    slide_titles: list[str] | None = None,
) -> str:
    """Автономная страница колоды из описания собранного файла."""
    size = deck.get("slide_size") or {}
    width_emu = int(size.get("width_emu") or 12192000)
    height_emu = int(size.get("height_emu") or 6858000)
    width_pt = width_emu / EMU_PER_PT
    ratio = height_emu / width_emu if width_emu else 0.5625

    media = _media(deck, pptx_path)
    faces = _font_faces(deck)
    sections = [
        _slide(slide, index, width_pt, ratio, media, slide_titles or [])
        for index, slide in enumerate(deck.get("slides", []))
    ]
    return (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{faces}{_CSS}</style></head><body>"
        f"<h1>{html.escape(title)}</h1><div class=\"deck\">{''.join(sections)}</div>"
        "</body></html>"
    )


def _slide(
    slide: JsonDict,
    index: int,
    width_pt: float,
    ratio: float,
    media: dict[str, str],
    titles: list[str],
) -> str:
    parts: list[str] = []
    for obj in sorted(slide.get("objects", []), key=lambda o: int(o.get("z_order") or 0)):
        markup = _object(obj, width_pt, media)
        if markup:
            parts.append(markup)
    background = _color((slide.get("background") or {}).get("color")) or "#fff"
    label = titles[index] if index < len(titles) else f"Слайд {index + 1}"
    return (
        f'<section class="slide" id="slide-{index + 1}" aria-label="{html.escape(label)}"'
        f' style="aspect-ratio:{1 / ratio:.4f};background:{background}">'
        f"{''.join(parts)}<span class=\"num\">{index + 1}</span></section>"
    )


def _object(obj: JsonDict, width_pt: float, media: dict[str, str]) -> str:
    box = obj.get("bbox") or {}
    style = [
        f"left:{float(box.get('x') or 0) * 100:.3f}%",
        f"top:{float(box.get('y') or 0) * 100:.3f}%",
        f"width:{float(box.get('width') or 0) * 100:.3f}%",
        f"height:{float(box.get('height') or 0) * 100:.3f}%",
    ]
    rotation = float(obj.get("rotation_deg") or 0)
    if rotation:
        style.append(f"transform:rotate({rotation:.2f}deg)")
    fill = obj.get("fill") or {}
    if fill.get("kind") == "solid" and _color(fill.get("color")):
        style.append(f"background:{_color(fill.get('color'))}")

    kind = obj.get("kind")
    if kind == "picture":
        asset = (obj.get("picture") or {}).get("asset_id")
        src = media.get(str(asset))
        if not src:
            return ""
        fit = {"cover": "cover", "contain": "contain"}.get(
            str((obj.get("picture") or {}).get("fit") or ""), "fill"
        )
        alt = html.escape(str(obj.get("name") or ""))
        return (
            f'<div class="o" style="{";".join(style)}">'
            f'<img src="{src}" alt="{alt}" style="object-fit:{fit}"></div>'
        )

    text = obj.get("text") or {}
    paragraphs = text.get("paragraphs") or (
        [{"text": text.get("plain")}] if text.get("plain") else []
    )
    if not paragraphs:
        # Пустая рамка: она несёт заливку, если та есть, и ничего больше.
        return f'<div class="o" style="{";".join(style)}"></div>' if fill.get("color") else ""

    insets = text.get("insets") or {}
    style += [
        f"padding:{float(insets.get('top') or 0) * 100:.2f}cqw"
        f" {float(insets.get('right') or 0) * 100:.2f}cqw"
        f" {float(insets.get('bottom') or 0) * 100:.2f}cqw"
        f" {float(insets.get('left') or 0) * 100:.2f}cqw",
        "display:flex",
        "flex-direction:column",
        "justify-content:" + _justify(text.get("vertical_align")),
    ]
    base = text.get("computed_style") or {}
    body = "".join(_paragraph(p, base, width_pt) for p in paragraphs)
    return f'<div class="o" style="{";".join(style)}">{body}</div>'


def _paragraph(par: JsonDict, base: JsonDict, width_pt: float) -> str:
    style = {**base, **(par.get("style") or {})}
    font = {**(base.get("font") or {}), **((par.get("style") or {}).get("font") or {})}
    size_pt = float(font.get("size_pt") or 0)
    rules = []
    if size_pt > 0 and width_pt > 0:
        # Кегль в долях ширины слайда: страница масштабируется целиком.
        rules.append(f"font-size:{size_pt / width_pt * 100:.3f}cqw")
    family = str(font.get("family") or "").strip()
    if family:
        rules.append(f'font-family:"{html.escape(family)}",system-ui,sans-serif')
    if _color(font.get("color")):
        rules.append(f"color:{_color(font.get('color'))}")
    if font.get("bold"):
        rules.append("font-weight:700")
    if font.get("italic"):
        rules.append("font-style:italic")
    spacing = float(font.get("line_spacing") or 0)
    if spacing > 0:
        rules.append(f"line-height:{spacing:.2f}")
    align = str(par.get("align") or "").strip()
    if align in ("center", "right", "justify"):
        rules.append(f"text-align:{align}")
    if float(style.get("space_before_pt") or 0) and width_pt:
        rules.append(f"margin-top:{float(style['space_before_pt']) / width_pt * 100:.3f}cqw")
    if float(style.get("space_after_pt") or 0) and width_pt:
        rules.append(f"margin-bottom:{float(style['space_after_pt']) / width_pt * 100:.3f}cqw")
    bullet = (style.get("bullet") or {}).get("char") if style.get("bullet") else None
    prefix = f"{html.escape(str(bullet))} " if bullet else ""
    return f'<p style="{";".join(rules)}">{prefix}{html.escape(str(par.get("text") or ""))}</p>'


def _justify(value: Any) -> str:
    return {"middle": "center", "center": "center", "bottom": "flex-end"}.get(
        str(value or ""), "flex-start"
    )


def _color(value: Any) -> str | None:
    """Цвет описания в вид, понятный CSS; прозрачность — в rgba."""
    if not value:
        return None
    if isinstance(value, str):
        return value if value.startswith("#") else f"#{value}"
    if isinstance(value, dict):
        rgb = str(value.get("rgb") or value.get("hex") or "").lstrip("#")
        if not rgb:
            return None
        alpha = value.get("alpha")
        if alpha is not None and float(alpha) < 1:
            r, g, b = (int(rgb[i : i + 2], 16) for i in (0, 2, 4))
            return f"rgba({r},{g},{b},{float(alpha):.2f})"
        return f"#{rgb}"
    return None


def _media(deck: JsonDict, pptx_path: pathlib.Path | None) -> dict[str, str]:
    """Ресурсы колоды как `data:`-ссылки: страница остаётся автономной."""
    assets = {
        str(a.get("asset_id")): str(a.get("media_path") or "")
        for a in deck.get("assets") or []
        if a.get("asset_id") and a.get("media_path")
    }
    if not assets or pptx_path is None or not pathlib.Path(pptx_path).is_file():
        return {}
    out: dict[str, str] = {}
    try:
        with zipfile.ZipFile(pptx_path) as zf:
            names = set(zf.namelist())
            for asset_id, path in assets.items():
                name = path if path in names else f"ppt/media/{pathlib.Path(path).name}"
                if name not in names:
                    continue
                data = zf.read(name)
                mime = _mime(name)
                out[asset_id] = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    except (OSError, zipfile.BadZipFile):
        log.warning("ресурсы для html не прочитаны из %s", pptx_path, exc_info=True)
    return out


def _mime(name: str) -> str:
    suffix = pathlib.Path(name).suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
        ".emf": "image/emf",
        ".wmf": "image/wmf",
    }.get(suffix, "application/octet-stream")


def _font_faces(deck: JsonDict) -> str:
    """`@font-face` для гарнитур колоды — теми же файлами, какими её мерили.

    Без этого страница подставит системный шрифт, и вёрстка разъедется именно
    там, где мы её выверяли. Резолвер тот же, что у замера вместимости, так что
    файл найдётся ровно тот, по которому считались строки.
    """
    from presentation_designer.shared import text_metrics

    families: set[str] = set()
    for slide in deck.get("slides", []):
        for obj in slide.get("objects", []):
            font = ((obj.get("text") or {}).get("computed_style") or {}).get("font") or {}
            if font.get("family"):
                families.add(str(font["family"]))
    faces: list[str] = []
    for family in sorted(families):
        for bold in (False, True):
            resolved = text_metrics.resolve_font(family, bold=bold)
            path = pathlib.Path(resolved.file) if resolved.file else None
            if path is None or not path.is_file() or resolved.substituted:
                continue
            try:
                data = base64.b64encode(path.read_bytes()).decode("ascii")
            except OSError:
                continue
            fmt = "opentype" if path.suffix.lower() == ".otf" else "truetype"
            faces.append(
                f'@font-face{{font-family:"{family}";font-weight:{700 if bold else 400};'
                f"src:url(data:font/{fmt};base64,{data}) format('{fmt}')}}"
            )
    return "".join(faces)
