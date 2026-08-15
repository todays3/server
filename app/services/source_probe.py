"""Probe RSS/HTML collectors and cache the last result for the admin UI."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from time import perf_counter

from app.catalog.ref_sites import site_label
from app.services.sources import (
    _fetch,
    _parse_feed_body,
    _parse_html_list,
    collector_site_ids,
    site_feed_catalog,
    site_html_catalog,
)

PROBE_TIMEOUT = 8.0
PROBE_WORKERS = 8


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


@dataclass
class SiteProbe:
    site_id: str
    label: str
    ok: bool | None
    probed_at: datetime | None
    duration_ms: int = 0
    item_count: int = 0
    feeds: list[FeedProbe] = field(default_factory=list)


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
    return SiteProbe(site_id=site_id, label=site_label(site_id), ok=None, probed_at=None)


def list_probe_snapshot() -> list[SiteProbe]:
    ids = sorted(collector_site_ids())
    with _lock:
        return [_cache.get(sid) or empty_site_probe(sid) for sid in ids]


def _probe_rss(kind: str, name: str, url: str) -> FeedProbe:
    fetched = _fetch(url, timeout=PROBE_TIMEOUT)
    if not fetched.ok:
        return FeedProbe(
            channel="rss",
            kind=kind,
            name=name,
            url=url,
            ok=False,
            status_code=fetched.status_code,
            error=fetched.error or "요청 실패",
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
    )


def _probe_html(spec: HtmlListSpec) -> FeedProbe:
    url = spec.url.replace("{q}", "technology")
    fetched = _fetch(url, timeout=PROBE_TIMEOUT)
    if not fetched.ok:
        return FeedProbe(
            channel="html",
            kind=spec.kind,
            name=spec.source,
            url=url,
            ok=False,
            status_code=fetched.status_code,
            error=fetched.error or "요청 실패",
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
    )


def probe_site(site_id: str) -> SiteProbe:
    if site_id not in collector_site_ids():
        raise KeyError(site_id)
    started = perf_counter()
    feeds: list[FeedProbe] = []
    for kind, name, url in site_feed_catalog().get(site_id, []):
        feeds.append(_probe_rss(kind, name, url))
    for spec in site_html_catalog().get(site_id, []):
        feeds.append(_probe_html(spec))
    duration_ms = int((perf_counter() - started) * 1000)
    ok = any(feed.ok for feed in feeds) if feeds else False
    return _put(
        SiteProbe(
            site_id=site_id,
            label=site_label(site_id),
            ok=ok,
            probed_at=datetime.now(timezone.utc),
            duration_ms=duration_ms,
            item_count=sum(feed.item_count for feed in feeds),
            feeds=feeds,
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
