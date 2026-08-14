"""Live source gatherers (RSS / YouTube channel feeds / fetch).

These mirror the *capabilities* of rss-reader-mcp + youtube-mcp + fetch MCP,
but run inside FastAPI as plain Python — MCP servers stay for Cursor agents,
not for the scheduled digest worker.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote_plus

import feedparser
import httpx

USER_AGENT = "Oday3Bot/1.0 (+https://localhost; personal digest)"


@dataclass
class SourceItem:
    kind: str  # 유튜브 | 아티클 | 커뮤니티
    title: str
    url: str
    summary: str
    source: str


# YouTube channel IDs (public RSS, no API key)
_YT_CHANNELS: dict[str, tuple[str, str]] = {
    # label -> (channel_id, topic_hint)
    "삼프로TV": ("UChlgI3UHCOnwUGzWzbJEuYw", "주식"),
    "슈카월드": ("UCsJ6RuBiTVWRX1041hfhWYA", "경제"),
    "지식인사이드": ("UCGX5sP4ehPfCPU1yGT1JT3w", "라이프"),
}

# Static RSS catalogs keyed by topic fragment match
_RSS_CATALOG: list[tuple[str, str, str, str]] = [
    # (kind, source_name, topic_hint, url)
    ("아티클", "Google News KR 주식", "주식", "https://news.google.com/rss/search?q=%ED%95%9C%EA%B5%AD+%EC%A3%BC%EC%8B%9D&hl=ko&gl=KR&ceid=KR:ko"),
    ("아티클", "Google News US markets", "미국증시", "https://news.google.com/rss/search?q=US+stock+market&hl=en-US&gl=US&ceid=US:en"),
    ("아티클", "Google News 반도체", "반도체", "https://news.google.com/rss/search?q=%EB%B0%98%EB%8F%84%EC%B2%B4&hl=ko&gl=KR&ceid=KR:ko"),
    ("아티클", "Google News AI", "AI", "https://news.google.com/rss/search?q=artificial+intelligence&hl=en-US&gl=US&ceid=US:en"),
    ("아티클", "Google News 연애", "연애", "https://news.google.com/rss/search?q=%EC%97%B0%EC%95%A0&hl=ko&gl=KR&ceid=KR:ko"),
    ("아티클", "Google News 커리어", "커리어", "https://news.google.com/rss/search?q=%EC%9D%B4%EC%A7%81+%EB%A9%B4%EC%A0%91&hl=ko&gl=KR&ceid=KR:ko"),
    ("커뮤니티", "HN Frontpage", "IT", "https://hnrss.org/frontpage"),
    ("커뮤니티", "r/stocks", "주식", "https://www.reddit.com/r/stocks/.rss"),
    ("커뮤니티", "r/investing", "미국증시", "https://www.reddit.com/r/investing/.rss"),
    ("커뮤니티", "r/korea", "뉴스", "https://www.reddit.com/r/korea/.rss"),
]


def _clean(text: str, limit: int = 180) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _topic_blob(topics: list[str]) -> str:
    return " ".join(topics).lower()


def _feeds_for_topics(topics: list[str]) -> list[tuple[str, str, str]]:
    """Return (kind, source_name, url) list biased by user topics."""
    blob = _topic_blob(topics)
    picked: list[tuple[str, str, str]] = []

    for kind, name, hint, url in _RSS_CATALOG:
        if not topics or hint.lower() in blob or any(hint.lower() in t.lower() for t in topics):
            picked.append((kind, name, url))

    # Always include at least one KR + one US market feed when stock-related
    if any(x in blob for x in ("주식", "증시", "경제", "etf", "반도체")):
        for kind, name, hint, url in _RSS_CATALOG:
            if hint in ("주식", "미국증시") and (kind, name, url) not in picked:
                picked.append((kind, name, url))

    if not picked:
        # default mix
        picked = [(k, n, u) for k, n, _, u in _RSS_CATALOG[:4]]

    # Dynamic Google News query from first topic label
    if topics:
        q = topics[0].split("/")[-1]
        if q and q != "all":
            gurl = (
                "https://news.google.com/rss/search?q="
                + quote_plus(q)
                + "&hl=ko&gl=KR&ceid=KR:ko"
            )
            picked.insert(0, ("아티클", f"Google News · {q}", gurl))

    return picked[:8]


def _youtube_feeds_for_topics(topics: list[str]) -> list[tuple[str, str, str]]:
    blob = _topic_blob(topics)
    out: list[tuple[str, str, str]] = []
    for label, (channel_id, hint) in _YT_CHANNELS.items():
        if not topics or hint.lower() in blob or "주식" in blob or "경제" in blob or "라이프" in blob:
            url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
            out.append(("유튜브", label, url))
    if not out:
        # fallback one channel
        label, (channel_id, _) = next(iter(_YT_CHANNELS.items()))
        out.append(("유튜브", label, f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"))
    return out


def _parse_feed(kind: str, source: str, url: str, *, limit: int = 5) -> list[SourceItem]:
    try:
        with httpx.Client(timeout=12.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
            resp = client.get(url)
            resp.raise_for_status()
            raw = resp.text
    except Exception:
        return []

    parsed = feedparser.parse(raw)
    items: list[SourceItem] = []
    for entry in parsed.entries[:limit]:
        link = getattr(entry, "link", "") or ""
        title = _clean(getattr(entry, "title", "") or "제목 없음", 120)
        summary = _clean(getattr(entry, "summary", "") or getattr(entry, "description", "") or "", 220)
        if not link or not title:
            continue
        items.append(
            SourceItem(kind=kind, title=title, url=link, summary=summary or source, source=source)
        )
    return items


# User-selected reference site id → RSS/Atom feeds (prefer native feeds over News proxies)
_SITE_FEEDS: dict[str, list[tuple[str, str, str]]] = {
    "naver-finance": [
        (
            "아티클",
            "네이버 증권·뉴스",
            "https://news.google.com/rss/search?q=%EC%BD%94%EC%8A%A4%ED%94%BC+%EC%A6%9D%EA%B6%8C&hl=ko&gl=KR&ceid=KR:ko",
        ),
        ("커뮤니티", "네이버 종목토론 이슈", "https://news.google.com/rss/search?q=%EC%A2%85%EB%AA%A9%ED%86%A0%EB%A1%A0&hl=ko&gl=KR&ceid=KR:ko"),
    ],
    "toss-securities": [
        ("아티클", "토스 증권·투자", "https://news.google.com/rss/search?q=%ED%86%A0%EC%8A%A4%EC%A6%9D%EA%B6%8C&hl=ko&gl=KR&ceid=KR:ko"),
    ],
    "kakao-stock": [
        ("아티클", "카카오페이증권", "https://news.google.com/rss/search?q=%EC%B9%B4%EC%B9%B4%EC%98%A4%ED%8E%98%EC%9D%B4%EC%A6%9D%EA%B6%8C&hl=ko&gl=KR&ceid=KR:ko"),
    ],
    "dart": [
        ("아티클", "전자공시·DART", "https://news.google.com/rss/search?q=%EC%A0%84%EC%9E%90%EA%B3%B5%EC%8B%9C+OR+DART+%EA%B3%B5%EC%8B%9C&hl=ko&gl=KR&ceid=KR:ko"),
    ],
    "hankyung": [
        ("아티클", "한국경제 금융", "https://www.hankyung.com/feed/finance"),
        ("아티클", "한국경제 증권", "https://www.hankyung.com/feed/economy"),
    ],
    "mk-stock": [
        ("아티클", "매일경제", "https://www.mk.co.kr/rss/40300001/"),
        ("아티클", "매경 증권 뉴스", "https://news.google.com/rss/search?q=%EB%A7%A4%EC%9D%BC%EA%B2%BD%EC%A0%9C+%EC%A6%9D%EA%B6%8C&hl=ko&gl=KR&ceid=KR:ko"),
    ],
    "sampro": [
        ("유튜브", "삼프로TV", "https://www.youtube.com/feeds/videos.xml?channel_id=UChlgI3UHCOnwUGzWzbJEuYw"),
    ],
    "yahoo-finance": [
        ("아티클", "Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
        ("아티클", "Yahoo markets", "https://news.google.com/rss/search?q=site:finance.yahoo.com+markets&hl=en-US&gl=US&ceid=US:en"),
    ],
    "investing": [
        ("아티클", "Investing.com", "https://www.investing.com/rss/news.rss"),
        ("아티클", "Investing markets", "https://www.investing.com/rss/news_25.rss"),
    ],
    "bloomberg": [
        ("아티클", "Bloomberg markets", "https://feeds.bloomberg.com/markets/news.rss"),
        ("아티클", "Bloomberg Google", "https://news.google.com/rss/search?q=site:bloomberg.com+markets&hl=en-US&gl=US&ceid=US:en"),
    ],
    "cnbc": [
        ("아티클", "CNBC top news", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
        ("아티클", "CNBC world", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362"),
    ],
    "reddit-stocks": [
        ("커뮤니티", "r/stocks", "https://www.reddit.com/r/stocks/.rss"),
        ("커뮤니티", "r/investing", "https://www.reddit.com/r/investing/.rss"),
    ],
    "seeking-alpha": [
        ("아티클", "Seeking Alpha", "https://seekingalpha.com/market_currents.xml"),
        ("아티클", "Seeking Alpha news", "https://seekingalpha.com/feed.xml"),
    ],
    "hn": [
        ("커뮤니티", "Hacker News", "https://hnrss.org/frontpage"),
        ("커뮤니티", "HN best", "https://hnrss.org/best"),
    ],
    "github-trending": [
        ("아티클", "GitHub Blog", "https://github.blog/feed/"),
        ("커뮤니티", "r/github", "https://www.reddit.com/r/github/.rss"),
    ],
    "velog": [
        ("아티클", "velog", "https://news.google.com/rss/search?q=site:velog.io&hl=ko&gl=KR&ceid=KR:ko"),
    ],
    "okky": [
        ("커뮤니티", "OKKY 이슈", "https://news.google.com/rss/search?q=OKKY+%EA%B0%9C%EB%B0%9C&hl=ko&gl=KR&ceid=KR:ko"),
    ],
    "youtube-life": [
        ("유튜브", "지식인사이드", "https://www.youtube.com/feeds/videos.xml?channel_id=UCGX5sP4ehPfCPU1yGT1JT3w"),
        ("유튜브", "슈카월드", "https://www.youtube.com/feeds/videos.xml?channel_id=UCsJ6RuBiTVWRX1041hfhWYA"),
    ],
    "brunch": [
        ("아티클", "브런치", "https://news.google.com/rss/search?q=site:brunch.co.kr&hl=ko&gl=KR&ceid=KR:ko"),
    ],
    "wanted": [
        ("아티클", "원티드·채용", "https://news.google.com/rss/search?q=%EC%9B%90%ED%8B%B0%EB%93%9C+%EC%B1%84%EC%9A%A9&hl=ko&gl=KR&ceid=KR:ko"),
        ("아티클", "이직·커리어", "https://news.google.com/rss/search?q=%EC%9D%B4%EC%A7%81+%EC%BB%A4%EB%A6%AC%EC%96%B4&hl=ko&gl=KR&ceid=KR:ko"),
    ],
    "naver-news": [
        ("아티클", "네이버 뉴스", "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"),
        ("아티클", "네이버 경제", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ko&gl=KR&ceid=KR:ko"),
    ],
}


def _feeds_for_sites(site_ids: list[str]) -> list[tuple[str, str, str]]:
    feeds: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for sid in site_ids:
        for feed in _SITE_FEEDS.get(sid, []):
            if feed in seen:
                continue
            seen.add(feed)
            feeds.append(feed)
    return feeds


def catalog_site_ids() -> list[str]:
    return sorted(_SITE_FEEDS.keys())


def gather_candidates(
    topics: list[str],
    *,
    preferred_sites: list[str] | None = None,
    max_items: int = 24,
) -> list[SourceItem]:
    """Fetch RSS + YouTube feeds. Prefer user-checked reference sites when set."""
    site_feeds = _feeds_for_sites(preferred_sites or [])
    topic_feeds = _feeds_for_topics(topics) + _youtube_feeds_for_topics(topics)
    # site picks first (higher priority), then topic fallbacks
    feeds = site_feeds + [f for f in topic_feeds if f not in site_feeds]
    collected: list[SourceItem] = []
    seen: set[str] = set()

    for kind, source, url in feeds:
        for item in _parse_feed(kind, source, url, limit=4):
            if item.url in seen:
                continue
            seen.add(item.url)
            collected.append(item)
            if len(collected) >= max_items:
                return collected
    return collected


def candidates_as_prompt_block(items: list[SourceItem]) -> str:
    lines: list[str] = []
    for i, it in enumerate(items, start=1):
        lines.append(f"{i}. [{it.kind}] {it.title}")
        lines.append(f"   source={it.source}")
        lines.append(f"   url={it.url}")
        if it.summary:
            lines.append(f"   summary={it.summary}")
    return "\n".join(lines)
