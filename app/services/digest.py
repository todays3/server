"""Generate '하루만장' — three curated links from live sources (+ optional free LLM)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from time import perf_counter
from urllib.parse import quote_plus, urlparse
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Digest, Preference, User
from app.services import llm as llm_service
from app.services.crawl_log import persist_crawl_run
from app.services.pipeline_timing import elapsed_ms
from app.services.run_resources import peak_sampler
from app.services.pick_reason import KIND_WHY, clip_why, compose_pick_reason
from app.services.shortlist import shortlist_for_llm, topic_tokens
from app.services.sources import SourceItem, candidates_as_prompt_block, gather_candidates

SEOUL = "Asia/Seoul"
ProgressCb = Callable[[str], None]


def _emit(on_progress: ProgressCb | None, step: str) -> None:
    if on_progress:
        on_progress(step)

_CURATED: list[dict[str, str]] = [
    {
        "kind": "아티클",
        "title": "미국 증시·금리, 오늘만 필요한 요약",
        "blurb": "연준 발언 이후 기술주가 흔들렸지만, 인하 기대는 한 박자 늦춰졌다는 해석입니다. 나스닥·국채 금리만 짚은 단기 브리핑입니다.",
        "url": "https://news.google.com/rss/search?q=US+stock+market&hl=en-US&gl=US&ceid=US:en",
        "hint": "경제",
        "insight_q": "금리가 주가와 성장주에 미치는 영향은 무엇일까?",
        "insight_url": "https://www.investing.com/economic-calendar/",
    },
    {
        "kind": "커뮤니티",
        "title": "국내주식 토론 — 숫자부터 보기",
        "blurb": "종목 감정보다 공시·수급·밸류에이션을 먼저 보자는 스레드입니다. 과열·저평가 주장이 갈리는 지점만 추렸습니다.",
        "url": "https://finance.naver.com/",
        "hint": "경제",
        "insight_q": "공시·수급 숫자가 주가 해석에 왜 중요할까?",
        "insight_url": "https://dart.fss.or.kr/",
    },
    {
        "kind": "유튜브",
        "title": "삼프로TV · 시장 브리핑",
        "blurb": "국내 시황과 해외 이슈를 한 편에 묶어, 오늘 장에서 볼 포인트만 짧게 정리한 브리핑 영상입니다.",
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


def occupation_tokens(occupation: str) -> list[str]:
    parts = re.split(r"[\s,/·|]+", (occupation or "").strip())
    aliases = {
        "의사": ("의료", "병원", "의학", "진료"),
        "간호": ("의료", "병원", "간호"),
        "약사": ("의료", "약품", "약국"),
        "반도체": ("칩", "hbm", "파운드리", "웨이퍼"),
        "개발": ("소프트웨어", "프로그래밍", "코딩"),
        "개발자": ("소프트웨어", "프로그래밍", "코딩"),
        "교사": ("교육", "학교", "학생"),
        "교수": ("대학", "연구", "교육"),
        "연구원": ("연구", "논문"),
        "변호사": ("법률", "소송", "법원"),
        "회계": ("세무", "재무"),
        "기자": ("언론", "뉴스"),
    }
    tokens: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        key = token.lower()
        if len(token) < 2 or key in seen:
            return
        seen.add(key)
        tokens.append(token)

    for part in parts:
        add(part)
        for extra in aliases.get(part, ()):
            add(extra)
        for key, extras in aliases.items():
            if key != part and key in part:
                add(key)
                for extra in extras:
                    add(extra)
    return tokens


def age_band(birth: date | None, *, today: date | None = None) -> str:
    if birth is None:
        return ""
    day = today or datetime.now(ZoneInfo(SEOUL)).date()
    years = day.year - birth.year - ((day.month, day.day) < (birth.month, birth.day))
    if years < 0 or years > 120:
        return ""
    if years < 10:
        return f"{years}세"
    return f"{(years // 10) * 10}대"


def profile_brief(user: User, *, today: date | None = None) -> str:
    occ = (getattr(user, "occupation", None) or "").strip() or "(없음)"
    band = age_band(getattr(user, "birth_date", None), today=today) or "(없음)"
    return (
        f"닉네임: {(user.display_name or '').strip() or '(없음)'}\n"
        f"직업: {occ}\n"
        f"연령대: {band}\n"
        "직업·연령대에 실질적으로 도움이 되는 후보를 우선하고, 말투는 그 독자에 맞춥니다. "
        "생년월일·나이를 본문에 숫자로 쓰지 마세요. "
        "독자는 한국 사용자이므로 한국어 아티클·한국 영상을 우선하세요."
    )


_HANGUL = re.compile(r"[가-힣]")
_KR_HOST_MARKERS = (
    ".kr",
    "naver.com",
    "daum.net",
    "kakao.com",
    "tistory.com",
    "velog.io",
    "bloter.net",
    "clien.net",
    "dcinside.com",
    "chosun.com",
    "joongang.co.kr",
    "hani.co.kr",
    "mk.co.kr",
    "hankyung.com",
    "yonhapnews",
    "yna.co.kr",
    "ytn.co.kr",
    "sbs.co.kr",
    "kbs.co.kr",
    "mbc.co.kr",
    "ppomppu.co.kr",
    "okky.kr",
    "wanted.co.kr",
    "d2.naver.com",
    "techblogposts.com",
    "surfit.io",
    "disquiet.io",
    "innoforest.co.kr",
    "eopla.net",
    "rocketpunch.com",
    "designcompass.org",
)


def korean_content_score(item: SourceItem) -> int:
    title = item.title or ""
    summary = item.summary or ""
    source = item.source or ""
    score = 0
    if _HANGUL.search(title):
        score += 4
    if _HANGUL.search(summary):
        score += 2
    if _HANGUL.search(source):
        score += 1
    host = urlparse(item.url).netloc.lower()
    if any(marker in host for marker in _KR_HOST_MARKERS):
        score += 3
    if item.kind == "유튜브" and (_HANGUL.search(title) or _HANGUL.search(source)):
        score += 3
    return score


def personalize_candidates(candidates: list[SourceItem], user: User) -> list[SourceItem]:
    tokens = occupation_tokens(getattr(user, "occupation", "") or "")
    band = age_band(getattr(user, "birth_date", None))
    if band:
        tokens = [*tokens, band]
    if not candidates:
        return candidates

    def occupation_hits(item: SourceItem) -> int:
        if not tokens:
            return 0
        blob = f"{item.title} {item.summary} {item.source}".lower()
        return sum(1 for token in tokens if token.lower() in blob)

    def score(item: SourceItem) -> tuple[int, int]:
        return (occupation_hits(item), korean_content_score(item))

    ranked = sorted(candidates, key=score, reverse=True)
    return [
        replace(
            item,
            pick_reason=compose_pick_reason(item, job_match=occupation_hits(item) > 0),
        )
        for item in ranked
    ]


def llm_shortlist(candidates: list[SourceItem], user: User, topics: list[str]) -> list[SourceItem]:
    job = occupation_tokens(getattr(user, "occupation", "") or "")
    band = age_band(getattr(user, "birth_date", None))
    if band:
        job = [*job, band]
    korean_scores = {item.url: korean_content_score(item) for item in candidates}
    return shortlist_for_llm(
        candidates,
        job_tokens=job,
        topic_parts=topic_tokens(topics),
        korean_scores=korean_scores,
    )


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
    return f"{n}개 중 골랐습니다."


_ITEM_RULE = "────────"
_ORDINALS = ("첫째", "둘째", "셋째", "넷째", "다섯째")


def _lead_sentence(text: str, *, max_len: int = 80) -> str:
    raw = " ".join((text or "").split())
    if not raw:
        return ""
    earliest: tuple[int, str] | None = None
    for mark in ("습니다.", "입니다.", "할까요?", "까요?", "다.", "요.", ". ", "? ", "! "):
        idx = raw.find(mark)
        if idx >= 0 and (earliest is None or idx < earliest[0]):
            earliest = (idx, mark)
    if earliest is not None:
        raw = raw[: earliest[0] + len(earliest[1])]
    if len(raw) <= max_len:
        return raw
    return raw[: max_len - 1].rstrip() + "…"


def _format_body(
    name: str,
    items: list[dict[str, str]],
    pref: Preference,
    topics: list[str],
    *,
    reviewed_count: int | None = None,
) -> str:
    n = reviewed_count if reviewed_count is not None else len(items)
    _ = topics
    picked = max(len(items), 1)
    lines: list[str] = [
        f"하루만장 · {name}님",
        f"오늘 고른 {picked}개입니다.",
        "",
    ]
    show_insight = _wants_insights(pref)
    for i, item in enumerate(items):
        if i:
            lines.append(_ITEM_RULE)
            lines.append("")
        ordinal = _ORDINALS[i] if i < len(_ORDINALS) else f"{i + 1}"
        kind = item.get("kind") or "아티클"
        emoji = _kind_emoji(kind)
        title = (item.get("title") or "").strip()
        lines.append(f"{ordinal}. {emoji} {title}")
        blurb = _lead_sentence(item.get("blurb") or item.get("summary") or "")
        if blurb:
            lines.append(blurb)
        why = clip_why(item.get("why") or "")
        if why:
            lines.append(f"왜 {why}")
        lines.append(str(item.get("url") or ""))
        if show_insight:
            iq = _lead_sentence(item.get("insight_q") or "", max_len=60)
            iu = (item.get("insight_url") or "").strip()
            if iq and iu:
                lines.append(f"궁금 {iq}")
                lines.append(iu)
        lines.append("")
    custom = _customization(pref)
    if custom:
        lines.append(f"요청: {custom}")
    lines.append(reviewed_line(n))
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


def _why_for_item(item: dict[str, str], candidates: list[SourceItem]) -> str:
    existing = clip_why(item.get("why") or "")
    if existing:
        return existing
    url = item.get("url") or ""
    match = next((c for c in candidates if c.url == url), None)
    if match:
        return clip_why(match.pick_reason or compose_pick_reason(match))
    return clip_why(KIND_WHY.get(item.get("kind") or "", "오늘 후보 중 선별"))


def _attach_why(items: list[dict[str, str]], candidates: list[SourceItem]) -> list[dict[str, str]]:
    return [{**item, "why": _why_for_item(item, candidates)} for item in items]


def _heuristic_pick(candidates: list[SourceItem], seed: str) -> list[dict[str, str]]:
    """Pick up to 3 items from an already-ranked list, preferring kind diversity."""
    if not candidates:
        return []
    _ = seed

    picked: list[SourceItem] = []
    used_kinds: set[str] = set()
    for item in candidates:
        if item.kind in used_kinds:
            continue
        picked.append(item)
        used_kinds.add(item.kind)
        if len(picked) >= 3:
            break
    if len(picked) < 3:
        for item in candidates:
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
            "why": clip_why(p.pick_reason or compose_pick_reason(p)),
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
            "why": "고정 큐레이션",
        }
        for x in ordered[:3]
    ]


def _llm_curate(
    db: Session,
    user: User,
    pref: Preference,
    candidates: list[SourceItem],
    timings: dict[str, int] | None = None,
    on_progress: ProgressCb | None = None,
    *,
    pool: list[SourceItem] | None = None,
) -> tuple[tuple[str, str, list[dict[str, str]]] | None, str, str]:
    settings = get_settings()
    if not settings.llm_configured:
        return None, "llm_not_configured", ""
    if not candidates:
        return None, "no_candidates", ""
    reviewed = pool if pool is not None else candidates

    topics = ", ".join(_topic_list(pref)) or "일반"
    custom = _customization(pref) or "(없음)"
    insight_on = _wants_insights(pref)
    insight_rules = ""
    insight_json = ""
    if insight_on:
        insight_rules = (
            "각 item마다 insight_q·insight_url을 **하나씩만** 추가합니다.\n"
            "insight_q는 본문에서 자연스럽게 생길 수 있는 궁금증 한 문장입니다 "
            "(예: 금리 얘기면 '금리가 주가에 미치는 영향이 궁금합니다').\n"
            "insight_url은 그 궁금증을 해소하는 보조 링크입니다. 가능하면 후보 URL 중 "
            "본문 url과 다른 것을 쓰고, 없으면 검색 URL도 허용합니다.\n"
        )
        insight_json = ',"insight_q":"...","insight_url":"https://..."'
    prompt = (
        "너는 '하루만장' 큐레이터입니다. 아래 후보 목록에서만 골라 카카오톡용 브리프를 한국어로 만듭니다.\n"
        "독자는 한국 사용자입니다. 한국어 제목·요약인 아티클과 한국 채널 영상을 우선하세요. "
        "영어 전용 후보는 한국어 후보가 부족할 때만 고릅니다.\n"
        "반드시 후보에 있는 URL만 본문 url로 사용합니다. URL을 지어내지 마세요.\n"
        "유튜브·아티클·커뮤니티를 가능하면 섞어 **딱 3개**입니다.\n"
        "각 item의 title은 짧은 제목, blurb는 제목 바로 아래 넣을 **핵심 한 문장**입니다.\n"
        "blurb는 두괄식입니다. 요지를 첫 문장에 쓰고, 두 문장 이상으로 늘리지 마세요. "
        "메타 코멘트(예: '15분짜리')만 쓰지 마세요.\n"
        "후보 summary가 있으면 그걸 다듬어 blurb로 쓰고, 없으면 title을 바탕으로 요약을 만듭니다.\n"
        "독자에게 보이는 한국어(title·blurb·insight_q)는 기본으로 합니다/습니다 체를 씁니다. "
        "-다 체(한다/이다/됐다)와 반말은 쓰지 마세요. 커스터마이징에 다른 말투가 있으면 그걸 우선합니다.\n"
        "후보 줄의 why는 사이트별 선정 신호입니다(급상승·공식 블로그·조회수 하한 등). "
        "이 신호를 보고 고르세요. 조회수·좋아요·저자를 지어내지 마세요.\n"
        f"{insight_rules}"
        "JSON만 출력:\n"
        '{"title":"하루만장 · M/D (요일)","items":[{"kind":"유튜브|아티클|커뮤니티","title":"...","blurb":"...","url":"https://..."'
        f"{insight_json}"
        "}]}\n"
        f"수신자: {user.display_name}\n"
        f"{profile_brief(user)}\n"
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
            row["why"] = _why_for_item(row, reviewed)
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
        cleaned = _attach_why(_attach_insights(cleaned, pref=pref, candidates=reviewed), reviewed)
        topics_list = _topic_list(pref)
        for i, row in enumerate(cleaned):
            topic = topics_list[i % len(topics_list)] if topics_list else ""
            row["topic"] = topic.replace("/", " · ") if topic else ""
        title = str(data.get("title") or "").strip()[:120]
        if not title:
            now = datetime.now(ZoneInfo(SEOUL))
            weekday = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
            title = f"하루만장 · {now.month}/{now.day} ({weekday})"
        if timings is not None:
            timings["aggregation_ms"] = elapsed_ms(agg_started)
        _emit(on_progress, "format")
        fmt_started = perf_counter()
        body = _format_body(
            user.display_name or "당신",
            cleaned,
            pref,
            topics_list,
            reviewed_count=len(reviewed),
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


def build_digest_preview(
    db: Session,
    user: User,
    pref: Preference,
    on_progress: ProgressCb | None = None,
    shared_candidates: list[SourceItem] | None = None,
) -> DigestPreview:
    trigger_started = perf_counter()
    topics = _topic_list(pref)
    sites = _source_list(pref)
    now = datetime.now(ZoneInfo(SEOUL))
    weekday = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
    seed = f"{user.id}:{now.date().isoformat()}:{','.join(topics)}:{','.join(sites)}"
    name = user.display_name or "당신"
    title = f"하루만장 · {now.month}/{now.day} ({weekday})"
    settings = get_settings()
    trigger_ms = elapsed_ms(trigger_started)

    _emit(on_progress, "crawl")
    crawl_started = perf_counter()
    raw = shared_candidates if shared_candidates is not None else gather_candidates(topics, preferred_sites=sites)
    candidates = personalize_candidates(raw, user)
    crawl_ms = elapsed_ms(crawl_started)

    layer_ms: dict[str, int] = {"llm_ms": 0, "aggregation_ms": 0, "format_ms": 0}
    _emit(on_progress, "curate")
    llm, skip_reason, llm_raw = _llm_curate(
        db,
        user,
        pref,
        llm_shortlist(candidates, user, topics),
        timings=layer_ms,
        on_progress=on_progress,
        pool=candidates,
    )
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
    items = _attach_why(
        _attach_insights(picked or _static_fallback(topics, seed), pref=pref, candidates=candidates),
        candidates,
    )
    labeled: list[dict[str, str]] = []
    for i, item in enumerate(items):
        row = dict(item)
        topic = topics[i % len(topics)] if topics else item.get("hint", "")
        row["topic"] = topic.replace("/", " · ") if topic else ""
        labeled.append(row)
    aggregation_ms = elapsed_ms(agg_started)
    _emit(on_progress, "format")
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
    on_progress: ProgressCb | None = None,
    shared_candidates: list[SourceItem] | None = None,
) -> Digest:
    if pref.timezone != SEOUL:
        pref.timezone = SEOUL
        db.add(pref)
    with peak_sampler() as peak:
        preview = build_digest_preview(
            db, user, pref, on_progress=on_progress, shared_candidates=shared_candidates
        )
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
        cpu_peak_percent=peak.cpu_peak_percent,
        rss_peak_bytes=peak.rss_peak_bytes,
        rss_delta_bytes=peak.rss_delta_bytes,
    )
    db.commit()
    db.refresh(digest)
    return digest

