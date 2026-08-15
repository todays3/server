"""Persist per-crawl kind counts for admin review."""

from __future__ import annotations

import json
from typing import Iterable

from sqlalchemy.orm import Session

from datetime import datetime, timezone

from app.models import CrawlRun, User
from app.services.pipeline_timing import total_ms as sum_total_ms
from app.services.sources import SourceItem

CANONICAL_KINDS = ("아티클", "유튜브", "커뮤니티")


def kind_counts(candidates: Iterable[SourceItem]) -> dict[str, int]:
    counts: dict[str, int] = {kind: 0 for kind in CANONICAL_KINDS}
    for item in candidates:
        kind = (item.kind or "아티클").strip() or "아티클"
        counts[kind] = counts.get(kind, 0) + 1
    return counts


def persist_crawl_run(
    db: Session,
    user: User,
    candidates: Iterable[SourceItem],
    *,
    trigger: str,
    digest_id: int | None = None,
    slot_label: str = "",
    trigger_ms: int = 0,
    crawl_ms: int = 0,
    aggregation_ms: int = 0,
    llm_ms: int = 0,
    format_ms: int = 0,
    wait_ms: int = 0,
    send_ms: int = 0,
    lead_ms: int = 0,
    curator: str = "",
    llm_skip_reason: str = "",
    ready_at: datetime | None = None,
    cpu_peak_percent: int = 0,
    rss_peak_bytes: int = 0,
    rss_delta_bytes: int = 0,
) -> CrawlRun:
    kinds = kind_counts(candidates)
    row = CrawlRun(
        user_id=user.id,
        digest_id=digest_id,
        trigger=trigger,
        slot_label=slot_label,
        kinds_json=json.dumps(kinds, ensure_ascii=False),
        total_count=sum(kinds.values()),
        trigger_ms=trigger_ms,
        crawl_ms=crawl_ms,
        aggregation_ms=aggregation_ms,
        llm_ms=llm_ms,
        format_ms=format_ms,
        wait_ms=wait_ms,
        send_ms=send_ms,
        lead_ms=lead_ms,
        curator=curator,
        llm_skip_reason=llm_skip_reason,
        ready_at=ready_at or datetime.now(timezone.utc),
        cpu_peak_percent=max(0, int(cpu_peak_percent)),
        rss_peak_bytes=max(0, int(rss_peak_bytes)),
        rss_delta_bytes=max(0, int(rss_delta_bytes)),
    )
    row.total_ms = sum_total_ms(row)
    db.add(row)
    db.flush()
    return row
