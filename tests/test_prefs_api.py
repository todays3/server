"""Preference HTTP API (integration)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_access_token, hash_password
from app.db import Base, get_db
from app.main import app
from app.models import User


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
    user = User(
        email="user@example.com",
        display_name="테스트",
        password_hash=hash_password("user12345"),
        status="approved",
        is_admin=False,
    )
    session.add(user)
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


def _auth(db_session) -> dict[str, str]:
    user = db_session.query(User).one()
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.mark.asyncio
async def test_get_prefs_creates_default(client: AsyncClient, db_session):
    res = await client.get("/api/v1/prefs", headers=_auth(db_session))
    assert res.status_code == 200
    body = res.json()
    assert body["timezone"] == "Asia/Seoul"
    assert body["send_times"]


@pytest.mark.asyncio
async def test_update_prefs_topics_sources_and_slots(client: AsyncClient, db_session):
    headers = _auth(db_session)
    await client.get("/api/v1/prefs", headers=headers)
    res = await client.put(
        "/api/v1/prefs",
        headers=headers,
        json={
            "topics": ["경제/주식/국내증시", "  "],
            "sources": ["hn", "naver-finance"],
            "send_times": [{"hour": 8, "minute": 0}, {"hour": 18, "minute": 30}],
            "notes": "짧게",
            "insight_questions": True,
            "enabled": True,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["topics"] == ["경제/주식/국내증시"]
    assert body["sources"] == ["hn", "naver-finance"]
    assert body["send_hour"] == 8
    assert body["insight_questions"] is True


@pytest.mark.asyncio
async def test_update_prefs_rejects_empty_topics(client: AsyncClient, db_session):
    headers = _auth(db_session)
    await client.get("/api/v1/prefs", headers=headers)
    res = await client.put("/api/v1/prefs", headers=headers, json={"topics": ["  "]})
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_update_prefs_legacy_hour_minute(client: AsyncClient, db_session):
    headers = _auth(db_session)
    await client.get("/api/v1/prefs", headers=headers)
    res = await client.put("/api/v1/prefs", headers=headers, json={"send_hour": 9, "send_minute": 15})
    assert res.status_code == 200
    assert res.json()["send_hour"] == 9
    assert res.json()["send_minute"] == 15
