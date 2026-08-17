"""Spam and abuse rules for member suggestion posts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import StickyNote, User


@dataclass(frozen=True)
class NoteCreateDecision:
    allowed: bool
    status: int = 200
    detail: str = ""


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def naive_utc(dt: datetime | None) -> datetime | None:
    utc = as_utc(dt)
    if utc is None:
        return None
    return utc.replace(tzinfo=None)


def local_day_start(now: datetime, tz_name: str) -> datetime:
    when = as_utc(now) or datetime.now(timezone.utc)
    local = when.astimezone(ZoneInfo(tz_name))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc)


def evaluate_suggestion_create(
    *,
    is_admin: bool,
    today_count: int,
    open_count: int,
    last_created_at: datetime | None,
    duplicate: bool,
    now: datetime,
    daily_max: int,
    min_interval_seconds: float,
    max_open: int,
) -> NoteCreateDecision:
    if is_admin:
        return NoteCreateDecision(allowed=True)
    if duplicate:
        return NoteCreateDecision(allowed=False, status=409, detail="같은 내용의 쪽지가 이미 있습니다")
    if open_count >= max_open:
        return NoteCreateDecision(
            allowed=False,
            status=429,
            detail="붙인 쪽지가 너무 많습니다. 기존 쪽지를 지운 뒤 다시 시도하세요.",
        )
    if today_count >= daily_max:
        return NoteCreateDecision(allowed=False, status=429, detail="하루 건의 횟수를 넘었습니다. 내일 다시 붙여 주세요.")
    last = as_utc(last_created_at)
    when = as_utc(now) or datetime.now(timezone.utc)
    if last is not None and min_interval_seconds > 0:
        elapsed = (when - last).total_seconds()
        if elapsed < min_interval_seconds:
            return NoteCreateDecision(allowed=False, status=429, detail="잠시 후에 다시 붙여 주세요.")
    return NoteCreateDecision(allowed=True)


def suggestion_create_decision(
    db: Session,
    user: User,
    body: str,
    *,
    now: datetime | None = None,
) -> NoteCreateDecision:
    settings = get_settings()
    when = as_utc(now) or datetime.now(timezone.utc)
    day_start = naive_utc(local_day_start(when, settings.default_timezone))
    duplicate_after = naive_utc(when - timedelta(hours=settings.notes_duplicate_window_hours))

    today_count = db.scalar(
        select(func.count())
        .select_from(StickyNote)
        .where(
            StickyNote.user_id == user.id,
            StickyNote.kind == "suggestion",
            StickyNote.created_at >= day_start,
        )
    ) or 0
    open_count = db.scalar(
        select(func.count())
        .select_from(StickyNote)
        .where(StickyNote.user_id == user.id, StickyNote.kind == "suggestion")
    ) or 0
    last_created_at = db.scalar(
        select(StickyNote.created_at)
        .where(StickyNote.user_id == user.id, StickyNote.kind == "suggestion")
        .order_by(StickyNote.created_at.desc(), StickyNote.id.desc())
        .limit(1)
    )
    duplicate = db.scalar(
        select(StickyNote.id)
        .where(
            StickyNote.user_id == user.id,
            StickyNote.kind == "suggestion",
            StickyNote.body == body,
            StickyNote.created_at >= duplicate_after,
        )
        .limit(1)
    ) is not None
    return evaluate_suggestion_create(
        is_admin=bool(user.is_admin),
        today_count=int(today_count),
        open_count=int(open_count),
        last_created_at=last_created_at,
        duplicate=duplicate,
        now=when,
        daily_max=settings.notes_daily_max,
        min_interval_seconds=settings.notes_min_interval_seconds,
        max_open=settings.notes_max_open,
    )
