"""Cheap ranker before the LLM shortlist (no extra ML deps)."""

from __future__ import annotations

from app.services.curate_limits import LLM_SHORTLIST_MAX
from app.services.sources import SourceItem


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


def cheap_item_score(
    item: SourceItem,
    *,
    job_tokens: list[str],
    topic_parts: list[str],
    korean_score: int,
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
    return score


def shortlist_for_llm(
    ranked: list[SourceItem],
    *,
    job_tokens: list[str],
    topic_parts: list[str],
    korean_scores: dict[str, int],
    limit: int = LLM_SHORTLIST_MAX,
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
        )

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
        if item.kind in used_kinds:
            continue
        picked.append(item)
        used_kinds.add(item.kind)
        if len(picked) >= min(3, limit):
            break
    for item in eligible:
        if item in picked:
            continue
        picked.append(item)
        if len(picked) >= min(limit, len(eligible)):
            break
    return picked[:limit]
