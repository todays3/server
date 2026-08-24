"""Scheduler start/stop idempotency and morning-tick skip/send paths."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import hash_password
from app.db import Base
from app.models import Digest, Preference, User
from app.services import scheduler as sch
from app.services.shared_crawl import clear_shared_crawls
from app.services.sources import SourceItem


def test_start_and_stop_scheduler_are_idempotent(monkeypatch):
    class FakeSched:
        running = False

        def add_job(self, *_a, **_k):
            return None

        def start(self):
            self.running = True

        def shutdown(self, wait=False):
            self.running = False

    monkeypatch.setattr(sch, "scheduler", FakeSched())
    sch.start_scheduler()
    sch.start_scheduler()
    sch.stop_scheduler()
    sch.stop_scheduler()


@pytest.mark.asyncio
async def test_tick_morning_digests_skips_then_sends(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'tick.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    db = TestingSession()
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    user = User(
        email="slot@example.com",
        display_name="슬롯",
        password_hash=hash_password("abcdefgh"),
        status="approved",
    )
    pending = User(
        email="p2@example.com",
        display_name="대기",
        password_hash=hash_password("abcdefgh"),
        status="pending",
    )
    db.add_all([user, pending])
    db.flush()
    db.add(
        Preference(
            user_id=user.id,
            topics="경제",
            timezone="Asia/Seoul",
            enabled=True,
            send_times=f"{now.hour:02d}:{now.minute:02d}",
            send_hour=now.hour,
            send_minute=now.minute,
        )
    )
    db.add(Preference(user_id=pending.id, topics="경제", enabled=True, timezone="Asia/Seoul"))
    digest = Digest(
        user_id=user.id,
        title="이미보냄",
        body="x",
        status="sent",
        sent_at=datetime.now(timezone.utc),
    )
    db.add(digest)
    db.commit()
    digest.sent_at = now.astimezone(timezone.utc)
    db.commit()
    db.close()

    monkeypatch.setattr(sch, "SessionLocal", TestingSession)
    sch._sent_slots.clear()
    sch._prepared_slots.clear()
    sch._inflight_prepares.clear()
    await sch.tick_morning_digests()
    await sch.tick_morning_digests()

    db = TestingSession()
    user = db.query(User).filter(User.email == "slot@example.com").one()
    pref = user.preference
    pref.send_times = f"{(now.hour + 1) % 24:02d}:{now.minute:02d}"
    db.commit()
    db.close()
    await sch.tick_morning_digests()

    db = TestingSession()
    user = db.query(User).filter(User.email == "slot@example.com").one()
    user.preference.send_times = f"{now.hour:02d}:{now.minute:02d}"
    db.query(Digest).delete()
    db.commit()
    db.close()
    sch._sent_slots.clear()
    sch._prepared_slots.clear()
    sch._inflight_prepares.clear()
    clear_shared_crawls()

    items = [
        SourceItem(kind="아티클", title="A", url="https://a.example", summary="s", source="s"),
        SourceItem(kind="유튜브", title="B", url="https://b.example", summary="s", source="s"),
        SourceItem(kind="커뮤니티", title="C", url="https://c.example", summary="s", source="s"),
    ]
    monkeypatch.setattr("app.services.shared_crawl.gather_candidates", lambda *a, **k: items)
    monkeypatch.setattr("app.services.digest.gather_candidates", lambda *a, **k: items)

    async def send_ok(*_a, **_k):
        return True, "", 1

    monkeypatch.setattr("app.services.delivery.send_digest_via_kakao", send_ok)
    await sch.tick_morning_digests()


@pytest.mark.asyncio
async def test_send_job_does_not_start_second_create_while_prepare_runs(monkeypatch):
    created = {"n": 0}

    class FakeUser:
        id = 1
        status = "approved"

    class FakeDb:
        def get(self, *_a, **_k):
            return FakeUser()

        def close(self):
            return None

        def refresh(self, *_a, **_k):
            return None

    monkeypatch.setattr(sch, "SessionLocal", FakeDb)
    monkeypatch.setattr(sch, "_already_sent", lambda *_a, **_k: False)
    monkeypatch.setattr(sch, "_find_prepared_digest", lambda *_a, **_k: None)
    monkeypatch.setattr(
        sch, "aware_now", lambda _tz: datetime(2026, 8, 20, 7, 40, tzinfo=ZoneInfo("Asia/Seoul"))
    )
    monkeypatch.setattr(sch, "_create_scheduled_digest", lambda *_a, **_k: created.__setitem__("n", created["n"] + 1) or 99)
    job = sch.DueJob(
        user_id=1,
        slot_label="07:30",
        phase="send",
        day="2026-08-20",
        tz_name="Asia/Seoul",
        lead_ms=40_000,
    )
    sch._inflight_prepares.clear()
    sch._inflight_prepares.add((1, "2026-08-20", "07:30"))
    try:
        await sch.execute_send_job(job)
        assert created["n"] == 0
    finally:
        sch._inflight_prepares.clear()
