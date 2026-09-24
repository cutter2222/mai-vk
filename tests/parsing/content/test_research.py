"""Материалы из интернета для темы без материалов: разбор выдачи, отбор абзацев, кэш и место
найденного в контент-пакете. Сеть подменена: тесты не ходят в интернет."""

from __future__ import annotations

import pathlib
from contextlib import contextmanager
from typing import Any

import pytest

from presentation_designer.generation.grounding import topic_only
from presentation_designer.parsing.content import research as rs
from presentation_designer.parsing.content.importer import import_content

DDG_HTML = """
<html><body><div id="links">
  <div class="result results_links result--ad">
    <h2><a class="result__a" href="https://shop.example/ad">Купите сахарозаменитель</a></h2>
  </div>
  <div class="result results_links web-result">
    <div class="links_main result__body">
      <h2 class="result__title"><a class="result__a"
         href="https://news.example/sugar-study">Отказ от сладкого: исследование</a></h2>
      <a class="result__snippet" href="https://news.example/sugar-study">Учёные проверили</a>
    </div>
  </div>
  <div class="result results_links web-result">
    <h2><a class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwho.example%2Fsugars&rut=x"
       >ВОЗ о сахарах</a></h2>
  </div>
  <div class="result results_links web-result">
    <h2><a class="result__a" href="https://www.youtube.com/watch?v=1">Видео</a></h2>
  </div>
</div></body></html>
"""

PAGES = {
    "https://news.example/sugar-study": """
<html><head><title>Отказ от сладкого не снижает тягу</title></head><body>
<nav><p>Главная Новости Здоровье Питание Спорт Подписаться на рассылку сайта сегодня</p></nav>
<article>
<p>Учёные набрали 180 участников и полгода меняли долю сладких продуктов в их рационе,
чтобы проверить, снижается ли тяга к сладкому.</p>
<p>Отказ от сладких продуктов не изменил ни вес, ни тягу к сладкому: участники вернулись к
привычному уровню потребления после эксперимента.</p>
<p>Мы используем cookie, чтобы сайт работал лучше, продолжая, вы соглашаетесь с правилами.</p>
</article>
<footer><p>Все права защищены, перепечатка материалов сладких продуктов запрещена 2026</p></footer>
</body></html>
""",
    "https://who.example/sugars": """
<html><head><title>ВОЗ о сахарах</title></head><body>
<p>ВОЗ рекомендует сократить потребление свободных сахаров и сладких продуктов до менее
чем 10% от суммарной калорийности рациона взрослых и детей.</p>
<p>Короткий абзац.</p>
</body></html>
""",
}


class FakeResponse:
    def __init__(self, text: str = "", status: int = 200, json_data: Any = None) -> None:
        self.text = text
        self.status_code = status
        self._json = json_data
        self.headers = {"content-type": "text/html; charset=utf-8"}
        self.charset_encoding = "utf-8"

    def json(self) -> Any:
        return self._json

    def iter_bytes(self) -> Any:
        yield self.text.encode("utf-8")


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def post(self, url: str, data: Any = None) -> FakeResponse:
        self.calls.append(f"search:{data['q']}")
        return FakeResponse(DDG_HTML)

    def get(self, url: str, params: Any = None) -> FakeResponse:
        self.calls.append(f"wiki:{(params or {}).get('action')}")
        return FakeResponse(json_data={"query": {"search": []}})

    @contextmanager
    def stream(self, method: str, url: str) -> Any:
        self.calls.append(f"page:{url}")
        if url not in PAGES:
            yield FakeResponse(status=404)
            return
        yield FakeResponse(PAGES[url])

    def close(self) -> None:
        pass


BRIEF = {"title": "Исследование полезности отказа от сладких продуктов", "language": "ru"}


def test_ddg_results_skip_ads_and_unwrap_redirects() -> None:
    pages = rs.search_ddg(FakeClient(), "сахар")
    urls = [p.url for p in pages]
    assert "https://shop.example/ad" not in urls, "реклама не берётся"
    assert "https://who.example/sugars" in urls, "ссылка-переход DuckDuckGo раскрыта"
    assert rs._skipped("https://www.youtube.com/watch?v=1"), "видео без текста пропускается"


def test_page_paragraphs_drop_menu_cookies_and_footer() -> None:
    page = rs.fetch_page(FakeClient(), rs.WebPage("https://news.example/sugar-study", ""))
    assert page.title == "Отказ от сладкого не снижает тягу"
    chosen = rs.select(page, rs.stems(BRIEF["title"]), limit=4)
    assert len(chosen) == 2
    assert all("cookie" not in p and "права" not in p for p in chosen)
    assert chosen[0].startswith("Учёные набрали 180")


def test_queries_drop_service_words() -> None:
    queries = rs.queries_for(BRIEF)
    assert queries[0] == BRIEF["title"]
    assert "полезности" not in queries[1] and "сладких" in queries[1]


def test_research_builds_url_sources_and_caches(tmp_path: pathlib.Path) -> None:
    client = FakeClient()
    settings = rs.ResearchSettings(wikipedia=False)
    result = rs.research(BRIEF, settings, cache_dir=tmp_path, client=client)
    assert [s["url"] for s, _ in result.documents] == [
        "https://news.example/sugar-study",
        "https://who.example/sugars",
    ]
    source, doc = result.documents[0]
    assert source["kind"] == "url" and source["domain"] == "news.example"
    assert doc.blocks[0].kind == "heading" and all("web" in b.tags for b in doc.blocks)
    again = rs.research(BRIEF, settings, cache_dir=tmp_path, client=FakeClient())
    assert again.report["cache"] == "hit"
    assert [s["url"] for s, _ in again.documents] == [s["url"] for s, _ in result.documents]


def test_research_survives_network_failure() -> None:
    class Broken(FakeClient):
        def post(self, url: str, data: Any = None) -> FakeResponse:
            raise ConnectionError("нет сети")

    result = rs.research(BRIEF, rs.ResearchSettings(wikipedia=False), client=Broken())
    assert result.documents == [] and result.report["errors"]


def test_import_with_research_is_not_a_concept(monkeypatch: pytest.MonkeyPatch) -> None:
    real = rs.research

    def fake_research(brief: Any, settings: Any, **_: Any) -> rs.ResearchResult:
        return real(brief, rs.ResearchSettings(wikipedia=False), client=FakeClient())

    monkeypatch.setattr(rs, "research", fake_research)
    plain = import_content([], dict(BRIEF), package_id="pkg_plain")
    assert topic_only(plain.package), "без поиска тема остаётся концепцией"
    found = import_content([], dict(BRIEF), package_id="pkg_web", research=True, use_model=False)
    package = found.package
    assert not topic_only(package)
    web = [s for s in package["sources"] if s["kind"] == "url"]
    assert [s["url"] for s in web] == [
        "https://news.example/sugar-study",
        "https://who.example/sugars",
    ]
    web_ids = {s["source_id"] for s in web}
    facts = [f for f in package["facts"] if f["source_id"] in web_ids]
    assert facts, "числа страниц стали фактами"
    assert not any(f["must_keep"] for f in facts), "факт из выдачи — опора, не обязательство"


def test_import_with_files_does_not_search(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_: Any, **__: Any) -> Any:
        raise AssertionError("поиск при заметках брифа не нужен")

    monkeypatch.setattr(rs, "research", forbidden)
    brief = {**BRIEF, "notes": "Свои материалы: сахар и тяга к сладкому, опрос 40 человек."}
    result = import_content([], brief, package_id="pkg_notes", research=True, use_model=False)
    assert not any(s["kind"] == "url" for s in result.package["sources"])
