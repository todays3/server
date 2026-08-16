"""Nearby send slots share one crawl, then personalize per user."""

from __future__ import annotations

import time
from datetime import datetime
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


def _engine(tmp_path):
    return create_engine(
        f"sqlite:///{tmp_path / 'sched.db'}",
        connect_args={"check_same_thread": False},
    )


def _seed_users(session, *, emails: list[str], send_times: str) -> None:
    for email in emails:
        user = User(
            email=email,
            display_name=email.split("@")[0],
            password_hash=hash_password("abcdefgh"),
            status="approved",
        )
        session.add(user)
        session.flush()
        session.add(
            Preference(
                user_id=user.id,
                topics="경제",
                timezone="Asia/Seoul",
                enabled=True,
                send_times=send_times,
                send_hour=7,
                send_minute=30,
            )
        )
    session.commit()


@pytest.mark.asyncio
async def test_tick_runs_two_users_in_parallel(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    db = TestingSession()
    _seed_users(db, emails=["a@example.com", "b@example.com"], send_times="07:30")
    db.close()

    tz = ZoneInfo("Asia/Seoul")
    monkeypatch.setattr(sch, "SessionLocal", TestingSession)
    monkeypatch.setattr(sch, "aware_now", lambda _tz: datetime(2026, 8, 15, 7, 30, tzinfo=tz))
    monkeypatch.setattr(sch, "suggested_lead_seconds_from_db", lambda _db: 0)
    monkeypatch.setattr(sch, "max_parallel_users", lambda: 3)

    items = [
        SourceItem(kind="아티클", title="A", url="https://a.example", summary="s", source="s"),
        SourceItem(kind="유튜브", title="B", url="https://b.example", summary="s", source="s"),
        SourceItem(kind="커뮤니티", title="C", url="https://c.example", summary="s", source="s"),
    ]
    gathers = {"n": 0}

    def counting_gather(*_a, **_k):
        gathers["n"] += 1
        time.sleep(0.05)
        return items

    monkeypatch.setattr("app.services.shared_crawl.gather_candidates", counting_gather)
    monkeypatch.setattr("app.services.digest.gather_candidates", counting_gather)

    async def send_ok(*_a, **_k):
        return True, ""

    monkeypatch.setattr("app.services.delivery.send_digest_via_kakao", send_ok)
    sch._sent_slots.clear()
    sch._prepared_slots.clear()
    clear_shared_crawls()

    await sch.tick_morning_digests()

    db = TestingSession()
    sent = db.query(Digest).filter(Digest.status == "sent").all()
    db.close()
    assert len(sent) == 2
    assert gathers["n"] == 1


@pytest.mark.asyncio
async def test_one_user_failure_does_not_block_the_other(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    db = TestingSession()
    _seed_users(db, emails=["ok@example.com", "bad@example.com"], send_times="07:30")
    db.close()

    tz = ZoneInfo("Asia/Seoul")
    monkeypatch.setattr(sch, "SessionLocal", TestingSession)
    monkeypatch.setattr(sch, "aware_now", lambda _tz: datetime(2026, 8, 15, 7, 30, tzinfo=tz))
    monkeypatch.setattr(sch, "suggested_lead_seconds_from_db", lambda _db: 0)
    items = [
        SourceItem(kind="아티클", title="A", url="https://a.example", summary="s", source="s"),
        SourceItem(kind="유튜브", title="B", url="https://b.example", summary="s", source="s"),
        SourceItem(kind="커뮤니티", title="C", url="https://c.example", summary="s", source="s"),
    ]
    monkeypatch.setattr("app.services.shared_crawl.gather_candidates", lambda *a, **k: items)
    monkeypatch.setattr("app.services.digest.gather_candidates", lambda *a, **k: items)

    async def send_maybe(user, title, body, db=None):
        _ = (title, body, db)
        if user.email == "bad@example.com":
            raise RuntimeError("kakao down")
        return True, ""

    monkeypatch.setattr("app.services.delivery.send_digest_via_kakao", send_maybe)
    sch._sent_slots.clear()
    sch._prepared_slots.clear()
    clear_shared_crawls()

    await sch.tick_morning_digests()

    db = TestingSession()
    rows = {d.user.email: d.status for d in db.query(Digest).all()}
    db.close()
    assert rows["ok@example.com"] == "sent"
    assert "bad@example.com" in rows


def test_max_parallel_users_clamps(monkeypatch):
    monkeypatch.setenv("SCHEDULER_MAX_PARALLEL", "99")
    assert sch.max_parallel_users() == 8
    monkeypatch.setenv("SCHEDULER_MAX_PARALLEL", "0")
    assert sch.max_parallel_users() == 1
    monkeypatch.setenv("SCHEDULER_MAX_PARALLEL", "nope")
    assert sch.max_parallel_users() == 3


@pytest.mark.asyncio
async def test_nearby_send_times_share_one_crawl(tmp_path, monkeypatch):
    engine = _engine(tmp_path)
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    db = TestingSession()
    _seed_users(db, emails=["early@example.com"], send_times="07:30")
    _seed_users(db, emails=["late@example.com"], send_times="07:45")
    db.close()

    tz = ZoneInfo("Asia/Seoul")
    monkeypatch.setattr(sch, "SessionLocal", TestingSession)
    monkeypatch.setattr(sch, "aware_now", lambda _tz: datetime(2026, 8, 15, 7, 30, tzinfo=tz))
    monkeypatch.setattr(sch, "suggested_lead_seconds_from_db", lambda _db: 900)
    items = [
        SourceItem(kind="아티클", title="A", url="https://a.example", summary="s", source="s"),
        SourceItem(kind="유튜브", title="B", url="https://b.example", summary="s", source="s"),
        SourceItem(kind="커뮤니티", title="C", url="https://c.example", summary="s", source="s"),
    ]
    gathers = {"n": 0}

    def counting_gather(*_a, **_k):
        gathers["n"] += 1
        return items

    monkeypatch.setattr("app.services.shared_crawl.gather_candidates", counting_gather)
    monkeypatch.setattr("app.services.digest.gather_candidates", counting_gather)

    async def send_ok(*_a, **_k):
        return True, ""

    monkeypatch.setattr("app.services.delivery.send_digest_via_kakao", send_ok)
    sch._sent_slots.clear()
    sch._prepared_slots.clear()
    clear_shared_crawls()

    await sch.tick_morning_digests()
    assert gathers["n"] == 1
