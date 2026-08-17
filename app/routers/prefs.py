from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.models import Preference, User
from app.schemas import PreferenceOut, PreferenceUpdate, RoleSettingsOut, SendTimeSlot
from app.services.roles import encode_role_settings, encode_roles, parse_role_settings, parse_roles

MAX_ASSISTANTS_PER_USER = 2
from app.services.send_times import encode_send_times, normalize_slots, parse_send_times_raw

router = APIRouter(prefix="/prefs", tags=["prefs"])


def _pref_out(pref: Preference) -> PreferenceOut:
    topics = [t.strip() for t in pref.topics.split(",") if t.strip()]
    sources = [s.strip() for s in (pref.sources or "").split(",") if s.strip()]
    slots = parse_send_times_raw(pref.send_times, hour=pref.send_hour, minute=pref.send_minute)
    first = slots[0]
    return PreferenceOut(
        topics=topics,
        roles=parse_roles(pref.roles or ""),
        role_settings=RoleSettingsOut.model_validate(parse_role_settings(pref.role_settings or "{}")),
        tone=pref.tone,
        send_hour=first.hour,
        send_minute=first.minute,
        send_times=slots,
        timezone=pref.timezone,
        enabled=pref.enabled,
        notes=pref.notes,
        sources=sources,
        insight_questions=bool(pref.insight_questions),
    )


def _apply_send_times(pref: Preference, slots: list[SendTimeSlot]) -> None:
    normalized = normalize_slots(slots)
    pref.send_times = encode_send_times(normalized)
    pref.send_hour = normalized[0].hour
    pref.send_minute = normalized[0].minute


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
    if "roles" in data and data["roles"] is not None:
        roles = data.pop("roles")
        if not user.is_admin and len(roles) > MAX_ASSISTANTS_PER_USER:
            raise HTTPException(
                status_code=400,
                detail=f"어시스턴트는 최대 {MAX_ASSISTANTS_PER_USER}명까지 선택할 수 있습니다",
            )
        pref.roles = encode_roles(roles)
    role_settings = data.pop("role_settings", None)
    if role_settings is not None:
        pref.role_settings = encode_role_settings(dict(role_settings))
    if "sources" in data and data["sources"] is not None:
        sources = [s.strip() for s in data.pop("sources") if s and s.strip()]
        pref.sources = ",".join(sources)

    send_times = data.pop("send_times", None)
    send_hour = data.pop("send_hour", None)
    send_minute = data.pop("send_minute", None)
    data.pop("timezone", None)

    if send_times is not None:
        slots = [SendTimeSlot.model_validate(s) for s in send_times]
        _apply_send_times(pref, slots)
    elif send_hour is not None or send_minute is not None:
        current = parse_send_times_raw(pref.send_times, hour=pref.send_hour, minute=pref.send_minute)
        first = current[0]
        h = send_hour if send_hour is not None else first.hour
        m = send_minute if send_minute is not None else first.minute
        rest = current[1:]
        _apply_send_times(pref, [SendTimeSlot(hour=h, minute=m), *rest])

    for key, value in data.items():
        setattr(pref, key, value)
    pref.timezone = "Asia/Seoul"
    db.commit()
    db.refresh(pref)
    return _pref_out(pref)
