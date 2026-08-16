"""Drop stale candidates. Popularity never overrides age."""

from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.services.curate_limits import MAX_CONTENT_AGE_HOURS

SEOUL = ZoneInfo("Asia/Seoul")
MAX_AGE = timedelta(hours=MAX_CONTENT_AGE_HOURS)

_YMD = re.compile(r"(?:^|[^\d])((?:20\d{2})[-./](\d{1,2})[-./](\d{1,2}))(?:[^\d]|$)")
_MD = re.compile(r"(?:^|[^\d])((\d{1,2})/(\d{1,2}))(?:[^\d]|$)")
_KR_MD = re.compile(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_URL_YMD = re.compile(r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)")


def is_fresh(published_at: datetime | None, *, now: datetime | None = None) -> bool:
    """Undated items stay (feeds sometimes omit pubDate). Dated items must be within MAX_AGE."""
    if published_at is None:
        return True
    moment = now or datetime.now(timezone.utc)
    when = _as_utc(published_at)
    current = _as_utc(moment)
    if when > current + timedelta(hours=12):
        return False
    if when > current:
        return True
    return current - when <= MAX_AGE


def published_at_from_feed_entry(entry: object) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        converted = _from_struct(parsed)
        if converted is not None:
            return converted
    getter = getattr(entry, "get", None)
    if callable(getter):
        for key in ("published", "updated", "pubDate"):
            converted = parse_datetime(str(getter(key) or ""))
            if converted is not None:
                return converted
    return None


def published_at_from_text(*parts: str, now: datetime | None = None) -> datetime | None:
    blob = " ".join(p for p in parts if p)
    if not blob:
        return None
    moment = now or datetime.now(SEOUL)
    url_hit = _URL_YMD.search(blob)
    if url_hit:
        return _seoul_day(int(url_hit.group(1)), int(url_hit.group(2)), int(url_hit.group(3)))
    ymd = _YMD.search(blob)
    if ymd:
        return _seoul_day(int(ymd.group(1)[:4]), int(ymd.group(2)), int(ymd.group(3)))
    kr = _KR_MD.search(blob)
    if kr:
        return _md_this_or_last_year(int(kr.group(1)), int(kr.group(2)), moment)
    md = _MD.search(blob)
    if md:
        return _md_this_or_last_year(int(md.group(2)), int(md.group(3)), moment)
    return None


def parse_datetime(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    iso = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso)
        return _as_utc(parsed)
    except ValueError:
        return published_at_from_text(text)


def resolve_published_at(
    *,
    feed_entry: object | None = None,
    iso: str = "",
    title: str = "",
    url: str = "",
    now: datetime | None = None,
) -> datetime | None:
    if feed_entry is not None:
        from_feed = published_at_from_feed_entry(feed_entry)
        if from_feed is not None:
            return from_feed
    from_iso = parse_datetime(iso)
    if from_iso is not None:
        return from_iso
    return published_at_from_text(title, url, now=now)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SEOUL).astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def _from_struct(parsed: object) -> datetime | None:
    if parsed is None:
        return None
    try:
        year, month, day, hour, minute, second = (int(parsed[i]) for i in range(6))
    except (TypeError, ValueError, IndexError):
        return None
    if not year:
        return None
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def _seoul_day(year: int, month: int, day: int) -> datetime | None:
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    last = calendar.monthrange(year, month)[1]
    if day > last:
        return None
    return datetime(year, month, day, tzinfo=SEOUL)


def _md_this_or_last_year(month: int, day: int, now: datetime) -> datetime | None:
    local = now.astimezone(SEOUL) if now.tzinfo else now.replace(tzinfo=SEOUL)
    candidate = _seoul_day(local.year, month, day)
    if candidate is None:
        return None
    if candidate.date() > local.date() + timedelta(days=1):
        return _seoul_day(local.year - 1, month, day)
    return candidate
