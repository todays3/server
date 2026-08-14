"""Kakao OAuth + Talk '나에게 보내기' (memo) helpers.

Docs:
- OAuth: https://developers.kakao.com/docs/latest/ko/kakaologin/rest-api
- Memo: https://developers.kakao.com/docs/latest/ko/kakaotalk-message/rest-api#default-template-msg
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import KakaoAccount, Preference, User


AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
ME_URL = "https://kapi.kakao.com/v2/user/me"
MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

# talk_message enables 나에게 보내기; profile + email for signup identity
OAUTH_SCOPES = "talk_message,profile_nickname,account_email"


def build_authorize_url(state: str) -> str:
    settings = get_settings()
    if not settings.kakao_configured:
        raise RuntimeError("Kakao REST API key is not configured")
    params = {
        "client_id": settings.kakao_rest_api_key,
        "redirect_uri": settings.kakao_redirect_uri,
        "response_type": "code",
        "state": state,
        "scope": OAUTH_SCOPES,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    settings = get_settings()
    data = {
        "grant_type": "authorization_code",
        "client_id": settings.kakao_rest_api_key,
        "redirect_uri": settings.kakao_redirect_uri,
        "code": code,
    }
    if settings.kakao_client_secret:
        data["client_secret"] = settings.kakao_client_secret

    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(TOKEN_URL, data=data)
        resp.raise_for_status()
        return resp.json()


async def fetch_kakao_profile(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(ME_URL, headers={"Authorization": f"Bearer {access_token}"})
        resp.raise_for_status()
        return resp.json()


def parse_profile(profile: dict) -> tuple[str, str, str | None]:
    """Return (kakao_id, display_name, email|None)."""
    kakao_id = str(profile.get("id") or "")
    props = profile.get("properties") or {}
    account = profile.get("kakao_account") or {}
    nickname = (
        str(props.get("nickname") or "").strip()
        or str(account.get("profile", {}).get("nickname") or "").strip()
        or "카카오 친구"
    )
    email = account.get("email")
    email_norm = str(email).strip().lower() if email else None
    return kakao_id, nickname, email_norm


def upsert_kakao_account(
    db: Session,
    user: User,
    *,
    kakao_id: str,
    access_token: str,
    refresh_token: str,
) -> KakaoAccount:
    account = user.kakao
    if account is None:
        account = KakaoAccount(user_id=user.id, kakao_id=kakao_id)
        db.add(account)
    account.kakao_id = kakao_id
    account.access_token = access_token
    account.refresh_token = refresh_token or account.refresh_token
    db.commit()
    db.refresh(account)
    return account


def find_user_by_kakao_id(db: Session, kakao_id: str) -> User | None:
    account = db.scalar(select(KakaoAccount).where(KakaoAccount.kakao_id == kakao_id))
    return account.user if account else None


def resolve_or_create_oauth_user(
    db: Session,
    *,
    kakao_id: str,
    display_name: str,
    email: str | None,
    access_token: str,
    refresh_token: str,
) -> tuple[User, bool]:
    """Upsert user from Kakao identity. Returns (user, created)."""
    settings = get_settings()
    existing = find_user_by_kakao_id(db, kakao_id)
    if existing is not None:
        upsert_kakao_account(
            db,
            existing,
            kakao_id=kakao_id,
            access_token=access_token,
            refresh_token=refresh_token,
        )
        if display_name and not existing.display_name:
            existing.display_name = display_name
            db.commit()
            db.refresh(existing)
        return existing, False

    synthetic = f"kakao.{kakao_id}@users.oday3.app"
    email_norm = email or synthetic
    by_email = db.scalar(select(User).where(User.email == email_norm))
    if by_email is not None:
        upsert_kakao_account(
            db,
            by_email,
            kakao_id=kakao_id,
            access_token=access_token,
            refresh_token=refresh_token,
        )
        return by_email, False

    user = User(
        email=email_norm,
        display_name=display_name.strip() or "카카오 친구",
        password_hash=None,
        status="pending",
        is_admin=False,
    )
    db.add(user)
    db.flush()
    db.add(
        Preference(
            user_id=user.id,
            topics="경제/주식/all,IT/AI/all,연애/소개팅/all",
            tone="",
            timezone=settings.default_timezone,
        )
    )
    upsert_kakao_account(
        db,
        user,
        kakao_id=kakao_id,
        access_token=access_token,
        refresh_token=refresh_token,
    )
    db.refresh(user)
    return user, True


async def send_memo_to_me(access_token: str, title: str, body: str) -> dict:
    """Send a text template to the authenticated user's KakaoTalk 'me' chat."""
    template = {
        "object_type": "text",
        "text": f"{title}\n\n{body}"[:1000],
        "link": {
            "web_url": "https://localhost",
            "mobile_web_url": "https://localhost",
        },
        "button_title": "오늘의 3",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            MEMO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            data={"template_object": json.dumps(template, ensure_ascii=False)},
        )
        payload = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise RuntimeError(payload.get("msg") or resp.text or "Kakao send failed")
        return payload


async def send_digest_via_kakao(user: User, title: str, body: str) -> tuple[bool, str]:
    settings = get_settings()
    if not settings.kakao_configured:
        return True, "mock: kakao not configured — digest stored as sent"

    if user.kakao is None or not user.kakao.access_token:
        return False, "Kakao account is not connected"

    try:
        await send_memo_to_me(user.kakao.access_token, title, body)
        return True, ""
    except Exception as exc:  # noqa: BLE001 — surface to digest.error_message
        return False, str(exc)

