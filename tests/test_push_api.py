"""Push device HTTP API and public FCM web config / service worker."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.models import Preference, PushDevice, User


@pytest.fixture()
def db_session(db_engine):
    session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)()
    user = User(
        email="user@example.com",
        display_name="테스트",
        password_hash=hash_password("user12345"),
        status="approved",
    )
    session.add(user)
    session.flush()
    session.add(Preference(user_id=user.id, topics="경제", timezone="Asia/Seoul"))
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _bearer(db_session) -> dict[str, str]:
    user = db_session.query(User).filter_by(email="user@example.com").one()
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.mark.asyncio
async def test_health_includes_firebase_flag(client: AsyncClient):
    res = await client.get("/api/v1/health")
    assert res.status_code == 200
    assert "firebase_configured" in res.json()


@pytest.mark.asyncio
async def test_push_config_never_leaks_private_key(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("FIREBASE_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----\\nSECRET\\n-----END PRIVATE KEY-----\\n")
    monkeypatch.setenv("FIREBASE_CLIENT_EMAIL", "sa@example.iam.gserviceaccount.com")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-proj")
    monkeypatch.setenv("FIREBASE_WEB_API_KEY", "web-key")
    monkeypatch.setenv("FIREBASE_WEB_APP_ID", "1:1:web:abc")
    monkeypatch.setenv("FIREBASE_WEB_MESSAGING_SENDER_ID", "123")
    monkeypatch.setenv("FIREBASE_WEB_VAPID_KEY", "vapid")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        res = await client.get("/api/v1/push/config")
        assert res.status_code == 200
        body = res.json()
        dumped = str(body)
        assert "BEGIN PRIVATE KEY" not in dumped
        assert "SECRET" not in dumped
        assert body["configured"] is True
        assert body["vapid_key"] == "vapid"
        assert body["project_id"] == "demo-proj"
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_push_sw_allows_root_optional_but_is_javascript(client: AsyncClient):
    res = await client.get("/api/v1/push/firebase-messaging-sw.js")
    assert res.status_code == 200
    assert "javascript" in res.headers["content-type"]
    assert "firebase" in res.text.lower() or "skipWaiting" in res.text


@pytest.mark.asyncio
async def test_register_device_requires_auth(client: AsyncClient):
    res = await client.post("/api/v1/push/devices", json={"token": "a" * 24, "platform": "web"})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_register_device_rejects_when_firebase_web_not_configured(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setattr("app.routers.push.fcm_web_configured", lambda: False)
    headers = _bearer(db_session)
    res = await client.post(
        "/api/v1/push/devices",
        headers=headers,
        json={"token": "phone-token-aaaaaaaaaaaa", "platform": "web", "device_id": "phone"},
    )
    assert res.status_code == 503
    assert "설정" in res.json()["detail"]


@pytest.mark.asyncio
async def test_register_device_rejects_invalid_token(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setattr("app.routers.push.fcm_web_configured", lambda: True)
    monkeypatch.setattr("app.routers.push.probe_fcm_token", lambda _token: "gone")
    headers = _bearer(db_session)
    res = await client.post(
        "/api/v1/push/devices",
        headers=headers,
        json={"token": "phone-token-aaaaaaaaaaaa", "platform": "web", "device_id": "phone"},
    )
    assert res.status_code == 400
    user = db_session.query(User).filter_by(email="user@example.com").one()
    assert db_session.query(PushDevice).filter_by(user_id=user.id).count() == 0


@pytest.mark.asyncio
async def test_register_two_devices_and_list_count(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setattr("app.routers.push.fcm_web_configured", lambda: True)
    monkeypatch.setattr("app.routers.push.probe_fcm_token", lambda _token: "ok")
    headers = _bearer(db_session)
    first = await client.post(
        "/api/v1/push/devices",
        headers=headers,
        json={"token": "phone-token-aaaaaaaaaaaa", "platform": "web", "device_id": "phone"},
    )
    second = await client.post(
        "/api/v1/push/devices",
        headers=headers,
        json={"token": "laptop-token-bbbbbbbbbbbb", "platform": "web", "device_id": "laptop"},
    )
    assert first.status_code == 200
    assert second.json()["device_count"] == 2
    user = db_session.query(User).filter_by(email="user@example.com").one()
    assert db_session.query(PushDevice).filter_by(user_id=user.id).count() == 2


@pytest.mark.asyncio
async def test_unregister_device(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setattr("app.routers.push.fcm_web_configured", lambda: True)
    monkeypatch.setattr("app.routers.push.probe_fcm_token", lambda _token: "ok")
    headers = _bearer(db_session)
    await client.post(
        "/api/v1/push/devices",
        headers=headers,
        json={"token": "phone-token-aaaaaaaaaaaa", "platform": "web", "device_id": "phone"},
    )
    gone = await client.request(
        "DELETE",
        "/api/v1/push/devices",
        headers=headers,
        json={"token": "phone-token-aaaaaaaaaaaa"},
    )
    assert gone.status_code == 200
    assert gone.json()["device_count"] == 0
