"""Generate '오늘의 3' — three curated links from live sources (+ optional free LLM)."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Digest, Preference, User
from app.services import llm as llm_service
from app.services.sources import SourceItem, candidates_as_prompt_block, gather_candidates

SEOUL = "Asia/Seoul"

_CURATED: list[dict[str, str]] = [
    {
        "kind": "아티클",
        "title": "미국 증시·금리, 오늘만 필요한 요약",
        "blurb": "장 흐름을 15분 안에.",
        "url": "https://news.google.com/rss/search?q=US+stock+market&hl=en-US&gl=US&ceid=US:en",
        "hint": "경제",
    },
    {
        "kind": "커뮤니티",
        "title": "국내주식 토론 — 숫자부터 보기",
        "blurb": "감정보다 차트·공시.",
        "url": "https://finance.naver.com/",
        "hint": "경제",
    },
    {
        "kind": "유튜브",
        "title": "삼프로TV · 시장 브리핑",
        "blurb": "국내 시황 한 편.",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UChlgI3UHCOnwUGzWzbJEuYw",
        "hint": "주식",
    },
]


def _topic_list(pref: Preference) -> list[str]:
    return [t.strip() for t in pref.topics.split(",") if t.strip()]


def _customization(pref: Preference) -> str:
    return (pref.notes or "").strip()


def _format_body(name: str, items: list[dict[str, str]], pref: Preference, topics: list[str]) -> str:
    lines: list[str] = [
        f"{name}님을 위한 오늘의 3 · 유튜브·아티클·커뮤니티",
        "",
    ]
    for i, item in enumerate(items, start=1):
        topic = topics[(i - 1) % len(topics)] if topics else item.get("hint", "")
        lines.append(f"{i}) [{item['kind']}] {item['title']}")
        lines.append(f"   {item.get('blurb') or item.get('summary') or ''}")
        if topic:
            lines.append(f"   주제: {topic.replace('/', ' · ')}")
        lines.append(f"   {item['url']}")
        lines.append("")
    custom = _customization(pref)
    if custom:
        lines.append(f"요청 반영: {custom}")
    lines.append("— 오늘의 3")
    return "\n".join(lines).strip()


def _heuristic_pick(candidates: list[SourceItem], seed: str) -> list[dict[str, str]]:
    """Pick up to 3 items preferring kind diversity — works without LLM."""
    if not candidates:
        return []
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    start = int(digest[:8], 16) % len(candidates)
    ordered = candidates[start:] + candidates[:start]

    picked: list[SourceItem] = []
    used_kinds: set[str] = set()
    for item in ordered:
        if item.kind in used_kinds and len(picked) < 3:
            continue
        picked.append(item)
        used_kinds.add(item.kind)
        if len(picked) >= 3:
            break
    if len(picked) < 3:
        for item in ordered:
            if item not in picked:
                picked.append(item)
            if len(picked) >= 3:
                break

    return [
        {
            "kind": p.kind,
            "title": p.title,
            "blurb": p.summary or p.source,
            "url": p.url,
            "hint": p.source,
        }
        for p in picked[:3]
    ]


def _static_fallback(topics: list[str], seed: str) -> list[dict[str, str]]:
    start = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % len(_CURATED)
    ordered = _CURATED[start:] + _CURATED[:start]
    return [
        {
            "kind": x["kind"],
            "title": x["title"],
            "blurb": x["blurb"],
            "url": x["url"],
            "hint": x["hint"],
        }
        for x in ordered[:3]
    ]


def _llm_curate(
    db: Session,
    user: User,
    pref: Preference,
    candidates: list[SourceItem],
) -> tuple[str, str] | None:
    settings = get_settings()
    if not settings.llm_configured or not candidates:
        return None

    topics = ", ".join(_topic_list(pref)) or "일반"
    custom = _customization(pref) or "(없음)"
    prompt = (
        "너는 '오늘의 3' 큐레이터다. 아래 후보 목록에서만 골라 카카오톡용 브리프를 한국어로 만든다.\n"
        "반드시 후보에 있는 URL만 사용한다. URL을 지어내지 마라.\n"
        "유튜브·아티클·커뮤니티를 가능하면 섞어 **딱 3개**.\n"
        "JSON만 출력:\n"
        '{"title":"오늘의 3 · M/D (요일)","items":[{"kind":"유튜브|아티클|커뮤니티","title":"...","blurb":"...","url":"https://..."}]}\n'
        f"수신자: {user.display_name}\n"
        f"관심 주제: {topics}\n"
        f"커스터마이징: {custom}\n"
        f"후보:\n{candidates_as_prompt_block(candidates)}\n"
    )
    text, _usage = llm_service.chat_completion(
        db,
        user_id=user.id,
        purpose="digest_curate",
        messages=[
            {"role": "system", "content": "Return only valid JSON for three curated links."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
        max_tokens=900,
    )
    if not text:
        return None
    try:
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.M)
        data = json.loads(text)
        items = data.get("items") or []
        if len(items) < 3:
            return None
        allowed = {c.url for c in candidates}
        cleaned: list[dict[str, str]] = []
        for raw in items[:3]:
            url = str(raw.get("url") or "")
            if url not in allowed:
                match = next((c for c in candidates if c.url in url or url in c.url), None)
                if match is None:
                    continue
                url = match.url
            cleaned.append(
                {
                    "kind": str(raw.get("kind") or "아티클"),
                    "title": str(raw.get("title") or "")[:120],
                    "blurb": str(raw.get("blurb") or "")[:160],
                    "url": url,
                    "hint": "",
                }
            )
        if len(cleaned) < 3:
            return None
        title = str(data.get("title") or "").strip()[:120]
        if not title:
            now = datetime.now(ZoneInfo(SEOUL))
            weekday = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
            title = f"오늘의 3 · {now.month}/{now.day} ({weekday})"
        body = _format_body(user.display_name or "당신", cleaned, pref, _topic_list(pref))
        return title, body
    except Exception:
        return None


def _source_list(pref: Preference) -> list[str]:
    return [s.strip() for s in (pref.sources or "").split(",") if s.strip()]


def generate_digest_content(db: Session, user: User, pref: Preference) -> tuple[str, str]:
    topics = _topic_list(pref)
    sites = _source_list(pref)
    now = datetime.now(ZoneInfo(SEOUL))
    weekday = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
    seed = f"{user.id}:{now.date().isoformat()}:{','.join(topics)}:{','.join(sites)}"
    name = user.display_name or "당신"
    title = f"오늘의 3 · {now.month}/{now.day} ({weekday})"

    candidates = gather_candidates(topics, preferred_sites=sites)
    llm = _llm_curate(db, user, pref, candidates)
    if llm:
        return llm

    items = _heuristic_pick(candidates, seed) or _static_fallback(topics, seed)
    return title, _format_body(name, items, pref, topics)


def create_digest(
    db: Session,
    user: User,
    pref: Preference,
    *,
    status: str = "draft",
) -> Digest:
    if pref.timezone != SEOUL:
        pref.timezone = SEOUL
        db.add(pref)
    title, body = generate_digest_content(db, user, pref)
    digest = Digest(
        user_id=user.id,
        title=title,
        body=body,
        status=status,
        delivery_channel="kakao_me",
    )
    db.add(digest)
    db.commit()
    db.refresh(digest)
    return digest
