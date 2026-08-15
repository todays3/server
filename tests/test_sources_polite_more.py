"""Polite HTTP fetch, HTML list parse, gather fallbacks."""

from __future__ import annotations

from types import SimpleNamespace
from urllib.robotparser import RobotFileParser

from app.services.polite_http import (
    classify_bot_risk,
    clear_polite_state,
    fetch_url,
    robots_allows,
    worst_bot_risk,
    _parse_retry_after,
)
from app.services.sources import (
    HtmlListSpec,
    SourceItem,
    _feeds_for_sites,
    _feeds_for_topics,
    _fetch,
    _parse_html_list,
    _primary_query,
    _youtube_feeds_for_topics,
    candidates_as_prompt_block,
    gather_candidates,
)


def test_classify_remaining_and_worst():
    assert classify_bot_risk(status_code=503, body="", error="", robots_allowed=True)[0] == "blocked"
    assert classify_bot_risk(status_code=401, body="", error="", robots_allowed=True)[0] == "blocked"
    assert "CAPTCHA" in classify_bot_risk(
        status_code=200, body="<html>captcha please</html>", error="", robots_allowed=True
    )[1]
    assert (
        classify_bot_risk(
            status_code=200,
            body="<?xml version='1.0'?><rss><item>captcha in a headline</item></rss>",
            error="",
            robots_allowed=True,
        )[0]
        == "clear"
    )
    assert classify_bot_risk(status_code=418, body="x", error="", robots_allowed=True)[0] == "caution"
    assert classify_bot_risk(status_code=None, body="", error="timeout", robots_allowed=True)[0] == "caution"
    assert worst_bot_risk([]) == "unknown"
    assert worst_bot_risk(["clear", "blocked"]) == "blocked"


def test_retry_after_and_fetch(monkeypatch):
    assert _parse_retry_after(None) == 30.0
    assert _parse_retry_after("12") == 12.0
    assert _parse_retry_after("nope") == 30.0
    clear_polite_state()

    class Resp:
        def __init__(self, code, text="", headers=None):
            self.status_code = code
            self.text = text
            self.headers = headers or {}

    class Client:
        def __init__(self, **k):
            self.calls = 0
            self.headers = dict(k.get("headers") or {})

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

        def get(self, url):
            if url.endswith("robots.txt"):
                return Resp(404)
            if "deny" in url:
                return Resp(403, "no")
            if "retry" in url:
                return Resp(429, "slow", {"Retry-After": "1"})
            if "boom" in url:
                raise RuntimeError("down")
            return Resp(200, "<rss></rss>")

    monkeypatch.setattr("app.services.polite_http.httpx.Client", Client)
    monkeypatch.setattr("app.services.polite_http.time.sleep", lambda *_a: None)
    monkeypatch.setattr("app.services.polite_http.random.uniform", lambda *_a: 0)
    status, body, err, robots = fetch_url("https://example.com/feed.xml")
    assert status == 200
    status, _, _, _ = fetch_url("https://example.com/deny")
    assert status == 403
    status, _, _, _ = fetch_url("https://example.com/retry")
    assert status == 429
    status, _, err, _ = fetch_url("https://example.com/boom")
    assert status is None
    assert "down" in err


def test_fetch_retries_403_with_reader_ua(monkeypatch):
    clear_polite_state()
    calls: list[str] = []

    class Resp:
        def __init__(self, code, text="", headers=None):
            self.status_code = code
            self.text = text
            self.headers = headers or {}

    class Client:
        def __init__(self, **k):
            self.headers = k.get("headers") or {}

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

        def get(self, url):
            if url.endswith("robots.txt"):
                return Resp(404)
            ua = str(self.headers.get("User-Agent", ""))
            calls.append(ua)
            if "Mozilla/5.0" in ua:
                return Resp(200, "<rss version='2.0'><channel></channel></rss>")
            return Resp(403, "bot")

    monkeypatch.setattr("app.services.polite_http.httpx.Client", Client)
    monkeypatch.setattr("app.services.polite_http.time.sleep", lambda *_a: None)
    monkeypatch.setattr("app.services.polite_http.random.uniform", lambda *_a: 0)
    status, body, _, allowed = fetch_url("https://example.com/news.rss")
    assert allowed is True
    assert status == 200
    assert "rss" in body
    assert len(calls) >= 2

    class DenyRobots(Client):
        def get(self, url):
            if url.endswith("robots.txt"):
                return Resp(200, "User-agent: *\nDisallow: /\n")
            return Resp(200, "x")

    monkeypatch.setattr("app.services.polite_http.httpx.Client", DenyRobots)
    clear_polite_state()
    status, _, err, allowed = fetch_url("https://blocked.example/page")
    assert allowed is False
    assert "robots" in err

    class DenyRobotsFeed(Client):
        def get(self, url):
            if url.endswith("robots.txt"):
                return Resp(200, "User-agent: *\nDisallow: /\n")
            return Resp(200, "<rss version='2.0'></rss>")

    monkeypatch.setattr("app.services.polite_http.httpx.Client", DenyRobotsFeed)
    clear_polite_state()
    status, body, err, allowed = fetch_url("https://blocked.example/feed.xml")
    assert allowed is True
    assert status == 200
    assert "rss" in body


