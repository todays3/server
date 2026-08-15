"""YouTube Data API v3 — same source youtube-mcp uses (uploads + search).

RSS is the fallback when YOUTUBE_API_KEY is empty or the API call fails.
"""

from __future__ import annotations

import httpx

from app.config import get_settings

API_BASE = "https://www.googleapis.com/youtube/v3"
MIN_YOUTUBE_VIEWS = 10_000
STATS_BATCH = 25

# site_id → (label, channel_id)
SITE_CHANNELS: dict[str, list[tuple[str, str]]] = {
    "sampro": [("삼프로TV", "UChlgI3UHCOnwUGzWzbJEuYw")],
    "youtube-life": [
        ("지식인사이드", "UCGX5sP4ehPfCPU1yGT1JT3w"),
        ("슈카월드", "UCsJ6RuBiTVWRX1041hfhWYA"),
    ],
}

TOPIC_CHANNELS: list[tuple[str, str, str]] = [
    ("삼프로TV", "UChlgI3UHCOnwUGzWzbJEuYw", "주식"),
    ("슈카월드", "UCsJ6RuBiTVWRX1041hfhWYA", "경제"),
    ("지식인사이드", "UCGX5sP4ehPfCPU1yGT1JT3w", "라이프"),
]


def youtube_configured() -> bool:
    return bool(get_settings().youtube_api_key)


def uploads_playlist_id(channel_id: str) -> str:
    if channel_id.startswith("UC") and len(channel_id) >= 4:
        return "UU" + channel_id[2:]
    return channel_id


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def meets_view_floor(views: int | None) -> bool:
    return views is not None and views >= MIN_YOUTUBE_VIEWS


def views_from_feed_entry(entry: object) -> int | None:
    """YouTube Atom/RSS media:statistics views, if present."""
    for name in ("media_statistics", "yt_statistics"):
        stats = getattr(entry, name, None)
        parsed = _views_from_mapping(stats)
        if parsed is not None:
            return parsed
    getter = getattr(entry, "get", None)
    if callable(getter):
        parsed = _views_from_mapping(getter("media_statistics") or getter("yt_statistics"))
        if parsed is not None:
            return parsed
    return None


def _views_from_mapping(stats: object) -> int | None:
    if not isinstance(stats, dict):
        return None
    return _as_int(stats.get("views") or stats.get("viewCount"))


def _as_int(raw: object) -> int | None:
    if raw is None:
        return None
    try:
        return int(str(raw).replace(",", "").strip())
    except ValueError:
        return None


def _get(path: str, params: dict[str, str | int]) -> dict:
    settings = get_settings()
    query = {"key": settings.youtube_api_key, **params}
    with httpx.Client(timeout=12.0, follow_redirects=True) as client:
        resp = client.get(f"{API_BASE}/{path}", params=query)
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            return {}
        return payload


def video_view_counts(video_ids: list[str]) -> dict[str, int]:
    ids = [vid for vid in video_ids if vid][:50]
    if not ids or not youtube_configured():
        return {}
    try:
        payload = _get("videos", {"part": "statistics", "id": ",".join(ids)})
    except Exception:  # noqa: BLE001
        return {}
    counts: dict[str, int] = {}
    for entry in payload.get("items") or []:
        vid = str(entry.get("id") or "")
        stats = entry.get("statistics") or {}
        parsed = _as_int(stats.get("viewCount"))
        if vid and parsed is not None:
            counts[vid] = parsed
    return counts


def _item_from_playlist(entry: dict, label: str):
    from app.services.sources import SourceItem

    snippet = entry.get("snippet") or {}
    resource = snippet.get("resourceId") or {}
    video_id = str(resource.get("videoId") or "")
    title = str(snippet.get("title") or "").strip()
    if not video_id or not title or title in {".", "Private video", "Deleted video"}:
        return None
    desc = str(snippet.get("description") or "").replace("\n", " ").strip()[:220]
    return SourceItem(
        kind="유튜브",
        title=title[:120],
        url=watch_url(video_id),
        summary=desc or f"{label} 최신 영상",
        source=label,
    )


def _item_from_search(entry: dict, label: str):
    from app.services.sources import SourceItem

    snippet = entry.get("snippet") or {}
    video_id = str((entry.get("id") or {}).get("videoId") or "")
    title = str(snippet.get("title") or "").strip()
    if not video_id or not title:
        return None
    desc = str(snippet.get("description") or "").replace("\n", " ").strip()[:220]
    channel = str(snippet.get("channelTitle") or label)
    return SourceItem(
        kind="유튜브",
        title=title[:120],
        url=watch_url(video_id),
        summary=desc or f"{channel} 영상",
        source=channel,
    )


