from collections import Counter
from datetime import date, datetime, timedelta, timezone
from time import perf_counter
from typing import Annotated
import asyncio
import json
import queue
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.auth import get_admin_user
from app.config import get_settings
from app.db import get_db
from app.models import CrawlRun, Digest, LlmUsage, Preference, StickyNote, User
from app.schemas import (
    AdminDailyPoint,
    AdminDigestPreviewOut,
    AdminDigestPreviewRequest,
    AdminKakaoTestSendOut,
    AdminKakaoTestSendRequest,
    AdminLiveActivityOut,
    AdminLlmTestOut,
    AdminOverview,
    AdminPipelineFlagsOut,
    AdminPipelineFlagsUpdate,
    AdminPrefDetail,
    AdminSendScheduleStats,
    AdminSendSlotBucket,
    AdminSendSlotCountBucket,
    AdminRecentCallList,
    AdminRecentCallDetail,
    AdminDeliveryTraceStep,
    AdminPipelineStep,
    AdminStatusUpdate,
    AdminUpdateIn,
    AdminUpdateOut,
    StickyNoteOut,
    StickyNotePatch,
    AdminUsageEvent,
    AdminUsageSummary,
    AdminUserDetail,
    AdminUserOut,
    PreferenceOut,
    PreferenceUpdate,
    ProfileUpdate,
    RoleSettingsOut,
    UserOut,
    CrawlRunListOut,
    CrawlRunOut,
    DigestCandidateOut,
    DigestItemOut,
    LatencyLayerOut,
    LatencyListOut,
    LatencyRunOut,
    LlmQualityListOut,
    LlmQualityRunOut,
    QualityStatOut,
    SourceFeedProbeOut,
    SourceProbeListOut,
    SourceSiteProbeOut,
)
from app.routers.prefs import MAX_ASSISTANTS_PER_USER, _pref_out, apply_preference_update
from app.services.roles import parse_role_settings, parse_roles
from app.services.crawl_log import persist_crawl_run
from app.services.delivery import deliver_digest
from app.services.delivery_trace import parse_delivery_trace
from app.services.digest import build_digest_preview
from app.services.live_activity import (
    end_activity,
    snapshot as live_activity_snapshot,
    start_activity,
    update_activity,
)
from app.services.pipeline_flags import get_pipeline_flags, set_pipeline_flags
from app.services import llm as llm_service
from app.services.llm_quality import percentile_float
from app.services.run_resources import peak_sampler
from app.services.kakao import send_digest_via_kakao, split_memo_chunks
from app.services.fcm import (
    UPDATE_NOTE_KIND,
    UPDATE_NOTE_NICKNAME,
    UPDATE_PUSH_BODY,
    UPDATE_PUSH_TITLE,
    UPDATE_PUSH_URL,
    device_count,
    fcm_send_configured,
    notify_all_devices,
    notify_digest_sent,
    total_device_count,
)
from app.services.note_wall import serialize_one
from app.services.pipeline_timing import (
    LAYER_KEYS,
    percentile,
    prep_ms,
    suggested_lead_minutes_from_db,
    suggested_lead_seconds_from_db,
    timings_from,
    total_ms,
)
from app.services.send_times import MAX_SEND_TIMES, format_hm, parse_send_times_raw
from app.services.source_probe import bot_risk_counts, list_probe_snapshot, probe_all_sites, probe_site, summarize
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


def _delivery_status(usage: LlmUsage, digest: Digest | None) -> str:
    if not usage.success:
        return "failed"
    if digest is not None:
        return digest.status
    return ""


def _match_digests_for_usages(db: Session, usages: list[LlmUsage]) -> dict[int, Digest]:
    if not usages:
        return {}
    user_ids = {row.user_id for row in usages}
    times = [_aware(row.created_at) for row in usages if row.created_at]
    if not times:
        return {}
    t_min = min(times) - timedelta(seconds=30)
    t_max = max(times) + timedelta(minutes=45)
    digests = list(
        db.scalars(
            select(Digest)
            .where(
                Digest.user_id.in_(user_ids),
                Digest.created_at >= t_min,
                Digest.created_at <= t_max,
            )
            .order_by(Digest.created_at.asc())
        ).all()
    )
    used: set[int] = set()
    matched: dict[int, Digest] = {}
    for usage in sorted(usages, key=lambda row: _aware(row.created_at)):
        u_t = _aware(usage.created_at)
        for digest in digests:
            if digest.id in used or digest.user_id != usage.user_id:
                continue
            d_t = _aware(digest.created_at)
            if d_t < u_t - timedelta(seconds=30) or d_t > u_t + timedelta(minutes=45):
                continue
            used.add(digest.id)
            matched[usage.id] = digest
            break
    return matched


