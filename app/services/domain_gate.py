"""Per-desk domain allow/reject so digests stay on-mission."""

from __future__ import annotations

from typing import Iterable

from app.services.sources import SourceItem

# Market / tape signals — required for retail & analyst desks when site_id is untrusted.
_MARKET = (
    "증시",
    "코스피",
    "코스닥",
    "주식",
    "환율",
    "금리",
    "실적",
    "수급",
    "시황",
    "종목",
    "지수",
    "나스닥",
    "다우",
    "s&p",
    "etf",
    "공시",
    "배당",
    "외국인",
    "기관",
    "목표가",
    "투자의견",
    "시총",
    "주가",
    "매매",
    "상승",
    "하락",
    "마감",
    "개장",
    "선물",
    "옵션",
    "채권",
    "비트코인",
    "암호화폐",
    "가상자산",
)

_SEMI_TECH = (
    "반도체",
    "소자",
    "공정",
    "euv",
    "웨이퍼",
    "파운드리",
    "트랜지스터",
    "수율",
    "hbm",
    "gaa",
    "cfet",
    "isscc",
    "iedm",
    "리소",
    "회로",
    "검증",
    "설계",
    "finfet",
    "dram",
    "nand",
    "패키징",
    "pdk",
    "eda",
    "fab",
    "노드",
    "nm급",
    "nm ",
    "마스크",
    "증착",
    "식각",
    "cmp",
    "drc",
    "lvs",
    "타이밍",
)

_SEMI_REJECT = (
    "목표가",
    "매수의견",
    "매도의견",
    "투자의견",
    "배당락",
    "오늘 시황",
    "코스피 급등",
    "코스피 급락",
    "외국인 순매수",
    "기관 순매수",
    "etf 추천",
    "연애",
    "소개팅",
    "k-pop",
    "케이팝",
    "웹툰",
    "애니메",
    "라노벨",
    "취준",
    "면접 팁",
)

_INVESTOR_REJECT = (
    "연애",
    "소개팅",
    "k-pop",
    "케이팝",
    "웹툰",
    "라노벨",
    "isscc",
    "iedm",
    "트랜지스터 논문",
    "공정 레시피",
    "pdk ",
    "drc 위반",
)

_DEV = (
    "개발",
    "소프트웨어",
    "api",
    "프레임워크",
    "라이브러리",
    "오픈소스",
    "github",
    "kubernetes",
    "클라우드",
    "llm",
    "에이전트",
    "typescript",
    "rust",
    "golang",
    "보안",
    "취약점",
    "cve",
    "마이그레이션",
    "릴리즈",
    "devops",
    "인프라",
    "데이터베이스",
    "프론트",
    "백엔드",
)

_DEV_REJECT = (
    "연애",
    "소개팅",
    "코스피",
    "목표가",
    "매수의견",
    "웹툰",
    "라노벨",
)

_DOCTOR = (
    "임상",
    "진료",
    "환자",
    "가이드라인",
    "논문",
    "치료",
    "진단",
    "병원",
    "의약",
    "백신",
    "역학",
    "수술",
    "처방",
    "fda",
    "식약처",
    "nejm",
    "lancet",
    "cochrane",
)

_DOCTOR_REJECT = ("연애", "소개팅", "코스피", "목표가", "k-pop", "웹툰")

_CAREER = (
    "채용",
    "공고",
    "면접",
    "이력서",
    "자소서",
    "연봉",
    "이직",
    "신입",
    "인턴",
    "취업",
    "커리어",
    "합격",
)

