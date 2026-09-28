"""Материалы из интернета для темы без материалов.

Презентация по одной строке темы получалась «концепцией»: модели нечего пересказывать, и она
заполняет слайды общими словами с пометками «Идея:», «Гипотеза:». Здесь до построения
смыслового плана ищутся открытые источники по теме — выдача DuckDuckGo и статья Википедии, — и
из найденных страниц берутся абзацы по теме. Они входят в контент-пакет обычными блоками с
источником-ссылкой, а числа из них становятся фактами тем же извлекателем, что и у файлов:
дальше генерация опирается на них, как на загруженный документ.

Модель здесь не участвует: что написано на странице, то и попадает в пакет, с адресом
страницы. Подбор абзацев — по словам темы; обрывки меню, куки и подписки отсекаются.

Без сети (или при запрете в конфиге) импорт идёт как раньше — поиск только добавляет
материал и никогда не роняет импорт. Ответы кэшируются на диске по запросу и версии модуля:
повторная сборка той же темы получает те же источники и тот же смысловой план.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import pathlib
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from presentation_designer.parsing.content.parsers.base import (
    Location,
    ParsedBlock,
    ParsedDocument,
)

if TYPE_CHECKING:
    # Настройки приложения (config/app.yaml) с теми же полями, что и ResearchSettings.
    from presentation_designer.shared.settings import Research

log = logging.getLogger(__name__)

JsonDict = dict[str, Any]

RESEARCH_NAME = "web_research"
RESEARCH_VERSION = "0.1.0"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
DDG_URL = "https://html.duckduckgo.com/html/"
MAX_PAGE_BYTES = 2_000_000
CACHE_TTL_S = 7 * 24 * 3600

# Площадки, где текста страницы нет в разметке (видео, соцсети, магазины) или он за входом.
SKIP_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "vk.com",
    "vkvideo.ru",
    "ok.ru",
    "t.me",
    "instagram.com",
    "facebook.com",
    "tiktok.com",
    "pinterest.",
    "twitter.com",
    "x.com",
    "dzen.ru",
    "rutube.ru",
    "ozon.ru",
    "wildberries.ru",
    "market.yandex",
    "avito.ru",
    "aliexpress",
    "reddit.com",
    "otvet.mail.ru",
    "pikabu.ru",
)
SKIP_EXTENSIONS = (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".zip")

# Служебные слова темы: по ним абзац не отбирается («исследование полезности» — про всё).
STOP_WORDS = frozenset(
    {
        "презентация",
        "презентацию",
        "исследование",
        "исследования",
        "полезность",
        "полезности",
        "анализ",
        "обзор",
        "тема",
        "тему",
        "влияние",
        "роль",
        "значение",
        "современный",
        "современные",
        "основы",
        "введение",
        "проект",
        "доклад",
        "сделай",
        "про",
        "для",
        "как",
        "что",
        "это",
        "или",
        "при",
        "без",
        "над",
        "под",
        "сколько",
        "можно",
        "нужно",
        "день",
        "происходит",
        "почему",
        "какие",
        "какой",
        "чем",
        "учёные",
        "ученые",
        "факты",
        "статистика",
        "рекомендации",
        "пример",
        "примеры",
        "случаи",
        "эксперимент",
        "исследований",
        "исследованиях",
    }
)
# Обрывки интерфейса сайта, которые попадают в абзацы.
BOILERPLATE = re.compile(
    r"(?i)(cookie|куки|подпис(ать|ывайтесь|ка)|реклам|все права|©|javascript|"
    r"войти|регистрац|загрузка\.\.\.|политик[аи] конфиденциальности|читайте также|"
    r"поделиться|комментари|url:|https?://|дата обращения|\bdoi\b)"
)
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё-]+")
_NUMBER = re.compile(r"\d")
_SENTENCE = re.compile(r"(?<=[.!?…])\s+(?=[A-ZА-ЯЁ«\"(\d])")


@dataclass
class ResearchSettings:
    """Параметры поиска (config/app.yaml, `research`)."""

    enabled: bool = True
    max_pages: int = 6
    paragraphs_per_page: int = 4
    max_chars: int = 7000
    search_timeout_s: float = 8.0
    page_timeout_s: float = 7.0
    budget_s: float = 25.0
    wikipedia: bool = True


@dataclass
class WebPage:
    url: str
    title: str
    snippet: str = ""
    paragraphs: list[str] = field(default_factory=list)

    @property
    def domain(self) -> str:
        host = urllib.parse.urlsplit(self.url).netloc.lower()
        return host[4:] if host.startswith("www.") else host

    def as_dict(self) -> JsonDict:
        return {
            "url": self.url,
            "title": self.title,
            "snippet": self.snippet,
            "paragraphs": self.paragraphs,
        }


@dataclass
class ResearchResult:
    documents: list[tuple[JsonDict, ParsedDocument]]
    report: JsonDict


# ---------- запросы ----------


def topic_of(brief: JsonDict) -> str:
    """Тема поиска: название брифа, при наличии — цель."""
    title = str(brief.get("title") or "").strip()
    goal = str(brief.get("goal") or "").strip()
    return title if not goal or goal.lower() in title.lower() else f"{title}. {goal}"


def stems(text: str) -> set[str]:
    """Основы значимых слов: первые пять букв (грубо, но для русского падежа хватает)."""
    out = set()
    for word in _WORD.findall(text.lower()):
        if len(word) < 4 or word in STOP_WORDS:
            continue
        out.add(word[:5])
    return out


def queries_for(brief: JsonDict) -> list[str]:
    """Запросы поиска: тема как есть и тема словами без служебных, с уточнением «факты»."""
    topic = topic_of(brief)
    if not topic:
        return []
    words = [w for w in _WORD.findall(str(brief.get("title") or "")) if w.lower() not in STOP_WORDS]
    core = " ".join(words)
    out = [topic]
    if core and core.lower() != topic.lower():
        out.append(f"{core} факты исследования")
    else:
        out.append(f"{topic} факты")
    return list(dict.fromkeys(out))


QUERY_SCHEMA: JsonDict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["queries", "wikipedia"],
    "properties": {
        "queries": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {"type": "string", "minLength": 3, "maxLength": 120},
        },
        "wikipedia": {"type": ["string", "null"], "maxLength": 80},
    },
}


def model_queries(brief: JsonDict, llm_client: Any, skill: Any) -> tuple[list[str], str | None]:
    """Разные стороны темы запросами от модели (скилл web_researcher). Исключение — вызывающий
    строит запросы из темы сам."""
    from presentation_designer.llm.types import Deadline

    params = skill.manifest.params or {}
    request = skill.request(
        "research.queries",
        f"Тема: «{topic_of(brief)}». Язык: {brief.get('language') or 'ru'}.",
        schema=QUERY_SCHEMA,
        stage="import",
    )
    request.schema_name = "research_queries"
    request.deadline = Deadline.after(float(params.get("deadline_s", 15)))
    answer = llm_client.complete_sync(request).parsed
    if not isinstance(answer, dict):
        raise ValueError("ответ не объект")
    queries = [
        _squash(str(q)).strip("«»\"'") for q in answer.get("queries") or [] if str(q).strip()
    ]
    wiki = answer.get("wikipedia")
    return [q for q in queries if 3 <= len(q) <= 120][:4], (
        str(wiki).strip() or None
    ) if wiki else None


# ---------- поиск ----------


def _client(timeout: float) -> Any:
    import httpx

    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"},
        timeout=httpx.Timeout(timeout, connect=min(4.0, timeout)),
        follow_redirects=True,
    )


def search_ddg(client: Any, query: str, language: str = "ru") -> list[WebPage]:
    """Выдача DuckDuckGo (HTML-версия без JavaScript): заголовок, адрес, фрагмент."""
    from lxml import html

    region = "ru-ru" if language.lower().startswith("ru") else "us-en"
    response = client.post(DDG_URL, data={"q": query, "kl": region})
    if response.status_code != 200:
        raise RuntimeError(f"DuckDuckGo ответил {response.status_code}")
    tree = html.fromstring(response.text)
    pages: list[WebPage] = []
    for result in tree.xpath(
        '//div[contains(concat(" ", normalize-space(@class), " "), " result ")]'
    ):
        if "result--ad" in (result.get("class") or ""):
            continue
        link = result.xpath('.//a[@class="result__a"]')[0]
        url = _real_url(link.get("href") or "")
        if not url:
            continue
        snippet = result.xpath('.//a[@class="result__snippet"]') or result.xpath(
            './/*[contains(@class, "result__snippet")]'
        )
        pages.append(
            WebPage(
                url=url,
                title=_squash(link.text_content()),
                snippet=_squash(snippet[0].text_content()) if snippet else "",
            )
        )
    return pages


def _real_url(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parts = urllib.parse.urlsplit(href)
    if parts.netloc.endswith("duckduckgo.com") and parts.path.startswith("/l/"):
        target = urllib.parse.parse_qs(parts.query).get("uddg") or [""]
        href = target[0]
        parts = urllib.parse.urlsplit(href)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    if parts.netloc.endswith("duckduckgo.com"):
        return ""
    return href


def search_wikipedia(client: Any, query: str, language: str = "ru") -> WebPage | None:
    """Первая статья Википедии по запросу: чистый текст без разметки через API."""
    lang = "ru" if language.lower().startswith("ru") else "en"
    api = f"https://{lang}.wikipedia.org/w/api.php"
    found = client.get(
        api,
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": 1,
            "format": "json",
        },
    ).json()
    hits = ((found.get("query") or {}).get("search")) or []
    if not hits:
        return None
    title = str(hits[0].get("title") or "")
    data = client.get(
        api,
        params={
            "action": "query",
            "prop": "extracts",
            "explaintext": 1,
            "titles": title,
            "format": "json",
            "redirects": 1,
        },
    ).json()
    pages = ((data.get("query") or {}).get("pages")) or {}
    text = next((str(p.get("extract") or "") for p in pages.values()), "")
    if not text:
        return None
    url = f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
    page = WebPage(url=url, title=f"{title} — Википедия" if lang == "ru" else title)
    page.paragraphs = [
        _squash(p) for p in text.split("\n") if len(p.strip()) >= 60 and not p.startswith("==")
    ]
    return page


# ---------- страницы ----------


def fetch_page(client: Any, page: WebPage) -> WebPage:
    """Абзацы страницы: текст `<p>` без меню, подвала и скриптов."""
    from lxml import html

    with client.stream("GET", page.url) as response:
        if response.status_code != 200:
            raise RuntimeError(f"{response.status_code}")
        kind = response.headers.get("content-type", "")
        if "html" not in kind:
            raise RuntimeError(f"не HTML: {kind[:40]}")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_PAGE_BYTES:
                break
        raw = b"".join(chunks)
        charset = response.charset_encoding
    text = _decode(raw, charset)
    tree = html.fromstring(text)
    for bad in tree.xpath(
        "//script|//style|//noscript|//nav|//header|//footer|//aside|//form|//iframe|//svg"
        '|//*[contains(@class, "comment")]|//*[contains(@class, "footer")]'
        '|//*[contains(@class, "sidebar")]|//*[contains(@class, "menu")]'
    ):
        parent = bad.getparent()
        if parent is not None:
            parent.remove(bad)
    if not page.title:
        titles = tree.xpath("//title")
        page.title = _squash(titles[0].text_content()) if titles else page.domain
    paragraphs: list[str] = []
    for node in tree.xpath("//p|//li[not(.//p)]"):
        value = _squash(node.text_content())
        if len(value) < 60:
            continue
        links = sum(len(_squash(a.text_content())) for a in node.xpath(".//a"))
        if links > len(value) * 0.5:
            continue
        paragraphs.append(value)
    page.paragraphs = list(dict.fromkeys(paragraphs))
    return page


def _decode(raw: bytes, charset: str | None) -> str:
    if not charset:
        head = raw[:4096].decode("ascii", "ignore")
        match = re.search(r"""charset=["']?([A-Za-z0-9_-]+)""", head)
        charset = match.group(1) if match else "utf-8"
    try:
        return raw.decode(charset, "replace")
    except LookupError:
        return raw.decode("utf-8", "replace")


