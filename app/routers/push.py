from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import User
from app.services.fcm import (
    device_count,
    fcm_web_configured,
    firebase_messaging_sw_source,
    probe_fcm_token,
    public_web_config,
    register_push_device,
    unregister_push_device,
)

router = APIRouter(prefix="/push", tags=["push"])


class PushConfigOut(BaseModel):
    configured: bool
    api_key: str = ""
    auth_domain: str = ""
    project_id: str = ""
    messaging_sender_id: str = ""
    app_id: str = ""
    vapid_key: str = ""


class PushDeviceIn(BaseModel):
    token: str = Field(min_length=20, max_length=4096)
    platform: Literal["web", "android", "ios"] = "web"
    device_id: str = Field(default="", max_length=80)


class PushDeviceDeleteIn(BaseModel):
    token: str = Field(min_length=20, max_length=4096)


class PushDeviceOut(BaseModel):
    registered: bool
    device_count: int


@router.get("/config", response_model=PushConfigOut)
def push_config() -> PushConfigOut:
    return PushConfigOut(**public_web_config())


@router.get("/firebase-messaging-sw.js")
def firebase_messaging_sw() -> Response:
    return Response(
        firebase_messaging_sw_source(),
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "no-store", "Service-Worker-Allowed": "/api/v1/push/"},
    )


@router.post("/devices", response_model=PushDeviceOut)
def register_device(
    payload: PushDeviceIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PushDeviceOut:
    if not fcm_web_configured():
        raise HTTPException(status_code=503, detail="푸시가 설정되지 않았습니다")
    register_push_device(
        db,
        user.id,
        token=payload.token,
        platform=payload.platform,
        device_id=payload.device_id,
    )
    db.flush()
    probe = probe_fcm_token(payload.token)
    if probe == "gone":
        unregister_push_device(db, user.id, token=payload.token)
        db.commit()
        raise HTTPException(status_code=400, detail="유효하지 않은 푸시 토큰입니다")
    db.commit()
    return PushDeviceOut(registered=True, device_count=device_count(db, user.id))


@router.delete("/devices", response_model=PushDeviceOut)
def delete_device(
    payload: PushDeviceDeleteIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PushDeviceOut:
    unregister_push_device(db, user.id, token=payload.token)
    db.commit()
    return PushDeviceOut(registered=False, device_count=device_count(db, user.id))
