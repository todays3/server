"""Auth, digest, Kakao connect, and admin user HTTP flows (integration)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_access_token, hash_password
from app.config import get_settings
from app.db import Base, get_db
from app.main import app
from app.models import Digest, KakaoAccount, Preference, User
from app.routers.auth import _encode_oauth_state
from app.services.oauth_tickets import issue_login_ticket
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
        approved_at=datetime.now(timezone.utc),
    )
    member = User(
        email="user@example.com",
        display_name="테스트",
        password_hash=hash_password("user12345"),
        status="approved",
        is_admin=False,
    )
    pending = User(
        email="pending@example.com",
        display_name="대기",
        password_hash=hash_password("pending123"),
        status="pending",
        is_admin=False,
    )
    rejected = User(
        email="rejected@example.com",
        display_name="거절",
        password_hash=hash_password("rejected1"),
        status="rejected",
        is_admin=False,
    )
    session.add_all([admin, member, pending, rejected])
    session.flush()
    session.add(Preference(user_id=admin.id, topics="기술", timezone="Asia/Seoul"))
    session.add(Preference(user_id=member.id, topics="경제", timezone="UTC"))
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


def _user(db_session, email: str) -> User:
    return db_session.query(User).filter(User.email == email).one()


def _bearer(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.mark.asyncio
async def test_register_login_pending_rejected(client: AsyncClient, db_session):
    dup = await client.post(
        "/api/v1/auth/register",
        json={"email": "user@example.com", "password": "abcdefgh", "display_name": "X"},
    )
    assert dup.status_code == 409

    created = await client.post(
        "/api/v1/auth/register",
        json={"email": "new@example.com", "password": "abcdefgh", "display_name": "신규"},
    )
    assert created.status_code == 200
    assert created.json()["status"] == "pending"

    bad = await client.post("/api/v1/auth/login", json={"email": "nobody@x.com", "password": "abcdefgh"})
    assert bad.status_code == 401

    pend = await client.post(
        "/api/v1/auth/login", json={"email": "pending@example.com", "password": "pending123"}
    )
    assert pend.status_code == 403

    rej = await client.post(
        "/api/v1/auth/login", json={"email": "rejected@example.com", "password": "rejected1"}
    )
    assert rej.status_code == 403


@pytest.mark.asyncio
async def test_me_rejects_bad_and_pending_tokens(client: AsyncClient, db_session):
    assert (await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})).status_code == 401
    missing = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {create_access_token(99999)}"}
    )
    assert missing.status_code == 401
    pending = _user(db_session, "pending@example.com")
    blocked = await client.get("/api/v1/auth/me", headers=_bearer(pending))
    assert blocked.status_code == 403


@pytest.mark.asyncio
async def test_kakao_complete_and_redirect(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("KAKAO_REST_API_KEY", "")
    get_settings.cache_clear()
    try:
        res = await client.get("/api/v1/auth/kakao/redirect", follow_redirects=False)
        assert res.status_code == 307
        assert "kakao_not_configured" in res.headers["location"]
        bad = await client.post("/api/v1/auth/kakao/complete", json={"ticket": "x" * 20})
        assert bad.status_code == 400
        ticket = issue_login_ticket("access-from-ticket")
        ok = await client.post("/api/v1/auth/kakao/complete", json={"ticket": ticket})
        assert ok.status_code == 200
        assert ok.json()["access_token"] == "access-from-ticket"
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_kakao_callback_error_paths(client: AsyncClient, db_session, monkeypatch):
    missing = await client.get("/api/v1/auth/kakao/callback", follow_redirects=False)
    assert "missing_code" in missing.headers["location"]

    bad_state = await client.get(
        "/api/v1/auth/kakao/callback",
        params={"code": "c", "state": "nope"},
        follow_redirects=False,
    )
    assert "invalid_state" in bad_state.headers["location"]

    state = _encode_oauth_state(purpose="login")

    async def boom_exchange(_code: str):
        raise RuntimeError("exchange down")

    monkeypatch.setattr("app.services.kakao.exchange_code", boom_exchange)
    fail = await client.get(
        "/api/v1/auth/kakao/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )
    assert "token_exchange_failed" in fail.headers["location"]

    async def empty_token(_code: str):
        return {"access_token": "", "refresh_token": "", "expires_in": "nope"}

    monkeypatch.setattr("app.services.kakao.exchange_code", empty_token)
    missing_access = await client.get(
        "/api/v1/auth/kakao/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )
    assert "missing_access_token" in missing_access.headers["location"]

    async def ok_token(_code: str):
        return {
            "access_token": "tok",
            "refresh_token": "ref",
            "expires_in": 21600,
            "refresh_token_expires_in": "bad",
        }

    async def boom_profile(_access: str):
        raise RuntimeError("profile down")

    monkeypatch.setattr("app.services.kakao.exchange_code", ok_token)
    monkeypatch.setattr("app.services.kakao.fetch_kakao_profile", boom_profile)
    profile_fail = await client.get(
        "/api/v1/auth/kakao/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )
    assert "profile_failed" in profile_fail.headers["location"]

    async def empty_profile(_access: str):
        return {"id": ""}

    monkeypatch.setattr("app.services.kakao.fetch_kakao_profile", empty_profile)
    incomplete = await client.get(
        "/api/v1/auth/kakao/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )
    assert "incomplete_profile" in incomplete.headers["location"]


@pytest.mark.asyncio
async def test_kakao_callback_connect_and_rejected(client: AsyncClient, db_session, monkeypatch):
    member = _user(db_session, "user@example.com")
    rejected = _user(db_session, "rejected@example.com")

    async def ok_token(_code: str):
        return {"access_token": "tok", "refresh_token": "ref", "expires_in": 100}

    async def profile(_access: str):
        return {"id": 99, "properties": {"nickname": "카친"}}

    monkeypatch.setattr("app.services.kakao.exchange_code", ok_token)
    monkeypatch.setattr("app.services.kakao.fetch_kakao_profile", profile)

    missing_user = _encode_oauth_state(purpose="connect", user_id=99999)
    gone = await client.get(
        "/api/v1/auth/kakao/callback",
        params={"code": "c", "state": missing_user},
        follow_redirects=False,
    )
    assert "user_not_found" in gone.headers["location"]

    connect_state = _encode_oauth_state(purpose="connect", user_id=member.id)
    connected = await client.get(
        "/api/v1/auth/kakao/callback",
        params={"code": "c", "state": connect_state},
        follow_redirects=False,
    )
    assert "kakao=connected" in connected.headers["location"]

    rej_state = _encode_oauth_state(purpose="login")

    async def rej_profile(_access: str):
        return {"id": 77, "kakao_account": {"email": rejected.email, "profile": {}}}

    monkeypatch.setattr("app.services.kakao.fetch_kakao_profile", rej_profile)
    rej = await client.get(
        "/api/v1/auth/kakao/callback",
        params={"code": "c", "state": rej_state},
        follow_redirects=False,
    )
    assert "rejected" in rej.headers["location"]


@pytest.mark.asyncio
async def test_kakao_status_connect_disconnect(client: AsyncClient, db_session, monkeypatch):
    member = _user(db_session, "user@example.com")
    headers = _bearer(member)
    status = await client.get("/api/v1/kakao/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["connected"] is False

    monkeypatch.setenv("KAKAO_REST_API_KEY", "")
    get_settings.cache_clear()
    try:
        connect = await client.get("/api/v1/kakao/connect", headers=headers)
        assert connect.json()["configured"] is False
    finally:
        get_settings.cache_clear()

    monkeypatch.setenv("KAKAO_REST_API_KEY", "test-key")
    get_settings.cache_clear()
    try:
        connect_ok = await client.get("/api/v1/kakao/connect", headers=headers)
        assert connect_ok.json()["configured"] is True
        assert "kauth.kakao.com" in (connect_ok.json()["url"] or "")
    finally:
        get_settings.cache_clear()

    gone = await client.delete("/api/v1/kakao/disconnect", headers=headers)
    assert gone.json()["connected"] is False

    db_session.add(
        KakaoAccount(user_id=member.id, kakao_id="k1", access_token="a", refresh_token="r")
    )
    db_session.commit()
    db_session.refresh(member)
    deleted = await client.delete("/api/v1/kakao/disconnect", headers=headers)
    assert deleted.status_code == 200


@pytest.mark.asyncio
async def test_digests_list_and_preview(client: AsyncClient, db_session, monkeypatch):
    member = _user(db_session, "user@example.com")
    headers = _bearer(member)
    db_session.add(
        Digest(
            user_id=member.id,
            title="어제",
            body="본문",
            status="sent",
            items_json='[{"kind":"아티클","title":"t","url":"https://a.example","summary":"s"}, 1]',
        )
    )
    db_session.add(
        Digest(user_id=member.id, title="깨짐", body="x", status="draft", items_json="{")
    )
    db_session.commit()

    listed = await client.get("/api/v1/digests", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) >= 2

    items = [
        SourceItem(kind="아티클", title="A", url="https://a.example", summary="sa", source="s"),
        SourceItem(kind="유튜브", title="B", url="https://b.example", summary="sb", source="s"),
        SourceItem(kind="커뮤니티", title="C", url="https://c.example", summary="sc", source="s"),
    ]
    monkeypatch.setattr("app.services.digest.gather_candidates", lambda *a, **k: items)

    async def send_ok(*_a, **_k):
        return True, ""

    monkeypatch.setattr("app.services.delivery.send_digest_via_kakao", send_ok)

    preview = await client.post("/api/v1/digests/preview", headers=headers, json={"send": False})
    assert preview.status_code == 200
    assert preview.json()["items"]

    sent = await client.post("/api/v1/digests/preview", headers=headers, json={"send": True})
    assert sent.status_code == 200
    assert sent.json()["status"] == "sent"


@pytest.mark.asyncio
async def test_admin_users_approve_reject(client: AsyncClient, db_session):
    admin = _user(db_session, "admin@example.com")
    pending = _user(db_session, "pending@example.com")
    headers = _bearer(admin)

    details = await client.get("/api/v1/admin/users/detail", headers=headers, params={"status": "pending"})
    assert details.status_code == 200
    assert any(u["email"] == pending.email for u in details.json())

    listed = await client.get("/api/v1/admin/users", headers=headers, params={"status": "pending"})
    assert listed.status_code == 200

    missing = await client.post("/api/v1/admin/users/99999/approve", headers=headers)
    assert missing.status_code == 404
    admin_block = await client.post(f"/api/v1/admin/users/{admin.id}/approve", headers=headers)
    assert admin_block.status_code == 400

    ok = await client.post(f"/api/v1/admin/users/{pending.id}/approve", headers=headers)
    assert ok.status_code == 200
    assert ok.json()["status"] == "approved"

    reject_missing = await client.post("/api/v1/admin/users/99999/reject", headers=headers)
    assert reject_missing.status_code == 404
    reject_admin = await client.post(f"/api/v1/admin/users/{admin.id}/reject", headers=headers)
    assert reject_admin.status_code == 400

    rej = await client.post(f"/api/v1/admin/users/{pending.id}/reject", headers=headers)
    assert rej.json()["status"] == "rejected"


@pytest.mark.asyncio
async def test_admin_probe_all_and_decode_state(client: AsyncClient, db_session, monkeypatch):
    admin = _user(db_session, "admin@example.com")
    headers = _bearer(admin)
    monkeypatch.setattr(
        "app.routers.admin.probe_all_sites",
        lambda: [],
    )
    res = await client.post("/api/v1/admin/sources/probes", headers=headers)
    assert res.status_code == 200

    settings = get_settings()
    expired = jwt.encode(
        {"purpose": "login", "flow": "kakao_oauth", "exp": datetime.now(timezone.utc) - timedelta(hours=1)},
        settings.secret_key,
        algorithm="HS256",
    )
    bad = await client.get(
        "/api/v1/auth/kakao/callback",
        params={"code": "c", "state": expired},
        follow_redirects=False,
    )
    assert "invalid_state" in bad.headers["location"]

    weird = jwt.encode(
        {"purpose": "other", "flow": "kakao_oauth", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        settings.secret_key,
        algorithm="HS256",
    )
    other = await client.get(
        "/api/v1/auth/kakao/callback",
        params={"code": "c", "state": weird},
        follow_redirects=False,
    )
    assert "invalid_state" in other.headers["location"]

    noflow = jwt.encode(
        {"purpose": "login", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        settings.secret_key,
        algorithm="HS256",
    )
    nf = await client.get(
        "/api/v1/auth/kakao/callback",
        params={"code": "c", "state": noflow},
        follow_redirects=False,
    )
    assert "invalid_state" in nf.headers["location"]
