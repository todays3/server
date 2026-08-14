"""Digest scheduler at each user's chosen Asia/Seoul send times.

Runs every minute and sends digests when now matches any configured slot,
at most once per slot per calendar day per user.
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
from app.services.send_times import format_hm, parse_send_times_raw, slot_set

scheduler = AsyncIOScheduler()
_sent_slots: set[tuple[int, str, str]] = set()


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
            slots = slot_set(
                parse_send_times_raw(pref.send_times, hour=pref.send_hour, minute=pref.send_minute)
            )
            if (now.hour, now.minute) not in slots:
                continue

            slot_label = format_hm(now.hour, now.minute)
            day = now.date().isoformat()
            slot_key = (user.id, day, slot_label)
            if slot_key in _sent_slots:
                continue

            existing = db.scalars(
                select(Digest)
                .where(Digest.user_id == user.id, Digest.status == "sent")
                .order_by(Digest.created_at.desc())
                .limit(20)
            ).all()
            already = False
            for d in existing:
                if d.sent_at is None:
                    continue
                local = d.sent_at.astimezone(tz)
                if local.date() == now.date() and local.hour == now.hour and local.minute == now.minute:
                    already = True
                    break
            if already:
                _sent_slots.add(slot_key)
                continue

            digest = create_digest(db, user, pref, status="draft")
            ok, err = await send_digest_via_kakao(user, digest.title, digest.body, db=db)
            digest.status = "sent" if ok else "failed"
            digest.error_message = err
            digest.sent_at = datetime.now(timezone.utc) if ok else None
            db.commit()
            if ok:
                _sent_slots.add(slot_key)
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
