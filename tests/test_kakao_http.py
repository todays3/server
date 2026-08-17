"""Kakao HTTP helpers, memo errors, and send paths."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.services.kakao import (
    KakaoApiError,
    _is_token_error,
    _post_memo,
    build_authorize_url,
    exchange_code,
    fetch_kakao_profile,
    fetch_talk_message_agreed,
    parse_profile,
    refresh_access_token,
    send_digest_via_kakao,
    send_memo_to_me,
    split_memo_chunks,
    talk_message_from_scopes,
)


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text
        self.content = b"{}" if payload is not None else b""
        self.headers = {}

    def json(self):
        return self._payload


class _AsyncClient:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return None

    async def post(self, *_a, **_k):
        return self._resp

    async def get(self, *_a, **_k):
        return self._resp


def test_build_authorize_url_requires_key_and_prompt(monkeypatch):
    monkeypatch.setenv("KAKAO_REST_API_KEY", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError):
            build_authorize_url("s")
    finally:
        get_settings.cache_clear()
    monkeypatch.setenv("KAKAO_REST_API_KEY", "key")
    get_settings.cache_clear()
    try:
        url = build_authorize_url("st", prompt="consent")
        assert "prompt=consent" in url
        assert "scope=" not in url
        memo = build_authorize_url("st", prompt="consent", scopes="talk_message")
        assert "talk_message" in memo
        assert "account_email" not in memo
    finally:
        get_settings.cache_clear()


def test_talk_message_from_scopes_reads_agreed_flag():
    assert talk_message_from_scopes({"scopes": [{"id": "talk_message", "agreed": True}]}) is True
    assert talk_message_from_scopes({"scopes": [{"id": "talk_message", "agreed": False}]}) is False
    assert talk_message_from_scopes({"scopes": [{"id": "account_email", "agreed": True}]}) is False
    assert talk_message_from_scopes({}) is False


@pytest.mark.asyncio
async def test_fetch_talk_message_agreed_reads_kakao_scopes(monkeypatch):
    monkeypatch.setattr(
        "app.services.kakao.httpx.AsyncClient",
        lambda **_k: _AsyncClient(_Resp(200, {"scopes": [{"id": "talk_message", "agreed": True}]})),
    )
    assert await fetch_talk_message_agreed("tok") is True
    monkeypatch.setattr(
        "app.services.kakao.httpx.AsyncClient",
        lambda **_k: _AsyncClient(_Resp(403, {"msg": "denied"})),
    )
    assert await fetch_talk_message_agreed("tok") is False
    assert await fetch_talk_message_agreed("") is False


@pytest.mark.asyncio
async def test_exchange_refresh_profile_and_memo(monkeypatch):
    monkeypatch.setenv("KAKAO_REST_API_KEY", "key")
    monkeypatch.setenv("KAKAO_CLIENT_SECRET", "sec")
    get_settings.cache_clear()
    try:
        monkeypatch.setattr(
            "app.services.kakao.httpx.AsyncClient",
            lambda **_k: _AsyncClient(_Resp(200, {"access_token": "a", "refresh_token": "r"})),
        )
        token = await exchange_code("code")
        assert token["access_token"] == "a"
        refreshed = await refresh_access_token("r")
        assert refreshed["access_token"] == "a"
        profile = await fetch_kakao_profile("a")
        assert profile["access_token"] == "a"
        memo = await _post_memo("a", "hello")
        assert memo["access_token"] == "a"

        monkeypatch.setattr(
            "app.services.kakao.httpx.AsyncClient",
            lambda **_k: _AsyncClient(_Resp(400, {"error_description": "bad", "msg": "m", "code": -401})),
        )
        with pytest.raises(RuntimeError, match="bad"):
            await exchange_code("x")
        with pytest.raises(RuntimeError):
            await refresh_access_token("x")
        with pytest.raises(RuntimeError):
            await fetch_kakao_profile("x")
        with pytest.raises(KakaoApiError):
            await _post_memo("x", "t")
        with pytest.raises(RuntimeError, match="empty"):
            await refresh_access_token("")
    finally:
        get_settings.cache_clear()


def test_parse_profile_and_token_error():
    kid, name, email = parse_profile(
        {"id": 1, "properties": {}, "kakao_account": {"profile": {"nickname": "닉"}, "email": "A@B.COM"}}
    )
    assert kid == "1"
    assert name == "닉"
    assert email == "a@b.com"
    kid, name, email = parse_profile({})
    assert name == "카카오 친구"
    err = KakaoApiError("expired token", status_code=401, kakao_code=-401)
    assert _is_token_error(err) is True
    assert _is_token_error(KakaoApiError("nope", status_code=400, kakao_code=0)) is False


def test_split_memo_long_line():
    chunks = split_memo_chunks("제목", "가" * 2500, limit=200)
    assert len(chunks) >= 2
    assert all(len(c) <= 200 for c in chunks)


@pytest.mark.asyncio
async def test_send_digest_via_kakao_paths(monkeypatch):
    monkeypatch.setenv("KAKAO_REST_API_KEY", "")
    get_settings.cache_clear()
    try:
        ok, msg, _chunks = await send_digest_via_kakao(SimpleNamespace(kakao=None), "t", "b")
        assert ok is True
    finally:
        get_settings.cache_clear()

    monkeypatch.setenv("KAKAO_REST_API_KEY", "key")
    get_settings.cache_clear()
    try:
        user = SimpleNamespace(kakao=None)
        ok, msg, _chunks = await send_digest_via_kakao(user, "t", "b")
        assert ok is False

        user = SimpleNamespace(kakao=SimpleNamespace(access_token="a", refresh_token="r"))

        async def boom_fresh(*_a, **_k):
            raise RuntimeError("no refresh")

        monkeypatch.setattr("app.services.kakao.ensure_fresh_access_token", boom_fresh)
        ok, msg, _chunks = await send_digest_via_kakao(user, "t", "b", db=SimpleNamespace())
        assert ok is False
        assert "token refresh failed" in msg

        async def fresh(*_a, **_k):
            return "a"

        async def send_ok(*_a, **_k):
            return [{"ok": True}]

        monkeypatch.setattr("app.services.kakao.ensure_fresh_access_token", fresh)
        monkeypatch.setattr("app.services.kakao._post_memo", send_ok)
        ok, msg, _chunks = await send_digest_via_kakao(user, "t", "b", db=SimpleNamespace())
        assert ok is True

        async def token_err(*_a, **_k):
            raise KakaoApiError("expired", status_code=401, kakao_code=-401)

        async def refresh_fail(*_a, **_k):
            raise RuntimeError("still bad")

        monkeypatch.setattr("app.services.kakao._post_memo", token_err)
        monkeypatch.setattr("app.services.kakao._refresh_user_token", refresh_fail)
        ok, msg, _chunks = await send_digest_via_kakao(user, "t", "b", db=SimpleNamespace())
        assert ok is False

        async def refresh_ok(*_a, **_k):
            return "new"

        calls = {"n": 0}

        async def send_after(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise KakaoApiError("expired", status_code=401, kakao_code=-401)
            return {"ok": True}

        monkeypatch.setattr("app.services.kakao._refresh_user_token", refresh_ok)
        monkeypatch.setattr("app.services.kakao._post_memo", send_after)
        ok, msg, _chunks = await send_digest_via_kakao(user, "t", "b", db=SimpleNamespace())
        assert ok is True

        async def scope_err(*_a, **_k):
            raise KakaoApiError("insufficient scopes.", status_code=403, kakao_code=-402)

        monkeypatch.setattr("app.services.kakao._post_memo", scope_err)
        ok, msg, _chunks = await send_digest_via_kakao(user, "t", "b", db=SimpleNamespace())
        assert ok is False
        assert "나에게 보내기" in msg

        async def other_err(*_a, **_k):
            raise RuntimeError("network")

        monkeypatch.setattr("app.services.kakao.ensure_fresh_access_token", fresh)
        monkeypatch.setattr("app.services.kakao._post_memo", other_err)
        ok, msg, _chunks = await send_digest_via_kakao(user, "t", "b")
        assert ok is False
        assert "network" in msg
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_send_memo_to_me(monkeypatch):
    async def post(_token, text):
        return {"text": text}

    monkeypatch.setattr("app.services.kakao._post_memo", post)
    out = await send_memo_to_me("a", "t", "b")
    assert out

