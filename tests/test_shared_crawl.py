"""In-memory shared crawl pool — no DB."""

from __future__ import annotations

import threading
import time

from app.services import shared_crawl as pool
from app.services.sources import SourceItem


def _item(url: str, site_id: str = "") -> SourceItem:
    return SourceItem(kind="아티클", title=url, url=url, summary="s", source="s", site_id=site_id)


def test_ensure_shared_crawl_single_flight(monkeypatch):
    pool.clear_shared_crawls()
    calls = {"n": 0}

    def fake_gather(*_a, **_k):
        calls["n"] += 1
        time.sleep(0.08)
        return [_item("https://a.example", "hn")]

    monkeypatch.setattr(pool, "gather_candidates", fake_gather)
    got: list[list] = []

    def worker():
        got.append(pool.ensure_shared_crawl("2026-08-16|07:30", ["IT"], ["hn"]))

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls["n"] == 1
    assert all(len(g) == 1 for g in got)


def test_slice_shared_items_keeps_user_sites_drops_untagged():
    items = [
        _item("https://hn.example", "hn"),
        _item("https://stock.example", "naver-finance"),
        _item("https://topic.example", ""),
        _item("https://topic-it.example", "topic-IT"),
    ]
    sliced = pool.slice_shared_items(items, sites=["hn", "topic-IT"], max_items=24)
    urls = {i.url for i in sliced}
    assert "https://hn.example" in urls
    assert "https://topic-it.example" in urls
    assert "https://topic.example" not in urls
    assert "https://stock.example" not in urls
