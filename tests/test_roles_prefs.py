"""Preference roles + role_settings persistence."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.models import User


@pytest.fixture()
def db_session(db_engine):
    session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)()
    user = User(
        email="member@example.com",
        display_name="회원",
        password_hash=hash_password("user12345"),
        status="approved",
        is_admin=False,
    )
    session.add(user)
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _auth(db_session) -> dict[str, str]:
    user = db_session.query(User).filter_by(email="member@example.com").one()
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.mark.asyncio
async def test_put_prefs_stores_roles_and_settings(client: AsyncClient, db_session):
    res = await client.put(
        "/api/v1/prefs",
        headers=_auth(db_session),
        json={
            "roles": ["developer", "investor"],
            "role_settings": {
                "investor_market": "국내증시",
                "investor_themes": ["반도체"],
                "job_seeker_level": "new",
                "job_seeker_targets": [],
            },
            "topics": ["IT/개발/all", "경제/주식/국내증시/반도체"],
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["roles"] == ["developer", "investor"]
    assert body["role_settings"]["investor_themes"] == ["반도체"]

    got = await client.get("/api/v1/prefs", headers=_auth(db_session))
    assert got.json()["roles"] == ["developer", "investor"]


async def test_put_prefs_stores_assistant_names(client: AsyncClient, db_session):
    res = await client.put(
        "/api/v1/prefs",
        headers=_auth(db_session),
        json={
            "roles": ["developer"],
            "role_settings": {"assistant_names": {"developer": "지훈"}},
            "topics": ["IT/개발/all"],
        },
    )
    assert res.status_code == 200
    assert res.json()["role_settings"]["assistant_names"]["developer"] == "지훈"


@pytest.mark.asyncio
async def test_put_prefs_rejects_empty_roles(client: AsyncClient, db_session):
    res = await client.put(
        "/api/v1/prefs",
        headers=_auth(db_session),
        json={"roles": [], "topics": ["IT/개발/all"]},
    )
    assert res.status_code == 422