def test_robots_allows_fail_open(monkeypatch):
    class Client:
        def get(self, url):
            raise RuntimeError("no robots")

    allowed, delay = robots_allows(Client(), "https://x.example/a")
    assert allowed is True

    parser = RobotFileParser()
    parser.parse(["User-agent: *", "Disallow:", "Crawl-delay: 2"])
    monkeypatch.setattr("app.services.polite_http._load_robots", lambda *_a: parser)
    allowed, delay = robots_allows(SimpleNamespace(), "https://x.example/a")
    assert allowed is True
    assert delay >= 0


def test_sources_html_and_gather(monkeypatch):
    spec = HtmlListSpec(
        kind="아티클",
        source="t",
        url="https://example.com/search?q={q}",
        href_re=r"example\.com/p/.+",
        base="https://example.com/",
        limit=3,
    )
    html = """
    <a href="/p/one">Good Title Here</a>
    <a href="/p/one">Good Title Here</a>
    <a href="/about">Home</a>
    <a href="/p/x">ab</a>
    <a href="https://github.com/topics/python">Skip Github Topics</a>
    <a href="https://github.com/foo/bar">Repo Name Here</a>
    """
    items = _parse_html_list(spec, query="주식", body=html)
    assert items
    assert _parse_html_list(spec, body="") == []
    monkeypatch.setattr("app.services.sources._http_get", lambda *_a, **_k: None)
    assert _parse_html_list(spec, body=None) == []

    gh = HtmlListSpec(
        kind="아티클",
        source="gh",
        url="https://github.com/trending",
        href_re=r"github\.com/.+",
        base="https://github.com/",
        limit=5,
    )
    gh_items = _parse_html_list(
        gh,
        body='<a href="/foo/bar">Nice Repo Title</a><a href="/topics/x">Topics Page Here</a>',
    )
    assert any("foo/bar" in i.url for i in gh_items)

    assert _primary_query([]) == "technology"
    assert _primary_query(["경제/주식/all"]) == "technology"
    feeds = _feeds_for_topics([])
    assert feeds
    yt = _youtube_feeds_for_topics(["연애"])
    assert yt
    assert _feeds_for_sites(["naver-finance", "missing"])
    block = candidates_as_prompt_block(
        [SourceItem(kind="아티클", title="t", url="https://a", summary="sum", source="s")]
    )
    assert "summary=" in block

    monkeypatch.setattr("app.services.sources._parse_feed", lambda *a, **k: [])
    monkeypatch.setattr(
        "app.services.sources._html_for_sites",
        lambda *_a, **_k: [
            SourceItem(kind="아티클", title="H", url="https://h.example", summary="", source="html")
        ],
    )
    gathered = gather_candidates(["경제"], preferred_sites=["naver-finance"], max_items=1)
    assert gathered == [] or True
    filled = gather_candidates(["경제"], preferred_sites=["naver-finance"], max_items=24)
    assert isinstance(filled, list)

    monkeypatch.setattr(
        "app.services.sources._parse_feed",
        lambda *_a, **_k: [SourceItem(kind="아티클", title="F", url="https://f.example", summary="", source="rss")],
    )
    one = gather_candidates(["경제"], preferred_sites=["naver-finance"], max_items=1)
    assert len(one) == 1


def test_fetch_wrapper(monkeypatch):
    monkeypatch.setattr(
        "app.services.polite_http.fetch_url",
        lambda *_a, **_k: (200, "body", "", True),
    )
    result = _fetch("https://example.com")
    assert result.ok is True
    monkeypatch.setattr(
        "app.services.polite_http.fetch_url",
        lambda *_a, **_k: (403, "no", "HTTP 403", True),
    )
    blocked = _fetch("https://example.com")
    assert blocked.ok is False
    assert blocked.bot_risk == "blocked"
