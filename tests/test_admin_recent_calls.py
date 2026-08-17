"""Admin recent-calls list uses digest delivery status and pagination."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.models import Digest, LlmUsage, Preference, User
from app.routers.admin import _delivery_status, _match_digests_for_usages


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
    member = User(
        email="user@example.com",
        display_name="유저",
        password_hash=hash_password("user12345"),
        status="approved",
        is_admin=False,
    )
    session.add_all([admin, member])
    session.flush()
    session.add(Preference(user_id=admin.id, topics="기술", timezone="Asia/Seoul"))
    session.add(Preference(user_id=member.id, topics="기술", timezone="Asia/Seoul"))
    session.commit()

    try:
        yield session
    finally:
        session.close()


def _usage(
    user_id: int,
    *,
    success: bool = True,
    created_at: datetime | None = None,
    error: str = "",
) -> LlmUsage:
    return LlmUsage(
        user_id=user_id,
        purpose="digest_curate",
        provider="groq",
        model="test",
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
        success=success,
        error_message=error,
        created_at=created_at or datetime.now(timezone.utc),
    )


def test_delivery_status_uses_digest_state_unless_llm_failed():
    sent = Digest(user_id=1, title="t", body="b", status="sent")
    failed = Digest(user_id=1, title="t", body="b", status="failed")
    sending = Digest(user_id=1, title="t", body="b", status="sending")
    draft = Digest(user_id=1, title="t", body="b", status="draft")
    ok = _usage(1, success=True)
    fail = _usage(1, success=False)
    assert _delivery_status(ok, sent) == "sent"
    assert _delivery_status(ok, failed) == "failed"
    assert _delivery_status(ok, sending) == "sending"
    assert _delivery_status(ok, draft) == "draft"
    assert _delivery_status(fail, None) == "failed"
    assert _delivery_status(fail, sent) == "failed"
    assert _delivery_status(ok, None) == ""


def test_match_digests_pairs_usage_to_same_user_digest(db_session):
    member = db_session.query(User).filter_by(email="user@example.com").one()
    now = datetime.now(timezone.utc)
    usage = _usage(member.id, created_at=now)
    digest = Digest(
        user_id=member.id,
        title="하루만장",
        body="본문",
        status="failed",
        attempt_count=2,
        error_message="kakao down",
        created_at=now + timedelta(seconds=8),
    )
    db_session.add_all([usage, digest])
    db_session.commit()

    matched = _match_digests_for_usages(db_session, [usage])
    assert matched[usage.id].id == digest.id
    assert matched[usage.id].status == "failed"


@pytest.mark.asyncio
async def test_recent_calls_uses_digest_status_and_paginates(client: AsyncClient, db_session):
    admin = db_session.query(User).filter_by(email="admin@example.com").one()
    member = db_session.query(User).filter_by(email="user@example.com").one()
    now = datetime.now(timezone.utc)
    for i in range(12):
        created = now - timedelta(minutes=12 - i)
        usage = _usage(member.id, created_at=created)
        db_session.add(usage)
        db_session.flush()
        status = "sent" if i < 11 else "failed"
        db_session.add(
            Digest(
                user_id=member.id,
                title=f"브리프 {i}",
                body="본문",
                status=status,
                attempt_count=3 if status == "failed" else 1,
                created_at=created + timedelta(seconds=5),
            )
        )
    db_session.commit()

    token = create_access_token(admin.id)
    headers = {"Authorization": f"Bearer {token}"}
    first = await client.get("/api/v1/admin/recent-calls?page=1&page_size=10", headers=headers)
    assert first.status_code == 200
    body = first.json()
    assert body["total"] == 12
    assert body["page"] == 1
    assert body["page_size"] == 10
    assert len(body["items"]) == 10
    assert body["items"][0]["delivery_status"] == "failed"
    assert body["items"][0]["attempt_count"] == 3
    assert body["items"][0]["email"] == "user@example.com"
    assert all(row["delivery_status"] in {"sent", "failed", "sending", "draft"} for row in body["items"])

    second = await client.get("/api/v1/admin/recent-calls?page=2&page_size=10", headers=headers)
    assert second.status_code == 200
    page2 = second.json()
    assert len(page2["items"]) == 2
    assert page2["items"][0]["delivery_status"] == "sent"

    overview = await client.get("/api/v1/admin/overview", headers=headers)
    assert overview.status_code == 200
    recent = overview.json()["recent"]
    assert recent
    assert recent[0]["delivery_status"] == "failed"
