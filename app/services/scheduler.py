"""Digest scheduler at each user's chosen Asia/Seoul send times.

Nearby slots share one in-memory crawl (no extra DB). Personalize + send
still fan out per reservation on a small asyncio pool.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from os import getenv
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db import SessionLocal
from app.models import CrawlRun, Digest, Preference, User
from app.services.delivery import deliver_digest
from app.services.digest import create_digest
from app.services.pipeline_timing import (
    PREPARE_TICK_SECONDS,
    SEND_TICK_SECONDS,
    due_actions,
    suggested_lead_seconds_from_db,
)
from app.services.send_times import format_hm, parse_send_times_raw, slot_set
from app.services.shared_crawl import ensure_shared_crawl, get_shared_items, slice_shared_items
from app.services.slot_cluster import cluster_key, cluster_slot_labels
from app.services.sources import SourceItem
from app.services.live_activity import end_activity, start_activity, update_activity

log = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
_sent_slots: set[tuple[int, str, str]] = set()
_prepared_slots: set[tuple[int, str, str]] = set()
_inflight_prepares: set[tuple[int, str, str]] = set()
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
    cluster_key: str = ""


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
    lead_seconds = suggested_lead_seconds_from_db(db)
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
        for slot_label, phase in due_actions(now, slots, lead_seconds):
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
            # Catch-up only retries never-sent drafts (or partial) — no fresh prepare.
            if phase == "catchup":
                if digest is None or digest.status not in {"draft", "partial", "failed"}:
                    continue
                if digest.status == "failed" and int(digest.attempt_count or 0) >= 3:
                    continue
                phase = "send"
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


def attach_cluster_keys(jobs: list[DueJob], db: Session | None = None) -> list[DueJob]:
    by_day_tz: dict[tuple[str, str], list[DueJob]] = defaultdict(list)
    for job in jobs:
        by_day_tz[(job.day, job.tz_name)].append(job)
    out: list[DueJob] = []
    for (day, tz_name), group in by_day_tz.items():
        labels = {job.slot_label for job in group}
        if db is not None:
            prefs = db.scalars(select(Preference).where(Preference.enabled.is_(True))).all()
            for pref in prefs:
                if (pref.timezone or "Asia/Seoul") != tz_name:
                    continue
                for hour, minute in slot_set(
                    parse_send_times_raw(pref.send_times, hour=pref.send_hour, minute=pref.send_minute)
                ):
                    labels.add(format_hm(hour, minute))
        mapping: dict[str, str] = {}
        for members in cluster_slot_labels(list(labels)):
            start = members[0]
            for label in members:
                mapping[label] = cluster_key(day, start)
        for job in group:
            out.append(replace(job, cluster_key=mapping.get(job.slot_label, cluster_key(day, job.slot_label))))
    return out


def _split_csv(raw: str | None) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def union_topics_and_sites(db: Session, jobs: list[DueJob]) -> tuple[list[str], list[str]]:
    topics: list[str] = []
    sites: list[str] = []
    seen_topics: set[str] = set()
    seen_sites: set[str] = set()
    for job in jobs:
        user = db.get(User, job.user_id)
        pref = None if user is None else user.preference
        if pref is None:
            continue
        for topic in _split_csv(pref.topics):
            if topic not in seen_topics:
                seen_topics.add(topic)
                topics.append(topic)
        for site in _split_csv(pref.sources):
            if site not in seen_sites:
                seen_sites.add(site)
                sites.append(site)
    return topics, sites


def _candidates_for_job(job: DueJob, pref: Preference) -> list[SourceItem] | None:
    if not job.cluster_key:
        return None
    pool = get_shared_items(job.cluster_key)
    if pool is None:
        return None
    from app.services.domain_gate import topic_site_ids_for_roles, topic_site_ids_for_topics
    from app.services.roles import parse_roles

    sites = _split_csv(pref.sources)
    roles = parse_roles(getattr(pref, "roles", "") or "")
    topics = _split_csv(pref.topics)
    extra = topic_site_ids_for_roles(roles) | topic_site_ids_for_topics(topics)
    return slice_shared_items(pool, sites=sites + sorted(extra))


def _create_scheduled_digest(
    user_id: int,
    slot_label: str,
    lead_ms: int,
    cluster_key_value: str = "",
    activity_id: str | None = None,
) -> int | None:
    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        if user is None or user.preference is None:
            return None
        job = DueJob(
            user_id=user_id,
            slot_label=slot_label,
            phase="prepare",
            day="",
            tz_name="",
            lead_ms=lead_ms,
            cluster_key=cluster_key_value,
        )

        def on_progress(step: str) -> None:
            if activity_id:
                update_activity(activity_id, phase=step)

        digest = create_digest(
            db,
            user,
            user.preference,
            status="draft",
            trigger="schedule",
            slot_label=slot_label,
            lead_ms=lead_ms,
            shared_candidates=_candidates_for_job(job, user.preference),
            on_progress=on_progress if activity_id else None,
        )
        return digest.id
    finally:
        db.close()


async def execute_prepare_job(job: DueJob) -> None:
    """Create a draft digest before the slot; never sends."""
    slot_key = _slot_key(job.user_id, job.day, job.slot_label)
    db = SessionLocal()
    activity_id: str | None = None
    try:
        user = db.get(User, job.user_id)
        if user is None or user.status != "approved":
            return
        now = aware_now(ZoneInfo(job.tz_name))
        if _already_sent(db, user, job.slot_label, now):
            with _state_lock:
                _sent_slots.add(slot_key)
            return

        digest = _find_prepared_digest(db, user.id, job.slot_label, now)
        if digest is not None:
            with _state_lock:
                _prepared_slots.add(slot_key)
            return

        with _state_lock:
            if slot_key in _inflight_prepares:
                return
            _inflight_prepares.add(slot_key)
        activity_id = start_activity(
            kind="prepare",
            label=f"{user.display_name or user.email} · {job.slot_label} 작성",
            phase="crawl",
            detail="draft 생성",
            user_id=user.id,
            display_name=user.display_name or user.email,
            slot_label=job.slot_label,
            cluster_key=job.cluster_key,
        )
        try:
            digest_id = await asyncio.to_thread(
                _create_scheduled_digest,
                job.user_id,
                job.slot_label,
                job.lead_ms,
                job.cluster_key,
                activity_id,
            )
        finally:
            with _state_lock:
                _inflight_prepares.discard(slot_key)
                _prepared_slots.add(slot_key)
        if digest_id is None:
            return
    finally:
        if activity_id:
            end_activity(activity_id)
        db.close()


async def execute_send_job(job: DueJob) -> None:
    """Deliver at/after slot. Skips crawl when a draft already exists."""
    slot_key = _slot_key(job.user_id, job.day, job.slot_label)
    db = SessionLocal()
    activity_id: str | None = None
    try:
        user = db.get(User, job.user_id)
        if user is None or user.status != "approved":
            return
        tz = ZoneInfo(job.tz_name)
        now = aware_now(tz)
        if _already_sent(db, user, job.slot_label, now):
            with _state_lock:
                _sent_slots.add(slot_key)
            return

        hour, minute = (int(p) for p in job.slot_label.split(":", 1))
        slot_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < slot_dt:
            return

        digest = _find_prepared_digest(db, user.id, job.slot_label, now)
        if digest is not None and digest.status == "sent":
            with _state_lock:
                _sent_slots.add(slot_key)
            return

        if digest is None:
            with _state_lock:
                preparing = slot_key in _inflight_prepares
            if preparing:
                return

        activity_id = start_activity(
            kind="send",
            label=f"{user.display_name or user.email} · {job.slot_label} 전송",
            phase="send" if digest is not None else "crawl",
            detail="deliver_digest",
            user_id=user.id,
            display_name=user.display_name or user.email,
            slot_label=job.slot_label,
            cluster_key=job.cluster_key,
        )

        if digest is None:
            update_activity(activity_id, phase="crawl", detail="draft 없음 · 즉시 작성")
            digest_id = await asyncio.to_thread(
                _create_scheduled_digest,
                job.user_id,
                job.slot_label,
                job.lead_ms,
                job.cluster_key,
                activity_id,
            )
            with _state_lock:
                _prepared_slots.add(slot_key)
            if digest_id is None:
                return
            digest = db.get(Digest, digest_id)
            if digest is None:
                return

        db.refresh(user)
        update_activity(activity_id, phase="send", detail="카카오 전송")
        result = await deliver_digest(db, user, digest)
        if result.ok:
            with _state_lock:
                _sent_slots.add(slot_key)
                _prepared_slots.add(slot_key)
    finally:
        if activity_id:
            end_activity(activity_id)
        db.close()


async def execute_due_job(job: DueJob) -> None:
    """Backward-compatible entry: prepare-only or send depending on phase."""
    if job.phase == "prepare":
        await execute_prepare_job(job)
        return
    await execute_send_job(job)


async def _run_jobs(jobs: list[DueJob], runner) -> None:
    if not jobs:
        return
    sem = asyncio.Semaphore(max_parallel_users())

    async def _run(job: DueJob) -> None:
        async with sem:
            try:
                await runner(job)
            except Exception:
                log.exception(
                    "scheduled digest failed user_id=%s slot=%s phase=%s",
                    job.user_id,
                    job.slot_label,
                    job.phase,
                )

    await asyncio.gather(*(_run(job) for job in jobs))


async def _run_clustered_prepare(jobs: list[DueJob]) -> None:
    db = SessionLocal()
    try:
        unions = {key: union_topics_and_sites(db, group) for key, group in _jobs_by_cluster(jobs).items()}
    finally:
        db.close()

    async def _process_cluster(key: str, group: list[DueJob]) -> None:
        topics, sites = unions.get(key, ([], []))
        await asyncio.to_thread(ensure_shared_crawl, key, topics, sites)
        await _run_jobs(group, execute_prepare_job)

    clusters = _jobs_by_cluster(jobs)
    await asyncio.gather(*(_process_cluster(key, group) for key, group in clusters.items()))


def _collect_keyed_jobs(db: Session) -> list[DueJob]:
    return attach_cluster_keys(collect_due_jobs(db), db)


async def tick_send_digests() -> None:
    db = SessionLocal()
    try:
        jobs = [job for job in _collect_keyed_jobs(db) if job.phase == "send"]
    finally:
        db.close()
    if not jobs:
        return

    async def _process_cluster(key: str, group: list[DueJob]) -> None:
        need_crawl = False
        db = SessionLocal()
        try:
            for job in group:
                tz = ZoneInfo(job.tz_name)
                now = aware_now(tz)
                digest = _find_prepared_digest(db, job.user_id, job.slot_label, now)
                if digest is None:
                    need_crawl = True
                    break
            topics, sites = union_topics_and_sites(db, group) if need_crawl else ([], [])
        finally:
            db.close()
        if need_crawl:
            await asyncio.to_thread(ensure_shared_crawl, key, topics, sites)
        await _run_jobs(group, execute_send_job)

    clusters = _jobs_by_cluster(jobs)
    await asyncio.gather(*(_process_cluster(key, group) for key, group in clusters.items()))


async def tick_prepare_digests() -> None:
    db = SessionLocal()
    try:
        jobs = [job for job in _collect_keyed_jobs(db) if job.phase == "prepare"]
    finally:
        db.close()
    if not jobs:
        return
    await _run_clustered_prepare(jobs)


async def tick_morning_digests() -> None:
    await tick_send_digests()
    await tick_prepare_digests()


def _jobs_by_cluster(jobs: list[DueJob]) -> dict[str, list[DueJob]]:
    grouped: dict[str, list[DueJob]] = defaultdict(list)
    for job in jobs:
        grouped[job.cluster_key or job.slot_label].append(job)
    return grouped


async def tick_dart_ingest() -> None:
    """Off-peak DART zip ingest. Isolated from digest ticks; no-ops unless enabled."""
    from fastapi.concurrency import run_in_threadpool

    from app.config import get_settings
    from app.services.dart_ingest import run_dart_ingest_sync

    if not get_settings().dart_ingest_enabled:
        return
    try:
        await run_in_threadpool(run_dart_ingest_sync)
    except Exception:
        log.exception("dart ingest job failed")


def start_scheduler() -> None:
    if scheduler.running:
        return
    scheduler.add_job(
        tick_send_digests,
        "interval",
        seconds=SEND_TICK_SECONDS,
        id="digest-send",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        tick_prepare_digests,
        "interval",
        seconds=PREPARE_TICK_SECONDS,
        id="digest-prepare",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        tick_dart_ingest,
        "cron",
        hour=3,
        minute=17,
        timezone="Asia/Seoul",
        id="dart-ingest",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
