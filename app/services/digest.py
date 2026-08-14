"""Generate '오늘의 3' — three curated links (YouTube / article / community)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

from openai import OpenAI
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Digest, Preference, User

SEOUL = "Asia/Seoul"

_CURATED: list[dict[str, str]] = [
    {
        "kind": "아티클",
        "title": "미국 증시·금리, 오늘만 필요한 요약",
        "blurb": "장 흐름을 15분 안에.",
        "url": "https://www.youtube.com/results?search_query=us+stock+market+today",
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
        "title": "연애 초반 연락 템포, 과해석 줄이기",
        "blurb": "짧은 체크리스트.",
        "url": "https://www.youtube.com/results?search_query=%EC%97%B0%EC%95%A0+%EC%97%B0%EB%9D%BD",
        "hint": "연애",
    },
    {
        "kind": "아티클",
        "title": "생성형 AI를 일상에 붙이는 최소 루틴",
        "blurb": "도구 나열 대신 한 가지.",
        "url": "https://www.youtube.com/results?search_query=generative+ai+daily",
        "hint": "IT",
    },
    {
        "kind": "커뮤니티",
        "title": "이직·면접 질문 뼈대 모음",
        "blurb": "답변 골격만 챙기기.",
        "url": "https://www.reddit.com/r/cscareerquestions/",
        "hint": "커리어",
    },
    {
        "kind": "유튜브",
        "title": "바쁜 주의 수면·운동 최소선",
        "blurb": "완벽한 루틴 말고 버티는 루틴.",
        "url": "https://www.youtube.com/results?search_query=sleep+exercise+busy",
        "hint": "라이프",
    },
    {
        "kind": "아티클",
        "title": "환율·물가 한 장 브리핑",
        "blurb": "제목보다 흐름.",
        "url": "https://www.bok.or.kr/",
        "hint": "경제",
    },
    {
        "kind": "커뮤니티",
        "title": "이번 주 사회 이슈 팩트 링크",
        "blurb": "감정 댓글 거르기.",
        "url": "https://news.ycombinator.com/",
        "hint": "뉴스",
    },
    {
        "kind": "유튜브",
        "title": "비트코인·알트, 온체인만 짧게",
        "blurb": "공포·탐욕 뉴스 걸러 읽기.",
        "url": "https://www.youtube.com/results?search_query=bitcoin+onchain",
        "hint": "경제",
    },
]


def _topic_list(pref: Preference) -> list[str]:
    return [t.strip() for t in pref.topics.split(",") if t.strip()]


def _customization(pref: Preference) -> str:
    # notes = user customization prompt (tone field unused)
    return (pref.notes or "").strip()


def _pick_three(topics: list[str], seed: str) -> list[dict[str, str]]:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    start = int(digest[:8], 16) % len(_CURATED)
    ordered = _CURATED[start:] + _CURATED[:start]

    picked: list[dict[str, str]] = []
    topic_blob = " ".join(topics).lower()

    for item in ordered:
        if len(picked) >= 3:
            break
        hint = item["hint"].lower()
        if topics and hint and hint not in topic_blob:
            continue
        picked.append(item)

    if len(picked) < 3:
        for item in ordered:
            if item not in picked:
                picked.append(item)
            if len(picked) >= 3:
                break
    return picked[:3]


def _format_body(name: str, items: list[dict[str, str]], pref: Preference, topics: list[str]) -> str:
    lines: list[str] = [
        f"{name}님을 위한 오늘의 3 · 유튜브·아티클·커뮤니티",
        "",
    ]
    for i, item in enumerate(items, start=1):
        topic = topics[(i - 1) % len(topics)] if topics else item["hint"]
        lines.append(f"{i}) [{item['kind']}] {item['title']}")
        lines.append(f"   {item['blurb']}")
        lines.append(f"   주제: {topic.replace('/', ' · ')}")
        lines.append(f"   {item['url']}")
        lines.append("")
    custom = _customization(pref)
    if custom:
        lines.append(f"요청 반영: {custom}")
    lines.append("— 오늘의 3")
    return "\n".join(lines).strip()


def _template_digest(user: User, pref: Preference) -> tuple[str, str]:
    now = datetime.now(ZoneInfo(SEOUL))
    weekday = ["월", "화", "수", "목", "금", "토", "일"][now.weekday()]
    topics = _topic_list(pref)
    name = user.display_name or "당신"
    seed = f"{user.id}:{now.date().isoformat()}:{','.join(topics)}"
    items = _pick_three(topics, seed)

    title = f"오늘의 3 · {now.month}/{now.day} ({weekday})"
    body = _format_body(name, items, pref, topics)
    return title, body


def _llm_digest(user: User, pref: Preference) -> tuple[str, str] | None:
    settings = get_settings()
    if not settings.llm_api_key:
        return None

    topics = ", ".join(_topic_list(pref)) or "일반"
    custom = _customization(pref) or "(없음)"
    client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    prompt = (
        "너는 '오늘의 3' 큐레이터다. 카카오톡 '나에게 보내기'용 브리프를 한국어로 작성한다.\n"
        "유튜브·아티클·커뮤니티를 망라한 **읽을거리 3개만** 고른다.\n"
        "'오늘 한 줄'이나 '한 가지 행동'은 쓰지 않는다.\n"
        "형식:\n"
        "첫 줄: 제목 (예: 오늘의 3 · M/D (요일))\n"
        "본문: 1) 2) 3) 각각 [유튜브|아티클|커뮤니티] 제목 / 한 줄 설명 / 주제 / https:// URL\n"
        "사용자 커스터마이징 문장을 최우선으로 반영한다 (길이·제외·눈높이·매체 비중).\n"
        "최대 900자. 가짜 통계·이모지 남발 금지.\n"
        f"수신자: {user.display_name}\n"
        f"관심 주제(거대/대/소): {topics}\n"
        f"커스터마이징: {custom}\n"
    )
    try:
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {
                    "role": "system",
                    "content": "Curate exactly 3 linked reads for Kakao memo in Korean.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=800,
        )
        text = (resp.choices[0].message.content or "").strip()
        if not text:
            return None
        lines = text.splitlines()
        title = lines[0].lstrip("# ").strip()[:120]
        body = "\n".join(lines[1:]).strip() or text
        return title, body
    except Exception:
        return None


def generate_digest_content(user: User, pref: Preference) -> tuple[str, str]:
    return _llm_digest(user, pref) or _template_digest(user, pref)


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
    title, body = generate_digest_content(user, pref)
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
