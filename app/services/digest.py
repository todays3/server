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
from app.services.greeting import digest_opening_line
from app.services.pick_reason import KIND_WHY, clip_why, compose_pick_reason
from app.services.shortlist import shortlist_for_llm, topic_tokens
from app.services.sources import SourceItem, candidates_as_prompt_block, gather_candidates

SEOUL = "Asia/Seoul"
ProgressCb = Callable[[str], None]
TEST_DATA_NOTICE = "(본 내용은 테스트용 데이터입니다.)"


def _emit(on_progress: ProgressCb | None, step: str) -> None:
    if on_progress:
        on_progress(step)

# Desk live-preview SAMPLE_POOL, in Kakao body tone (합니다/습니다).
_CURATED: list[dict[str, str]] = [
    {
        "kind": "아티클",
        "title": "이번 주 미국 증시, 금리 발언만 짚기",
        "blurb": "연준 발언 이후 나스닥이 반등했지만, 금리 인하 기대는 다시 뒤로 밀렸다는 분석입니다.",
        "url": "https://www.hankyung.com/",
        "hint": "경제",
        "why": "한경 헤드라인",
        "insight_q": "금리가 주가에 미치는 영향이 궁금합니다.",
        "insight_url": "https://www.investing.com/economic-calendar/",
    },
    {
        "kind": "커뮤니티",
        "title": "국내주식 종목 토론 — 과열 vs 저평가",
        "blurb": "코스피 대형주 밸류에이션을 두고 이미 반영됐다는 쪽과 아직 싸다는 쪽이 갈립니다.",
        "url": "https://finance.naver.com/",
        "hint": "경제",
        "why": "네이버증권 주요뉴스",
        "insight_q": "공시·수급만으로 과열을 어떻게 가릴까요?",
        "insight_url": "https://dart.fss.or.kr/",
    },
    {
        "kind": "유튜브",
        "title": "연애 초반, 연락 텀 어떻게 두나요",
        "blurb": "초반 답장 속도와 만남 텀을 과해석하지 않는 법을 사례로 짚습니다.",
        "url": "https://www.youtube.com/results?search_query=%EC%97%B0%EC%95%A0+%EC%97%B0%EB%9D%BD",
        "hint": "연애",
        "why": "조회수 1만+ 영상",
        "insight_q": "연락 텀을 과해석하지 않으려면 무엇을 볼까요?",
        "insight_url": "https://brunch.co.kr/",
    },
    {
        "kind": "아티클",
        "title": "생성형 AI, 일상에 붙이는 최소 루틴",
        "blurb": "도구를 늘리기보다 아침 한 번의 프롬프트로 일정·메모를 정리하는 습관입니다.",
        "url": "https://news.ycombinator.com/",
        "hint": "IT",
        "why": "HN 프론트페이지",
        "insight_q": "생성형 AI가 업무 비용에 미치는 영향은 무엇일까요?",
        "insight_url": "https://news.ycombinator.com/",
    },
    {
        "kind": "커뮤니티",
        "title": "이직 면접에서 자주 나오는 질문 모음",
        "blurb": "왜 옮기나요, 갈등 경험, 최근 실패처럼 반복되는 질문의 답변 뼈대를 모았습니다.",
        "url": "https://www.wanted.co.kr/",
        "hint": "커리어",
        "why": "원티드 커리어글",
        "insight_q": "면접관이 이 질문에서 실제로 보려는 포인트는 무엇일까요?",
        "insight_url": "https://www.wanted.co.kr/",
    },
    {
        "kind": "유튜브",
        "title": "수면·운동, 바쁜 주에 지키는 최소선",
        "blurb": "완벽한 루틴 대신 취침 고정과 20분 걷기만 지키는 주간 실험입니다.",
        "url": "https://www.youtube.com/results?search_query=%EC%88%98%EB%A9%B4+%EC%9A%B4%EB%8F%99",
        "hint": "라이프",
        "why": "조회수 1만+ 영상",
        "insight_q": "수면 부채가 집중력에 미치는 영향은 무엇일까요?",
        "insight_url": "https://www.youtube.com/results?search_query=%EC%88%98%EB%A9%B4+%EB%B6%80%EC%B1%84",
    },
    {
        "kind": "아티클",
        "title": "환율·물가 한 장 브리핑",
        "blurb": "달러·원 환율이 다시 들썩인 배경과 수입 물가 파급만 추렸습니다.",
        "url": "https://www.bok.or.kr/",
        "hint": "경제",
        "why": "Investing 시황",
        "insight_q": "환율 변동이 수입 물가에 미치는 영향은 무엇일까요?",
        "insight_url": "https://www.investing.com/currencies/usd-krw",
    },
    {
        "kind": "커뮤니티",
        "title": "이번 주 사회 이슈, 팩트만 모음",
        "blurb": "원문·타임라인·수치 링크만으로 이슈를 재구성한 스레드입니다.",
        "url": "https://news.naver.com/",
        "hint": "뉴스",
        "why": "네이버뉴스 헤드라인",
        "insight_q": "이 이슈에서 확인된 사실과 추정은 어떻게 나눌까요?",
        "insight_url": "https://news.naver.com/",
    },
    {
        "kind": "유튜브",
        "title": "React 19, 실무에서 바뀌는 지점만",
        "blurb": "컴파일러·액션·폼 패턴이 기존 코드에 어떻게 붙는지 오늘 건드릴 파일 위주로 설명합니다.",
        "url": "https://github.com/trending",
        "hint": "IT",
        "why": "GitHub 오늘 트렌딩",
        "insight_q": "React 19 도입이 기존 번들·폼 코드에 미치는 영향은 무엇일까요?",
        "insight_url": "https://github.com/trending",
    },
    {
        "kind": "아티클",
        "title": "연봉 협상, 숫자부터 준비하기",
        "blurb": "시장 밴드·현재 총보상·이직 비용을 표로 맞춰 두고 말하는 법입니다.",
        "url": "https://www.levels.fyi/",
        "hint": "커리어",
        "why": "원티드 커리어글",
        "insight_q": "연봉 협상에서 먼저 맞춰 둘 숫자는 무엇일까요?",
        "insight_url": "https://www.wanted.co.kr/",
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


def profile_brief(user: User, pref: Preference | None = None, *, today: date | None = None) -> str:
    from app.services.roles import parse_role_settings, parse_roles, roles_profile_brief, role_tokens

    roles = parse_roles(getattr(pref, "roles", "") or "") if pref is not None else []
    settings = parse_role_settings(getattr(pref, "role_settings", "") or "{}") if pref is not None else {}
    occ = (getattr(user, "occupation", None) or "").strip()
    if roles:
        role_line = ", ".join(role_tokens(roles))
        occ = role_line or occ
    elif not occ:
        occ = "(없음)"
    band = age_band(getattr(user, "birth_date", None), today=today) or "(없음)"
    base = (
        f"닉네임: {(user.display_name or '').strip() or '(없음)'}\n"
        f"직무: {occ}\n"
        f"연령대: {band}\n"
        "직무·연령대에 실질적으로 도움이 되는 후보를 우선합니다. "
        "title·blurb·insight_q에는 선택한 어시스턴트의 대표 인간상·말투를 반영하되, 합니다/습니다 체는 유지합니다. "
        "생년월일·나이를 본문에 숫자로 쓰지 마세요. "
        "독자는 한국 사용자이므로 한국어 아티클·한국 영상을 우선하세요."
    )
    role_block = roles_profile_brief(roles, settings)
    if not role_block:
        return base
    return f"{base}\n{role_block}"


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


def _job_tokens(user: User, pref: Preference | None = None) -> list[str]:
    from app.services.roles import parse_roles, role_tokens

    if pref is not None:
        roles = parse_roles(getattr(pref, "roles", "") or "")
        if roles:
            return role_tokens(roles)
    return occupation_tokens(getattr(user, "occupation", "") or "")


def personalize_candidates(
    candidates: list[SourceItem], user: User, pref: Preference | None = None
) -> list[SourceItem]:
    tokens = _job_tokens(user, pref)
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


def llm_shortlist(
    candidates: list[SourceItem], user: User, topics: list[str], pref: Preference | None = None
) -> list[SourceItem]:
    job = _job_tokens(user, pref)
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


_ITEM_RULE = "────────"
_ORDINALS = ("첫째", "둘째", "셋째", "넷째", "다섯째")


class _PrefFocus:
    """Read-through preference with topic/role overrides for per-assistant curation."""

    def __init__(
        self,
        pref: Preference,
        *,
        topics: list[str] | None = None,
        roles: list[str] | None = None,
    ) -> None:
        object.__setattr__(self, "_pref", pref)
        object.__setattr__(self, "_topics", topics)
        object.__setattr__(self, "_roles", roles)

    def __getattr__(self, name: str):
        if name == "topics" and self._topics is not None:
            return ",".join(self._topics)
        if name == "roles" and self._roles is not None:
            return ",".join(self._roles)
        return getattr(self._pref, name)


def site_ids_for_role(role: str) -> set[str]:
    from app.catalog.ref_sites import MEGA_TO_GROUPS, catalog_groups
    from app.services.roles import ROLE_TOPIC_MEGAS

    wanted = set()
    for mega in ROLE_TOPIC_MEGAS.get(role, []):
        wanted.update(MEGA_TO_GROUPS.get(mega, []))
    ids: set[str] = set()
    for group in catalog_groups():
        if group["id"] in wanted:
            ids.update(site["id"] for site in group["sites"])
    return ids


def filter_candidates_for_role(candidates: list[SourceItem], role: str) -> list[SourceItem]:
    site_ids = site_ids_for_role(role)
    if not site_ids:
        return list(candidates)
    matched = [item for item in candidates if item.site_id and item.site_id in site_ids]
    return matched if matched else list(candidates)


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


def _format_item_lines(item: dict[str, str], index: int, *, show_insight: bool) -> list[str]:
    lines: list[str] = []
    if index:
        lines.append(_ITEM_RULE)
        lines.append("")
    ordinal = _ORDINALS[index] if index < len(_ORDINALS) else f"{index + 1}"
    kind = item.get("kind") or "아티클"
    emoji = _kind_emoji(kind)
    title = (item.get("title") or "").strip()
    lines.append(f"{ordinal}. {emoji} {title}")
    blurb = _lead_sentence(item.get("blurb") or item.get("summary") or "")
    if blurb:
        lines.append(blurb)
    why = clip_why(item.get("why") or "")
    if why:
        lines.append(f"선정이유: {why}")
    lines.append(str(item.get("url") or ""))
    if show_insight:
        iq = _lead_sentence(item.get("insight_q") or "", max_len=60)
        iu = (item.get("insight_url") or "").strip()
        if iq and iu:
            lines.append(f"추가 질문: {iq}")
            lines.append(iu)
    lines.append("")
    return lines


def _assistant_groups(items: list[dict[str, str]]) -> list[tuple[str, list[dict[str, str]]]]:
    groups: list[tuple[str, list[dict[str, str]]]] = []
    for item in items:
        name = (item.get("assistant") or "").strip()
        if groups and groups[-1][0] == name:
            groups[-1][1].append(item)
        else:
            groups.append((name, [item]))
    return groups


def _format_body(
    name: str,
    items: list[dict[str, str]],
    pref: Preference,
    topics: list[str],
    *,
    reviewed_count: int | None = None,
    now: datetime | None = None,
) -> str:
    n = reviewed_count if reviewed_count is not None else len(items)
    _ = topics
    when = now or datetime.now(ZoneInfo(SEOUL))
    from app.services.greeting import assistant_intro_line, greeting_line
    from app.services.roles import parse_role_settings, parse_roles, pick_digest_assistant

    roles = parse_roles(getattr(pref, "roles", "") or "")
    settings = parse_role_settings(getattr(pref, "role_settings", "") or "{}")
    local = when.astimezone(ZoneInfo(SEOUL)) if when.tzinfo else when.replace(tzinfo=ZoneInfo(SEOUL))
    show_insight = _wants_insights(pref)
    groups = _assistant_groups(items)
    named = [group for group in groups if group[0]]
    custom = _customization(pref)

    if len(named) > 1:
        lines: list[str] = [greeting_line(name, now=when), ""]
        for gi, (assistant, section) in enumerate(groups):
            if gi:
                lines.append(_ITEM_RULE)
                lines.append("")
            if assistant:
                lines.append(assistant_intro_line(assistant, now=when))
            lines.append(f"오늘 {n}개 중에 고른 {len(section)}개입니다.")
            lines.append("")
            for i, item in enumerate(section):
                lines.extend(_format_item_lines(item, i, show_insight=show_insight))
        if custom:
            lines.append(f"요청: {custom}")
        return "\n".join(lines).strip()

    picked = max(len(items), 1)
    assistant = named[0][0] if named else pick_digest_assistant(
        roles, settings, day=local.timetuple().tm_yday, hour=local.hour
    )
    lines = [
        digest_opening_line(name, assistant, now=when),
        f"오늘 {n}개 중에 고른 {picked}개입니다.",
        "",
    ]
    for i, item in enumerate(items):
        lines.extend(_format_item_lines(item, i, show_insight=show_insight))
    if custom:
        lines.append(f"요청: {custom}")
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


def _static_fallback(topics: list[str], seed: str, *, offset: int = 0, count: int = 3) -> list[dict[str, str]]:
    start = (int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) + offset) % len(_CURATED)
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
            "why": x.get("why") or "고정 큐레이션",
        }
        for x in ordered[:count]
    ]