def _usage_events(db: Session, rows: list[LlmUsage]) -> list[AdminUsageEvent]:
    user_map = {
        u.id: u
        for u in db.scalars(select(User).where(User.id.in_([r.user_id for r in rows] or [0]))).all()
    }
    digest_map = _match_digests_for_usages(db, rows)
    events: list[AdminUsageEvent] = []
    for row in rows:
        digest = digest_map.get(row.id)
        events.append(
            AdminUsageEvent(
                id=row.id,
                user_id=row.user_id,
                email=user_map[row.user_id].email if row.user_id in user_map else "",
                purpose=row.purpose,
                provider=row.provider,
                model=row.model,
                prompt_tokens=row.prompt_tokens,
                completion_tokens=row.completion_tokens,
                total_tokens=row.total_tokens,
                success=row.success,
                delivery_status=_delivery_status(row, digest),
                attempt_count=int(digest.attempt_count or 0) if digest else 0,
                error_message=(digest.error_message if digest and digest.error_message else row.error_message),
                digest_id=digest.id if digest else None,
                created_at=row.created_at,
            )
        )
    return events


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
        roles=parse_roles(pref.roles or ""),
        role_settings=RoleSettingsOut.model_validate(parse_role_settings(pref.role_settings or "{}")),
    )


def _send_schedule_stats(prefs: list[Preference]) -> AdminSendScheduleStats:
    time_counts: Counter[tuple[int, int]] = Counter()
    slot_n_counts: Counter[int] = Counter()
    enabled = 0
    disabled = 0
    for pref in prefs:
        slots = parse_send_times_raw(pref.send_times, hour=pref.send_hour, minute=pref.send_minute)
        if pref.enabled:
            enabled += 1
        else:
            disabled += 1
        slot_n_counts[len(slots)] += 1
        for slot in slots:
            time_counts[(slot.hour, slot.minute)] += 1
    return AdminSendScheduleStats(
        users_with_prefs=len(prefs),
        enabled=enabled,
        disabled=disabled,
        times=[
            AdminSendSlotBucket(label=format_hm(hour, minute), hour=hour, minute=minute, count=count)
            for (hour, minute), count in sorted(time_counts.items())
        ],
        slot_counts=[
            AdminSendSlotCountBucket(slots=n, count=slot_n_counts[n])
            for n in range(1, MAX_SEND_TIMES + 1)
        ],
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
    recent = _usage_events(db, recent_rows)

    last = db.scalar(select(CrawlRun).order_by(CrawlRun.created_at.desc()).limit(1))
    cpu_max = db.scalar(select(func.max(CrawlRun.cpu_peak_percent))) or 0
    rss_max = db.scalar(select(func.max(CrawlRun.rss_peak_bytes))) or 0

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
        last_run_cpu_peak_percent=int(last.cpu_peak_percent or 0) if last else 0,
        last_run_rss_peak_bytes=int(last.rss_peak_bytes or 0) if last else 0,
        last_run_rss_delta_bytes=int(last.rss_delta_bytes or 0) if last else 0,
        runs_cpu_peak_max_percent=int(cpu_max),
        runs_rss_peak_max_bytes=int(rss_max),
        send_schedule=_send_schedule_stats(list(db.scalars(select(Preference)).all())),
    )


@router.get("/live-activity", response_model=AdminLiveActivityOut)
def admin_live_activity(
    admin: Annotated[User, Depends(get_admin_user)],
) -> AdminLiveActivityOut:
    _ = admin
    return AdminLiveActivityOut.model_validate(live_activity_snapshot())


@router.get("/pipeline-flags", response_model=AdminPipelineFlagsOut)
def admin_get_pipeline_flags(
    admin: Annotated[User, Depends(get_admin_user)],
) -> AdminPipelineFlagsOut:
    _ = admin
    return AdminPipelineFlagsOut.model_validate(get_pipeline_flags())


@router.patch("/pipeline-flags", response_model=AdminPipelineFlagsOut)
def admin_patch_pipeline_flags(
    payload: AdminPipelineFlagsUpdate,
    admin: Annotated[User, Depends(get_admin_user)],
) -> AdminPipelineFlagsOut:
    _ = admin
    return AdminPipelineFlagsOut.model_validate(
        set_pipeline_flags(
            agent_enrichment=payload.agent_enrichment,
            digest_critique=payload.digest_critique,
        )
    )


def _find_crawl_run(db: Session, usage: LlmUsage, digest: Digest | None) -> CrawlRun | None:
    if digest is not None:
        row = db.scalar(
            select(CrawlRun).where(CrawlRun.digest_id == digest.id).order_by(CrawlRun.id.desc()).limit(1)
        )
        if row is not None:
            return row
    u_t = _aware(usage.created_at)
    if u_t is None:
        return None
    return db.scalar(
        select(CrawlRun)
        .where(
            CrawlRun.user_id == usage.user_id,
            CrawlRun.created_at >= u_t - timedelta(minutes=2),
            CrawlRun.created_at <= u_t + timedelta(minutes=45),
        )
        .order_by(CrawlRun.created_at.desc())
        .limit(1)
    )


