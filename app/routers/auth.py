from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import create_access_token, get_current_user, hash_password, verify_password
from app.config import get_settings
from app.db import get_db
from app.deps.rate_limit import rate_limit_auth
from app.models import Preference, User
from app.schemas import (
    KakaoCompleteRequest,
    KakaoOAuthStartOut,
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserOut,
)
from app.services import kakao as kakao_service
from app.services.oauth_tickets import consume_login_ticket, issue_login_ticket

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        is_admin=user.is_admin,
        kakao_connected=user.kakao is not None and bool(user.kakao.access_token),
    )


def _encode_oauth_state(*, purpose: Literal["login", "connect"], user_id: int | None = None) -> str:
    settings = get_settings()
    payload: dict = {
        "purpose": purpose,
        "flow": "kakao_oauth",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
    }
    if user_id is not None:
        payload["uid"] = user_id
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def _decode_oauth_state(state: str) -> dict:
    settings = get_settings()
    try:
        payload = jwt.decode(state, settings.secret_key, algorithms=["HS256"])
        if payload.get("flow") != "kakao_oauth":
            raise ValueError("bad flow")
        purpose = payload.get("purpose")
        if purpose not in ("login", "connect"):
            raise ValueError("bad purpose")
        return payload
    except (JWTError, KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid OAuth state") from exc


def _front_redirect(*, path: str = "/auth", **params: str | None) -> RedirectResponse:
    settings = get_settings()
    qs = urlencode({k: v for k, v in params.items() if v})
    suffix = f"?{qs}" if qs else ""
    return RedirectResponse(url=f"{settings.frontend_origin}{path}{suffix}")


@router.post("/register", response_model=RegisterResponse, deprecated=True)
def register(payload: RegisterRequest, db: Annotated[Session, Depends(get_db)]) -> RegisterResponse:
    """Email signup kept for seed/admin fallback. Primary path is Kakao OAuth."""
    settings = get_settings()
    existing = db.scalar(select(User).where(User.email == payload.email.lower()))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 가입된 이메일입니다")

    user = User(
        email=payload.email.lower(),
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
        status="pending",
        is_admin=False,
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
    db.commit()
    return RegisterResponse(
        message="가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다.",
        status="pending",
    )


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[None, Depends(rate_limit_auth)],
) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="이메일 또는 비밀번호가 올바르지 않습니다")

    if user.status == "pending":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="가입 승인 대기 중입니다. 관리자 승인 후 로그인할 수 있습니다.",
        )
    if user.status == "rejected":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="가입이 거절된 계정입니다.",
        )
    if user.status != "approved":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="로그인할 수 없는 계정입니다")

    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: Annotated[User, Depends(get_current_user)]) -> UserOut:
    return _user_out(user)


@router.get("/kakao/start", response_model=KakaoOAuthStartOut)
def kakao_oauth_start(
    _: Annotated[None, Depends(rate_limit_auth)],
) -> KakaoOAuthStartOut:
    """Begin Kakao social signup/login. Browser clients may follow `url`."""
    settings = get_settings()
    if not settings.kakao_configured:
        return KakaoOAuthStartOut(
            configured=False,
            url=None,
            message="KAKAO_REST_API_KEY가 없습니다. 카카오 개발자 콘솔에서 키와 Redirect URI를 설정하세요.",
        )
    state = _encode_oauth_state(purpose="login")
    url = kakao_service.build_authorize_url(state)
    return KakaoOAuthStartOut(configured=True, url=url)


@router.get("/kakao/redirect")
def kakao_oauth_redirect(
    _: Annotated[None, Depends(rate_limit_auth)],
) -> RedirectResponse:
    """302 helper for button links: /api/v1/auth/kakao/redirect → Kakao authorize."""
    settings = get_settings()
    if not settings.kakao_configured:
        return _front_redirect(error="kakao_not_configured")
    state = _encode_oauth_state(purpose="login")
    return RedirectResponse(url=kakao_service.build_authorize_url(state))


@router.get("/kakao/callback")
async def kakao_oauth_callback(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[None, Depends(rate_limit_auth)],
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    error: Annotated[str | None, Query()] = None,
    error_description: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    if error:
        return _front_redirect(error=error, detail=(error_description or "")[:120])
    if not code or not state:
        return _front_redirect(error="missing_code")

    try:
        payload = _decode_oauth_state(state)
    except HTTPException:
        return _front_redirect(error="invalid_state")

    purpose = payload["purpose"]
    dest = "/app" if purpose == "connect" else "/auth"

    try:
        token = await kakao_service.exchange_code(code)
    except Exception as exc:  # noqa: BLE001
        return _front_redirect(path=dest, error="token_exchange_failed", detail=str(exc)[:120])

    access = str(token.get("access_token") or "")
    refresh = str(token.get("refresh_token") or "")
    if not access:
        return _front_redirect(path=dest, error="missing_access_token")

    try:
        profile = await kakao_service.fetch_kakao_profile(access)
    except Exception as exc:  # noqa: BLE001
        return _front_redirect(path=dest, error="profile_failed", detail=str(exc)[:120])

    kakao_id, nickname, email = kakao_service.parse_profile(profile)
    if not kakao_id:
        return _front_redirect(path=dest, error="incomplete_profile")

    if purpose == "connect":
        user_id = int(payload.get("uid") or 0)
        user = db.get(User, user_id)
        if user is None:
            return _front_redirect(error="user_not_found")
        other = kakao_service.find_user_by_kakao_id(db, kakao_id)
        if other is not None and other.id != user.id:
            return _front_redirect(path="/app", error="kakao_already_linked")
        kakao_service.upsert_kakao_account(
            db,
            user,
            kakao_id=kakao_id,
            access_token=access,
            refresh_token=refresh,
        )
        return _front_redirect(path="/app", kakao="connected")

    user, _created = kakao_service.resolve_or_create_oauth_user(
        db,
        kakao_id=kakao_id,
        display_name=nickname,
        email=email,
        access_token=access,
        refresh_token=refresh,
    )

    if user.status == "pending":
        return _front_redirect(status="pending", name=user.display_name)
    if user.status == "rejected":
        return _front_redirect(error="rejected")
    if user.status != "approved":
        return _front_redirect(error="not_approved")

    ticket = issue_login_ticket(create_access_token(user.id))
    return _front_redirect(oauth_ticket=ticket)


@router.post("/kakao/complete", response_model=TokenResponse)
def kakao_oauth_complete(
    payload: KakaoCompleteRequest,
    _: Annotated[None, Depends(rate_limit_auth)],
) -> TokenResponse:
    token = consume_login_ticket(payload.ticket)
    if not token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="만료되었거나 잘못된 카카오 로그인입니다")
    return TokenResponse(access_token=token)