def _with_test_notice(body: str) -> str:
    if TEST_DATA_NOTICE in body:
        return body
    lines = body.split("\n")
    if not lines:
        return TEST_DATA_NOTICE
    rest = lines[1:]
    while rest and rest[0] == "":
        rest = rest[1:]
    return "\n".join([lines[0], "", TEST_DATA_NOTICE, ""] + rest).strip()


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
        "-다 체(한다/이다/됐다)와 반말은 쓰지 마세요. 커스터마이징에 다른 말투가 있으면 그걸 우선합니다. "
        "어시스턴트 성격(아래 대표 인간상)이 title·blurb·insight_q 문장 선택과 어휘에 드러나게 쓰세요.\n"
        "후보 줄의 why는 사이트별 선정 신호입니다(급상승·공식 블로그·조회수 하한 등). "
        "이 신호를 보고 고르세요. 조회수·좋아요·저자를 지어내지 마세요.\n"
        f"{insight_rules}"
        "JSON만 출력:\n"
        '{"title":"하루만장 · M/D (요일)","items":[{"kind":"유튜브|아티클|커뮤니티","title":"...","blurb":"...","url":"https://..."'
        f"{insight_json}"
        "}]}\n"
        f"수신자: {user.display_name}\n"
        f"{profile_brief(user, pref)}\n"
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
            now=datetime.now(ZoneInfo(SEOUL)),
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


