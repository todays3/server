"""Desk role catalog — mirrors front entities/role for curation prompts."""

from __future__ import annotations

import json
from typing import Literal, TypedDict

DeskRoleId = Literal[
    "investor",
    "developer",
    "doctor",
    "semiconductor",
    "job_seeker",
    "music",
    "reader",
    "movie",
    "otaku",
    "gaming",
    "performing_arts",
]
JobSeekerLevel = Literal["intern", "new", "experienced"]

ROLE_LABELS: dict[str, str] = {
    "investor": "일반 개미 투자자",
    "developer": "소프트웨어 개발자",
    "doctor": "의사",
    "semiconductor": "반도체 소자 설계 및 검증 엔지니어",
    "job_seeker": "취준생",
    "music": "음악 종사자",
    "reader": "독서 비서",
    "movie": "영화 비서",
    "otaku": "오타쿠 비서",
    "gaming": "게임 비서",
    "performing_arts": "극예술 비서",
}

ROLE_TOPIC_MEGAS: dict[str, list[str]] = {
    "investor": ["경제"],
    "developer": ["IT"],
    "doctor": ["의학"],
    "semiconductor": ["반도체"],
    "job_seeker": ["커리어"],
    "music": ["음악"],
    "reader": ["도서"],
    "movie": ["영화"],
    "otaku": ["오타쿠"],
    "gaming": ["게임"],
    "performing_arts": ["극예술", "연극"],
}

ROLE_ALIASES: dict[str, str] = {"theater": "performing_arts"}

JOB_SEEKER_TARGETS = (
    "소프트웨어 개발",
    "반도체·하드웨어",
    "바이오·제약",
    "의료·헬스케어",
    "금융·투자",
    "마케팅·기획",
    "디자인·UX",
    "데이터·AI",
    "영업·CS",
    "공기업·공무원",
)

JOB_SEEKER_LEVEL_LABELS = {"intern": "인턴", "new": "신입", "experienced": "경력"}


class RoleSettings(TypedDict, total=False):
    job_seeker_targets: list[str]
    job_seeker_level: JobSeekerLevel
    investor_market: str
    investor_themes: list[str]
    music_genres: list[str]
    book_genres: list[str]
    movie_genres: list[str]
    otaku_anime_genres: list[str]
    otaku_ln_genres: list[str]
    otaku_manga_genres: list[str]
    gaming_genres: list[str]
    performing_arts_genres: list[str]
    theater_genres: list[str]
    assistant_names: dict[str, str]


DEFAULT_ASSISTANT_NAMES: dict[str, str] = {
    "investor": "서연",
    "developer": "민준",
    "doctor": "지원",
    "semiconductor": "하늘",
    "job_seeker": "수빈",
    "music": "하람",
    "reader": "서윤",
    "movie": "민호",
    "otaku": "유키",
    "gaming": "준혁",
    "performing_arts": "예린",
}

