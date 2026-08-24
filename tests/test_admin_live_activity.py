"""Admin live-activity endpoint."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.models import User
from app.services.live_activity import clear_live_activities, start_activity


@pytest.fixture()
def db_session(db_engine):
    clear_live_activities()
    session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)()
    admin = User(
        email="admin@example.com",
        display_name="관리자",
        password_hash=hash_password("admin12345"),
        status="approved",
        is_admin=True,
    )
    session.add(admin)
    session.commit()
    try:
        yield session
    finally:
        clear_live_activities()
        session.close()


def _admin_header(db_session) -> dict[str, str]:
    admin = db_session.query(User).filter_by(email="admin@example.com").one()
    return {"Authorization": f"Bearer {create_access_token(admin.id)}"}


@pytest.mark.asyncio
async def test_live_activity_requires_admin(client: AsyncClient):
    res = await client.get("/api/v1/admin/live-activity")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_live_activity_lists_inflight(client: AsyncClient, db_session):
    start_activity(
        kind="send",
        label="박준석 · 08:15 전송",
        phase="send",
        user_id=3,
        display_name="박준석",
        slot_label="08:15",
    )
    res = await client.get("/api/v1/admin/live-activity", headers=_admin_header(db_session))
    assert res.status_code == 200
    data = res.json()
    assert data["active_count"] == 1
    assert data["user_count"] == 1
    assert "처리 중 1건" in data["summary"]
    assert data["items"][0]["kind"] == "send"
    assert data["items"][0]["phase"] == "send"
    assert data["items"][0]["display_name"] == "박준석"
