"""Admin Kakao memo test — send-to-me without crawl or LLM."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.models import Digest, KakaoAccount, Preference, User


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
    session.flush()
    session.add(Preference(user_id=member.id, topics="기술", timezone="Asia/Seoul"))
    session.add(
        KakaoAccount(
            user_id=member.id,
            kakao_id="kakao-member",
            access_token="access-member",
            refresh_token="refresh-member",
        )
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()




def _header(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.mark.asyncio
async def test_kakao_test_send_skips_ai_and_digest(client: AsyncClient, db_session, monkeypatch):
    sent: list[tuple[int, str, str]] = []

    async def fake_send(user, title, body, db=None):
        sent.append((user.id, title, body))
        return True, ""

    def boom(*_a, **_k):
        raise AssertionError("crawl/LLM must not run")

    monkeypatch.setattr("app.routers.admin.send_digest_via_kakao", fake_send)
    monkeypatch.setattr("app.services.digest.gather_candidates", boom)
    monkeypatch.setattr("app.services.llm.chat_completion", boom)

    admin = db_session.query(User).filter_by(email="admin@example.com").one()
    member = db_session.query(User).filter_by(email="user@example.com").one()
    res = await client.post(
        "/api/v1/admin/kakao/test-send",
        headers=_header(admin),
        json={"user_id": member.id, "title": "테스트 제목", "body": "테스트 본문"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["user_id"] == member.id
    assert body["kakao_connected"] is True
    assert body["error_message"] == ""
    assert sent == [(member.id, "테스트 제목", "테스트 본문")]
    assert db_session.scalar(select(func.count()).select_from(Digest)) == 0


@pytest.mark.asyncio
async def test_kakao_test_send_rejects_non_admin(client: AsyncClient, db_session):
    member = db_session.query(User).filter_by(email="user@example.com").one()
    res = await client.post(
        "/api/v1/admin/kakao/test-send",
        headers=_header(member),
        json={"user_id": member.id},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_kakao_test_send_missing_user(client: AsyncClient, db_session):
    admin = db_session.query(User).filter_by(email="admin@example.com").one()
    res = await client.post(
        "/api/v1/admin/kakao/test-send",
        headers=_header(admin),
        json={"user_id": 99999},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_kakao_test_send_without_kakao(client: AsyncClient, db_session, monkeypatch):
    async def fake_send(*_a, **_k):
        raise AssertionError("must not call kakao")

    monkeypatch.setattr("app.routers.admin.send_digest_via_kakao", fake_send)
    admin = db_session.query(User).filter_by(email="admin@example.com").one()
    res = await client.post(
        "/api/v1/admin/kakao/test-send",
        headers=_header(admin),
        json={"user_id": admin.id},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["kakao_connected"] is False
    assert "연결" in body["error_message"]