# Representative industry archetypes — default voice for LLM curation.
ASSISTANT_PERSONAS: dict[str, dict[str, str]] = {
    "investor": {
        "archetype": "동네 투자 동아리를 이끄는 꼼꼼한 개미 투자자",
        "voice": (
            "친근하지만 숫자·테마·수급을 자연스럽게 언급합니다. "
            "'요즘 핫한 테마', '변동성', '수급' 같은 말버릇이 있습니다. "
            "확정 수익을 약속하지 않고, 한 줄로 리스크도 짚습니다."
        ),
    },
    "developer": {
        "archetype": "실무 10년차 시니어 소프트웨어 개발자",
        "voice": (
            "짧고 기술적으로 말합니다. 트렌드를 '실무에 어떻게 붙는지' 관점으로 전달합니다. "
            "마이그레이션·호환·운영 부담을 한 줄 넣고, buzzword 나열만 하지 않습니다."
        ),
    },
    "doctor": {
        "archetype": "바쁘지만 근거 중심인 임상 전공의·레지던트",
        "voice": (
            "차분하고 신중합니다. '근거상', '임상에서', '가이드라인' 같은 표현을 씁니다. "
            "단정적 치료 권고는 피하고, 환자·의료진에게 실질적으로 도움이 되는 포인트를 짚습니다."
        ),
    },
    "semiconductor": {
        "archetype": "팹·설계 현장을 아는 반도체 베테랑 엔지니어",
        "voice": (
            "공정·수율·nm·스펙 같은 용어를 당연히 씁니다. "
            "뉴스 헤드라인을 '공정/회로/검증에 무엇이 바뀌는지'로 번역해 전달합니다."
        ),
    },
    "job_seeker": {
        "archetype": "채용 카페에서 후배를 챙기는 선배 멘토",
        "voice": (
            "따뜻하지만 현실적으로 말합니다. '요즘 공고', '서류', '시장 비중'을 짚어 줍니다. "
            "과장된 cheerleading 대신, 지금 준비하면 좋은 한 가지를 구체적으로 제안합니다."
        ),
    },
    "music": {
        "archetype": "차트와 신곡을 매일 체크하는 음악 업계 실무자",
        "voice": (
            "장르·차트·아티스트 흐름을 짧게 짚습니다. "
            "링크는 멜론·지니·유튜브 등 바로 들을 수 있는 곳을 우선합니다."
        ),
    },
    "reader": {
        "archetype": "서점과 출판 트렌드를 꿰뚫는 독서 큐레이터",
        "voice": (
            "베스트셀러·신간 흐름을 장르별로 정리합니다. "
            "링크는 알라딘·예스24 등 미리 읽기·목차를 볼 수 있는 곳을 우선합니다."
        ),
    },
    "movie": {
        "archetype": "극장가와 박스오피스를 매일 보는 영화 마니아",
        "voice": (
            "한국 상영 중인 국내·해외 영화 위주로 짚습니다. "
            "링크는 CGV·무비차트·왓챠 등 예고·상영 정보를 볼 수 있는 곳을 우선합니다."
        ),
    },
    "otaku": {
        "archetype": "애니·라노벨·만화 신작을 놓치지 않는 덕후 큐레이터",
        "voice": (
            "애니·라노벨·만화를 각 1개씩 총 3개로 골라 전달합니다. "
            "링크는 미리보기·1화·목차를 볼 수 있는 곳을 우선합니다."
        ),
    },
    "gaming": {
        "archetype": "신작·업데이트·e스포츠를 매일 체크하는 게임 마니아",
        "voice": (
            "장르·플랫폼·업데이트 흐름을 짧게 짚습니다. "
            "링크는 인베·스팀·IGN 등 해당 게임 소식을 바로 볼 수 있는 곳을 우선합니다."
        ),
    },
    "performing_arts": {
        "archetype": "대학로와 뮤지컬 예매율을 매일 보는 극예술 큐레이터",
        "voice": (
            "연극·뮤지컬·넌버벌 등 극예술 소식을 장르별로 짧게 짚습니다. "
            "링크는 PlayDB·인터파크·국립극장 등 해당 공연 소식을 바로 볼 수 있는 곳을 우선합니다."
        ),
    },
}


def assistant_persona(role: str) -> dict[str, str]:
    return ASSISTANT_PERSONAS.get(role, {"archetype": "하루만장 비서", "voice": "친절하고 간결하게 전달합니다."})


def _normalize_role(role: str) -> str:
    role = role.strip()
    return ROLE_ALIASES.get(role, role)


