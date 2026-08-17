"""Admin update news: sticky-note on the wall + generic push to every device."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.models import Preference, PushDevice, StickyNote, User


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
    member = User(
        email="user@example.com",
        display_name="유저",
        password_hash=hash_password("user12345"),
        status="approved",
        is_admin=False,
    )
    session.add_all([admin, member])
    session.flush()
    session.add(Preference(user_id=admin.id, topics="경제", timezone="Asia/Seoul"))
    session.add(Preference(user_id=member.id, topics="기술", timezone="Asia/Seoul"))
    session.add(
        PushDevice(user_id=member.id, token="member-phone-token-abcdefgh", device_id="phone", platform="web")
    )
    session.add(
        PushDevice(user_id=admin.id, token="admin-laptop-token-abcdefgh", device_id="laptop", platform="web")
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _header(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.mark.asyncio
async def test_admin_update_posts_note_and_pushes_generic_copy(client: AsyncClient, db_session, monkeypatch):
    sent: list[tuple[str, str, str]] = []

    def fake_notify(db, title: str, body: str, **kwargs) -> int:
        _ = db
        sent.append((title, body, str(kwargs.get("url") or "")))
        return 2

    monkeypatch.setattr("app.routers.admin.notify_all_devices", fake_notify)

    admin = db_session.query(User).filter_by(email="admin@example.com").one()
    res = await client.post(
        "/api/v1/admin/updates",
        headers=_header(admin),
        json={"body": "홈화면 추가 시 주소창이 사라지도록 고쳤습니다."},
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["kind"] == "update"
    assert payload["nickname"] == "하루만장"
    assert payload["body"] == "홈화면 추가 시 주소창이 사라지도록 고쳤습니다."
    assert payload["push_sent"] == 2
    assert payload["device_count"] == 2
    assert "user_id" not in payload
    assert sent == [("하루만장", "업데이트 소식이 있습니다", "/app/notes")]

    wall = await client.get(
        "/api/v1/notes",
        headers=_header(db_session.query(User).filter_by(email="user@example.com").one()),
    )
    assert wall.status_code == 200
    notes = wall.json()
    assert notes[0]["kind"] == "update"
    assert notes[0]["body"] == "홈화면 추가 시 주소창이 사라지도록 고쳤습니다."
    assert db_session.query(StickyNote).filter_by(kind="update").count() == 1


@pytest.mark.asyncio
async def test_member_cannot_publish_update(client: AsyncClient, db_session):
    member = db_session.query(User).filter_by(email="user@example.com").one()
    res = await client.post(
        "/api/v1/admin/updates",
        headers=_header(member),
        json={"body": "가짜 공지"},
    )
    assert res.status_code == 403
    assert db_session.query(StickyNote).count() == 0


@pytest.mark.asyncio
async def test_blank_update_rejected(client: AsyncClient, db_session):
    admin = db_session.query(User).filter_by(email="admin@example.com").one()
    res = await client.post(
        "/api/v1/admin/updates",
        headers=_header(admin),
        json={"body": "   "},
    )
    assert res.status_code == 422
