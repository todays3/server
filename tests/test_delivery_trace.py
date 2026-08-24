"""Delivery trace append + partial send status."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.models import Digest, Preference, User
from app.services.delivery import deliver_digest
from app.services.delivery_trace import parse_delivery_trace
from app.services.kakao import split_memo_chunks


@pytest.fixture()
def db_session(db_engine):
    session = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)()
    user = User(
        email="user@example.com",
        display_name="유저",
        password_hash=hash_password("user12345"),
        status="approved",
        is_admin=False,
    )
    session.add(user)
    session.flush()
    session.add(Preference(user_id=user.id, topics="기술", timezone="Asia/Seoul"))
    session.commit()
    try:
        yield session
    finally:
        session.close()


@pytest.mark.asyncio
async def test_deliver_digest_records_trace_and_partial_status(db_session, monkeypatch):
    user = db_session.query(User).one()
    body = "가" * 1800
    digest = Digest(user_id=user.id, title="하루만장", body=body, status="draft")
    db_session.add(digest)
    db_session.commit()

    chunks = split_memo_chunks(digest.title, digest.body)
    calls = {"count": 0}

    async def fake_send(_user, _title, _body, *, db=None, chunks_sent=0):
        calls["count"] += 1
        if calls["count"] == 1:
            return False, "chunk 2 failed", 1
        return True, "", len(chunks)

    monkeypatch.setattr("app.services.delivery.send_digest_via_kakao", fake_send)

    first = await deliver_digest(db_session, user, digest, wait_ms=0)
    db_session.refresh(digest)
    assert first.ok is False
    assert digest.status == "partial"
    assert digest.chunks_sent == 1
    trace = parse_delivery_trace(digest.delivery_trace_json)
    assert len(trace) >= 2
    assert trace[-1]["step"] == "kakao_send"
    assert trace[-1]["ok"] is False

    second = await deliver_digest(db_session, user, digest, wait_ms=0, force=True)
    db_session.refresh(digest)
    assert second.ok is True
    assert digest.status == "sent"
    assert digest.chunks_sent == len(chunks)
    assert len(parse_delivery_trace(digest.delivery_trace_json)) >= 4


@pytest.mark.asyncio
async def test_recent_call_detail_endpoint(client, db_session):
    from app.models import LlmUsage

    admin = User(
        email="admin@example.com",
        display_name="관리자",
        password_hash=hash_password("admin12345"),
        status="approved",
        is_admin=True,
    )
    member = db_session.query(User).filter_by(email="user@example.com").one()
    db_session.add(admin)
    db_session.flush()
    db_session.add(Preference(user_id=admin.id, topics="기술", timezone="Asia/Seoul"))
    now = datetime.now(timezone.utc)
    usage = LlmUsage(
        user_id=member.id,
        purpose="digest_curate",
        provider="groq",
        model="test",
        prompt_tokens=1,
        completion_tokens=2,
        total_tokens=3,
        success=True,
        created_at=now,
    )
    digest = Digest(
        user_id=member.id,
        title="브리프",
        body="본문",
        status="failed",
        attempt_count=2,
        error_message="kakao down",
        delivery_trace_json='[{"at":"2026-01-01T00:00:00+00:00","step":"kakao_send","ok":false,"error":"kakao down","chunks_sent":0,"chunk_total":1,"attempt":2,"note":""}]',
        created_at=now,
    )
    db_session.add_all([usage, digest])
    db_session.commit()

    token = create_access_token(admin.id)
    res = await client.get(f"/api/v1/admin/recent-calls/{usage.id}", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    assert data["usage"]["id"] == usage.id
    assert data["delivery_error"] == "kakao down"
    assert data["delivery_trace"][0]["error"] == "kakao down"
    assert data["pipeline"]