_MUSIC = ("음악", "차트", "앨범", "싱글", "가수", "아티스트", "멜론", "지니", "spotify", "빌보드", "콘서트")
_BOOK = ("도서", "책", "베스트셀러", "출판", "소설", "에세이", "알라딘", "예스24", "작가")
_MOVIE = ("영화", "개봉", "박스오피스", "극장", "cgv", "트레일러", "감독", "배우")
_OTAKU = ("애니", "애니메이션", "만화", "웹툰", "라노벨", "오타쿠", "원피스", "점프")
_GAME = ("게임", "출시", "스팀", "닌텐도", "플레이스테이션", "xbox", "e스포츠", "패치")
_THEATER = ("연극", "뮤지컬", "공연", "극장", "티켓", "연출", "배우", "콘서트홀")

ROLE_ALLOW: dict[str, tuple[str, ...]] = {
    "investor": _MARKET,
    "stock_analyst": _MARKET,
    "semiconductor": _SEMI_TECH,
    "developer": _DEV,
    "doctor": _DOCTOR,
    "job_seeker": _CAREER,
    "music": _MUSIC,
    "reader": _BOOK,
    "movie": _MOVIE,
    "otaku": _OTAKU,
    "gaming": _GAME,
    "performing_arts": _THEATER,
}

ROLE_REJECT: dict[str, tuple[str, ...]] = {
    "investor": _INVESTOR_REJECT,
    "stock_analyst": _INVESTOR_REJECT,
    "semiconductor": _SEMI_REJECT,
    "developer": _DEV_REJECT,
    "doctor": _DOCTOR_REJECT,
    "job_seeker": ("연애", "소개팅", "코스피 급등", "목표가"),
    "music": ("코스피", "목표가", "연애 상담"),
    "reader": ("코스피", "목표가", "연애 상담"),
    "movie": ("코스피", "목표가", "연애 상담"),
    "otaku": ("코스피", "목표가", "매수의견"),
    "gaming": ("코스피", "목표가", "연애 상담"),
    "performing_arts": ("코스피", "목표가", "연애 상담"),
}


def topic_site_id(mega: str) -> str:
    mega = (mega or "").strip()
    return f"topic-{mega}" if mega else ""


def topic_site_ids_for_roles(roles: Iterable[str]) -> set[str]:
    from app.services.roles import ROLE_TOPIC_MEGAS

    out: set[str] = set()
    for role in roles:
        for mega in ROLE_TOPIC_MEGAS.get(role, []):
            out.add(topic_site_id(mega))
    return out


def topic_site_ids_for_topics(topics: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for tag in topics:
        mega = tag.split("/")[0].strip() if tag else ""
        if mega:
            out.add(topic_site_id(mega))
    return out


def stamp_for_topics(topics: list[str]) -> str:
    """Synthetic site_id so shared-crawl slices stay mega-scoped."""
    megas: list[str] = []
    for tag in topics:
        mega = tag.split("/")[0].strip() if tag else ""
        if mega and mega not in megas:
            megas.append(mega)
    if not megas:
        return ""
    return topic_site_id(megas[0])


def _blob(item: SourceItem) -> str:
    return " ".join(
        [
            item.title or "",
            item.summary or "",
            item.source or "",
            item.kind or "",
        ]
    ).lower()


def _any_hit(blob: str, tokens: Iterable[str]) -> bool:
    return any(token.lower() in blob for token in tokens if len(token) >= 2)


def item_passes_role_domain(item: SourceItem, role: str, *, trusted_sites: set[str] | None = None) -> bool:
    """Return False when the item is clearly off-desk for this assistant."""
    blob = _blob(item)
    reject = ROLE_REJECT.get(role, ())
    if reject and _any_hit(blob, reject):
        return False

    sid = (item.site_id or "").strip()
    if sid.startswith("topic-"):
        # Topic RSS must still match desk vocabulary.
        allow = ROLE_ALLOW.get(role)
        return bool(allow and _any_hit(blob, allow))

    if trusted_sites is not None and sid and sid in trusted_sites:
        return True

    allow = ROLE_ALLOW.get(role)
    if not allow:
        return True
    if not sid:
        return _any_hit(blob, allow)
    # Known catalog site for another desk slipped in — require allow keywords.
    return _any_hit(blob, allow)
