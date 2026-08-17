from datetime import datetime, timedelta, timezone

import pytest

from app.services.delivery_policy import (
    MAX_DELIVERY_ATTEMPTS,
    compute_next_retry_at,
    is_retryable_error,
    schedule_retry,
    sending_is_stale,
    should_attempt_delivery,
)


class _Digest:
    def __init__(self, **kwargs):
        self.status = kwargs.get("status", "draft")
        self.attempt_count = kwargs.get("attempt_count", 0)
        self.next_retry_at = kwargs.get("next_retry_at")
        self.created_at = kwargs.get("created_at", datetime.now(timezone.utc))
        self.updated_at = kwargs.get("updated_at", self.created_at)


def test_compute_next_retry_exponential():
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    first = compute_next_retry_at(1, now)
    second = compute_next_retry_at(2, now)
    assert first == now + timedelta(seconds=20)
    assert second == now + timedelta(seconds=40)


def test_should_attempt_respects_next_retry_at():
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    digest = _Digest(status="failed", attempt_count=1, next_retry_at=now + timedelta(minutes=1))
    assert should_attempt_delivery(digest, now) is False
    assert should_attempt_delivery(digest, now + timedelta(minutes=2)) is True


def test_should_attempt_force_overrides_retry():
    digest = _Digest(status="failed", attempt_count=3, next_retry_at=None)
    assert should_attempt_delivery(digest, force=True) is True


def test_should_not_retry_after_max_attempts():
    digest = _Digest(status="failed", attempt_count=MAX_DELIVERY_ATTEMPTS, next_retry_at=None)
    assert should_attempt_delivery(digest) is False


def test_sending_is_stale_after_timeout():
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    digest = _Digest(status="sending", updated_at=now - timedelta(seconds=400))
    assert sending_is_stale(digest, now) is True


def test_schedule_retry_sets_next_retry_or_stops():
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    digest = _Digest(status="failed", attempt_count=1)
    assert schedule_retry(digest, now) is True
    assert digest.next_retry_at == now + timedelta(seconds=20)

    exhausted = _Digest(status="failed", attempt_count=MAX_DELIVERY_ATTEMPTS)
    assert schedule_retry(exhausted, now) is False
    assert exhausted.next_retry_at is None


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Kakao account is not connected", False),
        ("token refresh failed: boom", False),
        ("HTTP 503 upstream", True),
        ("network timeout", True),
    ],
)
def test_is_retryable_error(message, expected):
    assert is_retryable_error(message) is expected
