from contextlib import contextmanager
from types import SimpleNamespace

from pipeline.collect import from_scrape_links

SOURCE = {
    "id": "lab",
    "name": "Lab",
    "url": "https://lab.example/news",
    "base": "https://lab.example",
    "href_prefix": "/news/",
    "max_items": 2,
}


def _scrape(monkeypatch, page: str) -> list[dict]:
    @contextmanager
    def client():
        yield None

    monkeypatch.setattr("pipeline.collect.get_client", client)
    monkeypatch.setattr(
        "pipeline.collect.fetch",
        lambda url, client: SimpleNamespace(text=page, raise_for_status=lambda: None),
    )
    return from_scrape_links(SOURCE)


def test_scrape_unescapes_titles_without_shadowing_the_html_module(monkeypatch):
    """응답 본문을 html 변수에 담아 html.unescape가 실패했다 (2026-09-01~11-05 수집 중단)."""
    items = _scrape(monkeypatch, '<a href="/news/first-post">A long enough &amp;amp; title</a>')
    assert (items[0]["title"], items[0]["published"]) == ("A long enough & title", None)


def test_scrape_keeps_newest_dated_cards_and_drops_undated_navigation(monkeypatch):
    page = """
    <a href="/news/product">Product</a>
    <a href="/news/older-post">Older post title</a>
    <ul>
      <li><time>Oct 1, 2026</time><a href="/news/policy">Policy</a><a href="/news/older-post">Older post title</a></li>
      <li><time>Oct 3, 2026</time><a href="/news/newest-post">Newest post title</a></li>
      <li><time>Oct 2, 2026</time><a href="/news/middle-post">Middle post title</a></li>
    </ul>
    """
    items = _scrape(monkeypatch, page)
    assert [(i["url"].rsplit("/", 1)[1], i["published"][:10]) for i in items] == [
        ("newest-post", "2026-10-03"),
        ("middle-post", "2026-10-02"),
    ]


def test_scrape_replaces_generic_link_text_with_a_title_from_the_slug(monkeypatch):
    items = _scrape(monkeypatch, '<a href="/news/new-model-launch/">Learn More</a>')
    assert items[0]["title"] == "New Model Launch"
