"""Admin CRUD for another member's profile and preference."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
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
        approved_at=datetime.now(timezone.utc),
    )
    member = User(
        email="user@example.com",
        display_name="테스트",
        occupation="개발자",
        password_hash=hash_password("user12345"),
        status="approved",
        is_admin=False,
    )
    bare = User(
        email="bare@example.com",
        display_name="빈계정",
        password_hash=hash_password("bare12345"),
        status="approved",
        is_admin=False,
    )
    session.add_all([admin, member, bare])
    session.flush()
    session.add(Preference(user_id=member.id, topics="경제", roles="developer", timezone="UTC"))
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
async def test_admin_user_detail_includes_roles_and_profile(client: AsyncClient, db_session):
    admin = _user(db_session, "admin@example.com")
    res = await client.get("/api/v1/admin/users/detail", headers=_bearer(admin), params={"status": "approved"})
    assert res.status_code == 200
    member = next(row for row in res.json() if row["email"] == "user@example.com")
    assert member["occupation"] == "개발자"
    assert member["preference"]["roles"] == ["developer"]
    assert "role_settings" in member["preference"]


@pytest.mark.asyncio
async def test_admin_updates_member_prefs_and_profile(client: AsyncClient, db_session):
    admin = _user(db_session, "admin@example.com")
    member = _user(db_session, "user@example.com")
    headers = _bearer(admin)

    prefs = await client.put(
        f"/api/v1/admin/users/{member.id}/prefs",
        headers=headers,
        json={
            "topics": ["IT/개발/전체"],
            "roles": ["developer", "investor"],
            "notes": "짧게",
            "enabled": False,
            "send_times": [{"hour": 8, "minute": 0}],
        },
    )
    assert prefs.status_code == 200
    body = prefs.json()
    assert body["topics"] == ["IT/개발/전체"]
    assert body["roles"] == ["developer", "investor"]
    assert body["notes"] == "짧게"
    assert body["enabled"] is False
    assert body["send_times"] == [{"hour": 8, "minute": 0}]
    assert body["timezone"] == "Asia/Seoul"

    profile = await client.patch(
        f"/api/v1/admin/users/{member.id}/profile",
        headers=headers,
        json={"display_name": "민수", "occupation": "내과 의사", "birth_date": "1988-03-12"},
    )
    assert profile.status_code == 200
    assert profile.json()["display_name"] == "민수"
    assert profile.json()["occupation"] == "내과 의사"
    assert profile.json()["birth_date"] == "1988-03-12"


@pytest.mark.asyncio
async def test_admin_creates_and_deletes_member_prefs(client: AsyncClient, db_session):
    admin = _user(db_session, "admin@example.com")
    bare = _user(db_session, "bare@example.com")
    headers = _bearer(admin)

    created = await client.put(
        f"/api/v1/admin/users/{bare.id}/prefs",
        headers=headers,
        json={"topics": ["의학/전체/전체"], "roles": ["doctor"]},
    )
    assert created.status_code == 200
    assert created.json()["roles"] == ["doctor"]

    deleted = await client.delete(f"/api/v1/admin/users/{bare.id}/prefs", headers=headers)
    assert deleted.status_code == 204

    again = await client.delete(f"/api/v1/admin/users/{bare.id}/prefs", headers=headers)
    assert again.status_code == 404


@pytest.mark.asyncio
async def test_admin_prefs_crud_guards(client: AsyncClient, db_session):
    admin = _user(db_session, "admin@example.com")
    member = _user(db_session, "user@example.com")
    headers = _bearer(admin)

    blocked = await client.put(
        f"/api/v1/admin/users/{admin.id}/prefs",
        headers=headers,
        json={"notes": "nope"},
    )
    assert blocked.status_code == 400

    missing = await client.put(
        "/api/v1/admin/users/99999/prefs",
        headers=headers,
        json={"notes": "nope"},
    )
    assert missing.status_code == 404

    capped = await client.put(
        f"/api/v1/admin/users/{member.id}/prefs",
        headers=headers,
        json={"roles": ["developer", "investor", "doctor"]},
    )
    assert capped.status_code == 400

    forbidden = await client.put(
        f"/api/v1/admin/users/{member.id}/prefs",
        headers=_bearer(member),
        json={"notes": "nope"},
    )
    assert forbidden.status_code == 403
