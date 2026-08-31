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
from app.services.llm_policy import classify_llm_skip_reason, parse_json_object
from app.services.angles import ANGLE_ORDER, classify_angle, normalize_angle, order_items_by_angle, pick_three_by_angle
from app.services.crawl_log import persist_crawl_run
from app.services.pipeline_timing import elapsed_ms
from app.services.run_resources import peak_sampler
from app.services.greeting import digest_opening_line
from app.services.kakao import ASSISTANT_MEMO_BREAK
from app.services.financial_change import ai_change_comment, format_change_summary, load_financial_change
from app.services.pick_reason import KIND_WHY, clip_why, compose_pick_reason
from app.services.shortlist import shortlist_for_llm, topic_tokens
from app.services.sources import SourceItem, candidates_as_prompt_block, gather_candidates

SEOUL = "Asia/Seoul"
ProgressCb = Callable[[str], None]
TEST_DATA_NOTICE = "(본 내용은 테스트용 데이터입니다.)"


def _emit(on_progress: ProgressCb | None, step: str) -> None:
    if on_progress:
        on_progress(step)


def _apply_agent_enrichment(
    user: User,
    pref: Preference,
    items: list[dict[str, str]],
    body: str,
) -> tuple[list[dict[str, str]], str]:
    """Pipe-and-filter: never raise into crawl/send. Disabled in tests via env."""
    try:
        from app.services.agent_enrichment import enrich_digest_payload

        return enrich_digest_payload(user, pref, items, body)
    except Exception:
        return items, body

