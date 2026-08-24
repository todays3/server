"""Live activity registry for admin realtime panel."""

from __future__ import annotations

from app.services.live_activity import (
    clear_live_activities,
    end_activity,
    snapshot,
    start_activity,
    update_activity,
)


def setup_function() -> None:
    clear_live_activities()


def test_snapshot_empty():
    data = snapshot()
    assert data["active_count"] == 0
    assert data["summary"] == "지금 돌아가는 처리 없음"
    assert data["items"] == []


def test_tracks_prepare_and_send():
    a = start_activity(
        kind="prepare",
        label="유저A · 08:15 작성",
        phase="crawl",
        user_id=1,
        display_name="유저A",
        slot_label="08:15",
    )
    b = start_activity(
        kind="send",
        label="유저B · 08:15 전송",
        phase="send",
        user_id=2,
        display_name="유저B",
        slot_label="08:15",
    )
    update_activity(a, phase="curate")
    data = snapshot()
    assert data["active_count"] == 2
    assert data["user_count"] == 2
    assert data["by_kind"]["prepare"] == 1
    assert data["by_kind"]["send"] == 1
    assert "처리 중 2건" in data["summary"]
    assert "대상 2명" in data["summary"]
    phases = {row["id"]: row["phase"] for row in data["items"]}
    assert phases[a] == "curate"
    assert phases[b] == "send"
    end_activity(a)
    end_activity(b)
    assert snapshot()["active_count"] == 0
