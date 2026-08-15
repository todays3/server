"""PATCH /auth/me profile fields."""

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
        email="user@example.com",
        display_name="테스트",
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
    user = db_session.query(User).one()
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.mark.asyncio
async def test_patch_me_saves_nickname_job_and_birthday(client: AsyncClient, db_session):
    res = await client.patch(
        "/api/v1/auth/me",
        headers=_auth(db_session),
        json={"display_name": "민수", "occupation": "내과 의사", "birth_date": "1988-03-12"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["display_name"] == "민수"
    assert body["occupation"] == "내과 의사"
    assert body["birth_date"] == "1988-03-12"
    me = await client.get("/api/v1/auth/me", headers=_auth(db_session))
    assert me.json()["occupation"] == "내과 의사"


@pytest.mark.asyncio
async def test_patch_me_rejects_future_birthday(client: AsyncClient, db_session):
    res = await client.patch(
        "/api/v1/auth/me",
        headers=_auth(db_session),
        json={"display_name": "민수", "occupation": "", "birth_date": "2999-01-01"},
    )
    assert res.status_code == 422
