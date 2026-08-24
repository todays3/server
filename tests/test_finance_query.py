import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.config import get_settings
from app.models import User
from app.services.dart_db import init_dart_db


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
async def test_finance_query_requires_auth(client: AsyncClient):
    res = await client.post(
        "/api/v1/finance/query",
        json={"ticker": "005930", "question": "매출액은?"},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_finance_query_number_route(client: AsyncClient, db_session, tmp_path, monkeypatch):
    db = tmp_path / "dart_financials.db"
    init_dart_db(db)
    monkeypatch.setenv("DART_DB_PATH", str(db))
    get_settings.cache_clear()
    try:
        res = await client.post(
            "/api/v1/finance/query",
            headers=_auth(db_session),
            json={"ticker": "005930", "question": "2024 매출액은 얼마야?"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["route"] == "NUMBER"
        assert body["facts"]
        assert "answer" in body
    finally:
        get_settings.cache_clear()
