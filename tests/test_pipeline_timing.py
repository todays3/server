"""Per-layer digest latency and schedule lead time."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.pipeline_timing import (
    DEFAULT_LEAD_MINUTES,
    LAYER_KEYS,
    due_actions,
    percentile,
    prep_ms,
    suggested_lead_minutes,
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


def test_suggested_lead_minutes_default_and_p90():
    assert suggested_lead_minutes([]) == DEFAULT_LEAD_MINUTES
    # 90s p90 + 15s pad → 2 minutes, clamped to min 1
    samples = [1000] * 9 + [90_000]
    assert suggested_lead_minutes(samples) >= 1
    huge = [14 * 60_000] * 10
    assert suggested_lead_minutes(huge) <= 15


def test_due_actions_prepare_then_send():
    tz = ZoneInfo("Asia/Seoul")
    slot = datetime(2026, 8, 15, 7, 30, tzinfo=tz)
    lead = 8
    early = slot - timedelta(minutes=8)
    mid = slot - timedelta(minutes=3)
    at_slot = slot
    late = slot + timedelta(minutes=2)
    slots = {(7, 30)}
    assert due_actions(early, slots, lead) == [(format_hm(7, 30), "prepare")]
    assert due_actions(mid, slots, lead) == [(format_hm(7, 30), "prepare")]
    assert due_actions(at_slot, slots, lead) == [(format_hm(7, 30), "send")]
    assert due_actions(late, slots, lead) == [(format_hm(7, 30), "send")]
    too_late = slot + timedelta(minutes=20)
    assert due_actions(too_late, slots, lead) == []
    before = slot - timedelta(minutes=9)
    assert due_actions(before, slots, lead) == []
