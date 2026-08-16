"""Nearby send slots share one in-memory crawl; personalize per reservation."""

from __future__ import annotations

from app.services.scheduler import DueJob, attach_cluster_keys
from app.services.slot_cluster import CLUSTER_WINDOW_MINUTES, cluster_slot_labels, slot_minutes


def test_slot_minutes_and_empty_cluster():
    assert slot_minutes("07:30") == 7 * 60 + 30
    assert cluster_slot_labels([]) == []
    assert cluster_slot_labels(["07:30"]) == [["07:30"]]


def test_nearby_slots_share_a_cluster_from_the_start():
    groups = cluster_slot_labels(["08:05", "07:30", "07:45"], window_minutes=CLUSTER_WINDOW_MINUTES)
    assert groups[0] == ["07:30", "07:45"]
    assert groups[1] == ["08:05"]


def test_far_slots_stay_separate():
    groups = cluster_slot_labels(["07:30", "12:00"], window_minutes=30)
    assert groups == [["07:30"], ["12:00"]]


def test_attach_cluster_keys_groups_nearby_jobs():
    jobs = [
        DueJob(user_id=1, slot_label="07:30", phase="send", day="2026-08-16", tz_name="Asia/Seoul", lead_ms=0),
        DueJob(user_id=2, slot_label="07:45", phase="prepare", day="2026-08-16", tz_name="Asia/Seoul", lead_ms=0),
        DueJob(user_id=3, slot_label="12:00", phase="send", day="2026-08-16", tz_name="Asia/Seoul", lead_ms=0),
    ]
    out = attach_cluster_keys(jobs)
    keys = {job.user_id: job.cluster_key for job in out}
    assert keys[1] == keys[2]
    assert keys[1] == "2026-08-16|07:30"
    assert keys[3] == "2026-08-16|12:00"
