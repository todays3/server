"""Admin source probe API."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.models import Preference, User
from app.services import source_probe as probe_mod
from app.services.sources import FetchResult, collector_site_ids


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
    session.add(Preference(user_id=admin.id, topics="기술", timezone="Asia/Seoul"))
    user = User(
        email="user@example.com",
        display_name="유저",
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




@pytest.fixture(autouse=True)
def _clear_cache():
    probe_mod._cache.clear()
    yield
    probe_mod._cache.clear()


def _admin_header(db_session) -> dict[str, str]:
    admin = db_session.query(User).filter_by(email="admin@example.com").one()
    return {"Authorization": f"Bearer {create_access_token(admin.id)}"}


def _user_header(db_session) -> dict[str, str]:
    user = db_session.query(User).filter_by(email="user@example.com").one()
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.mark.asyncio
async def test_probes_require_admin(client: AsyncClient, db_session):
    res = await client.get("/api/v1/admin/sources/probes")
    assert res.status_code == 401
    res = await client.get("/api/v1/admin/sources/probes", headers=_user_header(db_session))
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_get_probes_unknown_then_run_one(client: AsyncClient, db_session, monkeypatch):
    rss = """<?xml version="1.0"?><rss version="2.0"><channel>
    <item><title>One</title><link>https://example.com/1</link></item>
    </channel></rss>"""
    monkeypatch.setattr(
        probe_mod,
        "_fetch",
        lambda url, **kwargs: FetchResult(url, True, 200, rss, ""),
    )
    listed = await client.get("/api/v1/admin/sources/probes", headers=_admin_header(db_session))
    assert listed.status_code == 200
    body = listed.json()
    assert body["unknown_count"] == len(collector_site_ids())
    site_id = sorted(collector_site_ids())[0]
    probed = await client.post(
        f"/api/v1/admin/sources/probes/{site_id}",
        headers=_admin_header(db_session),
    )
    assert probed.status_code == 200
    assert probed.json()["ok"] is True
    assert probed.json()["site_id"] == site_id


@pytest.mark.asyncio
async def test_probe_unknown_site_404(client: AsyncClient, db_session):
    res = await client.post(
        "/api/v1/admin/sources/probes/not-a-real-site",
        headers=_admin_header(db_session),
    )
    assert res.status_code == 404
