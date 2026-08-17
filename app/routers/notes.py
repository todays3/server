from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.deps.rate_limit import rate_limit_notes
from app.models import StickyNote, User
from app.schemas import StickyNoteIn, StickyNoteOut

router = APIRouter(prefix="/notes", tags=["notes"])


def _public_nickname(user: User) -> str:
    name = (user.display_name or "").strip()
    return name or "익명"


@router.get("", response_model=list[StickyNoteOut])
def list_notes(
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[StickyNote]:
    return list(
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


@router.post("", response_model=StickyNoteOut, dependencies=[Depends(rate_limit_notes)])
def create_note(
    payload: StickyNoteIn,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StickyNote:
    note = StickyNote(user_id=user.id, nickname=_public_nickname(user), body=payload.body, kind="suggestion")
    db.add(note)
    db.commit()
    db.refresh(note)
    return note
