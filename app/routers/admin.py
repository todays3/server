from datetime import date, datetime, timedelta, timezone
from typing import Annotated
import json
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.auth import get_admin_user
from app.config import get_settings
from app.db import get_db
from app.models import CrawlRun, LlmUsage, Preference, StickyNote, User
from app.schemas import (
    AdminDailyPoint,
    AdminDigestPreviewOut,
    AdminDigestPreviewRequest,
    AdminKakaoTestSendOut,
    AdminKakaoTestSendRequest,
    AdminOverview,
    AdminPrefDetail,
    AdminStatusUpdate,
    AdminUpdateIn,
    AdminUpdateOut,
    AdminUsageEvent,
    AdminUsageSummary,
    AdminUserDetail,
    AdminUserOut,
    CrawlRunListOut,
    CrawlRunOut,
    DigestCandidateOut,
    DigestItemOut,
    LatencyLayerOut,
    LatencyListOut,
    LatencyRunOut,
    SourceFeedProbeOut,
    SourceProbeListOut,
    SourceSiteProbeOut,
)
from app.services.crawl_log import persist_crawl_run
from app.services.digest import build_digest_preview
from app.services.run_resources import peak_sampler
from app.services.kakao import send_digest_via_kakao
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
from app.services.pipeline_timing import (
    LAYER_KEYS,
    percentile,
    prep_ms,
    suggested_lead_minutes_from_db,
    suggested_lead_seconds_from_db,
    timings_from,
    total_ms,
)
from app.services.send_times import parse_send_times_raw
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
    with peak_sampler() as peak:
        preview = build_digest_preview(db, user, pref)
    persist_crawl_run(
        db,
        user,
        preview.candidates,
        trigger="admin_preview",
        trigger_ms=preview.trigger_ms,
        crawl_ms=preview.crawl_ms,
        aggregation_ms=preview.aggregation_ms,
        llm_ms=preview.llm_ms,
        format_ms=preview.format_ms,
        curator=preview.curator,
        llm_skip_reason=preview.llm_skip_reason,
        cpu_peak_percent=peak.cpu_peak_percent,
        rss_peak_bytes=peak.rss_peak_bytes,
        rss_delta_bytes=peak.rss_delta_bytes,
    )
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
                why=str(item.get("why") or ""),
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
        sent_to_kakao=False,
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
            kakao_ok, kakao_error = await send_digest_via_kakao(user, payload.title, payload.body, db=db)
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
