"""Admin LLM ping — tiny completion without crawl or Kakao."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.config import get_settings
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
    session.add(Preference(user_id=admin.id, topics="경제", timezone="Asia/Seoul"))
    member = User(
        email="user@example.com",
        display_name="유저",
        password_hash=hash_password("user12345"),
        status="approved",
        is_admin=False,
    )
    session.add(member)
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _header(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.mark.asyncio
async def test_llm_test_rejects_non_admin(client: AsyncClient, db_session):
    member = db_session.query(User).filter_by(email="user@example.com").one()
    res = await client.post("/api/v1/admin/llm/test", headers=_header(member))
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_llm_test_reports_unconfigured(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_LOCAL_ENABLED", "false")
    get_settings.cache_clear()
    admin = db_session.query(User).filter_by(email="admin@example.com").one()
    res = await client.post("/api/v1/admin/llm/test", headers=_header(admin))
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["configured"] is False
    assert "설정" in body["error_message"]


@pytest.mark.asyncio
async def test_llm_test_ping_uses_chat_completion(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_LOCAL_ENABLED", "false")
    get_settings.cache_clear()

    captured: dict = {}

    def fake_chat(db, **kwargs):
        captured.update(kwargs)
        usage = SimpleNamespace(
            success=True,
            provider="groq",
            model="openai/gpt-oss-120b",
            error_message="",
        )
        return "pong", usage

    monkeypatch.setattr("app.routers.admin.llm_service.chat_completion", fake_chat)
    admin = db_session.query(User).filter_by(email="admin@example.com").one()
    res = await client.post("/api/v1/admin/llm/test", headers=_header(admin))
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["configured"] is True
    assert body["used_provider"] == "groq"
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["preview"] == "pong"
    assert body["remote_ready"] is True
    assert captured["purpose"] == "admin_llm_ping"
    assert captured["max_tokens"] <= 32
    assert captured["user_id"] == admin.id


@pytest.mark.asyncio
async def test_llm_test_empty_response_is_not_ok(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_LOCAL_ENABLED", "false")
    get_settings.cache_clear()

    def fake_chat(*_a, **_k):
        usage = SimpleNamespace(
            success=False,
            provider="groq",
            model="openai/gpt-oss-120b",
            error_message="empty_response",
        )
        return None, usage

    monkeypatch.setattr("app.routers.admin.llm_service.chat_completion", fake_chat)
    admin = db_session.query(User).filter_by(email="admin@example.com").one()
    res = await client.post("/api/v1/admin/llm/test", headers=_header(admin))
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert "empty_response" in body["error_message"]
