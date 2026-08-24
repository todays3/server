"""Per-layer digest latency helpers and schedule lead time."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from time import perf_counter
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CrawlRun
from app.services.send_times import format_hm

LAYER_KEYS = ("trigger", "crawl", "aggregation", "llm", "format", "wait", "send")
LAYER_COLUMNS = tuple(f"{key}_ms" for key in LAYER_KEYS)

DEFAULT_LEAD_SECONDS = 40
MIN_LEAD_SECONDS = 20
LEAD_PAD_MS = 5_000
# Include long local LLM runs when computing adaptive lead from history.
PREP_OUTLIER_MS = 45 * 60_000
SEND_TICK_SECONDS = 5
PREPARE_TICK_SECONDS = 15
TICK_SECONDS = SEND_TICK_SECONDS


def prep_lead_floor_seconds() -> int:
    from app.config import get_settings

    return max(0, int(get_settings().schedule_prep_lead_minutes)) * 60


def send_grace_minutes() -> int:
    from app.config import get_settings

    return max(1, int(get_settings().schedule_send_grace_minutes))


def send_catchup_minutes() -> int:
    """After grace, keep trying unsent drafts for this many minutes past the slot."""
    from app.config import get_settings

    grace = send_grace_minutes()
    catchup = max(0, int(getattr(get_settings(), "schedule_send_catchup_minutes", 180) or 0))
    return max(grace, catchup)


def suggested_lead_minutes_from_seconds(seconds: int) -> int:
    return max(1, math.ceil(max(0, int(seconds)) / 60))


def elapsed_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))


def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (p / 100.0)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return int(ordered[lo] * (1.0 - frac) + ordered[hi] * frac)


def timings_from(row: Any) -> dict[str, int]:
    if isinstance(row, Mapping):
        return {f"{key}_ms": int(row.get(f"{key}_ms", 0) or 0) for key in LAYER_KEYS}
    return {col: int(getattr(row, col, 0) or 0) for col in LAYER_COLUMNS}


def prep_ms(row: Any) -> int:
    t = timings_from(row)
    return t["trigger_ms"] + t["crawl_ms"] + t["aggregation_ms"] + t["llm_ms"] + t["format_ms"]


def total_ms(row: Any) -> int:
    t = timings_from(row)
    return prep_ms(t) + t["wait_ms"] + t["send_ms"]


def suggested_lead_seconds(prep_samples_ms: list[int]) -> int:
    floor = prep_lead_floor_seconds()
    samples = [n for n in prep_samples_ms if 0 < n <= PREP_OUTLIER_MS]
    if not samples:
        return max(floor, DEFAULT_LEAD_SECONDS)
    adaptive = math.ceil((percentile(samples, 90) + LEAD_PAD_MS) / 1000)
    adaptive = max(MIN_LEAD_SECONDS, adaptive)
    return max(floor, adaptive)


def suggested_lead_minutes(prep_samples_ms: list[int]) -> int:
    return suggested_lead_minutes_from_seconds(suggested_lead_seconds(prep_samples_ms))


def _schedule_prep_samples(db: Session) -> list[int]:
    rows = list(
        db.scalars(
            select(CrawlRun)
            .where(CrawlRun.trigger == "schedule")
            .order_by(CrawlRun.id.desc())
            .limit(30)
        ).all()
    )
    return [prep_ms(row) for row in rows]


def suggested_lead_seconds_from_db(db: Session) -> int:
    return suggested_lead_seconds(_schedule_prep_samples(db))


def suggested_lead_minutes_from_db(db: Session) -> int:
    return suggested_lead_minutes_from_seconds(suggested_lead_seconds_from_db(db))


def due_actions(
    now: datetime,
    slots: set[tuple[int, int]],
    lead_seconds: int,
) -> list[tuple[str, str]]:
    """Return (slot_label, phase) where phase is prepare | send | catchup."""
    actions: list[tuple[str, str]] = []
    lead = timedelta(seconds=max(0, int(lead_seconds)))
    grace = timedelta(minutes=send_grace_minutes())
    catchup_span = timedelta(minutes=send_catchup_minutes())
    for hour, minute in sorted(slots):
        slot_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        start_dt = slot_dt - lead
        label = format_hm(hour, minute)
        if start_dt <= now < slot_dt:
            actions.append((label, "prepare"))
        elif slot_dt <= now < slot_dt + grace:
            actions.append((label, "send"))
        else:
            # Same calendar day: keep trying unsent drafts until catchup window ends
            # (at least through end of local day for morning slots).
            day_end = slot_dt.replace(hour=23, minute=59, second=59, microsecond=0)
            catchup_end = max(slot_dt + catchup_span, day_end)
            if slot_dt + grace <= now <= catchup_end and now.date() == slot_dt.date():
                actions.append((label, "catchup"))
    return actions
