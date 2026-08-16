"""Per-layer digest latency and schedule lead time."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.pipeline_timing import (
    DEFAULT_LEAD_SECONDS,
    LAYER_KEYS,
    MAX_LEAD_SECONDS,
    MIN_LEAD_SECONDS,
    due_actions,
    percentile,
    prep_ms,
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


def test_suggested_lead_seconds_default_pad_and_outliers():
    assert suggested_lead_seconds([]) == DEFAULT_LEAD_SECONDS
    assert suggested_lead_minutes([]) == 1
    fast = [6_000] * 10
    assert MIN_LEAD_SECONDS <= suggested_lead_seconds(fast) <= 30
    assert suggested_lead_minutes(fast) == 1
    mixed = [5_000] * 9 + [12 * 60_000]
    assert suggested_lead_seconds(mixed) <= 30
    huge = [14 * 60_000] * 10
    assert suggested_lead_seconds(huge) == DEFAULT_LEAD_SECONDS
    assert suggested_lead_seconds(huge) <= MAX_LEAD_SECONDS


def test_due_actions_prepare_then_send():
    tz = ZoneInfo("Asia/Seoul")
    slot = datetime(2026, 8, 15, 7, 30, tzinfo=tz)
    lead = 40
    early = slot - timedelta(seconds=40)
    mid = slot - timedelta(seconds=12)
    at_slot = slot
    late = slot + timedelta(minutes=2)
    slots = {(7, 30)}
    assert due_actions(early, slots, lead) == [(format_hm(7, 30), "prepare")]
    assert due_actions(mid, slots, lead) == [(format_hm(7, 30), "prepare")]
    assert due_actions(at_slot, slots, lead) == [(format_hm(7, 30), "send")]
    assert due_actions(late, slots, lead) == [(format_hm(7, 30), "send")]
    too_late = slot + timedelta(minutes=20)
    assert due_actions(too_late, slots, lead) == []
    before = slot - timedelta(seconds=41)
    assert due_actions(before, slots, lead) == []
