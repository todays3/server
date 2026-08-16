"""Process-local crawl memory for a slot cluster. Not persisted."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from app.services.curate_limits import GATHER_FETCH_CAP, GATHER_MAX_ITEMS
from app.services.sources import SourceItem, gather_candidates

POOL_TTL_SECONDS = 50 * 60
SHARED_MAX_ITEMS = GATHER_FETCH_CAP

_lock = threading.Lock()
_pools: dict[str, tuple[float, list[SourceItem]]] = {}
_inflight: dict[str, threading.Event] = {}


@dataclass(frozen=True)
class SharedPool:
    items: list[SourceItem]
    fetched_at: float


def clear_shared_crawls() -> None:
    with _lock:
        _pools.clear()
        _inflight.clear()


def get_shared_items(key: str) -> list[SourceItem] | None:
    now = time.time()
    with _lock:
        row = _pools.get(key)
        if row is None:
            return None
        fetched_at, items = row
        if now - fetched_at > POOL_TTL_SECONDS:
            _pools.pop(key, None)
            return None
        return list(items)


def ensure_shared_crawl(key: str, topics: list[str], sites: list[str]) -> list[SourceItem]:
    existing = get_shared_items(key)
    if existing is not None:
        return existing
    with _lock:
        existing_row = _pools.get(key)
        if existing_row is not None and time.time() - existing_row[0] <= POOL_TTL_SECONDS:
            return list(existing_row[1])
        waiter = _inflight.get(key)
        leader = waiter is None
        if leader:
            waiter = threading.Event()
            _inflight[key] = waiter
    if not leader:
        waiter.wait(timeout=120)
        return get_shared_items(key) or []
    try:
        items = gather_candidates(topics, preferred_sites=sites, max_items=SHARED_MAX_ITEMS)
        with _lock:
            _pools[key] = (time.time(), list(items))
        return list(items)
    finally:
        waiter.set()
        with _lock:
            _inflight.pop(key, None)


def slice_shared_items(
    items: list[SourceItem],
    *,
    sites: list[str],
    max_items: int = GATHER_MAX_ITEMS,
) -> list[SourceItem]:
    if not items:
        return []
    if not sites:
        return items[:max_items]
    wanted = set(sites)
    picked = [item for item in items if not item.site_id or item.site_id in wanted]
    if not picked:
        return items[:max_items]
    return picked[:max_items]
