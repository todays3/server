"""Retry/backoff rules for Kakao digest delivery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.kakao import KakaoApiError, _is_token_error

MAX_DELIVERY_ATTEMPTS = 3
RETRY_BASE_SECONDS = 20
SENDING_STALE_SECONDS = 300

NON_RETRYABLE_MARKERS = (
    "not connected",
    "insufficient scope",
    "refresh_token is missing",
    "refresh did not return",
    "token refresh failed",
    "나에게 보내기",
)


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_next_retry_at(attempt_count: int, now: datetime | None = None) -> datetime:
    when = as_utc(now) or datetime.now(timezone.utc)
    exponent = max(0, attempt_count - 1)
    delay = RETRY_BASE_SECONDS * (2**exponent)
    return when + timedelta(seconds=delay)


def is_retryable_error(message: str, exc: KakaoApiError | None = None) -> bool:
    text = (message or "").strip().lower()
    if not text:
        return True
    for marker in NON_RETRYABLE_MARKERS:
        if marker in text:
            return False
    if exc is not None:
        if _is_token_error(exc):
            return False
        if exc.status_code in {401, 403}:
            return False
        if exc.status_code in {408, 429, 500, 502, 503, 504}:
            return True
    if "timeout" in text or "network" in text or "connection" in text:
        return True
    if "http 5" in text or "http 408" in text or "http 429" in text:
        return True
    return True


def sending_is_stale(digest, now: datetime | None = None) -> bool:
    when = as_utc(now) or datetime.now(timezone.utc)
    marker = as_utc(getattr(digest, "updated_at", None)) or as_utc(digest.created_at)
    if marker is None:
        return True
    return (when - marker).total_seconds() >= SENDING_STALE_SECONDS


def should_attempt_delivery(digest, now: datetime | None = None, *, force: bool = False) -> bool:
    if digest.status == "sent":
        return False
    if force:
        return True
    when = as_utc(now) or datetime.now(timezone.utc)
    if digest.status == "sending":
        return sending_is_stale(digest, when)
    next_retry = as_utc(getattr(digest, "next_retry_at", None))
    if next_retry is not None and next_retry > when:
        return False
    attempts = int(getattr(digest, "attempt_count", 0) or 0)
    if digest.status == "failed" and attempts >= MAX_DELIVERY_ATTEMPTS and next_retry is None:
        return False
    return True


def schedule_retry(digest, now: datetime | None = None) -> bool:
    attempts = int(getattr(digest, "attempt_count", 0) or 0)
    if attempts >= MAX_DELIVERY_ATTEMPTS:
        digest.next_retry_at = None
        return False
    digest.next_retry_at = compute_next_retry_at(attempts, now)
    return True
