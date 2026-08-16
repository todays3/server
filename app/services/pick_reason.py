"""Short, honest 'why we picked this' labels — site signals, not invented stats."""

from __future__ import annotations

from app.services.sources import SourceItem

MAX_WHY = 36

KIND_WHY: dict[str, str] = {
    "유튜브": "조회수 1만+ 영상",
    "커뮤니티": "커뮤니티 인기글",
    "아티클": "피드 상위 기사",
}

# What this collector actually samples (front page / official / trending lists).
SITE_WHY: dict[str, str] = {
    "naver-finance": "네이버증권 주요뉴스",
    "toss-securities": "토스증권 콘텐츠",
    "kakao-stock": "카카오페이증권 이슈",
    "dart": "DART 공식 공시",
    "hankyung": "한경 헤드라인",
    "mk-stock": "매경 증권 헤드라인",
    "sampro": "조회수 1만+ 시황",
    "yahoo-finance": "야후파이낸스 헤드라인",
    "investing": "Investing 시황",
    "bloomberg": "블룸버그 헤드라인",
    "reuters": "로이터 헤드라인",
    "cnbc": "CNBC 헤드라인",
    "reddit-stocks": "레딧 주식 인기글",
    "seeking-alpha": "Seeking Alpha 분석",
    "coindesk": "코인데스크 헤드라인",
    "cointelegraph": "코인텔레그래프 헤드라인",
    "the-block": "The Block 헤드라인",
    "quantstart": "QuantStart 가이드",
    "reddit-algotrading": "레딧 알고매매 인기",
    "hn": "HN 프론트페이지",
    "lobsters": "Lobsters 인기글",
    "github-trending": "GitHub 오늘 트렌딩",
    "stackoverflow": "SO 인기 질문",
    "geeksforgeeks": "GFG 인기 가이드",
    "velog": "벨로그 트렌딩",
    "okky": "OKKY 인기글",
    "techblogposts": "국내 기술블로그 모아보기",
    "naver-d2": "NAVER D2 공식",
    "qiita": "Qiita 트렌딩",
    "zenn": "Zenn 트렌딩",
    "producthunt": "Product Hunt 오늘",
    "infoq": "InfoQ 주요 아티클",
    "high-scalability": "스케일 아키텍처 글",
    "netflix-tech": "Netflix 공식 기술블로그",
    "uber-eng": "Uber 공식 기술블로그",
    "cloudflare-blog": "Cloudflare 공식 블로그",
    "geeknews": "긱뉴스 헤드라인",
    "clien": "클리앙 인기글",
    "hardbattle": "하드웨어배틀 이슈",
    "itchosun": "IT조선 헤드라인",
    "bloter": "블로터 헤드라인",
    "outstanding": "아웃스탠딩 헤드라인",
    "innoforest": "혁신의숲 스타트업",
    "eo-planet": "EO 플래닛 콘텐츠",
    "disquiet": "디스콰이엇 인기",
    "design-compass": "디자인나침반 큐레이션",
    "uibowl": "UIBowl 인기 UI",
    "surfit": "서핏 트렌딩",
    "behance": "Behance 주목작",
    "dribbble": "Dribbble 인기샷",
    "mobbin": "Mobbin 앱 UI",
    "youtube-life": "조회수 1만+ 영상",
    "brunch": "브런치 추천글",
    "wanted": "원티드 커리어글",
    "rocketpunch": "로켓펀치 채용·회사",
    "naver-news": "네이버뉴스 헤드라인",
}


def clip_why(text: str) -> str:
    return (text or "").strip()[:MAX_WHY]


def compose_pick_reason(item: SourceItem, *, job_match: bool = False) -> str:
    base = SITE_WHY.get(item.site_id) or KIND_WHY.get(item.kind) or "오늘 후보 중 선별"
    if job_match:
        base = f"{base}·직무"
    return clip_why(base)
