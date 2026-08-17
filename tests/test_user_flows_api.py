"""Auth, digest, Kakao connect, and admin user HTTP flows (integration)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import pytest
from httpx import AsyncClient
from jose import jwt
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.config import get_settings
from app.models import Digest, KakaoAccount, Preference, User
from app.routers.auth import _encode_oauth_state
from app.services.oauth_tickets import issue_login_ticket
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
    assert pend.status_code == 200
    assert "access_token" in pend.json()

    rej = await client.post(
        "/api/v1/auth/login", json={"email": "rejected@example.com", "password": "rejected1"}
    )
    assert rej.status_code == 403


@pytest.mark.asyncio
async def test_me_rejects_bad_tokens_and_allows_pending(client: AsyncClient, db_session):
    assert (await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})).status_code == 401
    missing = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {create_access_token(99999)}"}
    )
    assert missing.status_code == 401
    pending = _user(db_session, "pending@example.com")
    me = await client.get("/api/v1/auth/me", headers=_bearer(pending))
    assert me.status_code == 200
    assert me.json()["status"] == "pending"
    prefs = await client.get("/api/v1/prefs", headers=_bearer(pending))
    assert prefs.status_code == 403


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
    assert status.json()["talk_message"] is False

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

    async def agreed(_token: str) -> bool:
        return True

    monkeypatch.setattr("app.routers.kakao.kakao_service.fetch_talk_message_agreed", agreed)
    scoped = await client.get("/api/v1/kakao/status", headers=headers)
    assert scoped.json()["connected"] is True
    assert scoped.json()["talk_message"] is True

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
        return True, "", 1

    monkeypatch.setattr("app.services.delivery.send_digest_via_kakao", send_ok)

    preview = await client.post("/api/v1/digests/preview", headers=headers, json={"send": False})
    assert preview.status_code == 200
    assert preview.json()["items"]

    sent = await client.post("/api/v1/digests/preview", headers=headers, json={"send": True})
    assert sent.status_code == 200
    assert sent.json()["status"] == "sent"

    streamed = await client.post(
        "/api/v1/digests/preview",
        headers={**headers, "Accept": "application/x-ndjson"},
        json={"send": True},
    )
    assert streamed.status_code == 200
    events = [json.loads(line) for line in streamed.text.strip().split("\n") if line.strip()]
    steps = [row["step"] for row in events if row.get("type") == "step"]
    assert "crawl" in steps
    assert "curate" in steps
    assert "send" in steps
    done = next(row for row in events if row.get("type") == "done")
    assert done["digest"]["status"] == "sent"

    async def send_fail(*_a, **_k):
        return False, "insufficient scopes.", 0

    monkeypatch.setattr("app.services.delivery.send_digest_via_kakao", send_fail)
    failed = await client.post(
        "/api/v1/digests/preview",
        headers={**headers, "Accept": "application/x-ndjson"},
        json={"send": True},
    )
    assert failed.status_code == 200
    fail_events = [json.loads(line) for line in failed.text.strip().split("\n") if line.strip()]
    err = next(row for row in fail_events if row.get("type") == "error")
    assert err["step"] == "send"
    assert "insufficient" in err["message"]


@pytest.mark.asyncio
async def test_test_send_uses_dummy_content_and_real_kakao(client: AsyncClient, db_session, monkeypatch):
    member = _user(db_session, "user@example.com")
    headers = _bearer(member)

    def fail_gather(*_a, **_k):
        raise AssertionError("gather_candidates should not run for test-send")

    monkeypatch.setattr("app.services.digest.gather_candidates", fail_gather)

    async def send_ok(*_a, **_k):
        return True, "", 1

    monkeypatch.setattr("app.services.delivery.send_digest_via_kakao", send_ok)

    plain = await client.post("/api/v1/digests/test-send", headers=headers, json={})
    assert plain.status_code == 200
    body = plain.json()
    assert body["status"] == "sent"
    assert body["items"]
    assert "(본 내용은 테스트용 데이터입니다.)" in body["body"]
    assert "첫째." in body["body"]
    assert "선정이유:" in body["body"]

    streamed = await client.post(
        "/api/v1/digests/test-send",
        headers={**headers, "Accept": "application/x-ndjson"},
        json={},
    )
    assert streamed.status_code == 200
    events = [json.loads(line) for line in streamed.text.strip().split("\n") if line.strip()]
    steps = [row["step"] for row in events if row.get("type") == "step"]
    assert steps == ["crawl", "curate", "format", "send"]
    done = next(row for row in events if row.get("type") == "done")
    assert done["digest"]["status"] == "sent"


@pytest.mark.asyncio
async def test_resend_failed_digest(client: AsyncClient, db_session, monkeypatch):
    member = _user(db_session, "user@example.com")
    headers = _bearer(member)
    digest = Digest(user_id=member.id, title="실패 브리프", body="본문", status="failed", error_message="boom")
    db_session.add(digest)
    db_session.commit()
    db_session.refresh(digest)

    calls = {"n": 0}

    async def send_ok(*_a, **_k):
        calls["n"] += 1
        return True, "", 1

    monkeypatch.setattr("app.services.delivery.send_digest_via_kakao", send_ok)
    res = await client.post(f"/api/v1/digests/{digest.id}/resend", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "sent"
    assert data["can_resend"] is False
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_preview_stream_send_does_not_lazy_load_detached_user(
    client: AsyncClient, db_session, monkeypatch
):
    member = _user(db_session, "user@example.com")
    db_session.add(
        KakaoAccount(
            user_id=member.id,
            kakao_id="k-send",
            access_token="access",
            refresh_token="refresh",
            access_expires_at=datetime.now(timezone.utc) + timedelta(hours=4),
        )
    )
    db_session.commit()
    items = [
        SourceItem(kind="아티클", title="A", url="https://a.example", summary="sa", source="s"),
        SourceItem(kind="유튜브", title="B", url="https://b.example", summary="sb", source="s"),
        SourceItem(kind="커뮤니티", title="C", url="https://c.example", summary="sc", source="s"),
    ]
    monkeypatch.setattr("app.services.digest.gather_candidates", lambda *a, **k: items)

    async def send_ok(*_a, **_k):
        return True, "", 1

    monkeypatch.setattr("app.services.delivery.send_digest_via_kakao", send_ok)
    monkeypatch.setenv("KAKAO_REST_API_KEY", "test-key")
    get_settings.cache_clear()
    try:
        streamed = await client.post(
            "/api/v1/digests/preview",
            headers={**_bearer(member), "Accept": "application/x-ndjson"},
            json={"send": True},
        )
    finally:
        get_settings.cache_clear()
    assert streamed.status_code == 200
    events = [json.loads(line) for line in streamed.text.strip().split("\n") if line.strip()]
    err = next((row for row in events if row.get("type") == "error"), None)
    assert err is None, err
    done = next(row for row in events if row.get("type") == "done")
    assert done["digest"]["status"] == "sent"


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

    member = _user(db_session, "user@example.com")
    stopped = await client.patch(
        f"/api/v1/admin/users/{member.id}/status",
        headers=headers,
        json={"status": "stopped"},
    )
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "stopped"
    login = await client.post(
        "/api/v1/auth/login", json={"email": "user@example.com", "password": "user12345"}
    )
    assert login.status_code == 403
    assert "정지" in login.json()["detail"]

    admin_status = await client.patch(
        f"/api/v1/admin/users/{admin.id}/status",
        headers=headers,
        json={"status": "stopped"},
    )
    assert admin_status.status_code == 400

    invalid = await client.patch(
        f"/api/v1/admin/users/{member.id}/status",
        headers=headers,
        json={"status": "STOPPED"},
    )
    assert invalid.status_code == 422


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
