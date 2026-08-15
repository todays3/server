from datetime import date, datetime, timedelta, timezone
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.auth import get_admin_user
from app.config import get_settings
from app.db import get_db
from app.models import LlmUsage, Preference, User
from app.schemas import (
    AdminDailyPoint,
    AdminOverview,
    AdminPrefDetail,
    AdminUsageEvent,
    AdminUsageSummary,
    AdminUserDetail,
    AdminUserOut,
    DigestCandidateOut,
    DigestItemOut,
    SourceFeedProbeOut,
    SourceProbeListOut,
    SourceSiteProbeOut,
    AdminDigestPreviewOut,
    AdminDigestPreviewRequest,
)
from app.services.digest import build_digest_preview
from app.services.send_times import parse_send_times_raw
from app.services.source_probe import list_probe_snapshot, probe_all_sites, probe_site, summarize
from app.services.sources import collector_site_ids

router = APIRouter(prefix="/admin", tags=["admin"])
SEOUL = ZoneInfo("Asia/Seoul")


def _usage_summary(rows: list[LlmUsage]) -> AdminUsageSummary:
    return AdminUsageSummary(
        calls=len(rows),
        success_calls=sum(1 for r in rows if r.success),
        prompt_tokens=sum(r.prompt_tokens for r in rows),
        completion_tokens=sum(r.completion_tokens for r in rows),
        total_tokens=sum(r.total_tokens for r in rows),
    )


