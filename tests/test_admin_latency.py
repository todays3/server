"""Admin E2E latency dashboard API."""

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
            kinds_json='{"아티클": 10}',
            total_count=10,
            trigger_ms=2,
            crawl_ms=4000,
            aggregation_ms=12,
            llm_ms=1800,
            format_ms=3,
            wait_ms=240000,
            send_ms=250,
            total_ms=246067,
            lead_ms=480000,
            curator="llm",
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
async def test_latency_requires_admin(client: AsyncClient, db_session):
    member = db_session.query(User).filter_by(email="user@example.com").one()
    res = await client.get(
        "/api/v1/admin/latency",
        headers={"Authorization": f"Bearer {create_access_token(member.id)}"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_list_latency(client: AsyncClient, db_session):
    admin = db_session.query(User).filter_by(email="admin@example.com").one()
    res = await client.get(
        "/api/v1/admin/latency",
        headers={"Authorization": f"Bearer {create_access_token(admin.id)}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["lead_minutes"] >= 1
    ids = [layer["id"] for layer in body["layers"]]
    assert ids == ["trigger", "crawl", "aggregation", "llm", "format", "wait", "send"]
    row = body["runs"][0]
    assert row["layers"]["crawl"] == 4000
    assert row["layers"]["llm"] == 1800
    assert row["layers"]["send"] == 250
    assert row["prep_ms"] == 5817
    assert row["e2e_ms"] == 246067
    assert row["curator"] == "llm"