# Desk live-preview SAMPLE_POOL, in Kakao body tone (합니다/습니다).
_CURATED: list[dict[str, str]] = [
    {
        "kind": "아티클",
        "title": "이번 주 미국 증시, 금리 발언만 짚기",
        "blurb": "연준 발언 이후 나스닥이 반등했지만, 금리 인하 기대는 다시 뒤로 밀렸다는 분석입니다.",
        "url": "https://www.hankyung.com/",
        "hint": "경제",
        "why": "피드 1위·3시간 전",
        "insight_q": "금리가 주가에 미치는 영향이 궁금합니다.",
        "insight_url": "https://www.investing.com/economic-calendar/",
    },
    {
        "kind": "커뮤니티",
        "title": "국내주식 종목 토론 — 과열 vs 저평가",
        "blurb": "코스피 대형주 밸류에이션을 두고 이미 반영됐다는 쪽과 아직 싸다는 쪽이 갈립니다.",
        "url": "https://finance.naver.com/",
        "hint": "경제",
        "why": "DAU 250만·피드 2위",
        "insight_q": "공시·수급만으로 과열을 어떻게 가릴까요?",
        "insight_url": "https://dart.fss.or.kr/",
    },
    {
        "kind": "유튜브",
        "title": "연애 초반, 연락 텀 어떻게 두나요",
        "blurb": "초반 답장 속도와 만남 텀을 과해석하지 않는 법을 사례로 짚습니다.",
        "url": "https://www.youtube.com/results?search_query=%EC%97%B0%EC%95%A0+%EC%97%B0%EB%9D%BD",
        "hint": "연애",
        "why": "조회 2.4만·5시간 전",
        "insight_q": "연락 텀을 과해석하지 않으려면 무엇을 볼까요?",
        "insight_url": "https://brunch.co.kr/",
    },
    {
        "kind": "아티클",
        "title": "생성형 AI, 일상에 붙이는 최소 루틴",
        "blurb": "도구를 늘리기보다 아침 한 번의 프롬프트로 일정·메모를 정리하는 습관입니다.",
        "url": "https://news.ycombinator.com/",
        "hint": "IT",
        "why": "점수 214·피드 1위",
        "insight_q": "생성형 AI가 업무 비용에 미치는 영향은 무엇일까요?",
        "insight_url": "https://news.ycombinator.com/",
    },
    {
        "kind": "커뮤니티",
        "title": "이직 면접에서 자주 나오는 질문 모음",
        "blurb": "왜 옮기나요, 갈등 경험, 최근 실패처럼 반복되는 질문의 답변 뼈대를 모았습니다.",
        "url": "https://www.wanted.co.kr/",
        "hint": "커리어",
        "why": "피드 3위·오늘",
        "insight_q": "면접관이 이 질문에서 실제로 보려는 포인트는 무엇일까요?",
        "insight_url": "https://www.wanted.co.kr/",
    },
    {
        "kind": "유튜브",
        "title": "수면·운동, 바쁜 주에 지키는 최소선",
        "blurb": "완벽한 루틴 대신 취침 고정과 20분 걷기만 지키는 주간 실험입니다.",
        "url": "https://www.youtube.com/results?search_query=%EC%88%98%EB%A9%B4+%EC%9A%B4%EB%8F%99",
        "hint": "라이프",
        "why": "조회 2.4만·5시간 전",
        "insight_q": "수면 부채가 집중력에 미치는 영향은 무엇일까요?",
        "insight_url": "https://www.youtube.com/results?search_query=%EC%88%98%EB%A9%B4+%EB%B6%80%EC%B1%84",
    },
    {
        "kind": "아티클",
        "title": "환율·물가 한 장 브리핑",
        "blurb": "달러·원 환율이 다시 들썩인 배경과 수입 물가 파급만 추렸습니다.",
        "url": "https://www.bok.or.kr/",
        "hint": "경제",
        "why": "피드 1위·2시간 전",
        "insight_q": "환율 변동이 수입 물가에 미치는 영향은 무엇일까요?",
        "insight_url": "https://www.investing.com/currencies/usd-krw",
    },
    {
        "kind": "커뮤니티",
        "title": "이번 주 사회 이슈, 팩트만 모음",
        "blurb": "원문·타임라인·수치 링크만으로 이슈를 재구성한 스레드입니다.",
        "url": "https://news.naver.com/",
        "hint": "뉴스",
        "why": "DAU 1800만·피드 1위",
        "insight_q": "이 이슈에서 확인된 사실과 추정은 어떻게 나눌까요?",
        "insight_url": "https://news.naver.com/",
    },
    {
        "kind": "유튜브",
        "title": "React 19, 실무에서 바뀌는 지점만",
        "blurb": "컴파일러·액션·폼 패턴이 기존 코드에 어떻게 붙는지 오늘 건드릴 파일 위주로 설명합니다.",
        "url": "https://github.com/trending",
        "hint": "IT",
        "why": "피드 1위·오늘",
        "insight_q": "React 19 도입이 기존 번들·폼 코드에 미치는 영향은 무엇일까요?",
        "insight_url": "https://github.com/trending",
    },
    {
        "kind": "아티클",
        "title": "연봉 협상, 숫자부터 준비하기",
        "blurb": "시장 밴드·현재 총보상·이직 비용을 표로 맞춰 두고 말하는 법입니다.",
        "url": "https://www.levels.fyi/",
        "hint": "커리어",
        "why": "피드 3위·오늘",
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
        occ = "(none)"
    band = age_band(getattr(user, "birth_date", None), today=today) or "(none)"
    base = (
        f"Nickname: {(user.display_name or '').strip() or '(none)'}\n"
        f"Job: {occ}\n"
        f"Age band: {band}\n"
        "Prefer items that actually help this job/age. "
        "Kakao-visible title/blurb/insight_q must be Korean 합니다/습니다 and follow the hired persona. "
        "Never write birth year or exact age. Prefer Korean articles and Korean videos."
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
    topic_parts = topic_tokens(_topic_list(pref)) if pref is not None else []
    if not candidates:
        return candidates

    def occupation_hits(item: SourceItem) -> int:
        if not tokens:
            return 0
        blob = f"{item.title} {item.summary} {item.source}".lower()
        return sum(1 for token in tokens if token.lower() in blob)

    def topic_hits_of(item: SourceItem) -> int:
        if not topic_parts:
            return 0
        blob = f"{item.title} {item.summary} {item.source}".lower()
        return sum(1 for token in topic_parts if token.lower() in blob)

    def score(item: SourceItem) -> tuple[int, int]:
        return (occupation_hits(item), korean_content_score(item))

    ranked = sorted(candidates, key=score, reverse=True)
    return [
        replace(
            item,
            job_hits=occupation_hits(item),
            topic_hits=topic_hits_of(item),
            pick_reason=compose_pick_reason(
                item,
                job_hits=occupation_hits(item),
                topic_hits=topic_hits_of(item),
            ),
        )
        for item in ranked
    ]


def llm_shortlist(
    candidates: list[SourceItem],
    user: User,
    topics: list[str],
    pref: Preference | None = None,
    *,
    role: str | None = None,
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
        role=role,
    )


def _wants_insights(pref: Preference) -> bool:
    return bool(getattr(pref, "insight_questions", False))


def _is_match_stock(item: dict[str, str]) -> bool:
    from app.services.stock_match import MATCH_STOCK_KIND

    return (item.get("kind") or "") == MATCH_STOCK_KIND or (item.get("hint") or "") == "match_stock"


def _wants_match_stock(pref: Preference, *, role: str | None = None) -> bool:
    from app.services.roles import parse_roles

    if role is not None:
        return role == "stock_analyst"
    roles = parse_roles(getattr(pref, "roles", "") or "")
    return "stock_analyst" in roles


def _attach_match_stock(
    items: list[dict[str, str]],
    pref: Preference,
    raw_match: object = None,
    *,
    role: str | None = None,
) -> list[dict[str, str]]:
    from app.services.roles import parse_role_settings
    from app.services.stock_match import heuristic_match_stock, parse_match_stock

    articles = [item for item in items if not _is_match_stock(item)]
    if not _wants_match_stock(pref, role=role):
        return articles
    settings = parse_role_settings(getattr(pref, "role_settings", "") or "{}")
    market = str(settings.get("investor_market") or "국내증시")
    picked = parse_match_stock(raw_match) or heuristic_match_stock(articles, market=market)
    if not picked:
        return articles
    first = articles[0] if articles else {}
    if first.get("assistant"):
        picked["assistant"] = first["assistant"]
    if first.get("role"):
        picked["role"] = first["role"]
    return articles + [picked]


def _role_send_items(items: list[dict[str, str]], *, role: str | None = None) -> list[dict[str, str]]:
    articles = [item for item in items if not _is_match_stock(item)][:3]
    stocks = [item for item in items if _is_match_stock(item)][:1]
    if role == "stock_analyst":
        return stocks
    return articles


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
    from app.services.domain_gate import topic_site_id
    from app.services.roles import ROLE_TOPIC_MEGAS

    wanted = set()
    for mega in ROLE_TOPIC_MEGAS.get(role, []):
        wanted.update(MEGA_TO_GROUPS.get(mega, []))
    ids: set[str] = set()
    for group in catalog_groups():
        if group["id"] in wanted:
            ids.update(site["id"] for site in group["sites"])
    for mega in ROLE_TOPIC_MEGAS.get(role, []):
        stamp = topic_site_id(mega)
        if stamp:
            ids.add(stamp)
    return ids


def sources_for_role(role: str, user_sources: list[str] | None = None) -> set[str]:
    """Catalog sites for the role's mega topics, optionally limited to user picks."""
    from app.services.domain_gate import topic_site_ids_for_roles

    allowed = site_ids_for_role(role)
    topic_ids = topic_site_ids_for_roles([role])
    if not user_sources:
        return allowed
    catalog_picked = {site for site in user_sources if site in allowed and site not in topic_ids}
    if catalog_picked:
        return catalog_picked | topic_ids
    return allowed


def filter_candidates_for_role(
    candidates: list[SourceItem],
    role: str,
    *,
    user_sources: list[str] | None = None,
    role_topics: list[str] | None = None,
) -> list[SourceItem]:
    from app.services.domain_gate import item_passes_role_domain, topic_site_ids_for_roles

    _ = role_topics  # topics shape the crawl; domain gate enforces desk fit here
    allowed_sites = sources_for_role(role, user_sources)
    topic_ids = topic_site_ids_for_roles([role])
    trusted = site_ids_for_role(role) - topic_ids
    matched: list[SourceItem] = []
    for item in candidates:
        sid = (item.site_id or "").strip()
        if sid:
            if sid not in allowed_sites:
                continue
        elif not item_passes_role_domain(item, role, trusted_sites=set()):
            continue
        if not item_passes_role_domain(item, role, trusted_sites=trusted):
            continue
        matched.append(item)
    return matched


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


def _format_match_stock_lines(item: dict[str, str]) -> list[str]:
    lines = [_ITEM_RULE, "", "오늘의 종목 · 매수 추천이 아닙니다"]
    title = (item.get("title") or "").strip()
    if title:
        lines.append(title)
    blurb = _lead_sentence(item.get("blurb") or "")
    if blurb:
        lines.append(blurb)
    financial_change = (item.get("financial_change") or "").strip()
    financial_analysis = (item.get("financial_analysis") or "").strip()
    if financial_change:
        lines.append(financial_change)
        if financial_analysis:
            lines.append(f"도윤의 해석: {financial_analysis}")
    url = (item.get("url") or "").strip()
    if url:
        lines.append(f"재무제표: {url}")
    lines.append("")
    return lines


def _split_articles_and_stock(section: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    articles = [item for item in section if not _is_match_stock(item)]
    stocks = [item for item in section if _is_match_stock(item)]
    return articles, stocks


def _section_intro(articles: list[dict[str, str]], stocks: list[dict[str, str]], reviewed_count: int, *, empty_picked_min: int) -> str:
    if stocks and not articles:
        return "오늘 시장에서 종목 1개를 골랐습니다. 매수 추천이 아닙니다."
    if not articles and not stocks:
        return "오늘 조건에 맞는 자료를 찾지 못했습니다."
    picked = len(articles) if articles else empty_picked_min
    return f"오늘 {reviewed_count}개 중에 고른 {picked}개입니다."


def _format_item_lines(item: dict[str, str], index: int, *, show_insight: bool) -> list[str]:
    lines: list[str] = []
    if index:
        lines.append(_ITEM_RULE)
        lines.append("")
    ordinal = _ORDINALS[index] if index < len(_ORDINALS) else f"{index + 1}"
    kind = item.get("kind") or "아티클"
    emoji = _kind_emoji(kind)
    title = (item.get("title") or "").strip()
    angle = (item.get("angle") or "").strip()
    headline = f"{angle} · {title}" if angle and title else title
    lines.append(f"{ordinal}. {emoji} {headline}")
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


def _format_assistant_section(
    name: str,
    assistant: str,
    section: list[dict[str, str]],
    pref: Preference,
    reviewed_count: int,
    when: datetime,
    *,
    include_custom: bool,
    empty_picked_min: int,
) -> str:
    show_insight = _wants_insights(pref)
    articles, stocks = _split_articles_and_stock(section)
    lines = [
        digest_opening_line(name, assistant, now=when),
        _section_intro(articles, stocks, reviewed_count, empty_picked_min=empty_picked_min),
        "",
    ]
    for i, item in enumerate(articles):
        lines.extend(_format_item_lines(item, i, show_insight=show_insight))
    for stock in stocks:
        lines.extend(_format_match_stock_lines(stock))
    if include_custom:
        custom = _customization(pref)
        if custom:
            lines.append(f"요청: {custom}")
    return "\n".join(lines).strip()


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
    from app.services.roles import parse_role_settings, parse_roles, pick_digest_assistant

    roles = parse_roles(getattr(pref, "roles", "") or "")
    settings = parse_role_settings(getattr(pref, "role_settings", "") or "{}")
    local = when.astimezone(ZoneInfo(SEOUL)) if when.tzinfo else when.replace(tzinfo=ZoneInfo(SEOUL))
    groups = _assistant_groups(items)
    named = [group for group in groups if group[0]]

    if len(named) > 1:
        parts: list[str] = []
        last = len(groups) - 1
        for gi, (assistant, section) in enumerate(groups):
            aide = assistant or pick_digest_assistant(
                roles, settings, day=local.timetuple().tm_yday, hour=local.hour
            )
            parts.append(
                _format_assistant_section(
                    name,
                    aide,
                    section,
                    pref,
                    n,
                    when,
                    include_custom=gi == last,
                    empty_picked_min=0,
                )
            )
        return ASSISTANT_MEMO_BREAK.join(parts)

    assistant = named[0][0] if named else pick_digest_assistant(
        roles, settings, day=local.timetuple().tm_yday, hour=local.hour
    )
    return _format_assistant_section(
        name,
        assistant,
        items,
        pref,
        n,
        when,
        include_custom=True,
        empty_picked_min=1,
    )


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
    url = item.get("url") or ""
    match = next((c for c in candidates if c.url == url), None)
    if match:
        return clip_why(compose_pick_reason(match))
    existing = clip_why(item.get("why") or "")
    if existing and any(ch.isdigit() for ch in existing):
        return existing
    return clip_why(KIND_WHY.get(item.get("kind") or "", "48시간 이내"))


def _attach_why(items: list[dict[str, str]], candidates: list[SourceItem]) -> list[dict[str, str]]:
    return [{**item, "why": _why_for_item(item, candidates)} for item in items]


def _heuristic_pick(candidates: list[SourceItem], seed: str) -> list[dict[str, str]]:
    """Pick up to 3 items: 흐름 · 이슈 · 인물, then unused kinds if a bucket is empty."""
    if not candidates:
        return []
    _ = seed
    return [
        {
            "kind": p.kind,
            "title": p.title,
            "blurb": _summary_blurb(p),
            "url": p.url,
            "hint": p.source,
            "angle": ang,
            "why": clip_why(compose_pick_reason(p)),
        }
        for ang, p in pick_three_by_angle(candidates)
    ]


def _summary_blurb(item: SourceItem) -> str:
    summary = (item.summary or "").strip()
    if summary and summary.casefold() != item.source.casefold():
        return summary[:220]
    return f"{item.title}. {item.source}에서 가져온 핵심만 짧게 남겼습니다."[:220]


_ROLE_TEST_CURATED: dict[str, list[dict[str, str]]] = {
    "semiconductor": [
        {
            "kind": "아티클",
            "title": "HBM 패키징과 고대역폭 메모리 동향",
            "blurb": "메모리 적층과 패키징 기술이 대역폭과 수율에 미치는 영향을 짚습니다.",
            "url": "https://www.iedm.org/",
            "hint": "반도체",
        },
        {
            "kind": "아티클",
            "title": "GAA 공정의 전력·성능·수율 균형",
            "blurb": "게이트올어라운드 구조에서 전력과 수율을 함께 보는 기준을 정리합니다.",
            "url": "https://www.spie.org/",
            "hint": "반도체",
        },
        {
            "kind": "아티클",
            "title": "첨단 노드 검증에서 보는 변동성",
            "blurb": "미세 공정 검증에서 공정 편차와 설계 여유를 함께 확인하는 관점입니다.",
            "url": "https://ieeexplore.ieee.org/",
            "hint": "반도체",
        },
    ],
}


def _static_fallback(
    topics: list[str],
    seed: str,
    *,
    offset: int = 0,
    count: int = 3,
    role: str | None = None,
) -> list[dict[str, str]]:
    curated = _ROLE_TEST_CURATED.get(role or "", _CURATED)
    start = (int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) + offset) % len(curated)
    ordered = curated[start:] + curated[:start]
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
            "angle": ANGLE_ORDER[i % len(ANGLE_ORDER)],
        }
        for i, x in enumerate(ordered[:count])
    ]


def _with_test_notice(body: str) -> str:
    if "\x1e" in (body or ""):
        return "\x1e".join(_with_test_notice(part) for part in body.split("\x1e"))
    if TEST_DATA_NOTICE in body:
        return body
    lines = body.split("\n")
    if not lines:
        return TEST_DATA_NOTICE
    rest = lines[1:]
    while rest and rest[0] == "":
        rest = rest[1:]
    return "\n".join([lines[0], "", TEST_DATA_NOTICE, ""] + rest).strip()


def _role_curate_rules(role: str | None) -> str:
    """Extra LLM instructions for a hired desk role. Empty for generic briefs."""
    if role == "developer":
        return (
            "Developer desk rules (override generic angle hints when they conflict):\n"
            "Priority #1 is the latest tech trend / shipping technology — not evergreen tutorials.\n"
            "Prefer verified sources: official eng blogs, HN, GeekNews, InfoQ, Naver D2, GitHub trending, "
            "reputable company tech blogs. Deprioritize rumor-only community posts unless they cite a primary source.\n"
            "Angles for this desk:\n"
            "1) 흐름 = the week's field-wide tech trend (new stack, paradigm, adoption wave).\n"
            "2) 이슈 = one concrete release / CVE / migration / breaking change tied to that trend.\n"
            "3) 인물 = an engineer/exec commenting on that same trend, OR a hiring/move of someone tied to that tech. "
            "Reject celebrity bios and founder lore unrelated to current tech.\n"
            "Reject stock-tape, dating, entertainment, and off-domain lifestyle pieces.\n"
        )
    if role in {"investor", "stock_analyst"}:
        return (
            "Retail / market desk rules (hard domain lock):\n"
            "ONLY today's equity / macro market content: 시황, 수급, 지수, 환율·금리, 실적·공시, theme-stock moves.\n"
            "Reject chip-process papers, software tutorials, dating, K-pop, webtoon, and non-market lifestyle.\n"
            "If a theme is 반도체, cover it as a market theme (수급·실적·관련주), never as fab/process engineering.\n"
            "Angles: 흐름 = market-wide tape; 이슈 = one concrete market event; 인물 = investor/analyst voice on that tape.\n"
        )
    if role == "semiconductor":
        return (
            "Semiconductor device/design/verification desk rules (hard domain lock):\n"
            "ONLY process / device / circuit / EDA / yield / packaging technology trends "
            "(EUV, GAA, CFET, HBM process, foundry nodes, ISSCC/IEDM-class topics).\n"
            "Reject stock targets, buy/sell calls, Kospi tape, dating, K-pop, general startup fluff, "
            "and software-only stories with no silicon angle.\n"
            "Angles: 흐름 = field-wide process/device trend; 이슈 = one concrete tech event; "
            "인물 = engineer/researcher tied to that tech.\n"
        )
    if role == "doctor":
        return (
            "Clinical desk rules: ONLY medicine / guideline / clinical evidence. "
            "Reject markets, dating, and entertainment.\n"
        )
    if role == "job_seeker":
        return (
            "Hiring desk rules: ONLY jobs / hiring / career prep. Reject markets and entertainment fluff.\n"
        )
    if role in {"music", "reader", "movie", "otaku", "gaming", "performing_arts"}:
        return (
            f"Culture desk ({role}) rules: stay inside this desk's medium only. "
            "Reject stock markets, chip process papers, and unrelated lifestyle.\n"
        )
    return ""


def _llm_curate(
    db: Session,
    user: User,
    pref: Preference,
    candidates: list[SourceItem],
    timings: dict[str, int] | None = None,
    on_progress: ProgressCb | None = None,
    *,
    pool: list[SourceItem] | None = None,
    feedback: str = "",
    role: str | None = None,
) -> tuple[tuple[str, str, list[dict[str, str]]] | None, str, str]:
    settings = get_settings()
    if not settings.llm_configured:
        return None, "llm_not_configured", ""
    if not candidates:
        return None, "no_candidates", ""
    reviewed = pool if pool is not None else candidates

    topics = ", ".join(_topic_list(pref)) or "일반"
    custom = _customization(pref) or "(none)"
    insight_on = _wants_insights(pref)
    insight_rules = ""
    insight_json = ""
    if insight_on:
        insight_rules = (
            "Each item: one insight_q and one insight_url. "
            "insight_q is one Korean curiosity sentence that follows from the piece "
            "(e.g. rates → '금리가 주가에 미치는 영향이 궁금합니다'). "
            "insight_url is a helper link, preferably another candidate URL, else a search URL.\n"
        )
        insight_json = ',"insight_q":"...","insight_url":"https://..."'
    retry_block = ""
    if feedback.strip():
        retry_block = (
            "Prior picks failed the diversity check. Pick 3 again from the same candidates.\n"
            f"{feedback.strip()}\n"
            "No 3 from one site. One 흐름, one 이슈, one 인물.\n"
        )
    role_rules = _role_curate_rules(role)
    prompt = (
        "You curate Harumunjang Kakao briefs. Pick exactly 3 items from the candidate list only.\n"
        "Audience: Korean users. Prefer Korean titles/summaries and Korean YouTube channels. "
        "Use English-only items only if Korean candidates are scarce.\n"
        "Use candidate URLs only. Do not invent URLs.\n"
        "Mix 유튜브/아티클/커뮤니티 when possible.\n"
        "Three distinct angles, in this order: "
        "1) 흐름 = field-wide trend. "
        "2) 이슈 = one concrete hot issue. "
        "3) 인물 = notable person/interview. "
        "item.angle must be 흐름|이슈|인물.\n"
        "Do not pick 3 from the same site or only one high-DAU outlet.\n"
        f"{role_rules}"
        f"{retry_block}"
        "title = short headline. blurb = one lead sentence under the title (not two+). "
        "No meta like '15-min video'. Rewrite candidate summary as blurb when present, else summarize the title.\n"
        "Kakao-visible Korean (title, blurb, insight_q): polite 합니다/습니다. "
        "No -다 diary form or banmal unless customization asks otherwise. "
        "Let the assistant persona shape word choice.\n"
        "Candidate why/metrics are real signals (views, score, comments, age, feed rank). "
        "Do not invent views, likes, or authors.\n"
        f"{insight_rules}"
        "JSON object only. No markdown. Keep each title ≤ 40 chars and each blurb one short Korean sentence.\n"
        '{"title":"하루만장 · M/D (요일)","items":[{"kind":"유튜브|아티클|커뮤니티","title":"...","blurb":"...","url":"https://...","angle":"흐름|이슈|인물"'
        f"{insight_json}"
        "}]"
        "}\n"
        f"Recipient: {user.display_name}\n"
        f"{profile_brief(user, pref)}\n"
        f"Topics: {topics}\n"
        f"Customization: {custom}\n"
        f"Candidates:\n{candidates_as_prompt_block(candidates)}\n"
    )
    llm_started = perf_counter()
    messages = [
        {"role": "system", "content": "Return only valid JSON for three curated links."},
        {"role": "user", "content": prompt},
    ]
    max_tokens = 700 if insight_on else 500
    text, usage = llm_service.chat_completion(
        db,
        user_id=user.id,
        purpose="digest_curate",
        messages=messages,
        temperature=0.4,
        max_tokens=max_tokens,
    )
    data = parse_json_object(text) if text else None
    # Local Qwen often returns success=True with broken JSON; fall back to Groq once.
    used_local = usage is not None and (getattr(usage, "provider", "") or "") == "ollama"
    if data is None and used_local and settings.remote_llm_ready:
        text2, usage2 = llm_service.chat_completion(
            db,
            user_id=user.id,
            purpose="digest_curate",
            messages=messages,
            temperature=0.4,
            max_tokens=max_tokens,
            force_remote=True,
        )
        if text2:
            text, usage = text2, usage2
            data = parse_json_object(text)
    if timings is not None:
        timings["llm_ms"] = int(timings.get("llm_ms") or 0) + elapsed_ms(llm_started)
    if not text:
        return None, classify_llm_skip_reason(usage), ""
    try:
        agg_started = perf_counter()
        if data is None:
            return None, "invalid_json", text[:2000]
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
            guessed = normalize_angle(str(raw.get("angle") or "")) or classify_angle(row["title"], row["blurb"])
            row["angle"] = guessed or ANGLE_ORDER[len(cleaned)]
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
        cleaned = order_items_by_angle(cleaned)
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
    *,
    role: str | None = None,
) -> list[dict[str, str]]:
    items = _attach_match_stock(
        _attach_why(_attach_insights(picked, pref=pref, candidates=candidates), candidates),
        pref,
        role=role,
    )
    labeled: list[dict[str, str]] = []
    for i, item in enumerate(items):
        row = dict(item)
        topic = topics[i % len(topics)] if topics else item.get("hint", "")
        row["topic"] = topic.replace("/", " · ") if topic else ""
        labeled.append(row)
    return labeled


