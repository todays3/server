from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.freshness import is_fresh, published_at_from_text, resolve_published_at
from app.services.sources import SourceItem
from app.services import sources as sources_mod

SEOUL = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 8, 16, 17, 0, tzinfo=SEOUL)


def test_april_slash_date_in_title_is_stale_in_august():
    when = published_at_from_text("벨로그 트렌딩 4/25 회고", now=NOW)
    assert when is not None
    assert when.month == 4 and when.day == 25
    assert is_fresh(when, now=NOW) is False


def test_today_slash_date_is_fresh():
    when = published_at_from_text("속보 8/16 반도체", now=NOW)
    assert is_fresh(when, now=NOW) is True


def test_url_ymd_older_than_two_days_is_stale():
    when = published_at_from_text("https://blog.example/2026/04/25/post", now=NOW)
    assert is_fresh(when, now=NOW) is False


def test_undated_item_is_kept():
    assert is_fresh(None, now=NOW) is True


def test_hours_inside_window_are_fresh():
    recent = datetime(2026, 8, 15, 12, 0, tzinfo=SEOUL)
    assert is_fresh(recent, now=NOW) is True
    old = datetime(2026, 8, 13, 16, 0, tzinfo=SEOUL)
    assert is_fresh(old, now=NOW) is False


def test_rss_skips_april_post_and_keeps_today():
    raw = """<?xml version="1.0"?>
    <rss><channel>
      <item>
        <title>4/25 옛글</title>
        <link>https://velog.io/@a/old</link>
        <pubDate>Sat, 25 Apr 2026 09:00:00 +0900</pubDate>
      </item>
      <item>
        <title>오늘 동향</title>
        <link>https://velog.io/@a/now</link>
        <pubDate>Sun, 16 Aug 2026 08:00:00 +0900</pubDate>
      </item>
    </channel></rss>
    """
    items = sources_mod._parse_feed_body(
        "아티클",
        "velog",
        "https://example/rss",
        raw,
        limit=5,
        now=NOW,
    )
    assert [item.url for item in items] == ["https://velog.io/@a/now"]
    assert items[0].published_at is not None


def test_html_list_drops_titled_april_post():
    html = """
    <html><body>
      <a href="/@user/old-post">회고 4/25</a>
      <a href="/@user/fresh-post">오늘 본 스택 8/16</a>
    </body></html>
    """
    spec = sources_mod.HtmlListSpec(
        kind="아티클",
        source="velog",
        url="https://velog.io/trending",
        href_re=r"velog\.io/@",
        base="https://velog.io",
        limit=8,
    )
    items = sources_mod._parse_html_list(spec, body=html, now=NOW)
    assert [item.url for item in items] == ["https://velog.io/@user/fresh-post"]


def test_html_article_without_date_is_dropped():
    html = """
    <html><body>
      <a href="/@user/evergreen">인기 많은 옛글</a>
    </body></html>
    """
    spec = sources_mod.HtmlListSpec(
        kind="아티클",
        source="velog",
        url="https://velog.io/trending",
        href_re=r"velog\.io/@",
        base="https://velog.io",
        limit=8,
    )
    items = sources_mod._parse_html_list(spec, body=html, now=NOW)
    assert items == []


def test_source_item_default_published_at_none():
    item = SourceItem("아티클", "t", "https://a.example", "s", "src")
    assert item.published_at is None
    assert resolve_published_at(title="8/16 메모", now=NOW) is not None
