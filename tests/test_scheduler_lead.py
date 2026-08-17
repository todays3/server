"""Scheduler prepares a digest before the slot, then sends at the slot."""

from __future__ import annotations

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


@pytest.mark.asyncio
async def test_tick_prepares_early_then_sends_at_slot(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'lead.db'}",
        connect_args={"check_same_thread": False},
    )
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    db = TestingSession()
    tz = ZoneInfo("Asia/Seoul")
    today = datetime.now(tz)
    user = User(
        email="lead@example.com",
        display_name="선행",
        password_hash=hash_password("abcdefgh"),
        status="approved",
    )
    db.add(user)
    db.flush()
    db.add(
        Preference(
            user_id=user.id,
            topics="경제",
            timezone="Asia/Seoul",
            enabled=True,
            send_times="07:30",
            send_hour=7,
            send_minute=30,
        )
    )
    db.commit()
    db.close()

    clock = {"now": today.replace(hour=7, minute=22, second=0, microsecond=0)}
    monkeypatch.setattr(sch, "SessionLocal", TestingSession)
    monkeypatch.setattr(sch, "aware_now", lambda _tz: clock["now"])
    monkeypatch.setattr(sch, "suggested_lead_seconds_from_db", lambda _db: 480)
    items = [
        SourceItem(kind="아티클", title="A", url="https://a.example", summary="s", source="s"),
        SourceItem(kind="유튜브", title="B", url="https://b.example", summary="s", source="s"),
        SourceItem(kind="커뮤니티", title="C", url="https://c.example", summary="s", source="s"),
    ]
    monkeypatch.setattr("app.services.shared_crawl.gather_candidates", lambda *a, **k: items)
    monkeypatch.setattr("app.services.digest.gather_candidates", lambda *a, **k: items)

    sends: list[int] = []

    async def capture_send(user, title, body, db=None, **_k):
        _ = (user, title, body, db)
        sends.append(1)
        return True, "", 1

    monkeypatch.setattr("app.services.delivery.send_digest_via_kakao", capture_send)
    sch._sent_slots.clear()
    sch._prepared_slots.clear()
    clear_shared_crawls()

    await sch.tick_morning_digests()
    db = TestingSession()
    drafts = db.query(Digest).all()
    db.close()
    assert len(drafts) == 1
    assert drafts[0].status == "draft"
    assert sends == []

    clock["now"] = today.replace(hour=7, minute=30, second=0, microsecond=0)
    await sch.tick_morning_digests()
    db = TestingSession()
    rows = db.query(Digest).all()
    db.close()
    assert len(rows) == 1
    assert rows[0].status == "sent"
    assert len(sends) == 1