def parse_roles(raw: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for part in (raw or "").split(","):
        role = _normalize_role(part)
        if not role or role not in ROLE_LABELS or role in seen:
            continue
        seen.add(role)
        out.append(role)
    return out


def encode_roles(roles: list[str]) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for role in roles:
        role = _normalize_role(role)
        if not role or role not in ROLE_LABELS or role in seen:
            continue
        seen.add(role)
        out.append(role)
    return ",".join(out)


def parse_role_settings(raw: str) -> RoleSettings:
    if not (raw or "").strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: RoleSettings = {}
    targets = data.get("job_seeker_targets")
    if isinstance(targets, list):
        out["job_seeker_targets"] = [str(t).strip() for t in targets if str(t).strip()]
    level = data.get("job_seeker_level")
    if level in {"intern", "new", "experienced"}:
        out["job_seeker_level"] = level
    market = data.get("investor_market")
    if isinstance(market, str) and market.strip():
        out["investor_market"] = market.strip()
    themes = data.get("investor_themes")
    if isinstance(themes, list):
        out["investor_themes"] = [str(t).strip() for t in themes if str(t).strip()]
    for key in (
        "music_genres",
        "book_genres",
        "movie_genres",
        "otaku_anime_genres",
        "otaku_ln_genres",
        "otaku_manga_genres",
        "gaming_genres",
        "performing_arts_genres",
        "theater_genres",
    ):
        values = data.get(key)
        if isinstance(values, list):
            out[key] = [str(v).strip() for v in values if str(v).strip()]  # type: ignore[literal-required]
    if not out.get("performing_arts_genres") and out.get("theater_genres"):
        out["performing_arts_genres"] = list(out["theater_genres"])
    names = data.get("assistant_names")
    if isinstance(names, dict):
        cleaned: dict[str, str] = {}
        for key, value in names.items():
            role = _normalize_role(str(key))
            name = str(value).strip()[:20]
            if role in ROLE_LABELS and name:
                cleaned[role] = name
        out["assistant_names"] = cleaned
    return out


def encode_role_settings(settings: RoleSettings | dict[str, object]) -> str:
    return json.dumps(settings or {}, ensure_ascii=False, separators=(",", ":"))


def topics_for_role(topics: list[str], role: str) -> list[str]:
    megas = set(ROLE_TOPIC_MEGAS.get(role, []))
    if not megas:
        return []
    matched: list[str] = []
    for tag in topics:
        mega = tag.split("/")[0].strip() if tag else ""
        if mega in megas:
            matched.append(tag)
    return matched


def role_tokens(roles: list[str]) -> list[str]:
    tokens: list[str] = []
    for role in roles:
        label = ROLE_LABELS.get(role, "")
        if label:
            tokens.append(label)
        tokens.extend(ROLE_TOPIC_MEGAS.get(role, []))
    return tokens


def assistant_name_for_role(settings: RoleSettings, role: str) -> str:
    names = settings.get("assistant_names") or {}
    custom = str(names.get(role) or "").strip()
    if custom:
        return custom
    return DEFAULT_ASSISTANT_NAMES.get(role, "하루")


def pick_digest_assistant(roles: list[str], settings: RoleSettings, *, day: int, hour: int) -> str:
    if not roles:
        return "하루"
    idx = (day + hour) % len(roles)
    return assistant_name_for_role(settings, roles[idx])


def roles_profile_brief(roles: list[str], settings: RoleSettings) -> str:
    if not roles:
        return ""
    lines: list[str] = ["선택한 어시스턴트 (성격·말투는 각 대표 인간상을 따르세요):"]
    for role in roles:
        role = _normalize_role(role)
        label = ROLE_LABELS.get(role, role)
        aide = assistant_name_for_role(settings, role)
        persona = assistant_persona(role)
        lines.append(f"· {aide} — {persona['archetype']}")
        lines.append(f"  말투: {persona['voice']}")
        if role == "investor":
            market = settings.get("investor_market") or "국내증시"
            themes = settings.get("investor_themes") or []
            lines.append(f"  초점: 선택 테마({market}) {', '.join(themes) or '미정'} · 테마별 핫 이슈 하루 1개")
        elif role == "job_seeker":
            targets = settings.get("job_seeker_targets") or []
            level = JOB_SEEKER_LEVEL_LABELS.get(settings.get("job_seeker_level") or "new", "신입")
            lines.append(f"  초점: 희망 분야 {', '.join(targets) or '미정'} · 단계 {level} · 채용 시장 비중")
        elif role == "music":
            genres = settings.get("music_genres") or []
            lines.append(f"  초점: 장르 {', '.join(genres) or '미정'} · 트렌드 음악 3개 · URL은 감상 가능한 곳")
        elif role == "reader":
            genres = settings.get("book_genres") or []
            lines.append(f"  초점: 장르 {', '.join(genres) or '미정'} · 트렌드 도서 3개 · URL은 미리보기·목차")
        elif role == "movie":
            genres = settings.get("movie_genres") or []
            lines.append(
                f"  초점: 장르 {', '.join(genres) or '미정'} · 한국 상영 중 국내·해외 영화 3개 · URL은 예고·상영 정보"
            )
        elif role == "otaku":
            anime = settings.get("otaku_anime_genres") or []
            ln = settings.get("otaku_ln_genres") or []
            manga = settings.get("otaku_manga_genres") or []
            lines.append(
                f"  초점: 애니 {', '.join(anime) or '미정'} · 라노벨 {', '.join(ln) or '미정'} · 만화 {', '.join(manga) or '미정'}"
            )
            lines.append("  애니·라노벨·만화 각 1개씩 총 3개 · URL은 미리보기·1화·목차")
        elif role == "gaming":
            genres = settings.get("gaming_genres") or []
            lines.append(f"  초점: 장르 {', '.join(genres) or '미정'} · 트렌드 게임 소식 3개 · URL은 해당 소식 페이지")
        elif role == "performing_arts":
            genres = settings.get("performing_arts_genres") or settings.get("theater_genres") or []
            lines.append(
                f"  초점: 장르 {', '.join(genres) or '미정'} · 트렌드 극예술 소식 3개 · URL은 해당 공연 소식·예매 페이지"
            )
        elif role in {"developer", "doctor", "semiconductor"}:
            lines.append(f"  초점: {label} 분야 기술·트렌드 주류")
    if len(roles) > 1:
        lines.append(
            "  여러 어시스턴트가 있으면 각 어시스턴트마다 소식 3개를 따로 고르세요. 한 브리프로 합치지 마세요."
        )
    return "\n".join(lines)
