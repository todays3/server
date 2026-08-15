"""Phone notification webhook → Kakao me (no site polling)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import hash_password
from app.config import get_settings
from app.db import Base, get_db
from app.main import app
from app.models import Preference, User
from app.services import flash_alerts


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
    session.add(Preference(user_id=admin.id, topics="경제", timezone="Asia/Seoul"))
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


@pytest.fixture(autouse=True)
def _reset_flash_state(monkeypatch):
    flash_alerts.clear_seen()
    monkeypatch.setenv("FLASH_WEBHOOK_SECRET", "hook-secret-test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    flash_alerts.clear_seen()


PATH = "/api/v1/hooks/notifications"
PAYLOAD = {"app": "매일경제", "title": "[속보] 원/달러 급등", "text": "서울 외환시장"}


@pytest.mark.asyncio
async def test_notification_hook_disabled_without_secret(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("FLASH_WEBHOOK_SECRET", "")
    get_settings.cache_clear()
    res = await client.post(PATH, json=PAYLOAD, headers={"X-Webhook-Secret": "x"})
    assert res.status_code == 503


@pytest.mark.asyncio
async def test_notification_hook_rejects_bad_secret(client: AsyncClient):
    res = await client.post(PATH, json=PAYLOAD)
    assert res.status_code == 401
    res = await client.post(PATH, json=PAYLOAD, headers={"X-Webhook-Secret": "wrong"})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_notification_hook_sends_kakao_once(client: AsyncClient, monkeypatch):
    sent: list[tuple[str, str]] = []

    async def _fake_send(user, title, body, *, db=None):
        _ = (user, db)
        sent.append((title, body))
        return True, ""

    monkeypatch.setattr("app.routers.hooks.send_digest_via_kakao", _fake_send)
    headers = {"X-Webhook-Secret": "hook-secret-test"}
    first = await client.post(PATH, json=PAYLOAD, headers=headers)
    assert first.status_code == 200
    assert first.json() == {"accepted": True, "duplicate": False, "sent": True, "skipped": ""}
    second = await client.post(PATH, json=PAYLOAD, headers=headers)
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["sent"] is False
    assert len(sent) == 1
    assert sent[0][0].startswith("속보")
    assert "[속보] 원/달러 급등" in sent[0][1]


@pytest.mark.asyncio
async def test_notification_hook_bearer_and_missing_target(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setenv("FLASH_ALERT_USER_EMAIL", "nobody@example.com")
    get_settings.cache_clear()
    flash_alerts.clear_seen()
    res = await client.post(
        PATH,
        json={"app": "연합", "title": "다른 속보", "text": "본문"},
        headers={"Authorization": "Bearer hook-secret-test"},
    )
    assert res.status_code == 200
    assert res.json()["skipped"] == "target_missing"

    async def fail_send(*_a, **_k):
        return False, "Kakao account is not connected"

    monkeypatch.setattr("app.routers.hooks.send_digest_via_kakao", fail_send)
    monkeypatch.setenv("FLASH_ALERT_USER_EMAIL", "admin@example.com")
    get_settings.cache_clear()
    flash_alerts.clear_seen()
    fail = await client.post(
        PATH,
        json={"app": "연합", "title": "또 다른 속보", "text": "본문2"},
        headers={"X-Webhook-Secret": "hook-secret-test"},
    )
    assert fail.json()["skipped"] == "kakao_not_connected"
