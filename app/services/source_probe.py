"""Probe RSS/HTML collectors and cache the last result for the admin UI."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from time import perf_counter

from app.catalog.ref_sites import site_label
from app.services.polite_http import worst_bot_risk
from app.services.sources import (
    _fetch,
    _parse_feed_body,
    _parse_html_list,
    collector_site_ids,
    site_feed_catalog,
    site_html_catalog,
    HtmlListSpec,
)

PROBE_TIMEOUT = 8.0
PROBE_WORKERS = 2


@dataclass
class FeedProbe:
    channel: str  # rss | html
    kind: str
    name: str
    url: str
    ok: bool
    status_code: int | None = None
    item_count: int = 0
    sample_titles: list[str] = field(default_factory=list)
    error: str = ""
    bot_risk: str = "unknown"
    bot_signal: str = ""


@dataclass
class SiteProbe:
    site_id: str
    label: str
    ok: bool | None
    probed_at: datetime | None
    duration_ms: int = 0
    item_count: int = 0
    feeds: list[FeedProbe] = field(default_factory=list)
    bot_risk: str = "unknown"


_lock = Lock()
_cache: dict[str, SiteProbe] = {}


def _put(probe: SiteProbe) -> SiteProbe:
    with _lock:
        _cache[probe.site_id] = probe
    return probe


def cached_probe(site_id: str) -> SiteProbe | None:
    with _lock:
        return _cache.get(site_id)


def empty_site_probe(site_id: str) -> SiteProbe:
    return SiteProbe(site_id=site_id, label=site_label(site_id), ok=None, probed_at=None, bot_risk="unknown")


def list_probe_snapshot() -> list[SiteProbe]:
    ids = sorted(collector_site_ids())
    with _lock:
        return [_cache.get(sid) or empty_site_probe(sid) for sid in ids]


def _risk_from_fetch(fetched) -> tuple[str, str]:
    if getattr(fetched, "bot_risk", "unknown") not in ("", "unknown"):
        return fetched.bot_risk, fetched.bot_signal or ""
    from app.services.polite_http import classify_bot_risk

    return classify_bot_risk(
        status_code=fetched.status_code,
        body=fetched.body or "",
        error=fetched.error or "",
        robots_allowed=True,
    )


def _probe_rss(kind: str, name: str, url: str) -> FeedProbe:
    fetched = _fetch(url, timeout=PROBE_TIMEOUT)
    risk, signal = _risk_from_fetch(fetched)
    if not fetched.ok:
        return FeedProbe(
            channel="rss",
            kind=kind,
            name=name,
            url=url,
            ok=False,
            status_code=fetched.status_code,
            error=fetched.error or "요청 실패",
            bot_risk=risk,
            bot_signal=signal,
        )
    items = _parse_feed_body(kind, name, url, fetched.body, limit=5)
    titles = [item.title for item in items[:3]]
    if not items:
        return FeedProbe(
            channel="rss",
            kind=kind,
            name=name,
            url=url,
            ok=False,
            status_code=fetched.status_code,
            error="피드에 항목이 없습니다",
            bot_risk="caution" if risk == "clear" else risk,
            bot_signal=signal or "본문은 왔지만 항목이 없습니다",
        )
    return FeedProbe(
        channel="rss",
        kind=kind,
        name=name,
        url=url,
        ok=True,
        status_code=fetched.status_code,
        item_count=len(items),
        sample_titles=titles,
        bot_risk=risk,
        bot_signal=signal,
    )


def _probe_html(spec: HtmlListSpec) -> FeedProbe:
    url = spec.url.replace("{q}", "technology")
    fetched = _fetch(url, timeout=PROBE_TIMEOUT)
    risk, signal = _risk_from_fetch(fetched)
    if not fetched.ok:
        return FeedProbe(
            channel="html",
            kind=spec.kind,
            name=spec.source,
            url=url,
            ok=False,
            status_code=fetched.status_code,
            error=fetched.error or "요청 실패",
            bot_risk=risk,
            bot_signal=signal,
        )
    items = _parse_html_list(spec, query="technology", body=fetched.body)
    titles = [item.title for item in items[:3]]
    if not items:
        return FeedProbe(
            channel="html",
            kind=spec.kind,
            name=spec.source,
            url=url,
            ok=False,
            status_code=fetched.status_code,
            error="목록에서 링크를 찾지 못했습니다",
            bot_risk="caution" if risk == "clear" else risk,
            bot_signal=signal or "HTML은 왔지만 목록 링크가 없습니다",
        )
    return FeedProbe(
        channel="html",
        kind=spec.kind,
        name=spec.source,
        url=url,
        ok=True,
        status_code=fetched.status_code,
        item_count=len(items),
        sample_titles=titles,
        bot_risk=risk,
        bot_signal=signal,
    )


def probe_site(site_id: str) -> SiteProbe:
    if site_id not in collector_site_ids():
        raise KeyError(site_id)
    started = perf_counter()
    feeds: list[FeedProbe] = []
    from app.services.youtube import probe_youtube_site

    yt_feeds = probe_youtube_site(site_id)
    feeds.extend(yt_feeds)
    need_rss = not yt_feeds or not any(feed.ok for feed in yt_feeds)
    if need_rss:
        for kind, name, url in site_feed_catalog().get(site_id, []):
            feeds.append(_probe_rss(kind, name, url))
        for spec in site_html_catalog().get(site_id, []):
            feeds.append(_probe_html(spec))
    duration_ms = int((perf_counter() - started) * 1000)
    ok = any(feed.ok for feed in feeds) if feeds else False
    ok_risks = [feed.bot_risk for feed in feeds if feed.ok]
    bot_risk = worst_bot_risk(ok_risks if ok_risks else [feed.bot_risk for feed in feeds])
    return _put(
        SiteProbe(
            site_id=site_id,
            label=site_label(site_id),
            ok=ok,
            probed_at=datetime.now(timezone.utc),
            duration_ms=duration_ms,
            item_count=sum(feed.item_count for feed in feeds),
            feeds=feeds,
            bot_risk=bot_risk,
        )
    )


def probe_all_sites() -> list[SiteProbe]:
    ids = sorted(collector_site_ids())
    results: dict[str, SiteProbe] = {}
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        futures = {pool.submit(probe_site, sid): sid for sid in ids}
        for future in as_completed(futures):
            sid = futures[future]
            results[sid] = future.result()
    return [results[sid] for sid in ids]


def summarize(sites: list[SiteProbe]) -> tuple[int, int, int]:
    ok_count = sum(1 for s in sites if s.ok is True)
    fail_count = sum(1 for s in sites if s.ok is False)
    unknown_count = sum(1 for s in sites if s.ok is None)
    return ok_count, fail_count, unknown_count


def bot_risk_counts(sites: list[SiteProbe]) -> tuple[int, int, int]:
    blocked = sum(1 for s in sites if s.bot_risk == "blocked")
    caution = sum(1 for s in sites if s.bot_risk == "caution")
    clear = sum(1 for s in sites if s.bot_risk == "clear")
    return blocked, caution, clear
