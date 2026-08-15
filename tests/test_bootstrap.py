"""Schema patches, seed accounts, and app lifespan."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.auth import hash_password
from app.bootstrap import ensure_schema, seed_accounts
from app.db import Base
from app.models import User


def test_ensure_schema_adds_columns_on_legacy_sqlite(tmp_path, monkeypatch):
    db = tmp_path / "legacy.db"
    engine = create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, email VARCHAR, "
                "display_name VARCHAR, password_hash VARCHAR, created_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE preferences (id INTEGER PRIMARY KEY, user_id INTEGER, topics VARCHAR, "
                "tone VARCHAR, send_hour INTEGER, send_minute INTEGER, timezone VARCHAR, "
                "enabled BOOLEAN, notes VARCHAR)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE digests (id INTEGER PRIMARY KEY, user_id INTEGER, title VARCHAR, "
                "body VARCHAR, status VARCHAR)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE kakao_accounts (id INTEGER PRIMARY KEY, user_id INTEGER, "
                "kakao_id VARCHAR, access_token VARCHAR, refresh_token VARCHAR)"
            )
        )
    monkeypatch.setattr("app.bootstrap.engine", engine)
    ensure_schema()
    with engine.connect() as conn:
        user_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(users)")).fetchall()}
        pref_cols = {row[1] for row in conn.execute(text("PRAGMA table_info(preferences)")).fetchall()}
    assert "status" in user_cols
    assert "is_admin" in user_cols
    assert "send_times" in pref_cols
    assert "insight_questions" in pref_cols


def test_ensure_schema_on_empty_database(tmp_path, monkeypatch):
    empty = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    monkeypatch.setattr("app.bootstrap.engine", empty)
    ensure_schema()


def test_seed_accounts_deletes_admin_seed_and_repairs_member(monkeypatch):
    engine = create_engine("sqlite://")
    TestingSession = sessionmaker(bind=engine)
    Base.metadata.create_all(engine)
    monkeypatch.setattr("app.bootstrap.SessionLocal", TestingSession)
    leftover = TestingSession()
    leftover.add(
        User(
            email="admin@example.com",
            display_name="관리자",
            password_hash=hash_password("admin12345"),
            status="approved",
            is_admin=True,
        )
    )
    leftover.commit()
    leftover.close()

    seed_accounts()
    db = TestingSession()
    assert db.query(User).filter(User.email == "admin@example.com").one_or_none() is None
    member = db.query(User).filter(User.email == "user@example.com").one()
    assert member.is_admin is False
    member.status = "pending"
    db.commit()
    seed_accounts()
    db.refresh(member)
    assert member.status == "approved"
    db.close()


@pytest.mark.asyncio
async def test_lifespan_starts_and_stops_scheduler(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr("app.main.Base.metadata.create_all", lambda **_k: calls.append("create"))
    monkeypatch.setattr("app.main.ensure_schema", lambda: calls.append("schema"))
    monkeypatch.setattr("app.main.seed_accounts", lambda: calls.append("seed"))
    monkeypatch.setattr("app.main.start_scheduler", lambda: calls.append("start"))
    monkeypatch.setattr("app.main.stop_scheduler", lambda: calls.append("stop"))
    from app.main import app, lifespan

    async with lifespan(app):
        assert "start" in calls
    assert calls[-1] == "stop"