def _squash(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


# ---------- отбор ----------


def score(paragraph: str, topic: set[str]) -> float:
    """Близость абзаца к теме: сколько предметных слов темы и запросов в нём есть (до трёх),
    плюс за числа и за длину законченной мысли. Проходной балл — два слова или одно с числом."""
    if not topic or BOILERPLATE.search(paragraph):
        return 0.0
    hits = len(topic & stems(paragraph))
    if not hits:
        return 0.0
    value = min(hits, 3) / 3
    if _NUMBER.search(paragraph):
        value += 0.34
    if 120 <= len(paragraph) <= 600:
        value += 0.05
    return value


def select(page: WebPage, topic: set[str], limit: int, max_len: int = 600) -> list[str]:
    """Лучшие абзацы страницы в исходном порядке; длинные — первыми предложениями."""
    ranked = sorted(
        ((score(p, topic), i, p) for i, p in enumerate(page.paragraphs)),
        key=lambda item: (-item[0], item[1]),
    )
    chosen = [(i, p) for s, i, p in ranked if s >= 0.66][:limit]
    return [_first_sentences(p, max_len) for _i, p in sorted(chosen)]


def _first_sentences(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    out = ""
    for sentence in _SENTENCE.split(text):
        if len(out) + len(sentence) + 1 > limit:
            break
        out = f"{out} {sentence}".strip()
    return out or text[:limit].rsplit(" ", 1)[0] + "…"


# ---------- сборка ----------


def research(
    brief: JsonDict,
    settings: ResearchSettings | Research | None = None,
    *,
    cache_dir: pathlib.Path | None = None,
    client: Any = None,
    llm_client: Any = None,
    skill: Any = None,
) -> ResearchResult:
    """Документы пакета из найденных страниц: (источник, разобранный документ) на страницу.
    С моделью (`llm_client` и скилл web_researcher) запросы охватывают разные стороны темы;
    без неё — тема как есть и её предметные слова."""
    settings = settings or ResearchSettings()
    started = time.perf_counter()
    report: JsonDict = {
        "version": RESEARCH_VERSION,
        "queries": [],
        "pages": [],
        "errors": [],
        "cache": "miss",
    }
    queries = queries_for(brief)
    if not settings.enabled or not queries:
        report["skipped"] = "disabled" if not settings.enabled else "no_topic"
        return ResearchResult([], report)
    wiki_title: str | None = None
    if llm_client is not None and skill is not None:
        try:
            asked, wiki_title = model_queries(brief, llm_client, skill)
            if asked:
                # Первый запрос — тема как есть: выдача по ней самая близкая к замыслу.
                queries = list(dict.fromkeys([queries[0], *asked]))
                report["queries_by"] = "model"
        except Exception as e:
            report["errors"].append(f"запросы моделью: {type(e).__name__}: {e}"[:200])
    language = str(brief.get("language") or "ru")
    report["queries"] = queries
    if wiki_title:
        report["wikipedia_title"] = wiki_title
    key = hashlib.sha256(
        json.dumps(
            {
                "v": RESEARCH_VERSION,
                "q": queries,
                "w": wiki_title,
                "lang": language,
                "s": _plain(settings),
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    cached = _cache_get(cache_dir, key)
    if cached is not None:
        pages = [WebPage(**p) for p in cached]
        report["cache"] = "hit"
    else:
        own = client is None
        client = client or _client(settings.page_timeout_s)
        try:
            pages = _collect(client, queries, language, settings, report, started, wiki_title)
        finally:
            if own:
                client.close()
        if pages:
            _cache_put(cache_dir, key, [p.as_dict() for p in pages])
    documents = _documents(pages, brief, settings, queries)
    report["pages"] = [
        {"url": source["url"], "title": source["name"], "paragraphs": len(doc.blocks) - 1}
        for source, doc in documents
    ]
    report["total_ms"] = int((time.perf_counter() - started) * 1000)
    return ResearchResult(documents, report)


def _collect(
    client: Any,
    queries: list[str],
    language: str,
    settings: ResearchSettings | Research,
    report: JsonDict,
    started: float,
    wiki_title: str | None = None,
) -> list[WebPage]:
    # Выдачи запросов перемежаются: первые места каждого запроса раньше хвоста первого, иначе
    # все страницы — пересказы одной новости по самому буквальному запросу.
    found_by_query: list[list[WebPage]] = []
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    try:
        searches = [pool.submit(search_ddg, client, query, language) for query in queries]
        for query, search in zip(queries, searches, strict=True):
            try:
                found_by_query.append(search.result(timeout=settings.search_timeout_s + 2))
            except Exception as e:  # сеть, капча, разметка поменялась
                report["errors"].append(f"поиск «{query}»: {type(e).__name__}: {e}"[:200])
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    candidates: list[WebPage] = []
    seen: set[str] = set()
    depth = max((len(found) for found in found_by_query), default=0)
    for place in range(depth):
        for found in found_by_query:
            if place >= len(found):
                continue
            page = found[place]
            if page.domain in seen or _skipped(page.url):
                continue
            seen.add(page.domain)
            candidates.append(page)
    wiki: WebPage | None = None
    if settings.wikipedia:
        try:
            wiki = search_wikipedia(
                client, wiki_title or _wiki_query(queries[0]) or queries[0], language
            )
        except Exception as e:
            report["errors"].append(f"Википедия: {type(e).__name__}: {e}"[:200])
    budget = max(1.0, settings.budget_s - (time.perf_counter() - started))
    fetched: list[WebPage] = []
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=6)
    futures = {
        pool.submit(fetch_page, client, page): page for page in candidates[: settings.max_pages + 3]
    }
    try:
        for future in concurrent.futures.as_completed(futures, timeout=budget):
            page = futures[future]
            try:
                fetched.append(future.result())
            except Exception as e:
                report["errors"].append(f"{page.domain}: {type(e).__name__}: {e}"[:200])
    except concurrent.futures.TimeoutError:
        report["errors"].append("бюджет времени на страницы исчерпан")
    finally:
        # Зависшая страница не держит импорт: её поток доживает свой таймаут в фоне.
        pool.shutdown(wait=False, cancel_futures=True)
    # Порядок — как в выдаче: первые результаты обычно ближе к теме.
    rank = {page.url: i for i, page in enumerate(candidates)}
    fetched.sort(key=lambda page: rank.get(page.url, len(rank)))
    pages = ([wiki] if wiki is not None else []) + fetched
    return pages


def _plain(settings: Any) -> JsonDict:
    dump = getattr(settings, "model_dump", None)
    return dict(dump()) if callable(dump) else dict(vars(settings))


def _wiki_query(topic: str) -> str:
    """Для Википедии — слова темы без служебных: «отказа сладких продуктов»."""
    words = [w for w in _WORD.findall(topic) if w.lower() not in STOP_WORDS and len(w) > 2]
    return " ".join(words[:6])


def _skipped(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    host = parts.netloc.lower()
    if any(host == d or host.endswith("." + d) or d in host for d in SKIP_DOMAINS):
        return True
    return parts.path.lower().endswith(SKIP_EXTENSIONS)


def _documents(
    pages: list[WebPage], brief: JsonDict, settings: Any, queries: list[str] | None = None
) -> list[tuple[JsonDict, ParsedDocument]]:
    topic = stems(topic_of(brief)) | stems(" ".join(queries or []))
    out: list[tuple[JsonDict, ParsedDocument]] = []
    total = 0
    for page in pages:
        if len(out) >= settings.max_pages or total >= settings.max_chars:
            break
        chosen = select(page, topic, settings.paragraphs_per_page)
        chosen = [p for p in chosen if total + len(p) <= settings.max_chars or not out]
        if not chosen:
            continue
        doc = ParsedDocument("url")
        doc.blocks.append(
            ParsedBlock("heading", text=page.title or page.domain, level=2, tags=["web"])
        )
        offset = 0
        for paragraph in chosen:
            doc.blocks.append(
                ParsedBlock(
                    "paragraph",
                    text=paragraph,
                    location=Location(char_offset=offset),
                    tags=["web"],
                )
            )
            offset += len(paragraph) + 1
            total += len(paragraph)
        doc.units = {"chars": doc.text_chars}
        source = {
            "kind": "url",
            "name": page.title or page.domain,
            "url": page.url,
            "domain": page.domain,
            "extracted": True,
            "warnings": [],
            "parser": {"name": RESEARCH_NAME, "version": RESEARCH_VERSION},
            "units": {"chars": doc.text_chars},
        }
        out.append((source, doc))
    return out


# ---------- кэш ----------


# ---------- данные для правки слайда ----------

# Служебные слова просьбы из чата: поиску нужен предмет, а не «добавь на слайд».
_REQUEST_NOISE = re.compile(
    r"(?i)\b(добавь|добавьте|вставь|вставьте|покажи|покажите|приведи|приведите|дай|дайте|"
    r"найди|найдите|напиши|напишите|укажи|укажите|поставь|поставьте|сделай|сделайте|"
    r"пожалуйста|сюда|на\s+слайд\w*|в\s+слайд\w*|этот|эту|это)\b"
)


def evidence_query(request: str, topic: str = "") -> str:
    """Запрос поиска из просьбы: предмет без служебных слов; слишком общий — с темой."""
    query = re.sub(r"\s+", " ", _REQUEST_NOISE.sub(" ", request)).strip(" ,.:;—-")
    if len(stems(query)) < 2 and topic:
        query = f"{query} {topic}".strip()
    return query[:200]


def evidence(
    request: str,
    settings: Any = None,
    *,
    topic: str = "",
    language: str = "ru",
    cache_dir: pathlib.Path | None = None,
    budget_s: float = 12.0,
    limit: int = 6,
    client: Any = None,
) -> list[JsonDict]:
    """Абзацы с числами из интернета по просьбе пользователя — для правки слайда из чата:
    просят статистику, которой нет в материалах, и вместо «пришлите цифры» система ищет сама.
    Каждый абзац — с адресом и заголовком страницы: на слайд цифра идёт с источником."""
    if settings is not None and not getattr(settings, "enabled", True):
        return []
    query = evidence_query(request, topic)
    if not stems(query):
        return []
    light = ResearchSettings(
        max_pages=4,
        paragraphs_per_page=3,
        search_timeout_s=float(getattr(settings, "search_timeout_s", 8.0)),
        page_timeout_s=float(getattr(settings, "page_timeout_s", 7.0)),
        budget_s=budget_s,
        wikipedia=False,
    )
    key = hashlib.sha256(
        json.dumps(
            {"v": RESEARCH_VERSION, "evidence": query, "lang": language},
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    cached = _cache_get(cache_dir, key)
    if cached is not None:
        pages = [WebPage(**p) for p in cached]
    else:
        report: JsonDict = {"errors": []}
        own = client is None
        client = client or _client(light.page_timeout_s)
        try:
            pages = _collect(client, [query], language, light, report, time.perf_counter())
        except Exception as e:
            log.warning("поиск данных для правки не удался: %s", e)
            pages = []
        finally:
            if own:
                client.close()
        if pages:
            _cache_put(cache_dir, key, [p.as_dict() for p in pages])
    topic_stems = stems(query)
    found: list[JsonDict] = []
    for page in pages:
        for paragraph in select(page, topic_stems, limit=2, max_len=400):
            if _NUMBER.search(paragraph):
                found.append(
                    {"text": paragraph, "url": page.url, "title": page.title or page.domain}
                )
    return found[:limit]


def _cache_get(cache_dir: pathlib.Path | None, key: str) -> list[JsonDict] | None:
    if cache_dir is None:
        return None
    path = cache_dir / "web" / f"{key}.json"
    try:
        if time.time() - path.stat().st_mtime > CACHE_TTL_S:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


def _cache_put(cache_dir: pathlib.Path | None, key: str, pages: list[JsonDict]) -> None:
    if cache_dir is None:
        return
    folder = cache_dir / "web"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / f"{key}.tmp"
        tmp.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
        tmp.replace(folder / f"{key}.json")
    except OSError as e:
        log.warning("кэш поиска не записан: %s", e)


__all__ = [
    "RESEARCH_NAME",
    "RESEARCH_VERSION",
    "ResearchResult",
    "ResearchSettings",
    "WebPage",
    "evidence",
    "evidence_query",
    "queries_for",
    "research",
    "score",
    "select",
    "stems",
]
