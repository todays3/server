from datetime import datetime, timezone
from typing import Annotated
import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import Digest, User
from app.schemas import DigestItemOut, DigestOut, PreviewRequest
from app.services.digest import create_digest
from app.services.kakao import send_digest_via_kakao
from app.routers.prefs import _ensure_pref

router = APIRouter(prefix="/digests", tags=["digests"])


def _digest_out(digest: Digest) -> DigestOut:
    raw_items: list[DigestItemOut] = []
    try:
        parsed = json.loads(digest.items_json or "[]")
        if isinstance(parsed, list):
            for row in parsed:
                if not isinstance(row, dict):
                    continue
                raw_items.append(
                    DigestItemOut(
                        kind=str(row.get("kind") or "아티클"),
                        title=str(row.get("title") or ""),
                        blurb=str(row.get("blurb") or row.get("summary") or ""),
                        url=str(row.get("url") or ""),
                        topic=str(row.get("topic") or row.get("hint") or ""),
                        insight_q=str(row.get("insight_q") or ""),
                        insight_url=str(row.get("insight_url") or ""),
                    )
                )
    except json.JSONDecodeError:
        raw_items = []
    return DigestOut(
        id=digest.id,
        title=digest.title,
        body=digest.body,
        status=digest.status,
        delivery_channel=digest.delivery_channel,
        error_message=digest.error_message,
        created_at=digest.created_at,
        sent_at=digest.sent_at,
        items=raw_items,
    )


@router.get("", response_model=list[DigestOut])
def list_digests(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[DigestOut]:
    rows = list(
        db.scalars(
            select(Digest).where(Digest.user_id == user.id).order_by(Digest.created_at.desc()).limit(30)
        ).all()
    )
    return [_digest_out(d) for d in rows]


@router.post("/preview", response_model=DigestOut)
async def preview_digest(
    payload: PreviewRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DigestOut:
    pref = _ensure_pref(db, user)
    digest = create_digest(db, user, pref, status="preview")
    if payload.send:
        ok, err = await send_digest_via_kakao(user, digest.title, digest.body, db=db)
        digest.status = "sent" if ok else "failed"
        digest.error_message = err
        digest.sent_at = datetime.now(timezone.utc) if ok else None
        db.commit()
        db.refresh(digest)
    return _digest_out(digest)
