"""Kakao send + attach send-layer latency onto the matching CrawlRun."""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CrawlRun, Digest, User
from app.services.kakao import send_digest_via_kakao
from app.services.pipeline_timing import elapsed_ms, total_ms


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
) -> tuple[bool, str]:
    run = db.scalar(select(CrawlRun).where(CrawlRun.digest_id == digest.id).order_by(CrawlRun.id.desc()))
    send_started = datetime.now(timezone.utc)
    measured_wait = wait_ms if wait_ms is not None else wait_ms_since_ready(run, send_started)
    started = perf_counter()
    ok, err = await send_digest_via_kakao(user, digest.title, digest.body, db=db)
    digest.status = "sent" if ok else "failed"
    digest.error_message = err
    digest.sent_at = datetime.now(timezone.utc) if ok else None
    apply_send_timing(
        db,
        digest.id,
        send_ms=elapsed_ms(started),
        wait_ms=measured_wait,
        sent_at=digest.sent_at,
    )
    db.commit()
    db.refresh(digest)
    return ok, err
