"""Sticky-note wall serialization and idempotent hearts."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import StickyNote, StickyNoteLike, User
from app.schemas import StickyNoteOut


def _mine(note: StickyNote, user: User) -> bool:
    return note.user_id == user.id


def serialize_note(
    note: StickyNote,
    user: User,
    *,
    like_count: int,
    liked: bool,
) -> StickyNoteOut:
    return StickyNoteOut(
        id=note.id,
        nickname=note.nickname,
        body=note.body,
        kind=note.kind,
        created_at=note.created_at,
        like_count=max(0, int(like_count)),
        liked=bool(liked),
        mine=_mine(note, user),
    )


def serialize_notes(db: Session, user: User, notes: Iterable[StickyNote]) -> list[StickyNoteOut]:
    rows = list(notes)
    if not rows:
        return []
    ids = [note.id for note in rows]
    counts = dict(
        db.execute(
            select(StickyNoteLike.note_id, func.count())
            .where(StickyNoteLike.note_id.in_(ids))
            .group_by(StickyNoteLike.note_id)
        ).all()
    )
    liked_ids = set(
        db.scalars(
            select(StickyNoteLike.note_id).where(
                StickyNoteLike.user_id == user.id,
                StickyNoteLike.note_id.in_(ids),
            )
        ).all()
    )
    return [
        serialize_note(
            note,
            user,
            like_count=int(counts.get(note.id, 0)),
            liked=note.id in liked_ids,
        )
        for note in rows
    ]


def serialize_one(db: Session, user: User, note: StickyNote) -> StickyNoteOut:
    like_count = db.scalar(
        select(func.count()).select_from(StickyNoteLike).where(StickyNoteLike.note_id == note.id)
    ) or 0
    liked = (
        db.scalar(
            select(StickyNoteLike.id).where(
                StickyNoteLike.note_id == note.id,
                StickyNoteLike.user_id == user.id,
            )
        )
        is not None
    )
    return serialize_note(note, user, like_count=int(like_count), liked=liked)


def can_mutate_note(user: User, note: StickyNote) -> bool:
    if user.is_admin:
        return True
    if note.kind == "update":
        return False
    return note.user_id == user.id


def set_note_liked(db: Session, user: User, note: StickyNote, liked: bool) -> StickyNoteOut:
    existing = db.scalar(
        select(StickyNoteLike).where(StickyNoteLike.note_id == note.id, StickyNoteLike.user_id == user.id)
    )
    if liked:
        if existing is None:
            db.add(StickyNoteLike(note_id=note.id, user_id=user.id))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
        else:
            db.commit()
    else:
        if existing is not None:
            db.delete(existing)
        db.commit()
    db.refresh(note)
    return serialize_one(db, user, note)
