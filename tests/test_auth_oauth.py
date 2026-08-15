"""Async API tests (pytest-asyncio + httpx ASGITransport)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.models import Preference, User


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
    assert "/api/v1/auth/kakao/complete" in paths


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


def _patch_kakao_profile(monkeypatch, *, kakao_id: int = 99001, nickname: str = "민수", email: str | None = None):
    async def fake_exchange(_code: str) -> dict:
        return {"access_token": "kakao-access", "refresh_token": "kakao-refresh"}

    async def fake_profile(_access: str) -> dict:
        account: dict = {}
        if email:
            account["email"] = email
        return {"id": kakao_id, "properties": {"nickname": nickname}, "kakao_account": account}

    monkeypatch.setattr("app.services.kakao.exchange_code", fake_exchange)
    monkeypatch.setattr("app.services.kakao.fetch_kakao_profile", fake_profile)


@pytest.mark.asyncio
async def test_kakao_start_when_configured(client: AsyncClient, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("KAKAO_REST_API_KEY", "rest-key")
    get_settings.cache_clear()
    res = await client.get("/api/v1/auth/kakao/start")
    get_settings.cache_clear()
    assert res.status_code == 200
    data = res.json()
    assert data["configured"] is True
    assert data["url"]
    assert "kauth.kakao.com/oauth/authorize" in data["url"]
    assert "client_id=rest-key" in data["url"]
    assert "talk_message" in data["url"]
    assert "account_email" not in data["url"]


@pytest.mark.asyncio
async def test_kakao_callback_consent_denied(client: AsyncClient):
    res = await client.get(
        "/api/v1/auth/kakao/callback",
        params={"error": "access_denied", "error_description": "User denied"},
        follow_redirects=False,
    )
    assert res.status_code in (302, 303, 307)
    assert "error=access_denied" in res.headers["location"]


@pytest.mark.asyncio
async def test_kakao_callback_creates_pending_user(client: AsyncClient, db_session, monkeypatch):
    from app.models import User
    from app.routers.auth import _encode_oauth_state
    from app.services.nicknames import split_nickname

    _patch_kakao_profile(monkeypatch, kakao_id=4242, nickname="민수")
    state = _encode_oauth_state(purpose="login")
    res = await client.get(
        "/api/v1/auth/kakao/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )
    assert res.status_code in (302, 303, 307)
    location = res.headers["location"]
    assert "/pending" in location
    assert "oauth_ticket=" in location
    user = db_session.query(User).filter(User.email == "kakao.4242@users.oday3.app").one()
    assert user.status == "pending"
    assert user.display_name != "민수"
    doing, animal = split_nickname(user.display_name)
    assert doing
    assert animal
    assert user.kakao is not None
    assert user.kakao.kakao_id == "4242"


@pytest.mark.asyncio
async def test_kakao_callback_approved_issues_ticket(client: AsyncClient, db_session, monkeypatch):
    from app.auth import hash_password
    from app.models import KakaoAccount, Preference, User
    from app.routers.auth import _encode_oauth_state
    from urllib.parse import parse_qs, urlparse

    user = User(
        email="kakao.ok@example.com",
        display_name="승인유저",
        password_hash=hash_password("unusedpass"),
        status="approved",
        is_admin=False,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(Preference(user_id=user.id, topics="기술", timezone="Asia/Seoul"))
    db_session.add(KakaoAccount(user_id=user.id, kakao_id="777", access_token="old", refresh_token="old-r"))
    db_session.commit()

    _patch_kakao_profile(monkeypatch, kakao_id=777, nickname="승인유저")
    state = _encode_oauth_state(purpose="login")
    res = await client.get(
        "/api/v1/auth/kakao/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )
    assert res.status_code in (302, 303, 307)
    ticket = parse_qs(urlparse(res.headers["location"]).query).get("oauth_ticket", [None])[0]
    assert ticket

    complete = await client.post("/api/v1/auth/kakao/complete", json={"ticket": ticket})
    assert complete.status_code == 200
    assert complete.json()["access_token"]

    reuse = await client.post("/api/v1/auth/kakao/complete", json={"ticket": ticket})
    assert reuse.status_code == 400


@pytest.mark.asyncio
async def test_kakao_connect_rejects_other_users_account(client: AsyncClient, db_session, monkeypatch):
    from app.auth import create_access_token, hash_password
    from app.models import KakaoAccount, Preference, User
    from app.routers.auth import _encode_oauth_state

    owner = User(
        email="owner@example.com",
        display_name="소유자",
        password_hash=hash_password("password1"),
        status="approved",
        is_admin=False,
    )
    other = User(
        email="other@example.com",
        display_name="다른유저",
        password_hash=hash_password("password1"),
        status="approved",
        is_admin=False,
    )
    db_session.add_all([owner, other])
    db_session.flush()
    db_session.add_all(
        [
            Preference(user_id=owner.id, topics="기술", timezone="Asia/Seoul"),
            Preference(user_id=other.id, topics="기술", timezone="Asia/Seoul"),
            KakaoAccount(user_id=owner.id, kakao_id="555", access_token="a", refresh_token="r"),
        ]
    )
    db_session.commit()

    _patch_kakao_profile(monkeypatch, kakao_id=555, nickname="소유자")
    state = _encode_oauth_state(purpose="connect", user_id=other.id)
    res = await client.get(
        "/api/v1/auth/kakao/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )
    assert res.status_code in (302, 303, 307)
    assert "/app?" in res.headers["location"]
    assert "kakao_already_linked" in res.headers["location"]

    token = create_access_token(other.id)
    status = await client.get("/api/v1/kakao/status", headers={"Authorization": f"Bearer {token}"})
    assert status.json()["connected"] is False
