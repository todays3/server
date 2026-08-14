from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import Preference, User
from app.schemas import PreferenceOut, PreferenceUpdate

router = APIRouter(prefix="/prefs", tags=["prefs"])


def _pref_out(pref: Preference) -> PreferenceOut:
    topics = [t.strip() for t in pref.topics.split(",") if t.strip()]
    return PreferenceOut(
        topics=topics,
        tone=pref.tone,
        send_hour=pref.send_hour,
        send_minute=pref.send_minute,
        timezone=pref.timezone,
        enabled=pref.enabled,
        notes=pref.notes,
    )


def _ensure_pref(db: Session, user: User) -> Preference:
    if user.preference is None:
        pref = Preference(user_id=user.id)
        db.add(pref)
        db.commit()
        db.refresh(user)
    return user.preference


@router.get("", response_model=PreferenceOut)
def get_prefs(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PreferenceOut:
    return _pref_out(_ensure_pref(db, user))


@router.put("", response_model=PreferenceOut)
def update_prefs(
    payload: PreferenceUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PreferenceOut:
    pref = _ensure_pref(db, user)
    data = payload.model_dump(exclude_unset=True)
    if "topics" in data and data["topics"] is not None:
        topics = [t.strip() for t in data.pop("topics") if t and t.strip()]
        if not topics:
            raise HTTPException(status_code=400, detail="관심 주제를 하나 이상 선택하세요")
        pref.topics = ",".join(topics)
    data.pop("timezone", None)  # always Seoul
    for key, value in data.items():
        setattr(pref, key, value)
    pref.timezone = "Asia/Seoul"
    db.commit()
    db.refresh(pref)
    return _pref_out(pref)
