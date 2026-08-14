"""Async API tests (pytest-asyncio + httpx ASGITransport)."""

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
from app.models import Preference, User


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
    session.add(admin)
    session.flush()
    session.add(
        Preference(
            user_id=admin.id,
            topics="기술",
            tone="차분",
            timezone="Asia/Seoul",
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
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_health_openapi_version_header(client: AsyncClient):
    res = await client.get("/api/v1/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert "kakao_configured" in body
    assert res.headers.get("x-api-version") == "v1"


@pytest.mark.asyncio
async def test_openapi_includes_kakao_oauth_paths(client: AsyncClient):
    res = await client.get("/api/v1/openapi.json")
    assert res.status_code == 200
    paths = res.json()["paths"]
    assert "/api/v1/auth/kakao/start" in paths
    assert "/api/v1/auth/kakao/callback" in paths
    assert "/api/v1/auth/kakao/redirect" in paths


@pytest.mark.asyncio
async def test_kakao_start_when_not_configured(client: AsyncClient, monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("KAKAO_REST_API_KEY", "")
    get_settings.cache_clear()

    res = await client.get("/api/v1/auth/kakao/start")
    assert res.status_code == 200
    data = res.json()
    assert data["configured"] is False
    assert data["url"] is None

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_email_login_seed_admin(client: AsyncClient):
    res = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@example.com", "password": "admin12345"},
    )
    assert res.status_code == 200
    assert "access_token" in res.json()


@pytest.mark.asyncio
async def test_me_requires_auth(client: AsyncClient):
    res = await client.get("/api/v1/auth/me")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_me_with_token(client: AsyncClient, db_session):
    user = db_session.query(User).filter_by(email="admin@example.com").one()
    token = create_access_token(user.id)
    res = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["email"] == "admin@example.com"
    assert res.json()["is_admin"] is True


@pytest.mark.asyncio
async def test_register_validation_short_password(client: AsyncClient):
    res = await client.post(
        "/api/v1/auth/register",
        json={"email": "x@example.com", "password": "short", "display_name": "x"},
    )
    assert res.status_code == 422
