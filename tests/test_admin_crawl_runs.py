"""Admin crawl-run history API."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.models import CrawlRun, Preference, User
from app.services.sources import SourceItem


@pytest.fixture()
def db_session(db_engine):
    session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)()
    admin = User(
        email="admin@example.com",
        display_name="관리자",
        password_hash=hash_password("admin12345"),
        status="approved",
        is_admin=True,
    )
    member = User(
        email="user@example.com",
        display_name="테스트",
        password_hash=hash_password("user12345"),
        status="approved",
        is_admin=False,
    )
    session.add_all([admin, member])
    session.flush()
    session.add(Preference(user_id=admin.id, topics="경제", timezone="Asia/Seoul"))
    session.add(Preference(user_id=member.id, topics="경제", timezone="Asia/Seoul"))
    session.add(
        CrawlRun(
            user_id=member.id,
            trigger="schedule",
            slot_label="07:30",
            kinds_json='{"아티클": 10, "유튜브": 3, "커뮤니티": 5}',
            total_count=18,
            created_at=datetime.now(timezone.utc),
        )
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()




@pytest.mark.asyncio
async def test_crawl_runs_require_admin(client: AsyncClient, db_session):
    member = db_session.query(User).filter_by(email="user@example.com").one()
    res = await client.get(
        "/api/v1/admin/crawl-runs",
        headers={"Authorization": f"Bearer {create_access_token(member.id)}"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_list_crawl_runs(client: AsyncClient, db_session):
    admin = db_session.query(User).filter_by(email="admin@example.com").one()
    res = await client.get(
        "/api/v1/admin/crawl-runs",
        headers={"Authorization": f"Bearer {create_access_token(admin.id)}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["total_runs"] == 1
    row = body["runs"][0]
    assert row["trigger"] == "schedule"
    assert row["slot_label"] == "07:30"
    assert row["total"] == 18
    assert row["kinds"]["아티클"] == 10
    assert row["email"] == "user@example.com"


@pytest.mark.asyncio
async def test_create_digest_records_crawl_run(db_session, monkeypatch):
    from app.services.digest import create_digest

    member = db_session.query(User).filter_by(email="user@example.com").one()
    items = [
        SourceItem(kind="아티클", title="A", url="https://a.example", summary="s", source="s"),
        SourceItem(kind="유튜브", title="B", url="https://b.example", summary="s", source="s"),
        SourceItem(kind="커뮤니티", title="C", url="https://c.example", summary="s", source="s"),
    ]
    monkeypatch.setattr("app.services.digest.gather_candidates", lambda *a, **k: items)
    create_digest(db_session, member, member.preference, status="draft", trigger="schedule", slot_label="12:00")
    runs = db_session.query(CrawlRun).filter(CrawlRun.trigger == "schedule", CrawlRun.slot_label == "12:00").all()
    assert len(runs) == 1
    assert runs[0].total_count == 3
    assert runs[0].crawl_ms >= 0
    assert runs[0].trigger_ms >= 0
    assert runs[0].format_ms >= 0
    assert runs[0].cpu_peak_percent >= 0
    assert runs[0].rss_peak_bytes >= 0
