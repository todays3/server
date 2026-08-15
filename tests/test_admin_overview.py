"""Admin overview series for LLM serving dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.models import CrawlRun, LlmUsage, Preference, User
from app.routers.admin import _daily_series, _pref_detail


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
    session.add(
        Preference(
            user_id=admin.id,
            topics="기술",
            tone="차분",
            timezone="Asia/Seoul",
        )
    )
    session.add(
        LlmUsage(
            user_id=admin.id,
            purpose="digest_curate",
            provider="groq",
            model="test",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            success=True,
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    session.add(
        CrawlRun(
            user_id=admin.id,
            trigger="schedule",
            kinds_json="{}",
            total_count=0,
            cpu_peak_percent=45,
            rss_peak_bytes=104857600,
            rss_delta_bytes=20971520,
            created_at=datetime.now(timezone.utc),
        )
    )
    session.commit()

    try:
        yield session
    finally:
        session.close()




@pytest.mark.asyncio
async def test_admin_overview_includes_series(client: AsyncClient, db_session):
    admin = db_session.query(User).filter_by(email="admin@example.com").one()
    token = create_access_token(admin.id)
    res = await client.get(
        "/api/v1/admin/overview",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    body = res.json()
    assert len(body["series"]) == 14
    assert body["series"][-1]["date"]
    assert body["usage_all"]["total_tokens"] == 30
    assert body["series"][-1]["tokens_cumulative"] == 30
    assert any(p["tokens"] == 30 for p in body["series"])
    assert body["last_run_cpu_peak_percent"] == 45
    assert body["last_run_rss_peak_bytes"] == 104857600
    assert body["last_run_rss_delta_bytes"] == 20971520
    assert body["runs_cpu_peak_max_percent"] == 45
    assert body["runs_rss_peak_max_bytes"] == 104857600


def test_daily_series_filters_out_of_window_and_pref_detail_none():
    old = datetime.now(timezone.utc) - timedelta(days=40)
    users = [
        SimpleNamespace(created_at=None, approved_at=None, status="approved"),
        SimpleNamespace(created_at=old, approved_at=None, status="approved"),
        SimpleNamespace(
            created_at=datetime.now(timezone.utc),
            approved_at=None,
            status="pending",
        ),
    ]
    usage = [
        SimpleNamespace(created_at=None, total_tokens=1, prompt_tokens=1, completion_tokens=0, success=True),
        SimpleNamespace(created_at=old, total_tokens=5, prompt_tokens=2, completion_tokens=3, success=True),
        SimpleNamespace(
            created_at=datetime.now(timezone.utc) + timedelta(days=400),
            total_tokens=1,
            prompt_tokens=0,
            completion_tokens=0,
            success=False,
        ),
    ]
    series = _daily_series(users, usage, days=14)
    assert len(series) == 14
    assert _pref_detail(None) is None
    pref = Preference(
        topics="a,b",
        sources="hn",
        notes="",
        send_times="07:30",
        timezone="Asia/Seoul",
        enabled=True,
    )
    detail = _pref_detail(pref)
    assert detail is not None
    assert detail.topics == ["a", "b"]
