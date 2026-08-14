"""Digest scheduler at each user's chosen Asia/Seoul send time.

Runs every minute and sends digests whose send_hour:send_minute matches now (Seoul),
once per calendar day per user.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.db import SessionLocal
from app.models import Digest, Preference, User
from app.services.digest import create_digest
from app.services.kakao import send_digest_via_kakao

scheduler = AsyncIOScheduler()
_sent_today: set[tuple[int, str]] = set()


async def tick_morning_digests() -> None:
    db = SessionLocal()
    try:
        prefs = db.scalars(
            select(Preference)
            .where(Preference.enabled.is_(True))
            .options(joinedload(Preference.user).joinedload(User.kakao))
        ).all()

        for pref in prefs:
            user = pref.user
            if user is None or user.status != "approved":
                continue
            tz = ZoneInfo(pref.timezone or "Asia/Seoul")
            now = datetime.now(tz)
            if now.hour != pref.send_hour or now.minute != pref.send_minute:
                continue

            day_key = (user.id, now.date().isoformat())
            if day_key in _sent_today:
                continue

            # Also skip if we already have a successful send today
            existing = db.scalars(
                select(Digest)
                .where(Digest.user_id == user.id, Digest.status == "sent")
                .order_by(Digest.created_at.desc())
                .limit(5)
            ).all()
            if any(d.sent_at and d.sent_at.astimezone(tz).date() == now.date() for d in existing):
                _sent_today.add(day_key)
                continue

            digest = create_digest(db, user, pref, status="draft")
            ok, err = await send_digest_via_kakao(user, digest.title, digest.body)
            digest.status = "sent" if ok else "failed"
            digest.error_message = err
            digest.sent_at = datetime.now(timezone.utc) if ok else None
            db.commit()
            if ok:
                _sent_today.add(day_key)
    finally:
        db.close()


def start_scheduler() -> None:
    if scheduler.running:
        return
    scheduler.add_job(tick_morning_digests, "interval", minutes=1, id="morning-digest")
    scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
