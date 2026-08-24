"""Shared pytest fixtures and markers.

File-local `db_session` fixtures still win when a test module seeds its own users.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import Base, get_db
from app.main import app


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        name = item.path.name
        integration = (
            name.startswith("test_admin_")
            or name.endswith("_api.py")
            or name in {"test_auth_oauth.py", "test_hooks_notification.py"}
        )
        item.add_marker(pytest.mark.integration if integration else pytest.mark.unit)


@pytest.fixture(autouse=True)
def _roomy_rate_limits(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_AUTH_MAX", "10000")
    monkeypatch.setenv("RATE_LIMIT_HOOKS_MAX", "10000")
    monkeypatch.setenv("RATE_LIMIT_NOTES_MAX", "10000")
    monkeypatch.setenv("RATE_LIMIT_NOTE_HEARTS_MAX", "10000")
    monkeypatch.setenv("NOTES_DAILY_MAX", "10000")
    monkeypatch.setenv("NOTES_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("NOTES_MAX_OPEN", "10000")
    monkeypatch.setenv("AGENT_ENRICHMENT_ENABLED", "false")
    monkeypatch.setenv("AGENT_FAISS_ENABLED", "false")
    monkeypatch.setenv("DART_INGEST_ENABLED", "false")
    monkeypatch.setenv("DART_OCR_ENABLED", "false")
    # Keep hybrid local-first coverage in unit tests; prod sets this true.
    monkeypatch.setenv("LLM_DIGEST_PREFER_REMOTE", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def db_engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture()
def db_session(db_engine: Engine) -> Iterator[Session]:
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest_asyncio.fixture()
async def client(db_session: Session) -> AsyncIterator[AsyncClient]:
    def _override_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