def _should_run_critique() -> bool:
    from app.services.pipeline_flags import critique_enabled

    return critique_enabled()


def _critique_picks(
    db: Session,
    user: User,
    pref: Preference,
    items: list[dict[str, str]],
    candidates: list[SourceItem],
    timings: dict[str, int],
) -> object:
    from app.services.curate_agent import CRITIQUE_PURPOSE, build_critique_prompt, parse_critique

    if not get_settings().llm_configured or not items:
        return None
    started = perf_counter()
    text, _usage = llm_service.chat_completion(
        db,
        user_id=user.id,
        purpose=CRITIQUE_PURPOSE,
        messages=[
            {"role": "system", "content": "Return only valid JSON for a diversity critique."},
            {
                "role": "user",
                "content": build_critique_prompt(
                    items=items,
                    candidate_block=candidates_as_prompt_block(candidates),
                    profile=profile_brief(user, pref),
                    topics=", ".join(_topic_list(pref)) or "일반",
                ),
            },
        ],
        temperature=0.2,
        max_tokens=350,
    )
    timings["llm_ms"] = int(timings.get("llm_ms") or 0) + elapsed_ms(started)
    return parse_critique(text or "")


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
    *,
    role: str | None = None,
) -> tuple[list[dict[str, str]], str, str, str, str]:
    from app.services.curate_agent import should_retry

    shortlist = llm_shortlist(candidates, user, topics, pref, role=role)
    llm, skip_reason, llm_raw = _llm_curate(
        db,
        user,
        pref,
        shortlist,
        timings=timings,
        on_progress=on_progress,
        pool=pool,
        role=role,
    )
    if llm:
        title, _body, items = llm
        if _should_run_critique():
            critique = _critique_picks(db, user, pref, items, shortlist or candidates, timings)
            if should_retry(critique, 0):
                advice = getattr(critique, "advice", "") or getattr(critique, "reason", "")
                llm2, skip2, raw2 = _llm_curate(
                    db,
                    user,
                    pref,
                    shortlist,
                    timings=timings,
                    on_progress=on_progress,
                    pool=pool,
                    feedback=str(advice),
                    role=role,
                )
                if llm2:
                    title, _body, items = llm2
                    llm_raw = raw2
                skip_reason = skip_reason or skip2
        return items, title, "llm", "", llm_raw
    picked = _heuristic_pick(candidates, seed)
    curator = "heuristic" if picked else "static"
    if role and not picked:
        return [], "", curator, skip_reason, llm_raw
    fallback = picked or _static_fallback(topics, seed, role=role)
    labeled = _label_picked_items(fallback, pref, pool, topics, role=role)
    return labeled, "", curator, skip_reason, llm_raw


