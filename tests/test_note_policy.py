from datetime import datetime, timedelta, timezone

from app.services.note_policy import evaluate_suggestion_create


NOW = datetime(2026, 8, 17, 13, 0, tzinfo=timezone.utc)


def test_admin_bypasses_spam_policy():
    decision = evaluate_suggestion_create(
        is_admin=True,
        today_count=99,
        open_count=99,
        last_created_at=NOW,
        duplicate=True,
        now=NOW,
        daily_max=5,
        min_interval_seconds=120,
        max_open=20,
    )
    assert decision.allowed is True


def test_blocks_after_daily_cap():
    decision = evaluate_suggestion_create(
        is_admin=False,
        today_count=5,
        open_count=1,
        last_created_at=NOW - timedelta(hours=3),
        duplicate=False,
        now=NOW,
        daily_max=5,
        min_interval_seconds=120,
        max_open=20,
    )
    assert decision.allowed is False
    assert decision.status == 429
    assert "하루" in decision.detail


def test_blocks_within_cooldown():
    decision = evaluate_suggestion_create(
        is_admin=False,
        today_count=1,
        open_count=1,
        last_created_at=NOW - timedelta(seconds=30),
        duplicate=False,
        now=NOW,
        daily_max=5,
        min_interval_seconds=120,
        max_open=20,
    )
    assert decision.allowed is False
    assert decision.status == 429
    assert "잠시" in decision.detail


def test_blocks_duplicate_body():
    decision = evaluate_suggestion_create(
        is_admin=False,
        today_count=1,
        open_count=1,
        last_created_at=NOW - timedelta(hours=1),
        duplicate=True,
        now=NOW,
        daily_max=5,
        min_interval_seconds=120,
        max_open=20,
    )
    assert decision.allowed is False
    assert decision.status == 409


def test_blocks_too_many_open_notes():
    decision = evaluate_suggestion_create(
        is_admin=False,
        today_count=0,
        open_count=20,
        last_created_at=None,
        duplicate=False,
        now=NOW,
        daily_max=5,
        min_interval_seconds=120,
        max_open=20,
    )
    assert decision.allowed is False
    assert decision.status == 429


def test_allows_normal_member_post():
    decision = evaluate_suggestion_create(
        is_admin=False,
        today_count=1,
        open_count=2,
        last_created_at=NOW - timedelta(minutes=5),
        duplicate=False,
        now=NOW,
        daily_max=5,
        min_interval_seconds=120,
        max_open=20,
    )
    assert decision.allowed is True
