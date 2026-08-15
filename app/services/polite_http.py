"""Polite HTTP: identify as RSS reader, per-host throttle, robots.txt, 429 cooldown.

Does not rotate IPs or solve CAPTCHA/challenges. Public feed URLs are still fetched
when robots.txt Disallow: / would otherwise drop every RSS path.
"""

from __future__ import annotations

import random
import time
from threading import Lock
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.config import get_settings

MIN_HOST_INTERVAL_SECONDS = 1.5
ROBOTS_TTL_SECONDS = 3600.0
DEFAULT_TIMEOUT = 12.0

_host_lock = Lock()
_next_ok: dict[str, float] = {}
_robots: dict[str, tuple[float, RobotFileParser | None]] = {}


def crawler_user_agent() -> str:
    origin = get_settings().frontend_origin.rstrip("/")
    return f"Oday3RSS/1.1 (+{origin}; RSS reader)"


def reader_fallback_user_agent() -> str:
    origin = get_settings().frontend_origin.rstrip("/")
    return f"Mozilla/5.0 (compatible; Oday3RSS/1.1; +{origin})"


def looks_like_xml_feed(body: str) -> bool:
    head = (body or "")[:2500].lower()
    return "<rss" in head or "<feed" in head or "<rdf:rdf" in head


def is_feed_url(url: str) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    query = (parsed.query or "").lower()
    host = (parsed.netloc or "").lower()
    if any(path.endswith(ext) for ext in (".rss", ".xml", ".atom")):
        return True
    if any(token in path for token in ("/rss", "/feed", "/atom", "/feeds/", "videos.xml")):
        return True
    if "outputtype=xml" in query or "partnerid=wrss" in query:
        return True
    if "news.google.com" in host and "/rss" in path:
        return True
    return False


def classify_bot_risk(
    *,
    status_code: int | None,
    body: str,
    error: str,
    robots_allowed: bool,
) -> tuple[str, str]:
    if not robots_allowed:
        return "blocked", "robots.txt에서 이 경로를 막았습니다"
    if status_code in (401, 403, 407):
        return "blocked", f"HTTP {status_code} — 접근이 거부되어 봇으로 보인 상태입니다"
    if status_code == 429:
        return "blocked", "HTTP 429 — 요청이 너무 잦다고 거절했습니다"
    if status_code == 503:
        return "blocked", "HTTP 503 — 서버가 요청을 거절했습니다"
    if looks_like_xml_feed(body):
        if status_code is not None and status_code >= 400:
            return "caution", f"HTTP {status_code}"
        if error and not body:
            return "caution", error[:160]
        return "clear", "정상 응답 · 봇으로 보이지 않음"
    blob = (body or "").lower()
    needles = (
        "cf-browser-verification",
        "cdn-cgi/challenge",
        "just a moment",
        "attention required",
        "enable javascript and cookies",
        "checking your browser",
    )
    if any(n in blob for n in needles):
        return "blocked", "챌린지 페이지 — Cloudflare 등으로 봇 의심"
    if "captcha" in blob:
        return "blocked", "CAPTCHA 페이지 — 자동 우회하지 않습니다"
    if status_code is not None and status_code >= 400:
        return "caution", f"HTTP {status_code}"
    if error and not body:
        return "caution", error[:160]
    return "clear", "정상 응답 · 봇으로 보이지 않음"


def worst_bot_risk(risks: list[str]) -> str:
    rank = {"blocked": 3, "caution": 2, "clear": 1, "unknown": 0}
    if not risks:
        return "unknown"
    return max(risks, key=lambda r: rank.get(r, 0))


def clear_polite_state() -> None:
    with _host_lock:
        _next_ok.clear()
        _robots.clear()


def _host_key(url: str) -> str:
    parsed = urlparse(url)
    return parsed.netloc.lower() or url


def _throttle(host: str, extra_delay: float) -> None:
    wait = 0.0
    with _host_lock:
        now = time.monotonic()
        due = _next_ok.get(host, now)
        if due > now:
            wait = due - now
        gap = max(MIN_HOST_INTERVAL_SECONDS, extra_delay) + random.uniform(0.0, 0.35)
        _next_ok[host] = max(now, due) + gap
    if wait > 0:
        time.sleep(wait)


def _cooldown(host: str, seconds: float) -> None:
    with _host_lock:
        _next_ok[host] = max(_next_ok.get(host, 0.0), time.monotonic() + seconds)


def _parse_retry_after(value: str | None) -> float:
    if not value:
        return 30.0
    try:
        return min(max(float(value.strip()), 1.0), 300.0)
    except ValueError:
        return 30.0


def _load_robots(client: httpx.Client, url: str) -> RobotFileParser | None:
    host = _host_key(url)
    now = time.monotonic()
    with _host_lock:
        cached = _robots.get(host)
        if cached and now - cached[0] < ROBOTS_TTL_SECONDS:
            return cached[1]
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    parser = RobotFileParser()
    try:
        resp = client.get(robots_url)
        if resp.status_code >= 400:
            parser = None
        else:
            parser.parse(resp.text.splitlines())
    except Exception:  # noqa: BLE001 — fail-open if robots.txt is unreachable
        parser = None
    with _host_lock:
        _robots[host] = (now, parser)
    return parser


def robots_allows(client: httpx.Client, url: str) -> tuple[bool, float]:
    parser = _load_robots(client, url)
    ua = crawler_user_agent()
    if parser is None:
        return True, 0.0
    allowed = True
    try:
        allowed = bool(parser.can_fetch(ua, url))
    except Exception:  # noqa: BLE001
        allowed = True
    delay = 0.0
    try:
        crawl_delay = parser.crawl_delay(ua)
        if crawl_delay is not None:
            delay = float(crawl_delay)
    except Exception:  # noqa: BLE001
        delay = 0.0
    return allowed, delay


def _request_headers(url: str, user_agent: str) -> dict[str, str]:
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/html;q=0.8, */*;q=0.7",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    }
    host = _host_key(url)
    if "news.google.com" in host:
        # RSS readers send this so Google News does not stop on the consent interstitial.
        headers["Cookie"] = "CONSENT=YES+"
    return headers


def fetch_url(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> tuple[int | None, str, str, bool]:
    """Return status, body, error, robots_allowed. Caller maps into FetchResult."""
    host = _host_key(url)
    try:
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers=_request_headers(url, crawler_user_agent()),
        ) as client:
            allowed, crawl_delay = robots_allows(client, url)
            _throttle(host, crawl_delay)
            if not allowed and not is_feed_url(url):
                return None, "", "robots.txt disallow", False
            resp = client.get(url)
            if resp.status_code in (401, 403):
                client.headers.update(_request_headers(url, reader_fallback_user_agent()))
                resp = client.get(url)
            if resp.status_code in (429, 503):
                _cooldown(host, _parse_retry_after(resp.headers.get("Retry-After")))
            if resp.status_code >= 400:
                return resp.status_code, resp.text[:4000], f"HTTP {resp.status_code}", True
            return resp.status_code, resp.text, "", True
    except Exception as exc:  # noqa: BLE001 — gather/probe soft-fail
        return None, "", str(exc)[:200], True
