"""Admin overview series for LLM serving dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_access_token, hash_password
from app.db import Base, get_db
from app.main import app
from app.models import CrawlRun, LlmUsage, Preference, User


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSession()

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
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest_asyncio.fixture()
async def client(db_session):
    def _override_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


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
