"""Proactive Kakao access-token refresh before memo send."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models import KakaoAccount, User
from app.services import kakao as kakao_mod
from app.services.kakao import access_token_needs_refresh, apply_token_payload


def test_access_token_needs_refresh_when_expiry_unknown():
    account = KakaoAccount(access_token="a", refresh_token="r", access_expires_at=None)
    assert access_token_needs_refresh(account) is True


def test_access_token_needs_refresh_when_near_expiry():
    now = datetime.now(timezone.utc)
    account = KakaoAccount(
        access_token="a",
        refresh_token="r",
        access_expires_at=now + timedelta(minutes=3),
    )
    assert access_token_needs_refresh(account, now=now) is True


def test_access_token_skips_refresh_when_still_fresh():
    now = datetime.now(timezone.utc)
    account = KakaoAccount(
        access_token="a",
        refresh_token="r",
        access_expires_at=now + timedelta(hours=4),
    )
    assert access_token_needs_refresh(account, now=now) is False


def test_apply_token_payload_sets_expiry_and_rotated_refresh():
    now = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    account = KakaoAccount(access_token="old", refresh_token="old-r")
    apply_token_payload(
        account,
        {
            "access_token": "new-a",
            "expires_in": 21600,
            "refresh_token": "new-r",
            "refresh_token_expires_in": 5184000,
        },
        now=now,
    )
    assert account.access_token == "new-a"
    assert account.refresh_token == "new-r"
    assert account.access_expires_at == now + timedelta(seconds=21600)
    assert account.refresh_expires_at == now + timedelta(seconds=5184000)


@pytest.mark.asyncio
async def test_send_digest_refreshes_before_memo_when_stale(monkeypatch):
    monkeypatch.setattr(
        kakao_mod,
        "get_settings",
        lambda: SimpleNamespace(kakao_configured=True),
    )
    refreshed: list[str] = []

    async def _refresh(token: str) -> dict:
        refreshed.append(token)
        return {"access_token": "fresh-a", "expires_in": 21600}

    sent: list[str] = []

    async def _memo(access: str, title: str, body: str):
        sent.append(access)
        return [{}]

    monkeypatch.setattr(kakao_mod, "refresh_access_token", _refresh)
    monkeypatch.setattr(kakao_mod, "send_memo_to_me", _memo)

    user = User(email="u@example.com", display_name="유저", status="approved")
    user.kakao = KakaoAccount(access_token="stale-a", refresh_token="r", access_expires_at=None)
    db = SimpleNamespace(add=lambda *_a, **_k: None, commit=lambda: None, refresh=lambda *_a: None)

    ok, err = await kakao_mod.send_digest_via_kakao(user, "제목", "본문", db=db)
    assert ok is True
    assert err == ""
    assert refreshed == ["r"]
    assert sent == ["fresh-a"]
    assert user.kakao.access_token == "fresh-a"


@pytest.mark.asyncio
async def test_send_digest_does_not_refresh_when_access_still_valid(monkeypatch):
    monkeypatch.setattr(
        kakao_mod,
        "get_settings",
        lambda: SimpleNamespace(kakao_configured=True),
    )
    called = {"refresh": 0}

    async def _refresh(_token: str) -> dict:
        called["refresh"] += 1
        return {"access_token": "nope"}

    async def _memo(access: str, title: str, body: str):
        assert access == "good-a"
        return [{}]

    monkeypatch.setattr(kakao_mod, "refresh_access_token", _refresh)
    monkeypatch.setattr(kakao_mod, "send_memo_to_me", _memo)

    user = User(email="u@example.com", display_name="유저", status="approved")
    user.kakao = KakaoAccount(
        access_token="good-a",
        refresh_token="r",
        access_expires_at=datetime.now(timezone.utc) + timedelta(hours=5),
    )
    db = SimpleNamespace(add=lambda *_a, **_k: None, commit=lambda: None, refresh=lambda *_a: None)

    ok, _err = await kakao_mod.send_digest_via_kakao(user, "제목", "본문", db=db)
    assert ok is True
    assert called["refresh"] == 0
