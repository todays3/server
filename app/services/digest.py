"""Generate '오늘의 3' — three curated links from live sources (+ optional free LLM)."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from time import perf_counter
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Digest, Preference, User
from app.services import llm as llm_service
from app.services.crawl_log import persist_crawl_run
from app.services.pipeline_timing import elapsed_ms
from app.services.sources import SourceItem, candidates_as_prompt_block, gather_candidates

SEOUL = "Asia/Seoul"

_CURATED: list[dict[str, str]] = [
    {
        "kind": "아티클",
        "title": "미국 증시·금리, 오늘만 필요한 요약",
        "blurb": "연준 발언 이후 기술주가 흔들렸지만, 인하 기대는 한 박자 늦춰졌다는 해석. 나스닥·국채 금리만 짚은 단기 브리핑.",
        "url": "https://news.google.com/rss/search?q=US+stock+market&hl=en-US&gl=US&ceid=US:en",
        "hint": "경제",
        "insight_q": "금리가 주가와 성장주에 미치는 영향은 무엇일까?",
        "insight_url": "https://www.investing.com/economic-calendar/",
    },
    {
        "kind": "커뮤니티",
        "title": "국내주식 토론 — 숫자부터 보기",
        "blurb": "종목 감정보다 공시·수급·밸류에이션을 먼저 보자는 스레드. 과열·저평가 주장이 갈리는 지점만 추림.",
        "url": "https://finance.naver.com/",
        "hint": "경제",
        "insight_q": "공시·수급 숫자가 주가 해석에 왜 중요할까?",
        "insight_url": "https://dart.fss.or.kr/",
    },
    {
        "kind": "유튜브",
        "title": "삼프로TV · 시장 브리핑",
        "blurb": "국내 시황과 해외 이슈를 한 편에 묶어, 오늘 장에서 볼 포인트만 짧게 정리한 브리핑 영상.",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UChlgI3UHCOnwUGzWzbJEuYw",
        "hint": "주식",
        "insight_q": "오늘 시황에서 가장 먼저 확인할 지표는 무엇일까?",
        "insight_url": "https://finance.naver.com/sise/",
    },
]

_INSIGHT_HINTS: list[tuple[str, str]] = [
    ("금리", "금리가 주가와 자산 가격에 미치는 영향은 무엇일까?"),
    ("연준", "연준 발언이 시장 기대에 어떻게 반영될까?"),
    ("환율", "환율 변동이 수입·수출 기업에 미치는 영향은?"),
    ("실적", "이번 실적이 밸류에이션에 어떻게 반영될까?"),
    ("AI", "생성형 AI가 이 산업의 비용·수익에 미치는 영향은?"),
    ("면접", "이 질문에서 면접관이 실제로 보려는 포인트는?"),
    ("수면", "수면 부채가 집중력과 판단에 미치는 영향은?"),
    ("이직", "이직 타이밍을 숫자로 판단하려면 무엇을 볼까?"),
]


def _topic_list(pref: Preference) -> list[str]:
    return [t.strip() for t in pref.topics.split(",") if t.strip()]


def _customization(pref: Preference) -> str:
    return (pref.notes or "").strip()


def _wants_insights(pref: Preference) -> bool:
    return bool(getattr(pref, "insight_questions", False))


def _kind_emoji(kind: str) -> str:
    if kind == "유튜브":
        return "🎬"
    if kind == "커뮤니티":
        return "💬"
    return "📰"


def reviewed_line(count: int) -> str:
    n = max(count, 1)
    return f"{n}개의 아티클을 종합 검수했습니다"


def _format_body(
    name: str,
    items: list[dict[str, str]],
    pref: Preference,
    topics: list[str],
    *,
    reviewed_count: int | None = None,
) -> str:
    n = reviewed_count if reviewed_count is not None else len(items)
    lines: list[str] = [
        reviewed_line(n),
        "",
        f"📬 {name}님을 위한 오늘의 3",
        "",
    ]
    show_insight = _wants_insights(pref)
    for i, item in enumerate(items, start=1):
        topic = topics[(i - 1) % len(topics)] if topics else item.get("hint", "")
        kind = item.get("kind") or "아티클"
        emoji = _kind_emoji(kind)
        lines.append(f"{i}) {emoji} [{kind}] {item['title']}")
        blurb = (item.get("blurb") or item.get("summary") or "").strip()
        if blurb:
            lines.append(f"   {blurb}")
        if topic:
            lines.append(f"   주제: {topic.replace('/', ' · ')}")
        lines.append(f"   {item['url']}")
        if show_insight:
            iq = (item.get("insight_q") or "").strip()
            iu = (item.get("insight_url") or "").strip()
            if iq and iu:
                lines.append(f"   ✨ 인사이트: {iq}")
                lines.append(f"   → {iu}")
        lines.append("")
    custom = _customization(pref)
    if custom:
        lines.append(f"요청 반영: {custom}")
    lines.append("— 오늘의 3")
    return "\n".join(lines).strip()


def _insight_question(title: str, blurb: str) -> str:
    blob = f"{title} {blurb}"
    for needle, question in _INSIGHT_HINTS:
        if needle in blob:
            return question
    short = title.strip()[:36] or "이 내용"
    return f"「{short}」에서 더 궁금한 핵심은 무엇일까?"


def _insight_url_for(
    item: dict[str, str],
    leftovers: list[SourceItem],
    used_urls: set[str],
) -> str:
    while leftovers:
        cand = leftovers.pop(0)
        if cand.url not in used_urls and cand.url != item.get("url"):
            used_urls.add(cand.url)
            return cand.url
    q = _insight_question(item.get("title", ""), item.get("blurb", ""))
    return f"https://www.google.com/search?q={quote_plus(q)}"


def _attach_insights(
    items: list[dict[str, str]],
    *,
    pref: Preference,
    candidates: list[SourceItem] | None = None,
) -> list[dict[str, str]]:
    if not _wants_insights(pref):
        return items
    used = {i.get("url", "") for i in items if i.get("url")}
    leftovers = [c for c in (candidates or []) if c.url not in used]
    out: list[dict[str, str]] = []
    for item in items:
        next_item = dict(item)
        if not (next_item.get("insight_q") or "").strip():
            next_item["insight_q"] = _insight_question(
                next_item.get("title", ""),
                next_item.get("blurb", ""),
            )
        if not (next_item.get("insight_url") or "").strip():
            next_item["insight_url"] = _insight_url_for(next_item, leftovers, used)
        next_item["insight_q"] = next_item["insight_q"][:120]
        next_item["insight_url"] = next_item["insight_url"][:500]
        out.append(next_item)
    return out


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
            "blurb": _summary_blurb(p),
            "url": p.url,
            "hint": p.source,
        }
        for p in picked[:3]
    ]


def _summary_blurb(item: SourceItem) -> str:
    summary = (item.summary or "").strip()
    if summary and summary.casefold() != item.source.casefold():
        return summary[:220]
    return f"{item.title}. {item.source}에서 가져온 핵심만 짧게 남겼습니다."[:220]


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
            "insight_q": x.get("insight_q", ""),
            "insight_url": x.get("insight_url", ""),
        }
        for x in ordered[:3]
    ]


def _llm_curate(
    db: Session,
    user: User,
    pref: Preference,
    candidates: list[SourceItem],
    timings: dict[str, int] | None = None,
) -> tuple[tuple[str, str, list[dict[str, str]]] | None, str, str]:
    settings = get_settings()
    if not settings.llm_configured:
        return None, "llm_not_configured", ""
    if not candidates:
        return None, "no_candidates", ""

    topics = ", ".join(_topic_list(pref)) or "일반"
    custom = _customization(pref) or "(없음)"
    insight_on = _wants_insights(pref)
    insight_rules = ""
    insight_json = ""
    if insight_on:
        insight_rules = (
            "각 item마다 insight_q·insight_url을 **하나씩만** 추가한다.\n"
            "insight_q는 본문에서 자연스럽게 생길 수 있는 궁금증 한 문장이다 "
            "(예: 금리 얘기면 '금리가 주가에 미치는 영향이 궁금해요').\n"
            "insight_url은 그 궁금증을 해소하는 보조 링크다. 가능하면 후보 URL 중 "
            "본문 url과 다른 것을 쓰고, 없으면 검색 URL도 허용한다.\n"
        )
        insight_json = ',"insight_q":"...","insight_url":"https://..."'
    prompt = (
        "너는 '오늘의 3' 큐레이터다. 아래 후보 목록에서만 골라 카카오톡용 브리프를 한국어로 만든다.\n"
        "반드시 후보에 있는 URL만 본문 url로 사용한다. URL을 지어내지 마라.\n"
        "유튜브·아티클·커뮤니티를 가능하면 섞어 **딱 3개**.\n"
        "각 item의 title은 짧은 제목, blurb는 제목과 URL 사이에 넣을 **내용 요약**이다.\n"
        "blurb에는 헤드라인 요지·영상 설명·글 핵심을 1~2문장으로 담아라. 메타 코멘트(예: '15분짜리')만 쓰지 마라.\n"
        "후보 summary가 있으면 그걸 다듬어 blurb로 쓰고, 없으면 title을 바탕으로 요약을 만든다.\n"
        f"{insight_rules}"
        "JSON만 출력:\n"
        '{"title":"오늘의 3 · M/D (요일)","items":[{"kind":"유튜브|아티클|커뮤니티","title":"...","blurb":"...","url":"https://..."'
        f"{insight_json}"
        "}]}\n"
        f"수신자: {user.display_name}\n"
        f"관심 주제: {topics}\n"
        f"커스터마이징: {custom}\n"
        f"후보:\n{candidates_as_prompt_block(candidates)}\n"
    )
    llm_started = perf_counter()
    text, _usage = llm_service.chat_completion(
        db,
        user_id=user.id,
        purpose="digest_curate",
        messages=[
            {"role": "system", "content": "Return only valid JSON for three curated links."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
        max_tokens=1100 if insight_on else 900,
    )
    if timings is not None:
        timings["llm_ms"] = elapsed_ms(llm_started)
    if not text:
        return None, "empty_response", ""
    try:
        agg_started = perf_counter()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.M)
        data = json.loads(text)
        items = data.get("items") or []
        if len(items) < 3:
            if timings is not None:
                timings["aggregation_ms"] = elapsed_ms(agg_started)
            return None, "fewer_than_3_items", text[:2000]
        allowed = {c.url for c in candidates}
        cleaned: list[dict[str, str]] = []
        for raw in items[:3]:
            url = str(raw.get("url") or "")
            if url not in allowed:
                match = next((c for c in candidates if c.url in url or url in c.url), None)
                if match is None:
                    continue
                url = match.url
            row: dict[str, str] = {
                "kind": str(raw.get("kind") or "아티클"),
                "title": str(raw.get("title") or "")[:120],
                "blurb": str(raw.get("blurb") or "")[:220],
                "url": url,
                "hint": "",
            }
            if insight_on:
                row["insight_q"] = str(raw.get("insight_q") or "")[:120]
                insight_url = str(raw.get("insight_url") or "").strip()
                if insight_url in allowed or insight_url.startswith("http"):
                    row["insight_url"] = insight_url[:500]
            cleaned.append(row)
        if len(cleaned) < 3:
            if timings is not None:
                timings["aggregation_ms"] = elapsed_ms(agg_started)
            return None, "fewer_than_3_valid_urls", text[:2000]
        cleaned = _attach_insights(cleaned, pref=pref, candidates=candidates)
        topics_list = _topic_list(pref)
        for i, row in enumerate(cleaned):
            topic = topics_list[i % len(topics_list)] if topics_list else ""
            row["topic"] = topic.replace("/", " · ") if topic else ""
        title = str(data.get("title") or "").strip()[:120]
        if not title:
            now = datetime.now(ZoneInfo(SEOUL))
            weekday = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
            title = f"오늘의 3 · {now.month}/{now.day} ({weekday})"
        if timings is not None:
            timings["aggregation_ms"] = elapsed_ms(agg_started)
        fmt_started = perf_counter()
        body = _format_body(
            user.display_name or "당신",
            cleaned,
            pref,
            topics_list,
            reviewed_count=len(candidates),
        )
        if timings is not None:
            timings["format_ms"] = elapsed_ms(fmt_started)
        return (title, body, cleaned), "", text[:2000]
    except Exception:
        return None, "invalid_json", (text or "")[:2000]


def _source_list(pref: Preference) -> list[str]:
    return [s.strip() for s in (pref.sources or "").split(",") if s.strip()]


@dataclass
class DigestPreview:
    title: str
    body: str
    items: list[dict[str, str]]
    curator: str
    llm_configured: bool
    llm_skip_reason: str
    llm_raw: str
    candidates: list[SourceItem] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    trigger_ms: int = 0
    crawl_ms: int = 0
    aggregation_ms: int = 0
    llm_ms: int = 0
    format_ms: int = 0


def build_digest_preview(db: Session, user: User, pref: Preference) -> DigestPreview:
    trigger_started = perf_counter()
    topics = _topic_list(pref)
    sites = _source_list(pref)
    now = datetime.now(ZoneInfo(SEOUL))
    weekday = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
    seed = f"{user.id}:{now.date().isoformat()}:{','.join(topics)}:{','.join(sites)}"
    name = user.display_name or "당신"
    title = f"오늘의 3 · {now.month}/{now.day} ({weekday})"
    settings = get_settings()
    trigger_ms = elapsed_ms(trigger_started)

    crawl_started = perf_counter()
    candidates = gather_candidates(topics, preferred_sites=sites)
    crawl_ms = elapsed_ms(crawl_started)

    layer_ms: dict[str, int] = {"llm_ms": 0, "aggregation_ms": 0, "format_ms": 0}
    llm, skip_reason, llm_raw = _llm_curate(db, user, pref, candidates, timings=layer_ms)
    if llm:
        llm_title, llm_body, llm_items = llm
        return DigestPreview(
            title=llm_title,
            body=llm_body,
            items=llm_items,
            curator="llm",
            llm_configured=settings.llm_configured,
            llm_skip_reason="",
            llm_raw=llm_raw,
            candidates=candidates,
            topics=topics,
            sources=sites,
            trigger_ms=trigger_ms,
            crawl_ms=crawl_ms,
            aggregation_ms=layer_ms.get("aggregation_ms", 0),
            llm_ms=layer_ms.get("llm_ms", 0),
            format_ms=layer_ms.get("format_ms", 0),
        )

    agg_started = perf_counter()
    picked = _heuristic_pick(candidates, seed)
    curator = "heuristic" if picked else "static"
    items = _attach_insights(picked or _static_fallback(topics, seed), pref=pref, candidates=candidates)
    labeled: list[dict[str, str]] = []
    for i, item in enumerate(items):
        row = dict(item)
        topic = topics[i % len(topics)] if topics else item.get("hint", "")
        row["topic"] = topic.replace("/", " · ") if topic else ""
        labeled.append(row)
    aggregation_ms = elapsed_ms(agg_started)
    fmt_started = perf_counter()
    body = _format_body(name, labeled, pref, topics, reviewed_count=len(candidates))
    format_ms = elapsed_ms(fmt_started)
    return DigestPreview(
        title=title,
        body=body,
        items=labeled,
        curator=curator,
        llm_configured=settings.llm_configured,
        llm_skip_reason=skip_reason,
        llm_raw=llm_raw,
        candidates=candidates,
        topics=topics,
        sources=sites,
        trigger_ms=trigger_ms,
        crawl_ms=crawl_ms,
        aggregation_ms=aggregation_ms,
        llm_ms=layer_ms.get("llm_ms", 0),
        format_ms=format_ms,
    )


def generate_digest_content(
    db: Session, user: User, pref: Preference
) -> tuple[str, str, list[dict[str, str]]]:
    preview = build_digest_preview(db, user, pref)
    return preview.title, preview.body, preview.items


def create_digest(
    db: Session,
    user: User,
    pref: Preference,
    *,
    status: str = "draft",
    trigger: str = "preview",
    slot_label: str = "",
    lead_ms: int = 0,
) -> Digest:
    if pref.timezone != SEOUL:
        pref.timezone = SEOUL
        db.add(pref)
    preview = build_digest_preview(db, user, pref)
    digest = Digest(
        user_id=user.id,
        title=preview.title,
        body=preview.body,
        status=status,
        delivery_channel="kakao_me",
        items_json=json.dumps(preview.items, ensure_ascii=False),
    )
    db.add(digest)
    db.flush()
    persist_crawl_run(
        db,
        user,
        preview.candidates,
        trigger=trigger,
        digest_id=digest.id,
        slot_label=slot_label,
        trigger_ms=preview.trigger_ms,
        crawl_ms=preview.crawl_ms,
        aggregation_ms=preview.aggregation_ms,
        llm_ms=preview.llm_ms,
        format_ms=preview.format_ms,
        lead_ms=lead_ms,
        curator=preview.curator,
        llm_skip_reason=preview.llm_skip_reason,
    )
    db.commit()
    db.refresh(digest)
    return digest

