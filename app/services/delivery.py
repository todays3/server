"""Kakao send + attach send-layer latency onto the matching CrawlRun."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CrawlRun, Digest, User
from app.services.delivery_policy import (
    is_retryable_error,
    schedule_retry,
    should_attempt_delivery,
)
from app.services.fcm import notify_digest_sent
from app.services.kakao import send_digest_via_kakao
from app.services.pipeline_timing import elapsed_ms, total_ms
from app.services.run_resources import apply_peak_to_run, peak_sampler


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    error: str = ""
    skipped: bool = False


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def apply_send_timing(
    db: Session,
    digest_id: int | None,
    *,
    send_ms: int,
    wait_ms: int,
    sent_at: datetime | None,
) -> CrawlRun | None:
    if digest_id is None:
        return None
    row = db.scalar(select(CrawlRun).where(CrawlRun.digest_id == digest_id).order_by(CrawlRun.id.desc()))
    if row is None:
        return None
    row.send_ms = max(0, int(send_ms))
    row.wait_ms = max(0, int(wait_ms))
    row.sent_at = sent_at
    row.total_ms = total_ms(row)
    db.add(row)
    return row


def wait_ms_since_ready(row: CrawlRun | None, send_started: datetime) -> int:
    if row is None:
        return 0
    ready = _as_utc(row.ready_at) or _as_utc(row.created_at)
    start = _as_utc(send_started)
    if ready is None or start is None:
        return 0
    return max(0, int((start - ready).total_seconds() * 1000))


async def deliver_digest(
    db: Session,
    user: User,
    digest: Digest,
    *,
    wait_ms: int | None = None,
    force: bool = False,
    reset_attempts: bool = False,
) -> DeliveryResult:
    now = datetime.now(timezone.utc)
    if not should_attempt_delivery(digest, now, force=force):
        return DeliveryResult(ok=False, error=digest.error_message or "retry scheduled", skipped=True)

    run = db.scalar(select(CrawlRun).where(CrawlRun.digest_id == digest.id).order_by(CrawlRun.id.desc()))
    send_started = now
    measured_wait = wait_ms if wait_ms is not None else wait_ms_since_ready(run, send_started)

    if reset_attempts:
        digest.attempt_count = 0
        digest.next_retry_at = None
        digest.miss_notified = False

    digest.status = "sending"
    digest.attempt_count = int(digest.attempt_count or 0) + 1
    digest.error_message = ""
    db.add(digest)
    db.commit()
    db.refresh(digest)

    started = perf_counter()
    with peak_sampler() as peak:
        ok, err, chunks_sent = await send_digest_via_kakao(
            user,
            digest.title,
            digest.body,
            db=db,
            chunks_sent=int(digest.chunks_sent or 0),
        )
    if not ok and err:
        # send_digest_via_kakao already classifies; keep chunks_sent from return
        pass

    digest.chunks_sent = chunks_sent
    finished = datetime.now(timezone.utc)

    if ok:
        digest.status = "sent"
        digest.error_message = ""
        digest.next_retry_at = None
        digest.sent_at = finished
        notify_digest_sent(db, user.id, digest.title, digest.body)
    else:
        digest.status = "failed"
        digest.error_message = err
        digest.sent_at = None
        if is_retryable_error(err) and schedule_retry(digest, finished):
            pass
        else:
            digest.next_retry_at = None

    timed = apply_send_timing(
        db,
        digest.id,
        send_ms=elapsed_ms(started),
        wait_ms=measured_wait,
        sent_at=digest.sent_at if ok else None,
    )
    if timed is not None:
        apply_peak_to_run(timed, peak)
    db.add(digest)
    db.commit()
    db.refresh(digest)
    return DeliveryResult(ok=ok, error=err, skipped=False)
