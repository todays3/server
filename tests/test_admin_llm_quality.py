"""Admin LLM quality dashboard."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.models import LlmUsage, Preference, User


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
    session.add(Preference(user_id=admin.id, topics="경제", timezone="Asia/Seoul"))
    member = User(
        email="user@example.com",
        display_name="유저",
        password_hash=hash_password("user12345"),
        status="approved",
        is_admin=False,
    )
    session.add(member)
    session.commit()
    try:
        yield session
    finally:
        session.close()


def _header(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


@pytest.mark.asyncio
async def test_llm_quality_rejects_non_admin(client: AsyncClient, db_session):
    member = db_session.query(User).filter_by(email="user@example.com").one()
    res = await client.get("/api/v1/admin/llm-quality", headers=_header(member))
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_llm_quality_summarizes_ttft_tps_and_rag(client: AsyncClient, db_session):
    admin = db_session.query(User).filter_by(email="admin@example.com").one()
    db_session.add(
        LlmUsage(
            user_id=admin.id,
            purpose="dart_analysis",
            provider="ollama",
            model="qwen2.5:1.5b",
            prompt_tokens=40,
            completion_tokens=80,
            total_tokens=120,
            success=True,
            ttft_ms=200,
            total_ms=1200,
            tps=10.0,
            streamed=True,
            faithfulness=0.9,
            hallucination_rate=0.1,
            answer_relevance=0.7,
            context_precision=0.5,
        )
    )
    db_session.commit()
    res = await client.get("/api/v1/admin/llm-quality", headers=_header(admin))
    assert res.status_code == 200
    body = res.json()
    assert body["sample_size"] == 1
    assert body["rag_sample_size"] == 1
    assert body["ttft"]["p50"] == 200
    assert body["tps"]["p50"] == 10
    assert body["hallucination_rate"]["mean"] == 0.1
    assert body["runs"][0]["purpose"] == "dart_analysis"
    assert body["runs"][0]["streamed"] is True
