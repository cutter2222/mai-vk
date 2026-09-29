"""Стоковые фотографии для слайдов: поиск в Openverse по CC0-фотобанкам и загрузка в пакет.

Фото нужны, когда в материалах пользователя картинок нет, а слайд — только текст. Поиск
идёт в Openverse (каталог открытых изображений) и только по фотобанкам с лицензией CC0 —
StockSnap, rawpixel (public domain), Nappy, WordPress Photos: снимки профессиональные, без
водяных знаков, и их можно ставить в презентацию без указания автора. Автор и источник
всё равно записываются в подпись ресурса.

Найденное кладётся в каталог пакета (`stock/`), описание ресурса — отдельным файлом
`stock/assets/<asset_id>.json`: три варианта презентации собираются параллельно в разных
процессах, и общий индекс-файл они бы переписывали друг у друга. Вёрстка находит такие
ресурсы по `asset_id` через `load_assets` (layout/compose.py), поэтому пересборка и правки
видят их так же, как картинки из материалов.

Сеть — только здесь; ошибка поиска или загрузки означает «фото нет», а не сбой генерации.
Без ключа Openverse даёт 20 запросов в минуту и 200 в день: результаты поиска кэшируются
по запросу в каталоге пакета (`stock/q_<hash>.json`), так что три варианта одной
презентации делят и поиск, и файлы.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import pathlib
import re
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]

OPENVERSE_URL = "https://api.openverse.org/v1/images/"
SOURCES = ("stocksnap", "rawpixel", "nappy", "wordpress")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126 Safari/537.36 presentation-designer"
)
STOCK_DIR = "stock"
MIN_WIDTH_PX = 1000
MAX_WIDTH_PX = 1800  # больше на слайде не видно, а файл растёт
PAGE_SIZE = 12
# Рамка фото на слайде — альбомная (≈ 1,2 : 1); портретный снимок в ней обрезается до
# случайной полосы (рамка и вывеска вместо остановки, 29.09.2026).
MIN_ASPECT = 1.15
# Не фотографии и не современные снимки: архивные кадры в сепии, векторные элементы,
# коллажи, макеты. Сверяется с названием и автором снимка в нижнем регистре.
NOT_PHOTO = re.compile(
    r"\b(?:archives?|vintage|retro|historic(?:al)?|antique|old|vector|illustrations?|"
    r"drawings?|collage|element|mockup|mock-up|template|psd|png|poster|clipart|icon|"
    r"pattern|lithograph|engraving|painting|sketch|cartoon)\b"
    r"|\b1[89]\d\d\b|/19\d\d\b"
)


@dataclass
class StockPhoto:
    asset_id: str
    path: str  # относительно каталога пакета
    width_px: int
    height_px: int
    sha256: str
    caption: str
    query: str

    def asset(self) -> JsonDict:
        """Ресурс пакета (ContentPackage.assets[])."""
        return {
            "asset_id": self.asset_id,
            "kind": "photo",
            "path": self.path,
            "width_px": self.width_px,
            "height_px": self.height_px,
            "caption": self.caption,
            "sha256": self.sha256,
            "mime": "image/jpeg",
        }


def load_assets(package_dir: pathlib.Path | None) -> dict[str, JsonDict]:
    """Стоковые фото, уже загруженные в пакет: asset_id → ресурс пакета."""
    if package_dir is None:
        return {}
    out: dict[str, JsonDict] = {}
    for file in sorted((pathlib.Path(package_dir) / STOCK_DIR / "assets").glob("*.json")):
        try:
            asset = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(asset, dict) and asset.get("asset_id"):
            out[str(asset["asset_id"])] = asset
    return out


def find_photo(
    query: str,
    package_dir: pathlib.Path,
    *,
    exclude: set[str] | None = None,
    timeout_s: float = 8.0,
    client: Any = None,
) -> StockPhoto | None:
    """Первое подходящее фото по запросу, загруженное в пакет; None — не нашлось.

    `exclude` — asset_id, уже стоящие в презентации: одно фото дважды не ставится."""
    query = _clean(query)
    if not query:
        return None
    exclude = exclude or set()
    own = client is None
    if own:
        import httpx

        client = httpx.Client(
            timeout=timeout_s, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
    try:
        for attempt in _attempts(query):
            for result in _search(attempt, package_dir, client):
                asset_id = _asset_id(result)
                if asset_id in exclude or not _usable(result):
                    continue
                photo = _download(result, asset_id, attempt, package_dir, client)
                if photo is not None:
                    return photo
        return None
    finally:
        if own:
            client.close()


def _clean(query: str) -> str:
    words = re.findall(r"[a-z][a-z-]*", query.lower())
    return " ".join(words[:4])


def _attempts(query: str) -> list[str]:
    """Запрос целиком, затем короче: у фотобанков узкие запросы часто пустые.

    Короче — не меньше двух слов (29.09.2026): одно слово («shelter», «frame») находило
    хижины и рамки вместо остановки; без фото слайд лучше, чем со случайным. Сначала два
    последних слова — у английской фразы это обычно сам предмет («bus shelter»)."""
    words = query.split()
    out = [query]
    if len(words) > 2:
        out.append(" ".join(words[-2:]))
        out.append(" ".join(words[:2]))
    return list(dict.fromkeys(out))


def _usable(result: JsonDict) -> bool:
    """Современная альбомная фотография: не архив, не вектор, не портрет."""
    width, height = int(result.get("width") or 0), int(result.get("height") or 0)
    if not width or not height or width < height * MIN_ASPECT:
        return False
    text = f"{result.get('title') or ''} {result.get('creator') or ''}".lower()
    return NOT_PHOTO.search(text) is None


def _search(query: str, package_dir: pathlib.Path, client: Any) -> list[JsonDict]:
    cache = pathlib.Path(package_dir) / STOCK_DIR / f"q_{_hash(query)[:16]}.json"
    if cache.is_file():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(cached, list):
                return cached
        except (OSError, json.JSONDecodeError):
            pass
    params = {
        "q": query,
        "license_type": "commercial",
        "source": ",".join(SOURCES),
        "page_size": str(PAGE_SIZE),
        "mature": "false",
    }
    headers = {}
    token = os.environ.get("PD_OPENVERSE_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        response = client.get(OPENVERSE_URL, params=params, headers=headers)
    except Exception as e:  # сеть недоступна — фото нет
        log.info("фото: поиск «%s» не удался: %s", query, e)
        return []
    if response.status_code != 200:
        log.info("фото: поиск «%s» — HTTP %s", query, response.status_code)
        return []
    words = set(query.split())
    results = [
        {
            k: r.get(k)
            for k in ("id", "url", "width", "height", "creator", "source", "license", "title")
        }
        for r in (response.json().get("results") or [])
        if r.get("url") and int(r.get("width") or 0) >= MIN_WIDTH_PX
        and int(r.get("height") or 0) >= MIN_WIDTH_PX // 2
        and _relevant(r, words)
    ]  # fmt: skip
    _write(cache, json.dumps(results, ensure_ascii=False).encode())
    return results


def _relevant(result: JsonDict, words: set[str]) -> bool:
    """Слово запроса есть в названии или тегах снимка: полнотекстовый поиск фотобанка по
    редкому слову отдаёт случайные кадры, и такое фото хуже, чем никакого."""
    text = " ".join(
        [str(result.get("title") or "")]
        + [str(t.get("name") or "") for t in result.get("tags") or [] if isinstance(t, dict)]
    ).lower()
    found = set(re.findall(r"[a-z]+", text))
    hits = sum(1 for w in words if w in found or w.rstrip("s") in found or f"{w}s" in found)
    # Все слова запроса, кроме одного: «smartphone notifications settings» не должен
    # находить сервировку стола по одному «settings».
    return hits >= max(1, len(words) - 1)


def _download(
    result: JsonDict, asset_id: str, query: str, package_dir: pathlib.Path, client: Any
) -> StockPhoto | None:
    stock = pathlib.Path(package_dir) / STOCK_DIR
    meta = stock / "assets" / f"{asset_id}.json"
    if meta.is_file():
        try:
            asset = json.loads(meta.read_text(encoding="utf-8"))
            return StockPhoto(
                asset_id=asset_id,
                path=str(asset["path"]),
                width_px=int(asset["width_px"]),
                height_px=int(asset["height_px"]),
                sha256=str(asset["sha256"]),
                caption=str(asset.get("caption") or ""),
                query=query,
            )
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            pass
    try:
        response = client.get(str(result["url"]))
    except Exception as e:
        log.info("фото: загрузка %s не удалась: %s", result.get("url"), e)
        return None
    if response.status_code != 200 or not response.content:
        return None
    blob = _normalized(response.content)
    if blob is None:
        return None
    data, width, height = blob
    sha = hashlib.sha256(data).hexdigest()
    rel = f"{STOCK_DIR}/{sha[:24]}.jpg"
    _write(pathlib.Path(package_dir) / rel, data)
    creator = str(result.get("creator") or "").strip()
    source = str(result.get("source") or "")
    caption = f"Фото: {creator + ', ' if creator else ''}{source} (CC0)"
    photo = StockPhoto(
        asset_id=asset_id,
        path=rel,
        width_px=width,
        height_px=height,
        sha256=sha,
        caption=caption,
        query=query,
    )
    _write(meta, json.dumps(photo.asset(), ensure_ascii=False).encode())
    return photo


def _normalized(blob: bytes) -> tuple[bytes, int, int] | None:
    """JPEG шириной до MAX_WIDTH_PX; None — не картинка."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(blob)) as img:
            rgb = img.convert("RGB")
            if rgb.width > MAX_WIDTH_PX:
                rgb = rgb.resize(
                    (MAX_WIDTH_PX, round(rgb.height * MAX_WIDTH_PX / rgb.width)),
                    Image.Resampling.LANCZOS,
                )
            out = io.BytesIO()
            rgb.save(out, "JPEG", quality=85, optimize=True)
            return out.getvalue(), rgb.width, rgb.height
    except Exception:
        return None


def _asset_id(result: JsonDict) -> str:
    return f"stock_{_hash(str(result.get('id') or result.get('url')))[:16]}"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _write(path: pathlib.Path, data: bytes) -> None:
    """Запись через временный файл: соседний вариант не прочтёт недописанное."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.monotonic_ns()}")
    tmp.write_bytes(data)
    tmp.replace(path)


__all__ = ["StockPhoto", "find_photo", "load_assets"]
