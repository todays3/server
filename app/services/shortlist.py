"""Cheap ranker before the LLM shortlist (no extra ML deps)."""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.curate_limits import LLM_SHORTLIST_MAX, SHORTLIST_MAX_PER_SITE
from app.services.sources import SourceItem

# Prefer primary / reputation-checked eng outlets for the developer desk.
_DEVELOPER_VERIFIED_SITES = frozenset(
    {
        "hn",
        "geeknews",
        "github-trending",
        "infoq",
        "naver-d2",
        "geeksforgeeks",
        "stackoverflow",
        "qiita",
        "cloudflare-blog",
        "netflix-tech",
        "okky",
    }
)
_DEVELOPER_TREND = (
    "트렌드",
    "도입",
    "채택",
    "마이그레이션",
    "릴리즈",
    "출시",
    "ga ",
    " ga",
    "rfc",
    "cve",
    "취약점",
    "llm",
    "에이전트",
    "kubernetes",
    "rust",
    "wasm",
    "typescript",
    "golang",
    "open source",
    "오픈소스",
)
_DEVELOPER_PERSON = (
    "영입",
    "합류",
    "이직",
    "퇴사",
    "cto",
    "키노트",
    "발언",
    "인터뷰",
    "채용",
    "인사",
)


def topic_tokens(topics: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for topic in topics:
        for part in topic.replace("/", " ").replace(",", " ").split():
            token = part.strip()
            if len(token) < 2:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(token)
    return out


def _freshness_boost(item: SourceItem) -> int:
    published = item.published_at
    if published is None:
        return 0
    when = published if published.tzinfo else published.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - when.astimezone(timezone.utc)).total_seconds() / 3600.0
    if age_h <= 24:
        return 8
    if age_h <= 72:
        return 4
    return 1


def cheap_item_score(
    item: SourceItem,
    *,
    job_tokens: list[str],
    topic_parts: list[str],
    korean_score: int,
    role: str | None = None,
) -> int:
    title = (item.title or "").strip()
    if len(title) < 8 or not (item.url or "").strip():
        return -100
    blob = f"{title} {item.summary or ''} {item.source or ''}".lower()
    score = korean_score * 3
    score += sum(3 for token in job_tokens if token.lower() in blob)
    score += sum(2 for token in topic_parts if token.lower() in blob)
    if (item.summary or "").strip():
        score += 1
    if role == "developer":
        score += _freshness_boost(item)
        sid = (item.site_id or "").strip()
        if sid in _DEVELOPER_VERIFIED_SITES:
            score += 6
        score += sum(3 for token in _DEVELOPER_TREND if token in blob)
        score += sum(2 for token in _DEVELOPER_PERSON if token in blob)
        # Soft-penalize evergreen tutorial tone without a shipping signal.
        if any(token in blob for token in ("입문", "기초", "튜토리얼", "how to", "getting started")) and not any(
            token in blob for token in _DEVELOPER_TREND
        ):
            score -= 4
    elif role in {"investor", "stock_analyst"}:
        score += _freshness_boost(item)
        score += sum(
            3
            for token in (
                "시황",
                "코스피",
                "코스닥",
                "수급",
                "실적",
                "환율",
                "금리",
                "종목",
                "지수",
            )
            if token in blob
        )
        if any(token in blob for token in ("isscc", "iedm", "pdk", "트랜지스터 논문", "연애")):
            score -= 8
    elif role == "semiconductor":
        score += _freshness_boost(item)
        sid = (item.site_id or "").strip()
        if sid.startswith("ieee") or sid in {"isscc", "iedm", "spie", "sciencedirect"} or sid.startswith("topic-반도체"):
            score += 6
        score += sum(
            3
            for token in (
                "euv",
                "공정",
                "소자",
                "수율",
                "파운드리",
                "hbm",
                "gaa",
                "cfet",
                "웨이퍼",
            )
            if token in blob
        )
        if any(token in blob for token in ("목표가", "매수의견", "코스피", "투자의견", "연애")):
            score -= 8
    return score


def shortlist_for_llm(
    ranked: list[SourceItem],
    *,
    job_tokens: list[str],
    topic_parts: list[str],
    korean_scores: dict[str, int],
    limit: int = LLM_SHORTLIST_MAX,
    role: str | None = None,
) -> list[SourceItem]:
    """Keep a small, diverse set for the transformer; inspect the full ranked pool first."""
    if not ranked or limit <= 0:
        return []

    def score_of(item: SourceItem) -> int:
        return cheap_item_score(
            item,
            job_tokens=job_tokens,
            topic_parts=topic_parts,
            korean_score=korean_scores.get(item.url, 0),
            role=role,
        )

    def site_ok(item: SourceItem, picked: list[SourceItem]) -> bool:
        sid = (item.site_id or "").strip()
        if not sid:
            return True
        n = sum(1 for row in picked if (row.site_id or "") == sid)
        if n < SHORTLIST_MAX_PER_SITE:
            return True
        return len(picked) < min(3, limit)

    scored = sorted(ranked, key=score_of, reverse=True)
    eligible = [item for item in scored if score_of(item) >= 0]
    if len(eligible) < min(3, len(ranked)):
        for item in ranked:
            if item not in eligible:
                eligible.append(item)
            if len(eligible) >= min(3, len(ranked)):
                break

    picked: list[SourceItem] = []
    used_kinds: set[str] = set()
    for item in eligible:
        if item.kind in used_kinds or not site_ok(item, picked):
            continue
        picked.append(item)
        used_kinds.add(item.kind)
        if len(picked) >= min(3, limit):
            break
    for item in eligible:
        if item in picked or not site_ok(item, picked):
            continue
        picked.append(item)
        if len(picked) >= min(limit, len(eligible)):
            break
    if len(picked) < min(3, len(eligible)):
        for item in eligible:
            if item in picked:
                continue
            picked.append(item)
            if len(picked) >= min(3, limit):
                break
    return picked[:limit]
