from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.deps.rate_limit import rate_limit_note_hearts, rate_limit_notes
from app.models import StickyNote, User
from app.schemas import StickyNoteHeartIn, StickyNoteIn, StickyNoteOut, StickyNotePatch
from app.services.note_policy import suggestion_create_decision
from app.services.note_wall import can_mutate_note, serialize_notes, serialize_one, set_note_liked

router = APIRouter(prefix="/notes", tags=["notes"])


def _public_nickname(user: User) -> str:
    name = (user.display_name or "").strip()
    return name or "익명"


def _get_owned_note(db: Session, note_id: int) -> StickyNote:
    note = db.get(StickyNote, note_id)
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="쪽지를 찾을 수 없습니다")
    return note


@router.get("", response_model=list[StickyNoteOut])
def list_notes(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[StickyNoteOut]:
    rows = list(
        db.scalars(
            select(StickyNote)
            .order_by(
                case((StickyNote.kind == "update", 0), else_=1),
                StickyNote.created_at.desc(),
                StickyNote.id.desc(),
            )
            .limit(80)
        ).all()
    )
    return serialize_notes(db, user, rows)


@router.post("", response_model=StickyNoteOut, dependencies=[Depends(rate_limit_notes)])
def create_note(
    payload: StickyNoteIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StickyNoteOut:
    decision = suggestion_create_decision(db, user, payload.body)
    if not decision.allowed:
        raise HTTPException(status_code=decision.status, detail=decision.detail)
    note = StickyNote(user_id=user.id, nickname=_public_nickname(user), body=payload.body, kind="suggestion")
    db.add(note)
    db.commit()
    db.refresh(note)
    return serialize_one(db, user, note)


@router.patch("/{note_id}", response_model=StickyNoteOut)
def update_note(
    note_id: int,
    payload: StickyNotePatch,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StickyNoteOut:
    note = _get_owned_note(db, note_id)
    if not can_mutate_note(user, note):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="이 쪽지를 고칠 수 없습니다")
    max_len = 800 if note.kind == "update" else 400
    if len(payload.body) > max_len:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"본문은 {max_len}자까지입니다")
    note.body = payload.body
    db.add(note)
    db.commit()
    db.refresh(note)
    return serialize_one(db, user, note)


@router.delete("/{note_id}", status_code=204)
def delete_note(
    note_id: int,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    note = _get_owned_note(db, note_id)
    if not can_mutate_note(user, note):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="이 쪽지를 지울 수 없습니다")
    db.delete(note)
    db.commit()


@router.put("/{note_id}/heart", response_model=StickyNoteOut, dependencies=[Depends(rate_limit_note_hearts)])
def set_heart(
    note_id: int,
    payload: StickyNoteHeartIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StickyNoteOut:
    note = _get_owned_note(db, note_id)
    return set_note_liked(db, user, note, payload.liked)
