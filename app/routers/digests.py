from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import Digest, User
from app.schemas import DigestOut, PreviewRequest
from app.services.digest import create_digest
from app.services.kakao import send_digest_via_kakao
from app.routers.prefs import _ensure_pref

router = APIRouter(prefix="/digests", tags=["digests"])


@router.get("", response_model=list[DigestOut])
def list_digests(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[Digest]:
    return list(
        db.scalars(
            select(Digest).where(Digest.user_id == user.id).order_by(Digest.created_at.desc()).limit(30)
        ).all()
    )


@router.post("/preview", response_model=DigestOut)
async def preview_digest(
    payload: PreviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Digest:
    pref = _ensure_pref(db, user)
    digest = create_digest(db, user, pref, status="preview")
    if payload.send:
        ok, err = await send_digest_via_kakao(user, digest.title, digest.body)
        digest.status = "sent" if ok else "failed"
        digest.error_message = err
        digest.sent_at = datetime.now(timezone.utc) if ok else None
        db.commit()
        db.refresh(digest)
    return digest
