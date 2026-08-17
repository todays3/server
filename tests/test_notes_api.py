"""Sticky-note suggestions HTTP API (integration)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.models import User


@pytest.fixture()
def db_session(db_engine):
    session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)()
    session.add(
        User(
            email="writer@example.com",
            display_name="쪽지남",
            password_hash=hash_password("user12345"),
            status="approved",
            is_admin=False,
        )
    )
    session.add(
        User(
            email="reader@example.com",
            display_name="읽는이",
            password_hash=hash_password("user12345"),
            status="approved",
            is_admin=False,
        )
    )
    session.add(
        User(
            email="wait@example.com",
            display_name="대기중",
            password_hash=hash_password("user12345"),
            status="pending",
            is_admin=False,
        )
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _auth(db_session, email: str) -> dict[str, str]:
    user = db_session.query(User).filter_by(email=email).one()
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.mark.asyncio
async def test_notes_require_login(client: AsyncClient):
    res = await client.get("/api/v1/notes")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_pending_user_cannot_post_notes(client: AsyncClient, db_session):
    res = await client.post(
        "/api/v1/notes",
        headers=_auth(db_session, "wait@example.com"),
        json={"body": "승인 전에 민원"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_post_uses_nickname_and_list_is_public_to_other_users(client: AsyncClient, db_session):
    posted = await client.post(
        "/api/v1/notes",
        headers=_auth(db_session, "writer@example.com"),
        json={"body": "미리보기 글자가 작아요"},
    )
    assert posted.status_code == 200
    note = posted.json()
    assert note["nickname"] == "쪽지남"
    assert note["body"] == "미리보기 글자가 작아요"
    assert note["kind"] == "suggestion"
    assert "user_id" not in note
    assert "email" not in note
    assert "writer@example.com" not in posted.text

    listed = await client.get("/api/v1/notes", headers=_auth(db_session, "reader@example.com"))
    assert listed.status_code == 200
    notes = listed.json()
    assert len(notes) == 1
    assert notes[0]["nickname"] == "쪽지남"
    assert notes[0]["body"] == "미리보기 글자가 작아요"
    assert notes[0]["kind"] == "suggestion"
    assert "user_id" not in notes[0]
    assert "reader@example.com" not in listed.text
    assert "writer@example.com" not in listed.text


@pytest.mark.asyncio
async def test_member_cannot_set_update_kind_on_post(client: AsyncClient, db_session):
    posted = await client.post(
        "/api/v1/notes",
        headers=_auth(db_session, "writer@example.com"),
        json={"body": "이건 공지처럼 보이게", "kind": "update"},
    )
    assert posted.status_code == 200
    assert posted.json()["kind"] == "suggestion"


@pytest.mark.asyncio
async def test_blank_and_oversized_body_rejected(client: AsyncClient, db_session):
    headers = _auth(db_session, "writer@example.com")
    blank = await client.post("/api/v1/notes", headers=headers, json={"body": "   "})
    assert blank.status_code == 422
    huge = await client.post("/api/v1/notes", headers=headers, json={"body": "가" * 401})
    assert huge.status_code == 422