def _candidate_dicts(candidates: list[SourceItem]) -> list[dict[str, str]]:
    return [
        {
            "title": item.title or "",
            "blurb": item.summary or "",
            "summary": item.summary or "",
            "source": item.source or "",
            "site_id": getattr(item, "site_id", "") or "",
            "url": item.url or "",
        }
        for item in candidates
    ]


def _llm_curate_stock(
    db: Session,
    user: User,
    pref: Preference,
    candidates: list[SourceItem],
    timings: dict[str, int],
    on_progress: ProgressCb | None,
) -> tuple[object | None, str, str]:
    settings = get_settings()
    if not settings.llm_configured:
        return None, "llm_not_configured", ""
    topics = ", ".join(_topic_list(pref)) or "경제"
    custom = _customization(pref) or "(none)"
    prompt = (
        "You are the Harumunjang stock analyst (도윤). Pick exactly ONE listed stock for today's tape "
        "from the candidate evidence. Do not pick 3 articles.\n"
        "Hard rules:\n"
        "1) Prefer names backed by clear market signals in the candidates: "
        "외국인/기관 순매수·순매도, 거래대금·거래량 상위, 시간외·장후 급등, 공시·수주·실적, 특징주.\n"
        "2) Prefer candidates from finance portals (네이버 증권, 한경, 매경, 토스, Yahoo, DART).\n"
        "3) Do NOT default to 삼성전자 unless it is the day's clearest signal in the candidates.\n"
        "4) Not a buy/invest call. ticker = KR 6-digit or US symbol. Omit financials_url.\n"
        "5) why must cite the concrete signal in one short Korean 합니다/습니다 sentence.\n"
        "JSON only:\n"
        '{"match_stock":{"name":"SK하이닉스","ticker":"000660","market":"KR","why":"외국인 순매수와 거래대금 상위 소식에 가장 분명합니다."}}\n'
        f"Recipient: {user.display_name}\n"
        f"{profile_brief(user, pref)}\n"
        f"Topics: {topics}\n"
        f"Customization: {custom}\n"
        f"Candidates:\n{candidates_as_prompt_block(candidates) if candidates else '(none)'}\n"
    )
    _emit(on_progress, "curate")
    llm_started = perf_counter()
    text, usage = llm_service.chat_completion(
        db,
        user_id=user.id,
        purpose="digest_curate",
        messages=[
            {
                "role": "system",
                "content": (
                    "Return only valid JSON for one listed stock pick grounded in candidate signals. "
                    "Never invent tickers. Avoid Samsung unless clearly the top signal."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.35,
        max_tokens=400,
    )
    timings["llm_ms"] = int(timings.get("llm_ms") or 0) + elapsed_ms(llm_started)
    if not text:
        return None, classify_llm_skip_reason(usage), ""
    try:
        data = parse_json_object(text)
        if data is None:
            return None, "invalid_json", text[:2000]
        return data.get("match_stock"), "", text[:2000]
    except Exception:
        return None, "invalid_json", (text or "")[:2000]


def _curate_stock_pick(
    db: Session,
    user: User,
    pref: Preference,
    candidates: list[SourceItem],
    timings: dict[str, int],
    on_progress: ProgressCb | None,
) -> tuple[list[dict[str, str]], str, str, str, str]:
    from app.services.roles import parse_role_settings
    from app.services.stock_match import heuristic_match_stock, parse_match_stock

    settings = parse_role_settings(getattr(pref, "role_settings", "") or "{}")
    market = str(settings.get("investor_market") or "국내증시")
    # Prefer finance-portal / signal-heavy rows for the model context.
    ordered = sorted(
        candidates,
        key=lambda item: (
            0
            if any(
                token in f"{item.source} {item.site_id} {item.title} {item.summary}".lower()
                for token in (
                    "외국인",
                    "거래대금",
                    "거래량",
                    "시간외",
                    "순매수",
                    "특징주",
                    "naver",
                    "네이버",
                    "한경",
                    "매경",
                    "dart",
                )
            )
            else 1,
            item.list_rank or 99,
        ),
    )
    focus = ordered[:24] if ordered else candidates
    raw_match, skip_reason, llm_raw = _llm_curate_stock(
        db, user, pref, focus, timings, on_progress
    )
    picked = parse_match_stock(raw_match)
    curator = "llm"
    if not picked:
        picked = heuristic_match_stock(_candidate_dicts(candidates), market=market)
        curator = "heuristic"
    if picked:
        try:
            change = load_financial_change(picked.get("ticker") or "")
            if change is None:
                picked["financial_change"] = "재무 변화: 비교 재무 데이터가 없습니다."
            else:
                picked["financial_change"] = format_change_summary(change)
                picked["financial_analysis"], analysis_ms = ai_change_comment(
                    db,
                    user_id=user.id,
                    change=change,
                )
                timings["llm_ms"] = int(timings.get("llm_ms") or 0) + analysis_ms
        except Exception:
            picked["financial_change"] = "재무 변화: 비교 재무 데이터를 확인하지 못했습니다."
    return [picked], "", curator, skip_reason, llm_raw


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
    _emit(on_progress, "preprocess")
    candidates = personalize_candidates(raw, user, pref)
    crawl_ms = elapsed_ms(crawl_started)

    layer_ms: dict[str, int] = {"llm_ms": 0, "aggregation_ms": 0, "format_ms": 0}
    _emit(on_progress, "curate")
    from app.services.roles import (
        assistant_name_for_role,
        default_topics_for_role,
        parse_role_settings,
        parse_roles,
        topics_for_role,
    )

    roles = parse_roles(getattr(pref, "roles", "") or "")
    role_settings = parse_role_settings(getattr(pref, "role_settings", "") or "{}")
    agg_started = perf_counter()
    if roles:
        used_urls: set[str] = set()
        labeled: list[dict[str, str]] = []
        curator = "heuristic"
        skip_reason = ""
        llm_raw = ""
        for role in roles:
            role_topics = topics_for_role(topics, role) or default_topics_for_role(role, role_settings)
            role_pool = [
                item
                for item in filter_candidates_for_role(
                    candidates,
                    role,
                    user_sources=sites,
                    role_topics=role_topics,
                )
                if item.url not in used_urls
            ]
            focus = _PrefFocus(pref, topics=role_topics, roles=[role])
            if role == "stock_analyst":
                role_items, llm_title, role_curator, skip, raw = _curate_stock_pick(
                    db,
                    user,
                    focus,
                    role_pool,
                    layer_ms,
                    on_progress,
                )
            else:
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
                    role=role,
                )
            if role_curator == "llm":
                curator = "llm"
                if llm_title:
                    title = llm_title
            skip_reason = skip_reason or skip
            llm_raw = llm_raw or raw
            aide = assistant_name_for_role(role_settings, role)
            bundle = _role_send_items(role_items, role=role)
            for row in bundle:
                row["assistant"] = aide
                row["role"] = role
                if row.get("url") and not _is_match_stock(row):
                    used_urls.add(row["url"])
            labeled.extend(bundle)
        aggregation_ms = elapsed_ms(agg_started)
        _emit(on_progress, "format")
        fmt_started = perf_counter()
        body = _format_body(name, labeled, pref, topics, reviewed_count=len(candidates), now=now)
        format_only_ms = elapsed_ms(fmt_started)
        _emit(on_progress, "enrich")
        enrich_started = perf_counter()
        labeled, body = _apply_agent_enrichment(user, pref, labeled, body)
        enrich_ms = elapsed_ms(enrich_started)
        format_ms = format_only_ms + enrich_ms
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
    format_only_ms = elapsed_ms(fmt_started)
    _emit(on_progress, "enrich")
    enrich_started = perf_counter()
    items, body = _apply_agent_enrichment(user, pref, items, body)
    enrich_ms = elapsed_ms(enrich_started)
    format_ms = format_only_ms + enrich_ms
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
            batch = _role_send_items(
                _label_picked_items(
                    _static_fallback(topics, f"{seed}:{role}", offset=index * 3, role=role),
                    pref,
                    [],
                    topics,
                    role=role,
                ),
                role=role,
            )
            aide = assistant_name_for_role(role_settings, role)
            for row_i, row in enumerate(batch):
                row["assistant"] = aide
                row["role"] = role
                if row.get("url"):
                    row["url"] = f"{row['url']}#{role}-{row_i}"
            items.extend(batch)
    else:
        role = roles[0] if roles else None
        items = _role_send_items(
            _label_picked_items(
                _static_fallback(topics, seed, role=role),
                pref,
                [],
                topics,
                role=role,
            ),
            role=role,
        )
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
        db.commit()
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