def fetch_channel_videos(channel_id: str, label: str, *, limit: int = 5) -> list:
    if not youtube_configured():
        return []
    try:
        payload = _get(
            "playlistItems",
            {
                "part": "snippet",
                "playlistId": uploads_playlist_id(channel_id),
                "maxResults": max(limit, min(STATS_BATCH, 50)),
            },
        )
    except Exception:  # noqa: BLE001 — fall back to RSS
        return []
    mapped = []
    ids: list[str] = []
    for entry in payload.get("items") or []:
        row = _item_from_playlist(entry, label)
        if row is None:
            continue
        vid = row.url.rsplit("v=", 1)[-1]
        mapped.append((vid, row))
        ids.append(vid)
    counts = video_view_counts(ids)
    items = []
    for vid, row in mapped:
        if not meets_view_floor(counts.get(vid)):
            continue
        items.append(row)
        if len(items) >= limit:
            break
    return items


def search_videos(query: str, *, limit: int = 5) -> list:
    q = (query or "").strip()
    if not q or not youtube_configured():
        return []
    try:
        payload = _get(
            "search",
            {
                "part": "snippet",
                "q": q,
                "type": "video",
                "order": "date",
                "maxResults": max(limit, min(STATS_BATCH, 50)),
                "relevanceLanguage": "ko",
                "regionCode": "KR",
            },
        )
    except Exception:  # noqa: BLE001
        return []
    mapped = []
    ids: list[str] = []
    for entry in payload.get("items") or []:
        row = _item_from_search(entry, q)
        if row is None:
            continue
        vid = str((entry.get("id") or {}).get("videoId") or "")
        mapped.append((vid, row))
        ids.append(vid)
    counts = video_view_counts(ids)
    items = []
    for vid, row in mapped:
        if not meets_view_floor(counts.get(vid)):
            continue
        items.append(row)
        if len(items) >= limit:
            break
    return items


def channels_for(topics: list[str], preferred_sites: list[str] | None) -> list[tuple[str, str]]:
    sites = preferred_sites or []
    wanted: list[tuple[str, str]] = []
    seen: set[str] = set()
    for sid in sites:
        for label, cid in SITE_CHANNELS.get(sid, []):
            if cid in seen:
                continue
            seen.add(cid)
            wanted.append((label, cid))
    blob = " ".join(topics)
    semi = "반도체" in blob
    broad = any(token in blob for token in ("주식", "경제", "라이프", "연애"))
    for label, cid, hint in TOPIC_CHANNELS:
        if cid in seen:
            continue
        if not topics or hint.lower() in blob.lower() or (broad and not semi):
            seen.add(cid)
            wanted.append((label, cid))
    if not wanted and not semi:
        label, cid, _ = TOPIC_CHANNELS[0]
        wanted.append((label, cid))
    return wanted


def collect_youtube_items(
    topics: list[str],
    preferred_sites: list[str] | None = None,
    *,
    limit_per: int = 4,
) -> list:
    """API first (youtube-mcp / Data API v3), RSS leftover handled by caller."""
    collected = []
    seen: set[str] = set()
    for label, cid in channels_for(topics, preferred_sites):
        for item in fetch_channel_videos(cid, label, limit=limit_per):
            if item.url in seen:
                continue
            seen.add(item.url)
            collected.append(item)
    sites = preferred_sites or []
    blob = " ".join(topics)
    if "youtube-life" in sites or "반도체" in blob:
        q = (topics[0].split("/")[-1] if topics else "라이프").strip() or "라이프"
        for item in search_videos(q, limit=limit_per):
            if item.url in seen:
                continue
            seen.add(item.url)
            collected.append(item)
    return collected


def probe_youtube_site(site_id: str) -> list:
    from app.services.source_probe import FeedProbe

    if not youtube_configured():
        return []
    probes = []
    for label, cid in SITE_CHANNELS.get(site_id, []):
        items = fetch_channel_videos(cid, label, limit=5)
        probes.append(
            FeedProbe(
                channel="youtube",
                kind="유튜브",
                name=label,
                url=f"https://www.youtube.com/channel/{cid}",
                ok=bool(items),
                status_code=200 if items else None,
                item_count=len(items),
                sample_titles=[item.title for item in items[:3]],
                error="" if items else "YouTube Data API에서 영상을 받지 못했습니다",
                bot_risk="clear" if items else "caution",
                bot_signal="YouTube Data API v3" if items else "API 응답이 비었습니다",
            )
        )
    if site_id == "youtube-life":
        extra = search_videos("라이프", limit=4)
        probes.append(
            FeedProbe(
                channel="youtube",
                kind="유튜브",
                name="YouTube 검색 · 라이프",
                url="https://www.googleapis.com/youtube/v3/search",
                ok=bool(extra),
                status_code=200 if extra else None,
                item_count=len(extra),
                sample_titles=[item.title for item in extra[:3]],
                error="" if extra else "YouTube 검색 결과가 없습니다",
                bot_risk="clear" if extra else "caution",
                bot_signal="youtube-mcp searchVideos" if extra else "",
            )
        )
    return probes
