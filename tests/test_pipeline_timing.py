"""Per-layer digest latency and schedule lead time."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.pipeline_timing import (
    DEFAULT_LEAD_SECONDS,
    LAYER_KEYS,
    MIN_LEAD_SECONDS,
    due_actions,
    percentile,
    prep_lead_floor_seconds,
    prep_ms,
    send_catchup_minutes,
    send_grace_minutes,
    suggested_lead_minutes,
    suggested_lead_seconds,
    total_ms,
)
from app.services.send_times import format_hm


def test_percentile_empty_and_single():
    assert percentile([], 90) == 0
    assert percentile([10], 90) == 10
    assert percentile([10, 20, 30, 40], 50) == 25


def test_prep_and_total_ms():
    timings = {
        "trigger_ms": 1,
        "crawl_ms": 100,
        "aggregation_ms": 10,
        "llm_ms": 50,
        "format_ms": 2,
        "wait_ms": 8000,
        "send_ms": 20,
    }
    assert prep_ms(timings) == 163
    assert total_ms(timings) == 8183
    assert LAYER_KEYS == ("trigger", "crawl", "aggregation", "llm", "format", "wait", "send")


def test_suggested_lead_seconds_floor_and_adaptive():
    floor = prep_lead_floor_seconds()
    assert floor == 30 * 60
    assert suggested_lead_seconds([]) == floor
    assert suggested_lead_minutes([]) == 30
    fast = [6_000] * 10
    assert suggested_lead_seconds(fast) == floor
    assert suggested_lead_minutes(fast) == 30
    mixed = [5_000] * 9 + [12 * 60_000]
    assert suggested_lead_seconds(mixed) == floor
    long_prep = [40 * 60_000] * 10
    assert suggested_lead_seconds(long_prep) >= 40 * 60
    assert MIN_LEAD_SECONDS <= suggested_lead_seconds([6_000])
    assert DEFAULT_LEAD_SECONDS >= 20


def test_due_actions_prepare_send_catchup():
    tz = ZoneInfo("Asia/Seoul")
    slot = datetime(2026, 8, 15, 7, 30, tzinfo=tz)
    lead = 30 * 60
    early = slot - timedelta(minutes=30)
    mid = slot - timedelta(minutes=12)
    at_slot = slot
    late = slot + timedelta(minutes=2)
    slots = {(7, 30)}
    assert due_actions(early, slots, lead) == [(format_hm(7, 30), "prepare")]
    assert due_actions(mid, slots, lead) == [(format_hm(7, 30), "prepare")]
    assert due_actions(at_slot, slots, lead) == [(format_hm(7, 30), "send")]
    assert due_actions(late, slots, lead) == [(format_hm(7, 30), "send")]
    grace = send_grace_minutes()
    catchup = send_catchup_minutes()
    assert catchup >= grace
    still_ok = slot + timedelta(minutes=grace - 5)
    assert due_actions(still_ok, slots, lead) == [(format_hm(7, 30), "send")]
    after_grace = slot + timedelta(minutes=grace + 5)
    assert due_actions(after_grace, slots, lead) == [(format_hm(7, 30), "catchup")]
    evening = slot.replace(hour=21, minute=0)
    assert due_actions(evening, slots, lead) == [(format_hm(7, 30), "catchup")]
    next_morning_early = (slot + timedelta(days=1)).replace(hour=0, minute=30)
    assert due_actions(next_morning_early, slots, lead) == []
    before = slot - timedelta(minutes=31)
    assert due_actions(before, slots, lead) == []