def _pipeline_steps(
    usage: LlmUsage,
    crawl_run: CrawlRun | None,
    digest: Digest | None,
    *,
    chunk_total: int,
) -> list[AdminPipelineStep]:
    steps: list[AdminPipelineStep] = []
    if crawl_run is not None:
        steps.extend(
            [
                AdminPipelineStep(
                    phase="trigger",
                    label="트리거",
                    detail=crawl_run.trigger or "",
                    ms=int(crawl_run.trigger_ms or 0),
                ),
                AdminPipelineStep(
                    phase="crawl",
                    label="크롤링",
                    ok=True,
                    ms=int(crawl_run.crawl_ms or 0),
                ),
                AdminPipelineStep(
                    phase="aggregate",
                    label="집계",
                    ok=True,
                    ms=int(crawl_run.aggregation_ms or 0),
                ),
                AdminPipelineStep(
                    phase="llm",
                    label="LLM 큐레이션",
                    ok=usage.success,
                    detail=crawl_run.llm_skip_reason or crawl_run.curator or "",
                    ms=int(crawl_run.llm_ms or 0),
                ),
                AdminPipelineStep(
                    phase="format",
                    label="본문 포맷",
                    ok=True,
                    ms=int(crawl_run.format_ms or 0),
                ),
            ]
        )
    else:
        steps.append(
            AdminPipelineStep(
                phase="llm",
                label="LLM 호출",
                ok=usage.success,
                detail=usage.error_message or usage.purpose,
            )
        )
    if digest is not None:
        steps.append(
            AdminPipelineStep(
                phase="delivery",
                label="카카오 전송",
                ok=digest.status in {"sent", "partial"},
                detail=digest.error_message or digest.status,
                ms=int(crawl_run.send_ms or 0) if crawl_run else None,
            )
        )
        if chunk_total > 1:
            steps.append(
                AdminPipelineStep(
                    phase="chunks",
                    label="메모 청크",
                    ok=digest.status in {"sent", "partial"},
                    detail=f"{int(digest.chunks_sent or 0)}/{chunk_total}",
                )
            )
    return steps


def _recent_call_detail(db: Session, usage: LlmUsage) -> AdminRecentCallDetail:
    user = db.get(User, usage.user_id)
    digest_map = _match_digests_for_usages(db, [usage])
    digest = digest_map.get(usage.id)
    crawl_run = _find_crawl_run(db, usage, digest)
    chunk_total = len(split_memo_chunks(digest.title, digest.body)) if digest is not None else 0
    trace_raw = parse_delivery_trace(getattr(digest, "delivery_trace_json", "") if digest else "")
    trace = [
        AdminDeliveryTraceStep(
            at=str(row.get("at") or ""),
            step=str(row.get("step") or ""),
            ok=bool(row.get("ok")),
            error=str(row.get("error") or ""),
            chunks_sent=int(row.get("chunks_sent") or 0),
            chunk_total=int(row.get("chunk_total") or 0),
            attempt=int(row.get("attempt") or 0),
            note=str(row.get("note") or ""),
        )
        for row in trace_raw
    ]
    llm_error = "" if usage.success else (usage.error_message or "LLM 실패")
    delivery_error = ""
    if digest is not None and digest.status not in {"sent"}:
        delivery_error = digest.error_message or digest.status
    event = AdminUsageEvent(
        id=usage.id,
        user_id=usage.user_id,
        email=user.email if user else "",
        purpose=usage.purpose,
        provider=usage.provider,
        model=usage.model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        success=usage.success,
        error_message=(digest.error_message if digest and digest.error_message else usage.error_message),
        created_at=usage.created_at,
        delivery_status=_delivery_status(usage, digest),
        attempt_count=int(digest.attempt_count or 0) if digest else 0,
        digest_id=digest.id if digest else None,
    )
    body_preview = ""
    if digest is not None and digest.body:
        body_preview = digest.body[:480] + ("…" if len(digest.body) > 480 else "")
    return AdminRecentCallDetail(
        usage=event,
        llm_error=llm_error,
        delivery_error=delivery_error,
        digest_id=digest.id if digest else None,
        digest_status=digest.status if digest else "",
        chunks_sent=int(digest.chunks_sent or 0) if digest else 0,
        chunk_total=chunk_total,
        next_retry_at=digest.next_retry_at if digest else None,
        sent_at=digest.sent_at if digest else None,
        delivery_trace=trace,
        pipeline=_pipeline_steps(usage, crawl_run, digest, chunk_total=chunk_total),
        crawl_run_id=crawl_run.id if crawl_run else None,
        digest_title=digest.title if digest else "",
        digest_body_preview=body_preview,
    )


