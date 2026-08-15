"""Digest scheduler at each user's chosen Asia/Seoul send times.

Personalized prep (crawl + LLM) is blocking I/O, so a few users run
concurrently via a bounded asyncio semaphore + worker threads.
One OS thread per user is unnecessary at 2–3 recipients; a cap of 3
overlaps Groq/Kakao/RSS waits without extra SQLite writer stampede.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from os import getenv
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db import SessionLocal
from app.models import CrawlRun, Digest, Preference, User
from app.services.delivery import deliver_digest
from app.services.digest import create_digest
from app.services.pipeline_timing import due_actions, suggested_lead_minutes_from_db
from app.services.send_times import parse_send_times_raw, slot_set

log = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
_sent_slots: set[tuple[int, str, str]] = set()
_prepared_slots: set[tuple[int, str, str]] = set()
_state_lock = threading.Lock()

DEFAULT_MAX_PARALLEL = 3
MAX_PARALLEL_CAP = 8


def max_parallel_users() -> int:
    raw = getenv("SCHEDULER_MAX_PARALLEL", str(DEFAULT_MAX_PARALLEL)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_MAX_PARALLEL
    return max(1, min(MAX_PARALLEL_CAP, value))


@dataclass(frozen=True)
class DueJob:
    user_id: int
    slot_label: str
    phase: str
    day: str
    tz_name: str
    lead_ms: int


def aware_now(tz: ZoneInfo) -> datetime:
    return datetime.now(tz)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _slot_key(user_id: int, day: str, slot_label: str) -> tuple[int, str, str]:
    return (user_id, day, slot_label)


def _on_local_day(dt: datetime | None, now: datetime) -> bool:
    utc = _as_utc(dt)
    if utc is None:
        return True
    return utc.astimezone(now.tzinfo).date() == now.date()


def _already_sent(db: Session, user: User, slot_label: str, now: datetime) -> bool:
    hour, minute = (int(p) for p in slot_label.split(":", 1))
    existing = db.scalars(
        select(Digest)
        .where(Digest.user_id == user.id, Digest.status == "sent")
        .order_by(Digest.created_at.desc())
        .limit(20)
    ).all()
    for digest in existing:
        if digest.sent_at is None:
            continue
        local = _as_utc(digest.sent_at)
        if local is None:
            continue
        local = local.astimezone(now.tzinfo)
        if local.date() == now.date() and local.hour == hour and local.minute == minute:
            return True
    runs = db.scalars(
        select(CrawlRun)
        .where(
            CrawlRun.user_id == user.id,
            CrawlRun.trigger == "schedule",
            CrawlRun.slot_label == slot_label,
        )
        .order_by(CrawlRun.id.desc())
        .limit(10)
    ).all()
    for run in runs:
        if run.sent_at is None:
            continue
        if _on_local_day(run.sent_at, now):
            return True
    return False


def _find_prepared_digest(db: Session, user_id: int, slot_label: str, now: datetime) -> Digest | None:
    runs = db.scalars(
        select(CrawlRun)
        .where(
            CrawlRun.user_id == user_id,
            CrawlRun.trigger == "schedule",
            CrawlRun.slot_label == slot_label,
        )
        .order_by(CrawlRun.id.desc())
        .limit(10)
    ).all()
    for run in runs:
        if not _on_local_day(run.created_at, now):
            continue
        if run.digest_id is None:
            continue
        digest = db.get(Digest, run.digest_id)
        if digest is not None:
            return digest
    return None


def _lead_ms_for(now: datetime, slot_label: str) -> int:
    hour, minute = (int(p) for p in slot_label.split(":", 1))
    slot_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return max(0, int((slot_dt - now).total_seconds() * 1000))


def collect_due_jobs(db: Session) -> list[DueJob]:
    prefs = db.scalars(
        select(Preference)
        .where(Preference.enabled.is_(True))
        .options(joinedload(Preference.user).joinedload(User.kakao))
    ).all()
    lead_minutes = suggested_lead_minutes_from_db(db)
    jobs: list[DueJob] = []
    for pref in prefs:
        user = pref.user
        if user is None or user.status != "approved":
            continue
        tz_name = pref.timezone or "Asia/Seoul"
        tz = ZoneInfo(tz_name)
        now = aware_now(tz)
        slots = slot_set(
            parse_send_times_raw(pref.send_times, hour=pref.send_hour, minute=pref.send_minute)
        )
        day = now.date().isoformat()
        for slot_label, phase in due_actions(now, slots, lead_minutes):
            slot_key = _slot_key(user.id, day, slot_label)
            with _state_lock:
                already_marked = slot_key in _sent_slots
                prepared = slot_key in _prepared_slots
            if already_marked:
                continue
            if _already_sent(db, user, slot_label, now):
                with _state_lock:
                    _sent_slots.add(slot_key)
                continue
            digest = _find_prepared_digest(db, user.id, slot_label, now)
            if digest is not None and digest.status == "sent":
                with _state_lock:
                    _sent_slots.add(slot_key)
                continue
            if digest is None and phase == "prepare" and prepared:
                continue
            jobs.append(
                DueJob(
                    user_id=user.id,
                    slot_label=slot_label,
                    phase=phase,
                    day=day,
                    tz_name=tz_name,
                    lead_ms=_lead_ms_for(now, slot_label),
                )
            )
    return jobs


def _create_scheduled_digest(user_id: int, slot_label: str, lead_ms: int) -> int | None:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None or user.preference is None:
            return None
        digest = create_digest(
            db,
            user,
            user.preference,
            status="draft",
            trigger="schedule",
            slot_label=slot_label,
            lead_ms=lead_ms,
        )
        return digest.id
    finally:
        db.close()


async def execute_due_job(job: DueJob) -> None:
    tz = ZoneInfo(job.tz_name)
    slot_key = _slot_key(job.user_id, job.day, job.slot_label)
    db = SessionLocal()
    try:
        user = db.get(User, job.user_id)
        if user is None or user.status != "approved":
            return
        now = aware_now(tz)
        if _already_sent(db, user, job.slot_label, now):
            with _state_lock:
                _sent_slots.add(slot_key)
            return

        digest = _find_prepared_digest(db, user.id, job.slot_label, now)
        if digest is not None and digest.status == "sent":
            with _state_lock:
                _sent_slots.add(slot_key)
            return

        if digest is None:
            digest_id = await asyncio.to_thread(
                _create_scheduled_digest, job.user_id, job.slot_label, job.lead_ms
            )
            with _state_lock:
                _prepared_slots.add(slot_key)
            if digest_id is None:
                return
            digest = db.get(Digest, digest_id)
            if digest is None:
                return

        hour, minute = (int(p) for p in job.slot_label.split(":", 1))
        slot_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        clock = aware_now(tz)
        if job.phase == "prepare" and clock < slot_dt:
            return

        db.refresh(user)
        ok, _err = await deliver_digest(db, user, digest)
        if ok:
            with _state_lock:
                _sent_slots.add(slot_key)
                _prepared_slots.add(slot_key)
    finally:
        db.close()


async def tick_morning_digests() -> None:
    db = SessionLocal()
    try:
        jobs = collect_due_jobs(db)
    finally:
        db.close()
    if not jobs:
        return
    sem = asyncio.Semaphore(max_parallel_users())

    async def _run(job: DueJob) -> None:
        async with sem:
            try:
                await execute_due_job(job)
            except Exception:
                log.exception(
                    "scheduled digest failed user_id=%s slot=%s",
                    job.user_id,
                    job.slot_label,
                )

    await asyncio.gather(*(_run(job) for job in jobs))


def start_scheduler() -> None:
    if scheduler.running:
        return
    scheduler.add_job(
        tick_morning_digests,
        "interval",
        minutes=1,
        id="morning-digest",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
