"""Quantitative 'why we picked this' — only numbers we actually have."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from app.catalog.ref_sites import site_dau
from app.services.curate_limits import MAX_CONTENT_AGE_HOURS
from app.services.sources import SourceItem

MAX_WHY = 48
YOUTUBE_VIEW_FLOOR = 10_000

KIND_WHY: dict[str, str] = {
    "유튜브": f"조회 {YOUTUBE_VIEW_FLOOR // 10_000}만+",
    "커뮤니티": f"{MAX_CONTENT_AGE_HOURS}시간 이내",
    "아티클": f"{MAX_CONTENT_AGE_HOURS}시간 이내",
}

# Kept so every catalog site still has a documented collector signal.
SITE_WHY: dict[str, str] = {}

_POINTS = re.compile(
    r"(?:points?|점수|추천|공감|좋아요)\s*[:=]?\s*([\d,]{1,11})|([\d,]{1,11})\s+points?",
    re.I,
)
_COMMENTS = re.compile(
    r"(?:#\s*)?(?:comments?|댓글)\s*[:=]?\s*([\d,]{1,11})|([\d,]{1,11})\s+comments?",
    re.I,
)
_VIEWS = re.compile(
    r"(?:조회수?|view(?:s|count)?)\s*[:=]?\s*([\d,]{1,11})|([\d,]{1,11})\s+views?\b",
    re.I,
)


def clip_why(text: str) -> str:
    return (text or "").strip()[:MAX_WHY]


def format_compact_int(value: int) -> str:
    n = max(0, int(value))
    if n >= 100_000_000:
        text = f"{n / 100_000_000:.1f}".rstrip("0").rstrip(".")
        return f"{text}억"
    if n >= 10_000:
        text = f"{n / 10_000:.1f}".rstrip("0").rstrip(".")
        return f"{text}만"
    return str(n)


def parse_metric_int(raw: str | None) -> int | None:
    if not raw:
        return None
    digits = str(raw).replace(",", "").strip()
    if not digits.isdigit():
        return None
    return int(digits)


def views_from_text(*parts: str) -> int | None:
    blob = " ".join(part for part in parts if part)
    if not blob:
        return None
    hit = _VIEWS.search(blob)
    if not hit:
        return None
    return parse_metric_int(hit.group(1) or hit.group(2))


def engagement_from_text(*parts: str) -> tuple[int | None, int | None]:
    blob = " ".join(part for part in parts if part)
    if not blob:
        return None, None
    points = None
    comments = None
    hit = _POINTS.search(blob)
    if hit:
        points = parse_metric_int(hit.group(1) or hit.group(2))
    hit = _COMMENTS.search(blob)
    if hit:
        comments = parse_metric_int(hit.group(1) or hit.group(2))
    return points, comments


def age_label(published_at: datetime | None, *, now: datetime | None = None) -> str | None:
    if published_at is None:
        return None
    current = now or datetime.now(timezone.utc)
    when = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
    moment = current if current.tzinfo else current.replace(tzinfo=timezone.utc)
    seconds = (moment.astimezone(timezone.utc) - when.astimezone(timezone.utc)).total_seconds()
    if seconds < 0:
        return "1시간 내"
    hours = seconds / 3600
    if hours < 1:
        return "1시간 내"
    if hours < 24:
        return f"{int(hours)}시간 전"
    days = int(hours / 24)
    if days <= 2:
        return f"{days}일 전"
    return None


def compose_pick_reason(
    item: SourceItem,
    *,
    job_match: bool = False,
    job_hits: int | None = None,
    topic_hits: int | None = None,
    now: datetime | None = None,
) -> str:
    parts: list[str] = []
    views = getattr(item, "views", None)
    if views:
        parts.append(f"조회 {format_compact_int(views)}")
    points = getattr(item, "points", None)
    if points:
        parts.append(f"점수 {format_compact_int(points)}")
    comments = getattr(item, "comments", None)
    if comments:
        parts.append(f"댓글 {format_compact_int(comments)}")
    age = age_label(item.published_at, now=now)
    if age:
        parts.append(age)
    rank = getattr(item, "list_rank", None)
    if rank and 1 <= int(rank) <= 10:
        parts.append(f"피드 {int(rank)}위")
    hits_job = job_hits if job_hits is not None else getattr(item, "job_hits", 0)
    hits_topic = topic_hits if topic_hits is not None else getattr(item, "topic_hits", 0)
    if job_match and not hits_job:
        hits_job = max(1, int(hits_job or 0))
    if hits_topic:
        parts.append(f"주제 {int(hits_topic)}일치")
    if hits_job:
        parts.append(f"직무 {int(hits_job)}일치")
    if not parts:
        dau = site_dau(item.site_id) if item.site_id else 0
        if dau:
            parts.append(f"DAU {format_compact_int(dau)}")
        elif item.kind == "유튜브":
            parts.append(KIND_WHY["유튜브"])
        else:
            parts.append(KIND_WHY.get(item.kind) or f"{MAX_CONTENT_AGE_HOURS}시간 이내")
    packed: list[str] = []
    for part in parts:
        candidate = "·".join(packed + [part])
        if packed and len(candidate) > MAX_WHY:
            break
        packed.append(part)
    return clip_why("·".join(packed))