def _label_picked_items(
    picked: list[dict[str, str]],
    pref: Preference,
    candidates: list[SourceItem],
    topics: list[str],
) -> list[dict[str, str]]:
    items = _attach_why(_attach_insights(picked, pref=pref, candidates=candidates), candidates)
    labeled: list[dict[str, str]] = []
    for i, item in enumerate(items):
        row = dict(item)
        topic = topics[i % len(topics)] if topics else item.get("hint", "")
        row["topic"] = topic.replace("/", " · ") if topic else ""
        labeled.append(row)
    return labeled


def _curate_three(
    db: Session,
    user: User,
    pref: Preference,
    candidates: list[SourceItem],
    topics: list[str],
    seed: str,
    timings: dict[str, int],
    on_progress: ProgressCb | None,
    pool: list[SourceItem],
) -> tuple[list[dict[str, str]], str, str, str, str]:
    llm, skip_reason, llm_raw = _llm_curate(
        db,
        user,
        pref,
        llm_shortlist(candidates, user, topics, pref),
        timings=timings,
        on_progress=on_progress,
        pool=pool,
    )
    if llm:
        title, _body, items = llm
        return items, title, "llm", "", llm_raw
    picked = _heuristic_pick(candidates, seed)
    curator = "heuristic" if picked else "static"
    labeled = _label_picked_items(picked or _static_fallback(topics, seed), pref, pool, topics)
    return labeled, "", curator, skip_reason, llm_raw


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
    candidates = personalize_candidates(raw, user, pref)
    crawl_ms = elapsed_ms(crawl_started)

    layer_ms: dict[str, int] = {"llm_ms": 0, "aggregation_ms": 0, "format_ms": 0}
    _emit(on_progress, "curate")
    from app.services.roles import assistant_name_for_role, parse_role_settings, parse_roles, topics_for_role

    roles = parse_roles(getattr(pref, "roles", "") or "")
    role_settings = parse_role_settings(getattr(pref, "role_settings", "") or "{}")
    agg_started = perf_counter()
    if len(roles) > 1:
        used_urls: set[str] = set()
        labeled: list[dict[str, str]] = []
        curator = "heuristic"
        skip_reason = ""
        llm_raw = ""
        for role in roles:
            role_topics = topics_for_role(topics, role) or topics
            role_pool = [item for item in filter_candidates_for_role(candidates, role) if item.url not in used_urls]
            if not role_pool:
                role_pool = [item for item in candidates if item.url not in used_urls] or list(candidates)
            focus = _PrefFocus(pref, topics=role_topics, roles=[role])
            role_items, llm_title, role_curator, skip, raw = _curate_three(
                db,
                user,
                focus,
                role_pool,
                role_topics,
                f"{seed}:{role}",
                layer_ms,
                on_progress,
                role_pool,
            )
            if role_curator == "llm":
                curator = "llm"
                if llm_title:
                    title = llm_title
            skip_reason = skip_reason or skip
            llm_raw = llm_raw or raw
            aide = assistant_name_for_role(role_settings, role)
            for row in role_items[:3]:
                row["assistant"] = aide
                row["role"] = role
                if row.get("url"):
                    used_urls.add(row["url"])
            labeled.extend(role_items[:3])
        aggregation_ms = elapsed_ms(agg_started)
        _emit(on_progress, "format")
        fmt_started = perf_counter()
        body = _format_body(name, labeled, pref, topics, reviewed_count=len(candidates), now=now)
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

    focus = _PrefFocus(pref, roles=roles) if roles else pref
    items, llm_title, curator, skip_reason, llm_raw = _curate_three(
        db,
        user,
        focus,
        llm_shortlist(candidates, user, topics, focus),
        topics,
        seed,
        layer_ms,
        on_progress,
        candidates,
    )
    if llm_title:
        title = llm_title
    if roles:
        aide = assistant_name_for_role(role_settings, roles[0])
        for row in items:
            row["assistant"] = aide
            row["role"] = roles[0]
    aggregation_ms = elapsed_ms(agg_started)
    _emit(on_progress, "format")
    fmt_started = perf_counter()
    body = _format_body(name, items, pref, topics, reviewed_count=len(candidates), now=now)
    format_ms = elapsed_ms(fmt_started)
    return DigestPreview(
        title=title,
        body=body,
        items=items,
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


def build_test_digest_preview(
    user: User,
    pref: Preference,
    on_progress: ProgressCb | None = None,
) -> DigestPreview:
    """Sample digest for desk test-send — no crawl, LLM, or CrawlRun."""
    topics = _topic_list(pref)
    sites = _source_list(pref)
    now = datetime.now(ZoneInfo(SEOUL))
    weekday = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
    seed = f"test:{user.id}:{now.date().isoformat()}:{','.join(topics)}"
    title = f"하루만장 · {now.month}/{now.day} ({weekday})"

    _emit(on_progress, "crawl")
    _emit(on_progress, "curate")
    from app.services.roles import assistant_name_for_role, parse_role_settings, parse_roles

    roles = parse_roles(getattr(pref, "roles", "") or "")
    role_settings = parse_role_settings(getattr(pref, "role_settings", "") or "{}")
    if len(roles) > 1:
        items: list[dict[str, str]] = []
        for index, role in enumerate(roles):
            batch = _label_picked_items(
                _static_fallback(topics, f"{seed}:{role}", offset=index * 3),
                pref,
                [],
                topics,
            )
            aide = assistant_name_for_role(role_settings, role)
            for row_i, row in enumerate(batch):
                row["assistant"] = aide
                row["role"] = role
                if row.get("url"):
                    row["url"] = f"{row['url']}#{role}-{row_i}"
            items.extend(batch)
    else:
        items = _label_picked_items(_static_fallback(topics, seed), pref, [], topics)
        if roles:
            aide = assistant_name_for_role(role_settings, roles[0])
            for row in items:
                row["assistant"] = aide
                row["role"] = roles[0]
    _emit(on_progress, "format")
    body = _with_test_notice(
        _format_body(
            user.display_name or "당신",
            items,
            pref,
            topics,
            reviewed_count=len(_CURATED),
            now=now,
        )
    )
    return DigestPreview(
        title=title,
        body=body,
        items=items,
        curator="test_static",
        llm_configured=False,
        llm_skip_reason="test_send",
        llm_raw="",
        candidates=[],
        topics=topics,
        sources=sites,
    )


def create_test_digest(
    db: Session,
    user: User,
    pref: Preference,
    *,
    status: str = "preview",
    on_progress: ProgressCb | None = None,
) -> Digest:
    if pref.timezone != SEOUL:
        pref.timezone = SEOUL
        db.add(pref)
    preview = build_test_digest_preview(user, pref, on_progress=on_progress)
    digest = Digest(
        user_id=user.id,
        title=preview.title,
        body=preview.body,
        status=status,
        delivery_channel="kakao_me",
        items_json=json.dumps(preview.items, ensure_ascii=False),
    )
    db.add(digest)
    db.commit()
    db.refresh(digest)
    return digest


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

