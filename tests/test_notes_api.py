"""Sticky-note suggestions HTTP API (integration)."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.config import get_settings
from app.models import StickyNote, User


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
    assert note["like_count"] == 0
    assert note["liked"] is False
    assert note["mine"] is True
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
    assert notes[0]["like_count"] == 0
    assert notes[0]["liked"] is False
    assert notes[0]["mine"] is False
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


@pytest.mark.asyncio
async def test_owner_can_patch_and_delete_own_note(client: AsyncClient, db_session):
    writer = _auth(db_session, "writer@example.com")
    reader = _auth(db_session, "reader@example.com")
    posted = await client.post("/api/v1/notes", headers=writer, json={"body": "내 건의"})
    note_id = posted.json()["id"]

    forbidden = await client.patch(
        f"/api/v1/notes/{note_id}",
        headers=reader,
        json={"body": "남의 건의 수정"},
    )
    assert forbidden.status_code == 403

    patched = await client.patch(f"/api/v1/notes/{note_id}", headers=writer, json={"body": "고친 건의"})
    assert patched.status_code == 200
    assert patched.json()["body"] == "고친 건의"
    assert patched.json()["mine"] is True

    deleted_other = await client.delete(f"/api/v1/notes/{note_id}", headers=reader)
    assert deleted_other.status_code == 403

    deleted = await client.delete(f"/api/v1/notes/{note_id}", headers=writer)
    assert deleted.status_code == 204
    listed = await client.get("/api/v1/notes", headers=writer)
    assert listed.json() == []


@pytest.mark.asyncio
async def test_member_cannot_mutate_update_notes(client: AsyncClient, db_session):
    writer = db_session.query(User).filter_by(email="writer@example.com").one()
    note = StickyNote(user_id=writer.id, nickname="하루만장", body="공지", kind="update")
    db_session.add(note)
    db_session.commit()
    headers = _auth(db_session, "writer@example.com")
    patched = await client.patch(f"/api/v1/notes/{note.id}", headers=headers, json={"body": "공지 수정"})
    assert patched.status_code == 403
    deleted = await client.delete(f"/api/v1/notes/{note.id}", headers=headers)
    assert deleted.status_code == 403


@pytest.mark.asyncio
async def test_heart_is_idempotent_one_like_per_user(client: AsyncClient, db_session):
    writer = _auth(db_session, "writer@example.com")
    reader = _auth(db_session, "reader@example.com")
    posted = await client.post("/api/v1/notes", headers=writer, json={"body": "하트 주세요"})
    note_id = posted.json()["id"]

    first = await client.put(f"/api/v1/notes/{note_id}/heart", headers=reader, json={"liked": True})
    assert first.status_code == 200
    assert first.json()["liked"] is True
    assert first.json()["like_count"] == 1

    again = await client.put(f"/api/v1/notes/{note_id}/heart", headers=reader, json={"liked": True})
    assert again.status_code == 200
    assert again.json()["like_count"] == 1

    listed = await client.get("/api/v1/notes", headers=reader)
    assert listed.json()[0]["liked"] is True
    assert listed.json()[0]["like_count"] == 1

    writer_view = await client.get("/api/v1/notes", headers=writer)
    assert writer_view.json()[0]["liked"] is False
    assert writer_view.json()[0]["like_count"] == 1

    off = await client.put(f"/api/v1/notes/{note_id}/heart", headers=reader, json={"liked": False})
    assert off.status_code == 200
    assert off.json()["liked"] is False
    assert off.json()["like_count"] == 0

    off_again = await client.put(f"/api/v1/notes/{note_id}/heart", headers=reader, json={"liked": False})
    assert off_again.status_code == 200
    assert off_again.json()["like_count"] == 0


@pytest.mark.asyncio
async def test_spam_policy_blocks_duplicate_and_daily_cap(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setenv("NOTES_DAILY_MAX", "1")
    monkeypatch.setenv("NOTES_MIN_INTERVAL_SECONDS", "0")
    get_settings.cache_clear()
    headers = _auth(db_session, "writer@example.com")
    first = await client.post("/api/v1/notes", headers=headers, json={"body": "같은 말"})
    assert first.status_code == 200
    duplicate = await client.post("/api/v1/notes", headers=headers, json={"body": "같은 말"})
    assert duplicate.status_code == 409
    second = await client.post("/api/v1/notes", headers=headers, json={"body": "다른 말"})
    assert second.status_code == 429
    assert "하루" in second.json()["detail"]
