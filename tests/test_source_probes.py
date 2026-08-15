"""Per-site collector probe tests (mocked HTTP)."""

from __future__ import annotations

import pytest

from app.services.source_probe import list_probe_snapshot, probe_all_sites, probe_site, summarize
from app.services.sources import FetchResult, collector_site_ids, site_html_catalog
from app.services import source_probe as probe_mod

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Sample</title>
    <item>
      <title>First sample headline</title>
      <link>https://example.com/p/1</link>
      <description>hello</description>
    </item>
    <item>
      <title>Second sample headline</title>
      <link>https://example.com/p/2</link>
      <description>world</description>
    </item>
  </channel>
</rss>
"""

# YouTube RSS keeps items only when media:statistics views >= 10_000.
SAMPLE_YOUTUBE_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
  <title>Sample channel</title>
  <entry>
    <title>Popular sample video</title>
    <link href="https://www.youtube.com/watch?v=hitfixture01"/>
    <media:group>
      <media:community>
        <media:statistics views="25000"/>
      </media:community>
    </media:group>
  </entry>
</feed>
"""

HTML_FIXTURES: dict[str, str] = {
    "github-trending": '<html><body><a href="/octocat/hello-world">Hello World Repo</a></body></html>',
    "geeksforgeeks": '<html><body><a href="https://www.geeksforgeeks.org/python-tutorial/">Python Tutorial Guide</a></body></html>',
    "stackoverflow": '<html><body><a href="/questions/12345/how-to-test-this">How to test this thing</a></body></html>',
    "hardbattle": '<html><body><a href="https://www.hwbattle.com/news/123-gpu">New GPU battle review</a></body></html>',
    "outstanding": '<html><body><a href="https://outstanding.kr/brand-story">Outstanding brand story</a></body></html>',
    "itchosun": '<html><body><a href="https://it.chosun.com/news/article.html">IT Chosun daily news</a></body></html>',
    "clien": '<html><body><a href="/service/board/news/1888123">Clien news title here</a></body></html>',
    "quantstart": '<html><body><a href="https://www.quantstart.com/articles/algo-intro">Algo trading intro</a></body></html>',
}


def _html_site_for_url(url: str) -> str | None:
    for site_id, specs in site_html_catalog().items():
        for spec in specs:
            if "{q}" in spec.url:
                prefix = spec.url.split("{q}")[0]
                if url.startswith(prefix):
                    return site_id
            elif url.rstrip("/") == spec.url.rstrip("/"):
                return site_id
    return None


def _ok_fetch(url: str, **kwargs: object) -> FetchResult:
    site_id = _html_site_for_url(url)
    if site_id:
        return FetchResult(url, True, 200, HTML_FIXTURES[site_id], "")
    if "youtube.com" in url:
        return FetchResult(url, True, 200, SAMPLE_YOUTUBE_ATOM, "")
    return FetchResult(url, True, 200, SAMPLE_RSS, "")


def _fail_fetch(url: str, **kwargs: object) -> FetchResult:
    return FetchResult(url, False, 403, "", "HTTP 403")


@pytest.fixture(autouse=True)
def _clear_probe_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "")
    from app.config import get_settings

    get_settings.cache_clear()
    probe_mod._cache.clear()
    yield
    probe_mod._cache.clear()
    get_settings.cache_clear()


@pytest.mark.parametrize("site_id", sorted(collector_site_ids()))
def test_probe_site_succeeds_with_fixture_payload(site_id: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(probe_mod, "_fetch", _ok_fetch)
    result = probe_site(site_id)
    assert result.site_id == site_id
    assert result.ok is True
    assert result.item_count >= 1
    assert result.feeds
    assert any(feed.ok for feed in result.feeds)
    assert any(feed.sample_titles for feed in result.feeds if feed.ok)
    assert result.bot_risk == "clear"


@pytest.mark.parametrize("site_id", sorted(collector_site_ids()))
def test_probe_site_reports_http_failure(site_id: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(probe_mod, "_fetch", _fail_fetch)
    result = probe_site(site_id)
    assert result.ok is False
    assert result.feeds
    assert all(not feed.ok for feed in result.feeds)
    assert all("403" in feed.error for feed in result.feeds)
    assert result.bot_risk == "blocked"
    assert all(feed.bot_risk == "blocked" for feed in result.feeds)


def test_probe_site_bot_risk_follows_successful_feed(monkeypatch: pytest.MonkeyPatch):
    def mixed_fetch(url: str, **kwargs: object) -> FetchResult:
        if "github.com/trending" in url or "github.com/search" in url:
            return FetchResult(url, False, 403, "", "HTTP 403", bot_risk="blocked", bot_signal="403")
        return FetchResult(url, True, 200, SAMPLE_RSS, "", bot_risk="clear", bot_signal="ok")

    monkeypatch.setattr(probe_mod, "_fetch", mixed_fetch)
    result = probe_site("github-trending")
    assert result.ok is True
    assert result.bot_risk == "clear"


def test_probe_all_sites_covers_catalog(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(probe_mod, "_fetch", _ok_fetch)
    results = probe_all_sites()
    ids = {row.site_id for row in results}
    assert ids == collector_site_ids()
    assert all(row.ok for row in results)


def test_snapshot_unknown_before_probe():
    snap = list_probe_snapshot()
    assert snap
    assert all(row.ok is None for row in snap)
    ok_count, fail_count, unknown_count = summarize(snap)
    assert ok_count == 0
    assert fail_count == 0
    assert unknown_count == len(snap)