@router.get("/recent-calls/{usage_id}", response_model=AdminRecentCallDetail)
def get_recent_call_detail(
    usage_id: int,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminRecentCallDetail:
    _ = admin
    usage = db.get(LlmUsage, usage_id)
    if usage is None:
        raise HTTPException(status_code=404, detail="호출 기록을 찾을 수 없습니다")
    return _recent_call_detail(db, usage)


@router.get("/recent-calls", response_model=AdminRecentCallList)
def list_recent_calls(
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 10,
) -> AdminRecentCallList:
    _ = admin
    total = db.scalar(select(func.count()).select_from(LlmUsage)) or 0
    offset = (page - 1) * page_size
    rows = list(
        db.scalars(
            select(LlmUsage).order_by(LlmUsage.created_at.desc()).offset(offset).limit(page_size)
        ).all()
    )
    return AdminRecentCallList(
        items=_usage_events(db, rows),
        total=int(total),
        page=page,
        page_size=page_size,
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
                occupation=u.occupation or "",
                birth_date=u.birth_date,
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


def _apply_member_status(user: User, status: str) -> None:
    if status == "approved":
        user.status = "approved"
        if user.approved_at is None:
            user.approved_at = datetime.now(timezone.utc)
        return
    if status == "rejected":
        user.status = "rejected"
        user.approved_at = None
        return
    if status == "pending":
        user.status = "pending"
        user.approved_at = None
        return
    user.status = status


def _mutable_member(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="관리자 계정은 변경할 수 없습니다")
    return user


@router.post("/users/{user_id}/approve", response_model=AdminUserOut)
def approve_user(
    user_id: int,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    _ = admin
    user = _mutable_member(db, user_id)
    _apply_member_status(user, "approved")
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
    user = _mutable_member(db, user_id)
    _apply_member_status(user, "rejected")
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}/status", response_model=AdminUserOut)
def set_user_status(
    user_id: int,
    payload: AdminStatusUpdate,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    _ = admin
    user = _mutable_member(db, user_id)
    _apply_member_status(user, payload.status)
    db.commit()
    db.refresh(user)
    return user


def _ensure_member_pref(db: Session, user: User) -> Preference:
    if user.preference is None:
        pref = Preference(user_id=user.id)
        db.add(pref)
        db.flush()
        return pref
    return user.preference


def _member_user_out(user: User) -> UserOut:
    kakao = user.kakao
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        occupation=user.occupation or "",
        birth_date=user.birth_date,
        status=user.status,
        is_admin=user.is_admin,
        kakao_connected=kakao is not None and bool(kakao.access_token),
    )


@router.put("/users/{user_id}/prefs", response_model=PreferenceOut)
def admin_update_user_prefs(
    user_id: int,
    payload: PreferenceUpdate,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PreferenceOut:
    _ = admin
    user = _mutable_member(db, user_id)
    pref = _ensure_member_pref(db, user)
    apply_preference_update(pref, payload, role_limit=MAX_ASSISTANTS_PER_USER)
    db.commit()
    db.refresh(pref)
    return _pref_out(pref)


@router.delete("/users/{user_id}/prefs", status_code=204)
def admin_delete_user_prefs(
    user_id: int,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    _ = admin
    user = _mutable_member(db, user_id)
    if user.preference is None:
        raise HTTPException(status_code=404, detail="설정이 없습니다")
    db.delete(user.preference)
    db.commit()
    return Response(status_code=204)


@router.patch("/users/{user_id}/profile", response_model=UserOut)
def admin_update_user_profile(
    user_id: int,
    payload: ProfileUpdate,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> UserOut:
    _ = admin
    user = _mutable_member(db, user_id)
    user.display_name = payload.display_name
    user.occupation = (payload.occupation or "").strip()
    user.birth_date = payload.birth_date
    db.commit()
    db.refresh(user)
    return _member_user_out(user)


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
        bot_risk=feed.bot_risk,
        bot_signal=feed.bot_signal,
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
        bot_risk=probe.bot_risk,
    )


def _list_out(sites) -> SourceProbeListOut:
    ok_count, fail_count, unknown_count = summarize(sites)
    blocked_count, caution_count, clear_count = bot_risk_counts(sites)
    return SourceProbeListOut(
        ok_count=ok_count,
        fail_count=fail_count,
        unknown_count=unknown_count,
        blocked_count=blocked_count,
        caution_count=caution_count,
        clear_count=clear_count,
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


def _wants_ndjson(accept: str | None) -> bool:
    return "application/x-ndjson" in (accept or "").lower()


def _admin_preview_out(
    user: User,
    preview,
    *,
    sent_to_kakao: bool,
    delivery_status: str,
    delivery_error: str,
    digest_id: int | None,
    send_ms: int,
) -> AdminDigestPreviewOut:
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
                why=str(item.get("why") or ""),
                angle=str(item.get("angle") or ""),
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
                why=c.pick_reason,
            )
            for c in preview.candidates
        ],
        sent_to_kakao=sent_to_kakao,
        delivery_status=delivery_status,
        delivery_error=delivery_error,
        digest_id=digest_id,
        trigger_ms=preview.trigger_ms,
        crawl_ms=preview.crawl_ms,
        aggregation_ms=preview.aggregation_ms,
        llm_ms=preview.llm_ms,
        format_ms=preview.format_ms,
        send_ms=send_ms,
        kakao_connected=user.kakao is not None and bool(user.kakao.access_token),
    )


@router.post("/digests/preview", response_model=None)
async def preview_digest_for_user(
    payload: AdminDigestPreviewRequest,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
    accept: Annotated[str | None, Header()] = None,
):
    _ = admin
    user = db.scalar(select(User).options(joinedload(User.kakao)).where(User.id == payload.user_id))
    if user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    pref = user.preference
    if pref is None:
        raise HTTPException(status_code=400, detail="이 사용자에게 설정이 없습니다")

    if not _wants_ndjson(accept):
        with peak_sampler() as peak:
            preview = build_digest_preview(db, user, pref)
        digest: Digest | None = None
        sent_to_kakao = False
        delivery_status = ""
        delivery_error = ""
        send_ms = 0
        if payload.send_kakao:
            digest = Digest(
                user_id=user.id,
                title=preview.title,
                body=preview.body,
                status="preview",
                delivery_channel="kakao_me",
                items_json=json.dumps(preview.items, ensure_ascii=False),
            )
            db.add(digest)
            db.flush()
            send_started = perf_counter()
            result = await deliver_digest(db, user, digest, wait_ms=0)
            send_ms = max(0, int((perf_counter() - send_started) * 1000))
            db.refresh(digest)
            sent_to_kakao = digest.status in {"sent", "partial"}
            delivery_status = digest.status
            delivery_error = "" if digest.status == "sent" else (digest.error_message or result.error)
        persist_crawl_run(
            db,
            user,
            preview.candidates,
            trigger="admin_preview_send" if payload.send_kakao else "admin_preview",
            digest_id=digest.id if digest else None,
            trigger_ms=preview.trigger_ms,
            crawl_ms=preview.crawl_ms,
            aggregation_ms=preview.aggregation_ms,
            llm_ms=preview.llm_ms,
            format_ms=preview.format_ms,
            send_ms=send_ms,
            curator=preview.curator,
            llm_skip_reason=preview.llm_skip_reason,
            cpu_peak_percent=peak.cpu_peak_percent,
            rss_peak_bytes=peak.rss_peak_bytes,
            rss_delta_bytes=peak.rss_delta_bytes,
        )
        db.commit()
        return _admin_preview_out(
            user,
            preview,
            sent_to_kakao=sent_to_kakao,
            delivery_status=delivery_status,
            delivery_error=delivery_error,
            digest_id=digest.id if digest else None,
            send_ms=send_ms,
        )

    # NDJSON: keep Cloudflare/proxy alive with pings while crawl+LLM run (often >120s).
    send_kakao = bool(payload.send_kakao)
    progress_q: queue.Queue[dict[str, object]] = queue.Queue()
    activity_id = start_activity(
        kind="admin_preview",
        label=f"{user.display_name or user.email} · E2E",
        phase="trigger",
        detail="admin digests/preview",
        user_id=user.id,
        display_name=user.display_name or user.email,
    )

    def on_progress(step: str) -> None:
        update_activity(activity_id, phase=step)
        progress_q.put({"type": "step", "step": step})

    def work_build():
        try:
            with peak_sampler() as peak:
                built = build_digest_preview(db, user, pref, on_progress=on_progress)
            return built, peak
        except Exception as exc:
            progress_q.put(
                {
                    "type": "error",
                    "step": "crawl",
                    "message": str(exc) or "수집에 실패했습니다",
                }
            )
            raise

    async def events():
        try:
            yield json.dumps({"type": "ping"}, ensure_ascii=False) + "\n"
            yield json.dumps({"type": "step", "step": "trigger"}, ensure_ascii=False) + "\n"
            loop = asyncio.get_running_loop()
            fut = loop.run_in_executor(None, work_build)
            while not fut.done():
                try:
                    item = progress_q.get(timeout=0.25)
                    yield json.dumps(item, ensure_ascii=False) + "\n"
                except queue.Empty:
                    yield json.dumps({"type": "ping"}, ensure_ascii=False) + "\n"
            while True:
                try:
                    item = progress_q.get_nowait()
                    yield json.dumps(item, ensure_ascii=False) + "\n"
                except queue.Empty:
                    break
            try:
                preview, peak = await fut
            except Exception:
                return

            digest: Digest | None = None
            sent_to_kakao = False
            delivery_status = ""
            delivery_error = ""
            send_ms = 0
            if send_kakao:
                update_activity(activity_id, phase="send", detail="카카오 전송")
                yield json.dumps({"type": "step", "step": "send"}, ensure_ascii=False) + "\n"
                digest = Digest(
                    user_id=user.id,
                    title=preview.title,
                    body=preview.body,
                    status="preview",
                    delivery_channel="kakao_me",
                    items_json=json.dumps(preview.items, ensure_ascii=False),
                )
                db.add(digest)
                db.flush()
                send_started = perf_counter()
                try:
                    result = await deliver_digest(db, user, digest, wait_ms=0)
                except Exception as exc:
                    yield json.dumps(
                        {
                            "type": "error",
                            "step": "send",
                            "message": str(exc) or "카카오 전송에 실패했습니다",
                        },
                        ensure_ascii=False,
                    ) + "\n"
                    return
                send_ms = max(0, int((perf_counter() - send_started) * 1000))
                db.refresh(digest)
                sent_to_kakao = digest.status in {"sent", "partial"}
                delivery_status = digest.status
                delivery_error = "" if digest.status == "sent" else (digest.error_message or result.error)

            persist_crawl_run(
                db,
                user,
                preview.candidates,
                trigger="admin_preview_send" if send_kakao else "admin_preview",
                digest_id=digest.id if digest else None,
                trigger_ms=preview.trigger_ms,
                crawl_ms=preview.crawl_ms,
                aggregation_ms=preview.aggregation_ms,
                llm_ms=preview.llm_ms,
                format_ms=preview.format_ms,
                send_ms=send_ms,
                curator=preview.curator,
                llm_skip_reason=preview.llm_skip_reason,
                cpu_peak_percent=peak.cpu_peak_percent,
                rss_peak_bytes=peak.rss_peak_bytes,
                rss_delta_bytes=peak.rss_delta_bytes,
            )
            db.commit()
            out = _admin_preview_out(
                user,
                preview,
                sent_to_kakao=sent_to_kakao,
                delivery_status=delivery_status,
                delivery_error=delivery_error,
                digest_id=digest.id if digest else None,
                send_ms=send_ms,
            )
            yield json.dumps(
                {"type": "done", "preview": out.model_dump(mode="json")},
                ensure_ascii=False,
            ) + "\n"
        finally:
            end_activity(activity_id)

    return StreamingResponse(
        events(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/kakao/test-send", response_model=AdminKakaoTestSendOut)
async def kakao_test_send(
    payload: AdminKakaoTestSendRequest,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminKakaoTestSendOut:
    _ = admin
    user = db.scalar(
        select(User).options(joinedload(User.kakao)).where(User.id == payload.user_id)
    )
    if user is None:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다")
    connected = user.kakao is not None and bool(user.kakao.access_token)
    kakao_ok = False
    kakao_error = ""
    if payload.send_kakao:
        if not connected:
            kakao_error = "카카오 나에게 보내기가 연결되지 않았습니다"
        else:
            kakao_ok, kakao_error, _chunks = await send_digest_via_kakao(user, payload.title, payload.body, db=db)
            db.commit()

    push_devices = device_count(db, user.id)
    push_sent = 0
    push_error = ""
    if payload.send_push:
        if not fcm_send_configured():
            push_error = "Firebase 전송이 설정되지 않았습니다"
        elif push_devices < 1:
            push_error = "등록된 푸시 기기가 없습니다"
        else:
            push_sent = notify_digest_sent(db, user.id, payload.title, payload.body)
            db.commit()
            if push_sent < 1:
                push_error = "푸시를 보내지 못했습니다"

    errors: list[str] = []
    if payload.send_kakao and not kakao_ok:
        errors.append(kakao_error or "카카오 전송 실패")
    if payload.send_push and push_sent < 1:
        errors.append(push_error or "푸시 전송 실패")
    if not payload.send_kakao and not payload.send_push:
        errors.append("카카오 또는 푸시를 고르세요")

    ok = not errors
    return AdminKakaoTestSendOut(
        ok=ok,
        user_id=user.id,
        display_name=user.display_name,
        kakao_connected=connected,
        error_message="; ".join(errors),
        kakao_ok=kakao_ok,
        push_devices=push_devices,
        push_sent=push_sent,
        push_error=push_error,
    )


_LLM_PING_MESSAGES = [
    {"role": "system", "content": "Reply with the single word pong and nothing else."},
    {"role": "user", "content": "ping"},
]


@router.post("/llm/test", response_model=AdminLlmTestOut)
def llm_test(
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminLlmTestOut:
    settings = get_settings()
    base = AdminLlmTestOut(
        ok=False,
        configured=settings.llm_configured,
        provider=settings.llm_provider,
        local_ready=settings.llm_local_ready,
        remote_ready=settings.remote_llm_ready,
    )
    if not settings.llm_configured:
        base.error_message = "LLM이 설정되지 않았습니다"
        return base

    started = perf_counter()
    text, usage = llm_service.chat_completion(
        db,
        user_id=admin.id,
        purpose="admin_llm_ping",
        messages=_LLM_PING_MESSAGES,
        temperature=0,
        max_tokens=32,
    )
    db.commit()
    preview = (text or "").strip()[:120]
    error = ""
    if usage is not None and not usage.success:
        error = usage.error_message or "LLM 실패"
    elif not preview:
        error = "빈 응답"
    used = str(getattr(usage, "provider", "") or "")
    return AdminLlmTestOut(
        ok=bool(preview),
        configured=True,
        provider=settings.llm_provider,
        used_provider=used,
        model=str(getattr(usage, "model", "") or ""),
        ms=int((perf_counter() - started) * 1000),
        preview=preview,
        error_message=error,
        local_ready=settings.llm_local_ready,
        remote_ready=settings.remote_llm_ready,
        fallback=bool(settings.llm_local_ready and used == "groq"),
    )


def _quality_stat(values: list[float]) -> QualityStatOut:
    if not values:
        return QualityStatOut()
    return QualityStatOut(
        p50=round(percentile_float(values, 50), 3),
        p90=round(percentile_float(values, 90), 3),
        p95=round(percentile_float(values, 95), 3),
        mean=round(sum(values) / len(values), 3),
    )


@router.get("/llm-quality", response_model=LlmQualityListOut)
def list_llm_quality(
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 80,
) -> LlmQualityListOut:
    _ = admin
    rows = list(db.scalars(select(LlmUsage).order_by(LlmUsage.created_at.desc()).limit(limit)).all())
    ttft = [float(row.ttft_ms or 0) for row in rows if (row.ttft_ms or 0) > 0 or (row.total_ms or 0) > 0]
    tps = [float(row.tps or 0) for row in rows if (row.tps or 0) > 0]
    hallu = [float(row.hallucination_rate) for row in rows if row.hallucination_rate is not None]
    faith = [float(row.faithfulness) for row in rows if row.faithfulness is not None]
    rel = [float(row.answer_relevance) for row in rows if row.answer_relevance is not None]
    prec = [float(row.context_precision) for row in rows if row.context_precision is not None]
    runs = [
        LlmQualityRunOut(
            id=row.id,
            created_at=row.created_at,
            purpose=row.purpose or "",
            provider=row.provider or "",
            model=row.model or "",
            success=bool(row.success),
            ttft_ms=int(row.ttft_ms or 0),
            total_ms=int(row.total_ms or 0),
            tps=float(row.tps or 0),
            streamed=bool(row.streamed),
            faithfulness=row.faithfulness,
            hallucination_rate=row.hallucination_rate,
            answer_relevance=row.answer_relevance,
            context_precision=row.context_precision,
        )
        for row in rows
    ]
    return LlmQualityListOut(
        sample_size=len(rows),
        rag_sample_size=len(hallu),
        ttft=_quality_stat(ttft),
        tps=_quality_stat(tps),
        hallucination_rate=_quality_stat(hallu),
        faithfulness=_quality_stat(faith),
        answer_relevance=_quality_stat(rel),
        context_precision=_quality_stat(prec),
        runs=runs,
    )


@router.patch("/notes/{note_id}", response_model=StickyNoteOut)
def update_note(
    note_id: int,
    payload: StickyNotePatch,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> StickyNoteOut:
    note = db.get(StickyNote, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="쪽지를 찾을 수 없습니다")
    max_len = 800 if note.kind == "update" else 400
    if len(payload.body) > max_len:
        raise HTTPException(status_code=422, detail=f"본문은 {max_len}자까지입니다")
    note.body = payload.body
    db.add(note)
    db.commit()
    db.refresh(note)
    return serialize_one(db, admin, note)


@router.delete("/notes/{note_id}", status_code=204)
def delete_note(
    note_id: int,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    note = db.get(StickyNote, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="쪽지를 찾을 수 없습니다")
    db.delete(note)
    db.commit()


@router.post("/updates", response_model=AdminUpdateOut)
def publish_update(
    payload: AdminUpdateIn,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUpdateOut:
    note = StickyNote(
        user_id=admin.id,
        nickname=UPDATE_NOTE_NICKNAME,
        body=payload.body,
        kind=UPDATE_NOTE_KIND,
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    devices = total_device_count(db)
    sent = notify_all_devices(db, UPDATE_PUSH_TITLE, UPDATE_PUSH_BODY, url=UPDATE_PUSH_URL)
    db.commit()
    return AdminUpdateOut(
        id=note.id,
        nickname=note.nickname,
        body=note.body,
        kind=note.kind,
        created_at=note.created_at,
        push_sent=sent,
        device_count=devices,
    )


def _parse_kinds(raw: str) -> dict[str, int]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        parsed = {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in parsed.items():
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


@router.get("/crawl-runs", response_model=CrawlRunListOut)
def list_crawl_runs(
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 80,
) -> CrawlRunListOut:
    _ = admin
    total_runs = db.scalar(select(func.count()).select_from(CrawlRun)) or 0
    rows = list(
        db.scalars(
            select(CrawlRun).order_by(CrawlRun.created_at.desc()).limit(limit)
        ).all()
    )
    user_ids = [r.user_id for r in rows]
    users = {
        u.id: u
        for u in db.scalars(select(User).where(User.id.in_(user_ids or [0]))).all()
    }
    runs = []
    for row in rows:
        owner = users.get(row.user_id)
        kinds = _parse_kinds(row.kinds_json)
        runs.append(
            CrawlRunOut(
                id=row.id,
                user_id=row.user_id,
                email=owner.email if owner else "",
                display_name=owner.display_name if owner else "",
                digest_id=row.digest_id,
                trigger=row.trigger,
                slot_label=row.slot_label,
                kinds=kinds,
                total=row.total_count,
                created_at=row.created_at,
            )
        )
    return CrawlRunListOut(runs=runs, total_runs=int(total_runs))


_LAYER_LABELS = {
    "trigger": "트리거",
    "crawl": "크롤링",
    "aggregation": "집계",
    "llm": "니즈 반영 (AI)",
    "format": "답변 형식",
    "wait": "정시 대기",
    "send": "카카오 나에게 보내기",
}


@router.get("/latency", response_model=LatencyListOut)
def list_latency(
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 80,
) -> LatencyListOut:
    _ = admin
    rows = list(db.scalars(select(CrawlRun).order_by(CrawlRun.created_at.desc()).limit(limit)).all())
    user_ids = [r.user_id for r in rows]
    users = {
        u.id: u
        for u in db.scalars(select(User).where(User.id.in_(user_ids or [0]))).all()
    }
    schedule_preps = [
        prep_ms(row)
        for row in rows
        if row.trigger == "schedule" and prep_ms(row) > 0
    ]
    lead_seconds = suggested_lead_seconds_from_db(db)
    lead_minutes = suggested_lead_minutes_from_db(db)
    layers_out: list[LatencyLayerOut] = []
    for key in LAYER_KEYS:
        samples = [int(getattr(row, f"{key}_ms", 0) or 0) for row in rows]
        n = len(samples)
        mean = int(sum(samples) / n) if n else 0
        layers_out.append(
            LatencyLayerOut(
                id=key,
                label=_LAYER_LABELS[key],
                p50_ms=percentile(samples, 50),
                p90_ms=percentile(samples, 90),
                p95_ms=percentile(samples, 95),
                mean_ms=mean,
            )
        )
    runs: list[LatencyRunOut] = []
    for row in rows:
        owner = users.get(row.user_id)
        t = timings_from(row)
        layers = {key: t[f"{key}_ms"] for key in LAYER_KEYS}
        runs.append(
            LatencyRunOut(
                id=row.id,
                user_id=row.user_id,
                email=owner.email if owner else "",
                display_name=owner.display_name if owner else "",
                digest_id=row.digest_id,
                trigger=row.trigger,
                slot_label=row.slot_label,
                curator=row.curator or "",
                llm_skip_reason=row.llm_skip_reason or "",
                created_at=row.created_at,
                ready_at=row.ready_at,
                sent_at=row.sent_at,
                lead_ms=int(row.lead_ms or 0),
                prep_ms=prep_ms(row),
                e2e_ms=int(row.total_ms or 0) or total_ms(row),
                layers=layers,
                cpu_peak_percent=int(row.cpu_peak_percent or 0),
                rss_peak_bytes=int(row.rss_peak_bytes or 0),
                rss_delta_bytes=int(row.rss_delta_bytes or 0),
            )
        )
    return LatencyListOut(
        lead_minutes=lead_minutes,
        lead_seconds=lead_seconds,
        sample_size=len(schedule_preps),
        layers=layers_out,
        runs=runs,
        cpu_peak_max_percent=max((r.cpu_peak_percent for r in runs), default=0),
        rss_peak_max_bytes=max((r.rss_peak_bytes for r in runs), default=0),
    )