def _today_start_utc() -> datetime:
    now = datetime.now(SEOUL)
    local_midnight = datetime(now.year, now.month, now.day, tzinfo=SEOUL)
    return local_midnight.astimezone(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _seoul_day(dt: datetime) -> date:
    return _aware(dt).astimezone(SEOUL).date()


def _daily_series(
    users: list[User],
    usage_rows: list[LlmUsage],
    *,
    days: int = 14,
) -> list[AdminDailyPoint]:
    today = datetime.now(SEOUL).date()
    start = today - timedelta(days=days - 1)
    day_list = [start + timedelta(days=i) for i in range(days)]

    tokens_by_day: dict[date, int] = {d: 0 for d in day_list}
    prompt_by_day: dict[date, int] = {d: 0 for d in day_list}
    completion_by_day: dict[date, int] = {d: 0 for d in day_list}
    calls_by_day: dict[date, int] = {d: 0 for d in day_list}
    success_by_day: dict[date, int] = {d: 0 for d in day_list}
    cum_tokens_before = 0
    cum_calls_before = 0

    for row in usage_rows:
        if row.created_at is None:
            continue
        day = _seoul_day(row.created_at)
        if day < start:
            cum_tokens_before += row.total_tokens
            cum_calls_before += 1
            continue
        if day not in tokens_by_day:
            continue
        tokens_by_day[day] += row.total_tokens
        prompt_by_day[day] += row.prompt_tokens
        completion_by_day[day] += row.completion_tokens
        calls_by_day[day] += 1
        if row.success:
            success_by_day[day] += 1

    # Cumulative users as of end-of-day (Seoul).
    created = sorted(
        (_seoul_day(u.created_at) for u in users if u.created_at is not None),
    )
    approved = sorted(
        (
            _seoul_day(u.approved_at)
            for u in users
            if u.approved_at is not None and u.status == "approved"
        ),
    )
    # Admins / already-approved without approved_at count from created_at.
    approved_fallback = sorted(
        (
            _seoul_day(u.created_at)
            for u in users
            if u.status == "approved" and u.approved_at is None and u.created_at is not None
        ),
    )
    pending = sorted(
        (
            _seoul_day(u.created_at)
            for u in users
            if u.status == "pending" and u.created_at is not None
        ),
    )

    series: list[AdminDailyPoint] = []
    cum_tokens = cum_tokens_before
    cum_calls = cum_calls_before
    ci = 0
    ai = 0
    afi = 0
    pi = 0
    n_created = len(created)
    n_approved = len(approved)
    n_fallback = len(approved_fallback)
    n_pending = len(pending)

    for day in day_list:
        while ci < n_created and created[ci] <= day:
            ci += 1
        while ai < n_approved and approved[ai] <= day:
            ai += 1
        while afi < n_fallback and approved_fallback[afi] <= day:
            afi += 1
        while pi < n_pending and pending[pi] <= day:
            pi += 1
        day_tokens = tokens_by_day[day]
        day_calls = calls_by_day[day]
        cum_tokens += day_tokens
        cum_calls += day_calls
        series.append(
            AdminDailyPoint(
                date=day.isoformat(),
                users_total=ci,
                users_approved=ai + afi,
                users_pending=pi,
                tokens=day_tokens,
                tokens_cumulative=cum_tokens,
                prompt_tokens=prompt_by_day[day],
                completion_tokens=completion_by_day[day],
                calls=day_calls,
                success_calls=success_by_day[day],
                calls_cumulative=cum_calls,
            )
        )
    return series


def _pref_detail(pref: Preference | None) -> AdminPrefDetail | None:
    if pref is None:
        return None
    slots = parse_send_times_raw(pref.send_times, hour=pref.send_hour, minute=pref.send_minute)
    return AdminPrefDetail(
        topics=[t.strip() for t in pref.topics.split(",") if t.strip()],
        sources=[s.strip() for s in (pref.sources or "").split(",") if s.strip()],
        notes=pref.notes or "",
        send_hour=slots[0].hour,
        send_minute=slots[0].minute,
        send_times=slots,
        timezone=pref.timezone,
        enabled=pref.enabled,
        insight_questions=bool(pref.insight_questions),
    )


@router.get("/overview", response_model=AdminOverview)
def admin_overview(
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminOverview:
    _ = admin
    settings = get_settings()
    users_total = db.scalar(select(func.count()).select_from(User)) or 0
    users_pending = (
        db.scalar(select(func.count()).select_from(User).where(User.status == "pending")) or 0
    )
    users_approved = (
        db.scalar(select(func.count()).select_from(User).where(User.status == "approved")) or 0
    )

    all_users = list(db.scalars(select(User)).all())
    all_usage = list(db.scalars(select(LlmUsage)).all())
    today_cut = _today_start_utc()
    today_usage = [r for r in all_usage if r.created_at and _aware(r.created_at) >= today_cut]

    recent_rows = list(
        db.scalars(select(LlmUsage).order_by(LlmUsage.created_at.desc()).limit(30)).all()
    )
    user_map = {
        u.id: u
        for u in db.scalars(select(User).where(User.id.in_([r.user_id for r in recent_rows] or [0]))).all()
    }
    recent = [
        AdminUsageEvent(
            id=r.id,
            user_id=r.user_id,
            email=user_map[r.user_id].email if r.user_id in user_map else "",
            purpose=r.purpose,
            provider=r.provider,
            model=r.model,
            prompt_tokens=r.prompt_tokens,
            completion_tokens=r.completion_tokens,
            total_tokens=r.total_tokens,
            success=r.success,
            error_message=r.error_message,
            created_at=r.created_at,
        )
        for r in recent_rows
    ]

    return AdminOverview(
        users_total=users_total,
        users_pending=users_pending,
        users_approved=users_approved,
        llm_configured=settings.llm_configured,
        llm_provider=settings.llm_provider,
        llm_model=settings.resolved_llm_model,
        usage_all=_usage_summary(all_usage),
        usage_today=_usage_summary(today_usage),
        series=_daily_series(all_users, all_usage, days=14),
        recent=recent,
    )


@router.get("/users/detail", response_model=list[AdminUserDetail])
def list_user_details(
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> list[AdminUserDetail]:
    _ = admin
    stmt = (
        select(User)
        .options(joinedload(User.preference), joinedload(User.kakao))
        .order_by(User.created_at.desc())
    )
    if status_filter:
        stmt = stmt.where(User.status == status_filter)
    users = list(db.scalars(stmt).unique().all())

    all_usage = list(db.scalars(select(LlmUsage)).all())
    today_cut = _today_start_utc()

    by_user: dict[int, list[LlmUsage]] = {}
    for row in all_usage:
        by_user.setdefault(row.user_id, []).append(row)

    out: list[AdminUserDetail] = []
    for u in users:
        rows = by_user.get(u.id, [])
        today_rows = [r for r in rows if r.created_at and _aware(r.created_at) >= today_cut]
        out.append(
            AdminUserDetail(
                id=u.id,
                email=u.email,
                display_name=u.display_name,
                status=u.status,
                is_admin=u.is_admin,
                kakao_connected=u.kakao is not None and bool(u.kakao.access_token),
                created_at=u.created_at,
                approved_at=u.approved_at,
                preference=_pref_detail(u.preference),
                usage_all=_usage_summary(rows),
                usage_today=_usage_summary(today_rows),
            )
        )
    return out


@router.get("/users", response_model=list[AdminUserOut])
def list_users(
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> list[User]:
    _ = admin
    stmt = select(User).order_by(User.created_at.desc())
    if status_filter:
        stmt = stmt.where(User.status == status_filter)
    return list(db.scalars(stmt).all())


@router.post("/users/{user_id}/approve", response_model=AdminUserOut)
def approve_user(
    user_id: int,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    _ = admin
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="관리자 계정은 변경할 수 없습니다")
    user.status = "approved"
    user.approved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/reject", response_model=AdminUserOut)
def reject_user(
    user_id: int,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    _ = admin
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="관리자 계정은 변경할 수 없습니다")
    user.status = "rejected"
    user.approved_at = None
    db.commit()
    db.refresh(user)
    return user


def _feed_out(feed) -> SourceFeedProbeOut:
    return SourceFeedProbeOut(
        channel=feed.channel,
        kind=feed.kind,
        name=feed.name,
        url=feed.url,
        ok=feed.ok,
        status_code=feed.status_code,
        item_count=feed.item_count,
        sample_titles=feed.sample_titles,
        error=feed.error,
    )


def _site_out(probe) -> SourceSiteProbeOut:
    return SourceSiteProbeOut(
        site_id=probe.site_id,
        label=probe.label,
        ok=probe.ok,
        probed_at=probe.probed_at,
        duration_ms=probe.duration_ms,
        item_count=probe.item_count,
        feeds=[_feed_out(feed) for feed in probe.feeds],
    )


def _list_out(sites) -> SourceProbeListOut:
    ok_count, fail_count, unknown_count = summarize(sites)
    return SourceProbeListOut(
        ok_count=ok_count,
        fail_count=fail_count,
        unknown_count=unknown_count,
        sites=[_site_out(site) for site in sites],
    )


@router.get("/sources/probes", response_model=SourceProbeListOut)
def get_source_probes(admin: Annotated[User, Depends(get_admin_user)]) -> SourceProbeListOut:
    _ = admin
    return _list_out(list_probe_snapshot())


@router.post("/sources/probes", response_model=SourceProbeListOut)
def run_all_source_probes(admin: Annotated[User, Depends(get_admin_user)]) -> SourceProbeListOut:
    _ = admin
    return _list_out(probe_all_sites())


@router.post("/sources/probes/{site_id}", response_model=SourceSiteProbeOut)
def run_one_source_probe(
    site_id: str,
    admin: Annotated[User, Depends(get_admin_user)],
) -> SourceSiteProbeOut:
    _ = admin
    if site_id not in collector_site_ids():
        raise HTTPException(status_code=404, detail="알 수 없는 수집 소스입니다")
    return _site_out(probe_site(site_id))


@router.post("/digests/preview", response_model=AdminDigestPreviewOut)
def preview_digest_for_user(
    payload: AdminDigestPreviewRequest,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminDigestPreviewOut:
    _ = admin
    user = db.get(User, payload.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    pref = user.preference
    if pref is None:
        raise HTTPException(status_code=400, detail="이 사용자에게 설정이 없습니다")
    preview = build_digest_preview(db, user, pref)
    db.commit()
    return AdminDigestPreviewOut(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        curator=preview.curator,
        llm_configured=preview.llm_configured,
        llm_skip_reason=preview.llm_skip_reason,
        llm_raw=preview.llm_raw,
        topics=preview.topics,
        sources=preview.sources,
        title=preview.title,
        body=preview.body,
        items=[
            DigestItemOut(
                kind=str(item.get("kind") or "아티클"),
                title=str(item.get("title") or ""),
                blurb=str(item.get("blurb") or item.get("summary") or ""),
                url=str(item.get("url") or ""),
                topic=str(item.get("topic") or item.get("hint") or ""),
                insight_q=str(item.get("insight_q") or ""),
                insight_url=str(item.get("insight_url") or ""),
            )
            for item in preview.items
        ],
        candidates=[
            DigestCandidateOut(
                kind=c.kind,
                title=c.title,
                url=c.url,
                summary=c.summary,
                source=c.source,
            )
            for c in preview.candidates
        ],
        sent_to_kakao=False,
    )
