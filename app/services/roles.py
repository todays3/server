"""Desk role catalog — mirrors front entities/role for curation prompts."""

from __future__ import annotations

import json
from typing import Literal, TypedDict

DeskRoleId = Literal[
    "investor",
    "stock_analyst",
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
    "stock_analyst": "주식 애널리스트",
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
    "stock_analyst": ["경제"],
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
    investor_match_stock: bool
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
    "stock_analyst": "도윤",
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

# Compact English personas for LLM prompts. Kakao text stays Korean 합니다/습니다.
ASSISTANT_PERSONAS: dict[str, dict[str, str]] = {
    "investor": {
        "archetype": "careful retail investor who runs a neighborhood investing club",
        "voice": (
            "Kakao Korean 합니다/습니다; friendly; mention 테마/변동성/수급 naturally; "
            "never promise returns; one-line risk."
        ),
    },
    "stock_analyst": {
        "archetype": "stock analyst who reads filings and flow",
        "voice": (
            "Kakao Korean 합니다/습니다; one listed name only; say it is not a buy call; "
            "tie to today's tape; attach a financials URL. Do not list 3 articles."
        ),
    },
    "developer": {
        "archetype": "senior software engineer who tracks shipping tech trends",
        "voice": (
            "Kakao Korean 합니다/습니다; short and technical; how the new tech lands in production; "
            "one line on migration/compat/ops; no evergreen tutorials or buzzword lists."
        ),
    },
    "doctor": {
        "archetype": "evidence-first clinical resident",
        "voice": (
            "Kakao Korean 합니다/습니다; calm; words like 근거상/임상에서/가이드라인; "
            "no treatment orders; one practical point for patients or staff."
        ),
    },
    "semiconductor": {
        "archetype": "veteran fab/design engineer",
        "voice": (
            "Kakao Korean 합니다/습니다; use process/yield/nm/spec terms; "
            "translate headlines into what changes in process/circuit/verification."
        ),
    },
    "job_seeker": {
        "archetype": "mentor who helps juniors in hiring cafes",
        "voice": (
            "Kakao Korean 합니다/습니다; warm but realistic; 공고/서류/시장 비중; "
            "one concrete prep step, not empty cheerleading."
        ),
    },
    "music": {
        "archetype": "music-industry desk who checks charts daily",
        "voice": (
            "Kakao Korean 합니다/습니다; genre/chart/artist in one beat; "
            "prefer Melon/Genie/YouTube listen links."
        ),
    },
    "reader": {
        "archetype": "book curator who tracks store and publisher trends",
        "voice": (
            "Kakao Korean 합니다/습니다; bestsellers/new titles by genre; "
            "prefer Aladin/Yes24 preview or TOC links."
        ),
    },
    "movie": {
        "archetype": "box-office watcher",
        "voice": (
            "Kakao Korean 합니다/습니다; films now in Korean theaters; "
            "prefer CGV/movie-chart/Watcha trailer or showtimes."
        ),
    },
    "otaku": {
        "archetype": "anime/LN/manga new-release curator",
        "voice": (
            "Kakao Korean 합니다/습니다; one anime, one LN, one manga; "
            "prefer preview/ep1/TOC links."
        ),
    },
    "gaming": {
        "archetype": "player who tracks launches, patches, esports",
        "voice": (
            "Kakao Korean 합니다/습니다; genre/platform/patch in one beat; "
            "prefer Inven/Steam/IGN story pages."
        ),
    },
    "performing_arts": {
        "archetype": "performing-arts curator watching Daehangno and musicals",
        "voice": (
            "Kakao Korean 합니다/습니다; play/musical/nonverbal by genre; "
            "prefer PlayDB/Interpark/National Theater pages."
        ),
    },
}


def assistant_persona(role: str) -> dict[str, str]:
    return ASSISTANT_PERSONAS.get(
        role, {"archetype": "Harumunjang aide", "voice": "Kakao Korean 합니다/습니다; kind and brief."}
    )


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
    if isinstance(data.get("investor_match_stock"), bool):
        out["investor_match_stock"] = data["investor_match_stock"]
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


def default_topics_for_role(role: str, settings: RoleSettings | dict[str, object] | None = None) -> list[str]:
    """Fallback topics when the user has no mega-matching tags for this desk."""
    role = _normalize_role(role)
    settings = settings or {}
    if role == "developer":
        return ["IT/개발/all"]
    if role == "doctor":
        return ["의학/진료과/all"]
    if role == "semiconductor":
        return ["반도체/기술동향/all"]
    if role == "job_seeker":
        targets = settings.get("job_seeker_targets") if isinstance(settings, dict) else None
        if isinstance(targets, list) and targets:
            return [f"커리어/희망직무/{t}" for t in targets if str(t).strip()]
        return ["커리어/이직/공고"]
    if role in {"investor", "stock_analyst"}:
        market = str(settings.get("investor_market") or "국내증시")
        themes = settings.get("investor_themes") if isinstance(settings, dict) else None
        if isinstance(themes, list) and themes:
            return [f"경제/주식/{market}/{t}" for t in themes if str(t).strip()]
        return [f"경제/주식/{market}/시황"]
    if role == "music":
        genres = settings.get("music_genres") if isinstance(settings, dict) else None
        if isinstance(genres, list) and genres:
            return [f"음악/장르/{g}" for g in genres if str(g).strip()]
        return ["음악/장르/K-POP"]
    if role == "reader":
        genres = settings.get("book_genres") if isinstance(settings, dict) else None
        if isinstance(genres, list) and genres:
            return [f"도서/장르/{g}" for g in genres if str(g).strip()]
        return ["도서/장르/소설"]
    if role == "movie":
        genres = settings.get("movie_genres") if isinstance(settings, dict) else None
        if isinstance(genres, list) and genres:
            return [f"영화/장르/{g}" for g in genres if str(g).strip()]
        return ["영화/장르/액션"]
    if role == "otaku":
        return ["오타쿠/애니/액션", "오타쿠/라노벨/판타지", "오타쿠/만화/웹툰"]
    if role == "gaming":
        genres = settings.get("gaming_genres") if isinstance(settings, dict) else None
        if isinstance(genres, list) and genres:
            return [f"게임/장르/{g}" for g in genres if str(g).strip()]
        return ["게임/장르/RPG"]
    if role == "performing_arts":
        genres = (
            settings.get("performing_arts_genres")
            if isinstance(settings, dict)
            else None
        ) or (settings.get("theater_genres") if isinstance(settings, dict) else None)
        if isinstance(genres, list) and genres:
            return [f"극예술/장르/{g}" for g in genres if str(g).strip()]
        return ["극예술/장르/뮤지컬"]
    megas = ROLE_TOPIC_MEGAS.get(role, [])
    return [f"{megas[0]}/all"] if megas else []


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
    lines: list[str] = ["Hired assistants (Kakao Korean voice follows each archetype):"]
    for role in roles:
        role = _normalize_role(role)
        label = ROLE_LABELS.get(role, role)
        aide = assistant_name_for_role(settings, role)
        persona = assistant_persona(role)
        lines.append(f"- {aide} — {persona['archetype']}")
        lines.append(f"  Voice: {persona['voice']}")
        if role == "investor":
            market = settings.get("investor_market") or "국내증시"
            themes = settings.get("investor_themes") or []
            lines.append(
                f"  Focus: TODAY's equity/macro market only ({market}); "
                f"themes {', '.join(themes) or '시황'} · 3 market articles. "
                "Never chip-process papers or off-domain lifestyle."
            )
        elif role == "stock_analyst":
            market = settings.get("investor_market") or "국내증시"
            themes = settings.get("investor_themes") or []
            lines.append(
                f"  Focus: themes ({market}) {', '.join(themes) or '시황'} · 1 listed stock/day. "
                "Ground the pick in today's tape signals from finance sources "
                "(foreign/institution flow, trading value leaders, after-hours movers, filings/news). "
                "Do not default to Samsung unless it is the clearest signal. "
                "Do not pick 3 articles. Not a buy call. Attach a financials URL. Market domain only."
            )
        elif role == "job_seeker":
            targets = settings.get("job_seeker_targets") or []
            level = JOB_SEEKER_LEVEL_LABELS.get(settings.get("job_seeker_level") or "new", "신입")
            lines.append(f"  Focus: fields {', '.join(targets) or 'unset'} · stage {level} · hiring mix")
        elif role == "music":
            genres = settings.get("music_genres") or []
            lines.append(f"  Focus: genres {', '.join(genres) or 'unset'} · 3 tracks · listen URLs")
        elif role == "reader":
            genres = settings.get("book_genres") or []
            lines.append(f"  Focus: genres {', '.join(genres) or 'unset'} · 3 books · preview/TOC URLs")
        elif role == "movie":
            genres = settings.get("movie_genres") or []
            lines.append(
                f"  Focus: genres {', '.join(genres) or 'unset'} · 3 films in KR theaters · trailer/showtimes"
            )
        elif role == "otaku":
            anime = settings.get("otaku_anime_genres") or []
            ln = settings.get("otaku_ln_genres") or []
            manga = settings.get("otaku_manga_genres") or []
            lines.append(
                f"  Focus: anime {', '.join(anime) or 'unset'} · LN {', '.join(ln) or 'unset'} · manga {', '.join(manga) or 'unset'}"
            )
            lines.append("  One anime + one LN + one manga. Preview/ep1/TOC URLs.")
        elif role == "gaming":
            genres = settings.get("gaming_genres") or []
            lines.append(f"  Focus: genres {', '.join(genres) or 'unset'} · 3 game stories · story URLs")
        elif role == "performing_arts":
            genres = settings.get("performing_arts_genres") or settings.get("theater_genres") or []
            lines.append(
                f"  Focus: genres {', '.join(genres) or 'unset'} · 3 performing-arts stories · show/ticket URLs"
            )
        elif role == "developer":
            lines.append(
                "  Focus (priority order): 1) latest tech trends / new stack adoption as #1, "
                "2) only from verified outlets (official blogs, HN/GeekNews/InfoQ/D2, reputable eng blogs), "
                "3) skip evergreen how-tos and old framework primers."
            )
            lines.append(
                "  Angles: 흐름 = field-wide latest tech trend; 이슈 = one concrete release/CVE/migration this week; "
                "인물 = eng leader commenting on that trend, or hiring/move of someone tied to that tech — "
                "not celebrity gossip or unrelated founder bios."
            )
        elif role == "semiconductor":
            lines.append(
                "  Focus: semiconductor process/device/design/verification trends only "
                "(EUV, GAA, yield, foundry nodes, ISSCC/IEDM). Reject stock-tape and buy/sell calls."
            )
        elif role == "doctor":
            lines.append(f"  Focus: mainstream {label} clinical/evidence only")
    if len(roles) > 1:
        lines.append(
            "If several assistants are hired, pick separately per role. "
            "News aides: 3 stories. Stock analyst: 1 listed stock. Do not merge into one brief."
        )
    return "\n".join(lines)
