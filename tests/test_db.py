"""Database session generator."""

from app.db import get_db


def test_get_db_closes_session(monkeypatch):
    closed = {"n": 0}

    class S:
        def close(self):
            closed["n"] += 1

    monkeypatch.setattr("app.db.SessionLocal", S)
    gen = get_db()
    next(gen)
    gen.close()
    assert closed["n"] == 1
