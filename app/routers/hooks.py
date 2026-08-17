"""Inbound webhooks from a personal Android phone (Tasker / MacroDroid)."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import get_db
from app.deps.rate_limit import rate_limit_hooks
from app.models import User
from app.schemas import NotificationHookIn, NotificationHookOut
from app.services.flash_alerts import already_seen, format_flash_message, notification_fingerprint
from app.services.kakao import send_digest_via_kakao

router = APIRouter(prefix="/hooks", tags=["hooks"])


def _provided_secret(request: Request, x_webhook_secret: str | None) -> str:
    if x_webhook_secret:
        return x_webhook_secret.strip()
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _require_webhook_secret(
    request: Request,
    x_webhook_secret: Annotated[str | None, Header()] = None,
) -> None:
    settings = get_settings()
    if not settings.flash_webhook_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="속보 웹훅이 꺼져 있습니다. FLASH_WEBHOOK_SECRET을 설정하세요.",
        )
    provided = _provided_secret(request, x_webhook_secret)
    expected = settings.flash_webhook_secret
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="웹훅 시크릿이 올바르지 않습니다",
        )


@router.post("/notifications", response_model=NotificationHookOut)
async def ingest_notification(
    payload: NotificationHookIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[None, Depends(rate_limit_hooks)],
    __: Annotated[None, Depends(_require_webhook_secret)],
) -> NotificationHookOut:
    """Receive a notification payload from the phone. Does not crawl the publisher."""
    settings = get_settings()
    app_label = payload.app.strip() or payload.source.strip()
    fingerprint = notification_fingerprint(title=payload.title, text=payload.text)
    if already_seen(fingerprint):
        return NotificationHookOut(accepted=True, duplicate=True, sent=False, skipped="duplicate")

    title, body = format_flash_message(app=app_label, title=payload.title, text=payload.text)
    user = db.scalar(
        select(User).options(joinedload(User.kakao)).where(User.email == settings.flash_alert_target_email)
    )
    if user is None:
        return NotificationHookOut(accepted=True, duplicate=False, sent=False, skipped="target_missing")

    ok, err, _chunks = await send_digest_via_kakao(user, title, body, db=db)
    if not ok:
        skipped = "kakao_not_connected" if "not connected" in (err or "").lower() else "kakao_failed"
        return NotificationHookOut(accepted=True, duplicate=False, sent=False, skipped=skipped)
    return NotificationHookOut(accepted=True, duplicate=False, sent=True, skipped="")
