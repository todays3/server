"""Admin digest preview — crawl + AI without Kakao send."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.auth import create_access_token, hash_password
from app.models import Digest, Preference, User
from app.services.sources import SourceItem


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
            topics="경제/주식/국내증시",
            sources="hn",
            timezone="Asia/Seoul",
        )
    )
    user = User(
        email="user@example.com",
        display_name="유저",
        password_hash=hash_password("user12345"),
        status="approved",
        is_admin=False,
    )
    session.add(user)
    session.flush()
    session.add(
        Preference(
            user_id=user.id,
            topics="기술",
            sources="hn",
            timezone="Asia/Seoul",
        )
    )
    nopref = User(
        email="nopref@example.com",
        display_name="설정없음",
        password_hash=hash_password("user12345"),
        status="approved",
        is_admin=False,
    )
    session.add(nopref)
    session.commit()
    try:
        yield session
    finally:
        session.close()




def _admin_header(db_session) -> dict[str, str]:
    admin = db_session.query(User).filter_by(email="admin@example.com").one()
    return {"Authorization": f"Bearer {create_access_token(admin.id)}"}


def _user_header(db_session) -> dict[str, str]:
    user = db_session.query(User).filter_by(email="user@example.com").one()
    return {"Authorization": f"Bearer {create_access_token(user.id)}"}


def _fake_candidates(*_args, **_kwargs) -> list[SourceItem]:
    return [
        SourceItem(
            kind="커뮤니티",
            title="후보 A",
            url="https://example.com/a",
            summary="요약 A",
            source="Hacker News",
        ),
        SourceItem(
            kind="아티클",
            title="후보 B",
            url="https://example.com/b",
            summary="요약 B",
            source="News",
        ),
        SourceItem(
            kind="유튜브",
            title="후보 C",
            url="https://example.com/c",
            summary="요약 C",
            source="YouTube",
        ),
    ]


@pytest.mark.asyncio
async def test_preview_requires_admin(client: AsyncClient, db_session):
    res = await client.post("/api/v1/admin/digests/preview", json={"user_id": 1})
    assert res.status_code == 401
    res = await client.post(
        "/api/v1/admin/digests/preview",
        json={"user_id": 1},
        headers=_user_header(db_session),
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_preview_returns_candidates_without_kakao_or_digest_row(
    client: AsyncClient, db_session, monkeypatch
):
    monkeypatch.setattr("app.services.digest.gather_candidates", _fake_candidates)
    monkeypatch.setattr(
        "app.services.digest._llm_curate",
        lambda *_a, **_k: (None, "llm_not_configured", ""),
    )
    kakao_calls: list[object] = []
    monkeypatch.setattr(
        "app.services.kakao.send_digest_via_kakao",
        lambda *_a, **_k: kakao_calls.append(True),
    )

    target = db_session.query(User).filter_by(email="user@example.com").one()
    before = db_session.scalar(select(func.count()).select_from(Digest)) or 0
    res = await client.post(
        "/api/v1/admin/digests/preview",
        json={"user_id": target.id},
        headers=_admin_header(db_session),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == "user@example.com"
    assert data["sent_to_kakao"] is False
    assert data["curator"] == "heuristic"
    assert data["llm_skip_reason"] == "llm_not_configured"
    assert len(data["candidates"]) == 3
    assert len(data["items"]) == 3
    assert "후보 A" in data["body"] or data["items"][0]["title"]
    after = db_session.scalar(select(func.count()).select_from(Digest)) or 0
    assert after == before
    assert kakao_calls == []


@pytest.mark.asyncio
async def test_preview_missing_user(client: AsyncClient, db_session):
    res = await client.post(
        "/api/v1/admin/digests/preview",
        json={"user_id": 99999},
        headers=_admin_header(db_session),
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_preview_user_without_preference(client: AsyncClient, db_session):
    nopref = db_session.query(User).filter_by(email="nopref@example.com").one()
    res = await client.post(
        "/api/v1/admin/digests/preview",
        json={"user_id": nopref.id},
        headers=_admin_header(db_session),
    )
    assert res.status_code == 400
