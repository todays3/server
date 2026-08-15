"""Admin crawl-run history API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_access_token, hash_password
from app.db import Base, get_db
from app.main import app
from app.models import CrawlRun, Preference, User
from app.services.sources import SourceItem


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSession()
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
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest_asyncio.fixture()
async def client(db_session) -> AsyncIterator[AsyncClient]:
    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


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
