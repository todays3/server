"""Live source gatherers (RSS / YouTube / public HTML lists).

Prefer native RSS/Atom. When a site has no usable feed (or the feed fails),
fall back to polite public listing/search HTML parsing — no login, no CAPTCHA
bypass, no anti-bot evasion. Soft-fail on 403/timeouts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import quote_plus, urljoin, urlparse

import feedparser
import httpx

USER_AGENT = (
    "Oday3Bot/1.0 (+https://localhost; personal study digest; "
    "contact=local-dev)"
)


@dataclass
class SourceItem:
    kind: str  # 유튜브 | 아티클 | 커뮤니티
    title: str
    url: str
    summary: str
    source: str


@dataclass(frozen=True)
class HtmlListSpec:
    """Public HTML list/search page → link extraction."""

    kind: str
    source: str
    # Absolute URL, or template with {q} for topic query
    url: str
    # Keep only href matching this (search against absolute or path)
    href_re: str
    base: str
    limit: int = 6


# YouTube channel IDs (public RSS, no API key)
_YT_CHANNELS: dict[str, tuple[str, str]] = {
    "삼프로TV": ("UChlgI3UHCOnwUGzWzbJEuYw", "주식"),
    "슈카월드": ("UCsJ6RuBiTVWRX1041hfhWYA", "경제"),
    "지식인사이드": ("UCGX5sP4ehPfCPU1yGT1JT3w", "라이프"),
}

# Static RSS catalogs keyed by topic fragment match
_RSS_CATALOG: list[tuple[str, str, str, str]] = [
    ("아티클", "Google News KR 주식", "주식", "https://news.google.com/rss/search?q=%ED%95%9C%EA%B5%AD+%EC%A3%BC%EC%8B%9D&hl=ko&gl=KR&ceid=KR:ko"),
    ("아티클", "Google News US markets", "미국증시", "https://news.google.com/rss/search?q=US+stock+market&hl=en-US&gl=US&ceid=US:en"),
    ("아티클", "Google News 반도체", "반도체", "https://news.google.com/rss/search?q=%EB%B0%98%EB%8F%84%EC%B2%B4&hl=ko&gl=KR&ceid=KR:ko"),
    ("아티클", "Google News AI", "AI", "https://news.google.com/rss/search?q=artificial+intelligence&hl=en-US&gl=US&ceid=US:en"),
    ("아티클", "Google News 연애", "연애", "https://news.google.com/rss/search?q=%EC%97%B0%EC%95%A0&hl=ko&gl=KR&ceid=KR:ko"),
    ("아티클", "Google News 커리어", "커리어", "https://news.google.com/rss/search?q=%EC%9D%B4%EC%A7%81+%EB%A9%B4%EC%A0%91&hl=ko&gl=KR&ceid=KR:ko"),
    ("커뮤니티", "HN Frontpage", "IT", "https://hnrss.org/frontpage"),
    ("커뮤니티", "r/stocks", "주식", "https://www.reddit.com/r/stocks/.rss"),
    ("커뮤니티", "r/investing", "미국증시", "https://www.reddit.com/r/investing/.rss"),
    ("커뮤니티", "r/algotrading", "퀀트", "https://www.reddit.com/r/algotrading/.rss"),
    ("커뮤니티", "r/korea", "뉴스", "https://www.reddit.com/r/korea/.rss"),
]


def _clean(text: str, limit: int = 180) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _topic_blob(topics: list[str]) -> str:
    return " ".join(topics).lower()


def _primary_query(topics: list[str]) -> str:
    if not topics:
        return "technology"
    q = topics[0].split("/")[-1].strip()
    return q if q and q != "all" else "technology"


def _feeds_for_topics(topics: list[str]) -> list[tuple[str, str, str]]:
    blob = _topic_blob(topics)
    picked: list[tuple[str, str, str]] = []

    for kind, name, hint, url in _RSS_CATALOG:
        if not topics or hint.lower() in blob or any(hint.lower() in t.lower() for t in topics):
            picked.append((kind, name, url))

    if any(x in blob for x in ("주식", "증시", "경제", "etf", "반도체")):
        for kind, name, hint, url in _RSS_CATALOG:
            if hint in ("주식", "미국증시") and (kind, name, url) not in picked:
                picked.append((kind, name, url))

    if not picked:
        picked = [(k, n, u) for k, n, _, u in _RSS_CATALOG[:4]]

    if topics:
        q = _primary_query(topics)
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
        label, (channel_id, _) = next(iter(_YT_CHANNELS.items()))
        out.append(("유튜브", label, f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"))
    return out


def _http_get(url: str, *, timeout: float = 12.0) -> str | None:
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            },
        ) as client:
            resp = client.get(url)
            if resp.status_code >= 400:
                return None
            return resp.text
    except Exception:
        return None


def _parse_feed(kind: str, source: str, url: str, *, limit: int = 5) -> list[SourceItem]:
    raw = _http_get(url)
    if not raw:
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


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._href = href
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        title = _clean("".join(self._parts), 140)
        self.links.append((self._href, title))
        self._href = None
        self._parts = []


def _parse_html_list(spec: HtmlListSpec, *, query: str = "") -> list[SourceItem]:
    url = spec.url.replace("{q}", quote_plus(query or "technology"))
    raw = _http_get(url)
    if not raw:
        return []

    parser = _AnchorCollector()
    try:
        parser.feed(raw)
    except Exception:
        return []

    href_re = re.compile(spec.href_re, re.I)
    seen: set[str] = set()
    items: list[SourceItem] = []
    for href, title in parser.links:
        abs_url = urljoin(spec.base, href)
        path = urlparse(abs_url).path
        if not href_re.search(abs_url) and not href_re.search(path):
            continue
        if abs_url in seen:
            continue
        if not title or len(title) < 3:
            continue
        # skip nav noise
        if title.lower() in {"sign in", "sign up", "login", "home", "about", "more"}:
            continue
        # GitHub non-repo first segments (topics/search/…)
        if "github.com" in abs_url:
            segs = [s for s in path.split("/") if s]
            if len(segs) >= 1 and segs[0].lower() in {
                "topics",
                "search",
                "settings",
                "orgs",
                "users",
                "explore",
                "marketplace",
                "login",
                "signup",
                "about",
                "features",
                "pricing",
                "enterprise",
                "collections",
                "sponsors",
                "codespaces",
                "notifications",
                "pulls",
                "issues",
                "new",
                "account",
            }:
                continue
            if len(segs) != 2:
                continue
        seen.add(abs_url)
        items.append(
            SourceItem(
                kind=spec.kind,
                title=title,
                url=abs_url,
                summary=f"{spec.source} 목록에서 수집",
                source=spec.source,
            )
        )
        if len(items) >= spec.limit:
            break
    return items


# User-selected reference site id → RSS/Atom feeds
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
    "reuters": [
        ("아티클", "Reuters business", "https://news.google.com/rss/search?q=site:reuters.com+business+OR+markets&hl=en-US&gl=US&ceid=US:en"),
        ("아티클", "Reuters world", "https://news.google.com/rss/search?q=site:reuters.com&hl=en-US&gl=US&ceid=US:en"),
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
    "coindesk": [
        ("아티클", "CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml"),
        ("아티클", "CoinDesk Google", "https://news.google.com/rss/search?q=site:coindesk.com&hl=en-US&gl=US&ceid=US:en"),
    ],
    "cointelegraph": [
        ("아티클", "CoinTelegraph", "https://cointelegraph.com/rss"),
        ("아티클", "CoinTelegraph Google", "https://news.google.com/rss/search?q=site:cointelegraph.com&hl=en-US&gl=US&ceid=US:en"),
    ],
    "the-block": [
        ("아티클", "The Block", "https://www.theblock.co/rss.xml"),
        ("아티클", "The Block Google", "https://news.google.com/rss/search?q=site:theblock.co&hl=en-US&gl=US&ceid=US:en"),
    ],
    "quantstart": [
        ("아티클", "QuantStart", "https://www.quantstart.com/articles/rss"),
        ("아티클", "QuantStart Google", "https://news.google.com/rss/search?q=site:quantstart.com+algorithmic+trading&hl=en-US&gl=US&ceid=US:en"),
    ],
    "reddit-algotrading": [
        ("커뮤니티", "r/algotrading", "https://www.reddit.com/r/algotrading/.rss"),
    ],
    "hn": [
        ("커뮤니티", "Hacker News", "https://hnrss.org/frontpage"),
        ("커뮤니티", "HN best", "https://hnrss.org/best"),
    ],
    "lobsters": [
        ("커뮤니티", "Lobsters", "https://lobste.rs/rss"),
        ("커뮤니티", "Lobsters hottest", "https://lobste.rs/hottest.rss"),
    ],
    "github-trending": [
        ("아티클", "GitHub Blog", "https://github.blog/feed/"),
    ],
    "stackoverflow": [
        ("커뮤니티", "SO python", "https://stackoverflow.com/feeds/tag?tagnames=python&sort=newest"),
        ("커뮤니티", "SO fastapi", "https://stackoverflow.com/feeds/tag?tagnames=fastapi&sort=newest"),
        ("커뮤니티", "SO javascript", "https://stackoverflow.com/feeds/tag?tagnames=javascript&sort=newest"),
    ],
    "geeksforgeeks": [
        ("아티클", "GeeksforGeeks", "https://www.geeksforgeeks.org/feed/"),
    ],
    "infoq": [
        ("아티클", "InfoQ", "https://feed.infoq.com/"),
    ],
    "high-scalability": [
        ("아티클", "High Scalability", "https://highscalability.com/rss/"),
    ],
    "netflix-tech": [
        ("아티클", "Netflix Tech Blog", "https://netflixtechblog.com/feed"),
    ],
    "uber-eng": [
        ("아티클", "Uber Engineering", "https://www.uber.com/blog/engineering/rss/"),
        ("아티클", "Uber Eng Google", "https://news.google.com/rss/search?q=site:uber.com/blog/engineering&hl=en-US&gl=US&ceid=US:en"),
    ],
    "cloudflare-blog": [
        ("아티클", "Cloudflare Blog", "https://blog.cloudflare.com/rss/"),
    ],
    "geeknews": [
        ("아티클", "긱뉴스", "https://news.hada.io/rss/news"),
        ("아티클", "긱뉴스 주간", "https://news.hada.io/rss/weekly"),
    ],
    "clien": [
        ("커뮤니티", "클리앙 새로운소식", "https://www.clien.net/service/board/news/rss"),
        ("커뮤니티", "클리앙 공지", "https://www.clien.net/service/board/notice/rss"),
    ],
    "hardbattle": [
        ("아티클", "하드웨어배틀", "https://news.google.com/rss/search?q=site:hwbattle.com+OR+%ED%95%98%EB%93%9C%EC%9B%A8%EC%96%B4+%EB%B0%B0%ED%8B%80&hl=ko&gl=KR&ceid=KR:ko"),
    ],
    "itchosun": [
        ("아티클", "IT조선", "https://news.google.com/rss/search?q=site:it.chosun.com&hl=ko&gl=KR&ceid=KR:ko"),
    ],
    "bloter": [
        ("아티클", "블로터", "https://www.bloter.net/feed"),
        ("아티클", "블로터 Google", "https://news.google.com/rss/search?q=site:bloter.net&hl=ko&gl=KR&ceid=KR:ko"),
    ],
    "outstanding": [
        ("아티클", "아웃스탠딩", "https://news.google.com/rss/search?q=site:outstanding.kr&hl=ko&gl=KR&ceid=KR:ko"),
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

# Sites without reliable native RSS (or as secondary fallback): public HTML lists/search
_SITE_HTML: dict[str, list[HtmlListSpec]] = {
    "github-trending": [
        HtmlListSpec(
            kind="커뮤니티",
            source="GitHub Trending",
            url="https://github.com/trending",
            href_re=r"^https://github\.com/[^/]+/[^/]+/?$",
            base="https://github.com",
            limit=8,
        ),
        HtmlListSpec(
            kind="커뮤니티",
            source="GitHub Trending · search",
            url="https://github.com/search?q={q}&type=repositories&s=stars&o=desc",
            href_re=r"^https://github\.com/[^/]+/[^/]+/?$",
            base="https://github.com",
            limit=6,
        ),
    ],
    "geeksforgeeks": [
        HtmlListSpec(
            kind="아티클",
            source="GeeksforGeeks search",
            url="https://www.geeksforgeeks.org/?s={q}",
            href_re=r"geeksforgeeks\.org/.+",
            base="https://www.geeksforgeeks.org/",
            limit=6,
        ),
    ],
    "stackoverflow": [
        HtmlListSpec(
            kind="커뮤니티",
            source="Stack Overflow search",
            url="https://stackoverflow.com/search?q={q}",
            href_re=r"stackoverflow\.com/questions/\d+",
            base="https://stackoverflow.com/",
            limit=6,
        ),
    ],
    "hardbattle": [
        HtmlListSpec(
            kind="아티클",
            source="하드웨어배틀 뉴스",
            url="https://www.hwbattle.com/news/",
            href_re=r"hwbattle\.com/.+",
            base="https://www.hwbattle.com/",
            limit=6,
        ),
    ],
    "outstanding": [
        HtmlListSpec(
            kind="아티클",
            source="아웃스탠딩 search",
            url="https://outstanding.kr/?s={q}",
            href_re=r"outstanding\.kr/.+",
            base="https://outstanding.kr/",
            limit=6,
        ),
    ],
    "itchosun": [
        HtmlListSpec(
            kind="아티클",
            source="IT조선 search",
            url="https://it.chosun.com/search?query={q}",
            href_re=r"it\.chosun\.com/.+",
            base="https://it.chosun.com/",
            limit=6,
        ),
    ],
    "clien": [
        HtmlListSpec(
            kind="커뮤니티",
            source="클리앙 새로운소식",
            url="https://www.clien.net/service/board/news",
            href_re=r"/service/board/news/\d+",
            base="https://www.clien.net",
            limit=8,
        ),
    ],
    "quantstart": [
        HtmlListSpec(
            kind="아티클",
            source="QuantStart articles",
            url="https://www.quantstart.com/articles/",
            href_re=r"quantstart\.com/articles/.+",
            base="https://www.quantstart.com/",
            limit=6,
        ),
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


def _html_for_sites(site_ids: list[str], topics: list[str]) -> list[SourceItem]:
    q = _primary_query(topics)
    collected: list[SourceItem] = []
    seen: set[str] = set()
    for sid in site_ids:
        for spec in _SITE_HTML.get(sid, []):
            for item in _parse_html_list(spec, query=q):
                if item.url in seen:
                    continue
                seen.add(item.url)
                collected.append(item)
    return collected


def collector_site_ids() -> set[str]:
    return set(_SITE_FEEDS) | set(_SITE_HTML)


def catalog_site_ids() -> list[str]:
    """Ids the gatherer can resolve (must cover app.catalog.ref_sites)."""
    from app.catalog.ref_sites import all_site_ids

    return sorted(all_site_ids())


def gather_candidates(
    topics: list[str],
    *,
    preferred_sites: list[str] | None = None,
    max_items: int = 24,
) -> list[SourceItem]:
    """Fetch RSS + YouTube + optional public HTML lists for selected sites."""
    sites = preferred_sites or []
    site_feeds = _feeds_for_sites(sites)
    topic_feeds = _feeds_for_topics(topics) + _youtube_feeds_for_topics(topics)
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

    # HTML fallback / supplement for sites that need list/search pages
    if sites:
        for item in _html_for_sites(sites, topics):
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
