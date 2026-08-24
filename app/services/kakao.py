"""Kakao OAuth + Talk '나에게 보내기' (memo) helpers.

Docs:
- OAuth: https://developers.kakao.com/docs/latest/ko/kakaologin/rest-api
- Memo: https://developers.kakao.com/docs/latest/ko/kakaotalk-message/rest-api#default-template-msg
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import DetachedInstanceError

from app.config import get_settings
from app.models import KakaoAccount, Preference, User
from app.services.nicknames import random_nickname


AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
ME_URL = "https://kapi.kakao.com/v2/user/me"
SCOPES_URL = "https://kapi.kakao.com/v2/user/scopes"
MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
# Joins per-assistant Kakao texts in stored digest.body. Never sent as visible text.
ASSISTANT_MEMO_BREAK = "\n\n\x1e\n\n"

# Optional extra scopes for 나에게 보내기 after login. Login itself omits scope
# so Kakao only asks for items already enabled in the developer console.
MEMO_SCOPES = "talk_message"

# Kakao text template hard limit
MEMO_TEXT_LIMIT = 1000
# Refresh before send if access token is missing expiry or expires within this window.
ACCESS_REFRESH_SKEW = timedelta(minutes=10)
# Status checks used to wait up to 10s on kapi; keep boot snappy.
SCOPES_HTTP_TIMEOUT = 2.0
SCOPES_CACHE_TTL_SECONDS = 300.0

# access_token → (monotonic_ts, agreed)
_talk_message_cache: dict[str, tuple[float, bool]] = {}


def clear_talk_message_cache(access_token: str | None = None) -> None:
    """Drop cached scope checks (all, or one token after disconnect/refresh)."""
    if access_token is None:
        _talk_message_cache.clear()
        return
    _talk_message_cache.pop(access_token, None)


def build_authorize_url(state: str, *, prompt: str | None = None, scopes: str | None = None) -> str:
    settings = get_settings()
    if not settings.kakao_configured:
        raise RuntimeError("Kakao REST API key is not configured")
    params = {
        "client_id": settings.kakao_rest_api_key,
        "redirect_uri": settings.kakao_redirect_uri,
        "response_type": "code",
        "state": state,
    }
    if scopes:
        params["scope"] = scopes
    if prompt:
        params["prompt"] = prompt
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
        payload = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise RuntimeError(
                str(payload.get("error_description") or payload.get("error") or payload.get("msg") or resp.text)
            )
        return payload


async def refresh_access_token(refresh_token: str) -> dict:
    """Exchange refresh_token for a new access_token (+ optionally rotated refresh)."""
    settings = get_settings()
    if not refresh_token:
        raise RuntimeError("Kakao refresh_token is empty")
    data = {
        "grant_type": "refresh_token",
        "client_id": settings.kakao_rest_api_key,
        "refresh_token": refresh_token,
    }
    if settings.kakao_client_secret:
        data["client_secret"] = settings.kakao_client_secret
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(TOKEN_URL, data=data)
        payload = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise RuntimeError(payload.get("error_description") or payload.get("msg") or resp.text)
        return payload


async def fetch_kakao_profile(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(ME_URL, headers={"Authorization": f"Bearer {access_token}"})
        payload = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            raise RuntimeError(str(payload.get("msg") or payload.get("error_description") or resp.text))
        return payload


def talk_message_from_scopes(payload: dict) -> bool:
    for row in payload.get("scopes") or []:
        if isinstance(row, dict) and row.get("id") == MEMO_SCOPES:
            return bool(row.get("agreed"))
    return False


async def fetch_talk_message_agreed(
    access_token: str,
    *,
    timeout: float = SCOPES_HTTP_TIMEOUT,
    use_cache: bool = True,
) -> bool:
    """Check talk_message scope. Cached — /kakao/status must not wait on kapi every boot."""
    if not access_token:
        return False
    now = time.monotonic()
    if use_cache:
        cached = _talk_message_cache.get(access_token)
        if cached is not None and now - cached[0] <= SCOPES_CACHE_TTL_SECONDS:
            return cached[1]
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                SCOPES_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                params={"scopes": MEMO_SCOPES},
            )
            payload = resp.json() if resp.content else {}
            if resp.status_code >= 400:
                agreed = False
            else:
                agreed = talk_message_from_scopes(payload if isinstance(payload, dict) else {})
    except (httpx.TimeoutException, httpx.TransportError, OSError):
        # Prefer last known answer over failing the whole desk boot.
        if use_cache:
            cached = _talk_message_cache.get(access_token)
            if cached is not None:
                return cached[1]
        return False
    _talk_message_cache[access_token] = (now, agreed)
    return agreed


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


def apply_token_payload(
    account: KakaoAccount,
    payload: dict,
    *,
    now: datetime | None = None,
) -> None:
    """Copy Kakao token JSON onto the account, including expiry timestamps."""
    stamp = now or datetime.now(timezone.utc)
    access = str(payload.get("access_token") or "")
    if access:
        if account.access_token and account.access_token != access:
            clear_talk_message_cache(account.access_token)
        account.access_token = access
        expires_in = payload.get("expires_in")
        if expires_in is not None:
            account.access_expires_at = stamp + timedelta(seconds=int(expires_in))
    refresh = str(payload.get("refresh_token") or "")
    if refresh:
        account.refresh_token = refresh
        refresh_expires_in = payload.get("refresh_token_expires_in")
        if refresh_expires_in is not None:
            account.refresh_expires_at = stamp + timedelta(seconds=int(refresh_expires_in))


def access_token_needs_refresh(account: KakaoAccount, *, now: datetime | None = None) -> bool:
    if not account.refresh_token:
        return False
    if account.access_expires_at is None:
        return True
    stamp = now or datetime.now(timezone.utc)
    expires = account.access_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return expires <= stamp + ACCESS_REFRESH_SKEW


def upsert_kakao_account(
    db: Session,
    user: User,
    *,
    kakao_id: str,
    access_token: str,
    refresh_token: str,
    expires_in: int | None = None,
    refresh_token_expires_in: int | None = None,
) -> KakaoAccount:
    account = user.kakao
    if account is None:
        account = KakaoAccount(user_id=user.id, kakao_id=kakao_id)
        db.add(account)
    account.kakao_id = kakao_id
    payload: dict = {"access_token": access_token, "refresh_token": refresh_token}
    if expires_in is not None:
        payload["expires_in"] = expires_in
    if refresh_token_expires_in is not None:
        payload["refresh_token_expires_in"] = refresh_token_expires_in
    apply_token_payload(account, payload)
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
    expires_in: int | None = None,
    refresh_token_expires_in: int | None = None,
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
            expires_in=expires_in,
            refresh_token_expires_in=refresh_token_expires_in,
        )
        changed = False
        if not (existing.display_name or "").strip():
            existing.display_name = random_nickname()
            changed = True
        if changed:
            db.add(existing)
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
            expires_in=expires_in,
            refresh_token_expires_in=refresh_token_expires_in,
        )
        return by_email, False

    user = User(
        email=email_norm,
        display_name=random_nickname(),
        password_hash=None,
        status="pending",
        is_admin=False,
        approved_at=None,
    )
    db.add(user)
    db.flush()
    db.add(
        Preference(
            user_id=user.id,
            topics="경제/주식/국내증시,경제/주식/미국증시",
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
        expires_in=expires_in,
        refresh_token_expires_in=refresh_token_expires_in,
    )
    db.refresh(user)
    return user, True


def memo_sections(body: str) -> list[str]:
    """Split stored digest.body into one Kakao memo per hired assistant."""
    return [part.strip() for part in (body or "").split("\x1e") if part.strip()]


def _split_length_chunks(title: str, body: str, *, limit: int) -> list[str]:
    """Split one assistant memo into Kakao text chunks under `limit` characters."""
    full = f"{title}\n\n{body}".strip()
    if len(full) <= limit:
        return [full]

    chunks: list[str] = []
    paragraphs = body.split("\n")
    part_idx = 1
    current = f"{title}\n\n"

    for line in paragraphs:
        candidate = current + line + "\n"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current.strip() and current != f"{title}\n\n":
            chunks.append(current.rstrip())
            part_idx += 1
            current = f"{title} · 이어서 ({part_idx})\n\n"
        while line:
            room = limit - len(current)
            if room <= 8:
                chunks.append(current.rstrip())
                part_idx += 1
                current = f"{title} · 이어서 ({part_idx})\n\n"
                room = limit - len(current)
            take = line[:room]
            current += take
            line = line[room:]
            if line:
                chunks.append(current.rstrip())
                part_idx += 1
                current = f"{title} · 이어서 ({part_idx})\n\n"
        current += "\n"

    if current.strip():
        chunks.append(current.rstrip())

    if len(chunks) <= 1:
        return [c[:limit] for c in chunks] or [full[:limit]]

    total = len(chunks)
    annotated: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        if i == 1 and chunk.startswith(title):
            labeled = chunk.replace(title, f"{title} ({i}/{total})", 1)
        elif f"· 이어서 ({i})" in chunk:
            labeled = chunk.replace(f"· 이어서 ({i})", f"({i}/{total})", 1)
        else:
            labeled = f"({i}/{total})\n{chunk}"
        if len(labeled) > limit:
            labeled = labeled[:limit]
        annotated.append(labeled)
    return annotated


def split_memo_chunks(title: str, body: str, *, limit: int = MEMO_TEXT_LIMIT) -> list[str]:
    """One Kakao memo per assistant, then length-split each memo if needed."""
    sections = memo_sections(body)
    if len(sections) <= 1:
        return _split_length_chunks(title, sections[0] if sections else (body or ""), limit=limit)
    chunks: list[str] = []
    for section in sections:
        chunks.extend(_split_length_chunks(title, section, limit=limit))
    return chunks


async def _post_memo(access_token: str, text: str) -> dict:
    template = {
        "object_type": "text",
        "text": text[:MEMO_TEXT_LIMIT],
        "link": {
            "web_url": "https://localhost",
            "mobile_web_url": "https://localhost",
        },
        "button_title": "하루만장",
    }
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post(
            MEMO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            data={"template_object": json.dumps(template, ensure_ascii=False)},
        )
        payload = resp.json() if resp.content else {}
        if resp.status_code >= 400:
            err = payload.get("msg") or resp.text or "Kakao send failed"
            code = payload.get("code")
            raise KakaoApiError(str(err), status_code=resp.status_code, kakao_code=code)
        return payload


class KakaoApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 400, kakao_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.kakao_code = kakao_code


def _is_token_error(exc: KakaoApiError) -> bool:
    # -401 auth errors / expired token
    if exc.kakao_code in (-401, -401):
        return True
    msg = str(exc).lower()
    return "token" in msg or "expired" in msg or "unauthorized" in msg or exc.status_code == 401


async def send_memo_to_me(
    access_token: str,
    title: str,
    body: str,
    *,
    start_chunk: int = 0,
) -> tuple[list[dict], int]:
    """Send memo chunks; resume from start_chunk. Returns API payloads and total chunks sent."""
    chunks = split_memo_chunks(title, body)
    start = max(0, min(int(start_chunk), len(chunks)))
    results: list[dict] = []
    sent = start
    for index in range(start, len(chunks)):
        results.append(await _post_memo(access_token, chunks[index]))
        sent = index + 1
    return results, sent


async def _refresh_user_token(db: Session, user: User) -> str:
    account = kakao_account_for(user, db)
    if account is None or not account.refresh_token:
        raise RuntimeError("Kakao refresh_token is missing — reconnect Kakao")
    token = await refresh_access_token(account.refresh_token)
    access = str(token.get("access_token") or "")
    if not access:
        raise RuntimeError("Kakao refresh did not return access_token")
    apply_token_payload(account, token)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account.access_token


async def ensure_fresh_access_token(db: Session, user: User) -> str:
    account = kakao_account_for(user, db)
    if account is None or not account.access_token:
        raise RuntimeError("Kakao account is not connected")
    if access_token_needs_refresh(account):
        return await _refresh_user_token(db, user)
    return account.access_token


def kakao_account_for(user: User, db: Session | None = None) -> KakaoAccount | None:
    """Load KakaoAccount without lazy-loading a detached User (send-now worker thread)."""
    try:
        state = sa_inspect(user, raiseerr=False)
        if state is None:
            return getattr(user, "kakao", None)
        if not state.detached:
            return user.kakao
    except DetachedInstanceError:
        pass
    user_id = getattr(user, "id", None)
    if db is None or user_id is None:
        return None
    return db.scalar(select(KakaoAccount).where(KakaoAccount.user_id == user_id))


def friendly_kakao_send_error(raw: str) -> str:
    text = (raw or "").strip()
    low = text.lower()
    if "insufficient scope" in low:
        return (
            "카카오 ‘나에게 보내기’ 권한이 없습니다. "
            "‘권한 갱신’을 눌러 메시지 권한을 허용한 뒤 다시 보내세요."
        )
    return text


async def send_digest_via_kakao(
    user: User,
    title: str,
    body: str,
    *,
    db: Session | None = None,
    chunks_sent: int = 0,
) -> tuple[bool, str, int]:
    settings = get_settings()
    if not settings.kakao_configured:
        total = len(split_memo_chunks(title, body))
        return True, "mock: kakao not configured — digest stored as sent", total

    account = kakao_account_for(user, db)
    if account is None or not account.access_token:
        return False, "Kakao account is not connected", int(chunks_sent)

    access = account.access_token
    if db is not None:
        try:
            access = await ensure_fresh_access_token(db, user)
        except Exception as refresh_exc:  # noqa: BLE001
            return False, f"token refresh failed: {refresh_exc}", int(chunks_sent)

    async def _send(token: str, start: int) -> tuple[bool, str, int, KakaoApiError | None]:
        chunks = split_memo_chunks(title, body)
        sent = max(0, min(int(start), len(chunks)))
        try:
            for index in range(sent, len(chunks)):
                await _post_memo(token, chunks[index])
                sent = index + 1
            return True, "", sent, None
        except KakaoApiError as exc:
            return False, friendly_kakao_send_error(str(exc)), sent, exc
        except Exception as exc:  # noqa: BLE001
            return False, friendly_kakao_send_error(str(exc)), sent, None

    ok, err, sent, api_exc = await _send(access, int(chunks_sent))
    if ok:
        return True, "", sent

    if db is not None and api_exc is not None and _is_token_error(api_exc):
        try:
            access = await _refresh_user_token(db, user)
            ok, err, sent, _api_exc = await _send(access, sent)
            if ok:
                return True, "refreshed token then sent", sent
            return False, err, sent
        except Exception as refresh_exc:  # noqa: BLE001
            return False, f"token refresh failed: {refresh_exc}", sent

    return False, err, sent
