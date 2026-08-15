from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.config import get_settings
from app.db import get_db
from app.models import User
from app.routers.auth import _encode_oauth_state
from app.schemas import KakaoConnectOut, KakaoDisconnectOut, KakaoStatusOut
from app.services import kakao as kakao_service

router = APIRouter(prefix="/kakao", tags=["kakao"])


@router.get("/connect", response_model=KakaoConnectOut)
def connect_kakao(user: Annotated[User, Depends(get_current_user)]) -> KakaoConnectOut:
    """Re-authorize talk_message scope while already logged in (token refresh)."""
    settings = get_settings()
    if not settings.kakao_configured:
        return KakaoConnectOut(
            configured=False,
            message="Set KAKAO_REST_API_KEY in server/.env.development or .env.production.",
            url=None,
        )
    url = kakao_service.build_authorize_url(
        _encode_oauth_state(purpose="connect", user_id=user.id),
        prompt="consent",
        scopes=kakao_service.MEMO_SCOPES,
    )
    return KakaoConnectOut(configured=True, url=url)


@router.get("/status", response_model=KakaoStatusOut)
def kakao_status(user: Annotated[User, Depends(get_current_user)]) -> KakaoStatusOut:
    settings = get_settings()
    connected = user.kakao is not None and bool(user.kakao.access_token)
    return KakaoStatusOut(
        configured=settings.kakao_configured,
        connected=connected,
        kakao_id=user.kakao.kakao_id if connected and user.kakao else None,
    )


@router.delete("/disconnect", response_model=KakaoDisconnectOut)
def disconnect_kakao(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> KakaoDisconnectOut:
    if user.kakao:
        db.delete(user.kakao)
        db.commit()
    return KakaoDisconnectOut(connected=False)
