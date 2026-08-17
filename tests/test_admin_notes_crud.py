"""Admin CRUD for sticky notes on the suggestions wall."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.models import StickyNote, User


@pytest.fixture()
def db_session(db_engine):
    session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)()
    session.add(
        User(
            email="admin@example.com",
            display_name="관리자",
            password_hash=hash_password("admin12345"),
            status="approved",
            is_admin=True,
        )
    )
    session.add(
        User(
            email="member@example.com",
            display_name="회원",
            password_hash=hash_password("user12345"),
            status="approved",
            is_admin=False,
        )
    )
    session.flush()
    member = session.query(User).filter_by(email="member@example.com").one()
    note = StickyNote(user_id=member.id, nickname="회원", body="고쳐 주세요", kind="suggestion")
    session.add(note)
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _auth(db_session, email: str) -> dict[str, str]:
    user = db_session.query(User).filter_by(email=email).one()
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.mark.asyncio
async def test_admin_can_patch_and_delete_note(client: AsyncClient, db_session):
    note_id = db_session.query(StickyNote).one().id
    admin = _auth(db_session, "admin@example.com")
    member = _auth(db_session, "member@example.com")

    patched = await client.patch(
        f"/api/v1/admin/notes/{note_id}",
        headers=admin,
        json={"body": "반영했습니다"},
    )
    assert patched.status_code == 200
    assert patched.json()["body"] == "반영했습니다"
    assert patched.json()["like_count"] == 0

    forbidden = await client.patch(
        f"/api/v1/admin/notes/{note_id}",
        headers=member,
        json={"body": "몰래 수정"},
    )
    assert forbidden.status_code == 403

    deleted = await client.delete(f"/api/v1/admin/notes/{note_id}", headers=admin)
    assert deleted.status_code == 204
    assert db_session.query(StickyNote).filter_by(id=note_id).count() == 0

    missing = await client.delete(f"/api/v1/admin/notes/{note_id}", headers=admin)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_admin_can_patch_any_note_on_member_endpoint(client: AsyncClient, db_session):
    note_id = db_session.query(StickyNote).one().id
    admin = _auth(db_session, "admin@example.com")
    patched = await client.patch(
        f"/api/v1/notes/{note_id}",
        headers=admin,
        json={"body": "관리자가 고침"},
    )
    assert patched.status_code == 200
    assert patched.json()["body"] == "관리자가 고침"
