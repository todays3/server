"""Reference-site catalog — single source of truth for UI + gatherer ids.

Front only renders what GET /api/v1/sources/catalog returns.
Feed/HTML collectors in app.services.sources must cover every site id here.
"""

from __future__ import annotations

from typing import Any

# (group_id, group_label, match_tags, sites[(id, label, blurb, url), ...])
_GROUPS: list[tuple[str, str, list[str], list[tuple[str, str, str, str]]]] = [
    (
        "stock-kr",
        "국내 주식",
        ["주식", "국내증시", "경제", "코스피", "코스닥", "반도체", "2차전지"],
        [
            (
                "naver-finance",
                "네이버 증권",
                "코스피·코스닥 실시간 시세, 종목 뉴스, 공시, 종목토론실을 한곳에서 보는 국내 대표 증권 포털입니다.",
                "https://finance.naver.com/",
            ),
            (
                "toss-securities",
                "토스 증권",
                "모바일 중심 국내 주식 거래 앱으로, 종목 정보와 커뮤니티·콘텐츠를 함께 제공하는 토스 계열 증권 서비스입니다.",
                "https://www.tossinvest.com/",
            ),
            (
                "kakao-stock",
                "카카오페이증권",
                "카카오페이 기반 증권 서비스로 시세·리포트·투자 정보를 확인하고 간편하게 국내 주식을 다루는 플랫폼입니다.",
                "https://stock.kakaopay.com/",
            ),
            (
                "dart",
                "DART 공시",
                "금융감독원 전자공시시스템. 상장사 사업보고서·분기보고서·주요사항보고 등 공식 공시 원문을 검색합니다.",
                "https://dart.fss.or.kr/",
            ),
            (
                "hankyung",
                "한국경제",
                "국내 경제·금융·기업 뉴스를 다루는 종합 경제지. 시장 흐름과 산업·기업 이슈를 기사로 빠르게 훑을 때 씁니다.",
                "https://www.hankyung.com/finance",
            ),
            (
                "mk-stock",
                "매일경제 증권",
                "매일경제의 증권·금융 섹션. 국내 증시 뉴스, 종목·섹터 이슈, 투자 관련 기사를 모아 볼 수 있습니다.",
                "https://www.mk.co.kr/stock/",
            ),
            (
                "sampro",
                "삼프로TV",
                "유튜브 경제·투자 채널. 국내외 시황 브리핑, 전문가 인터뷰, 시장 해설 영상을 주로 올립니다.",
                "https://www.youtube.com/@samproTV",
            ),
        ],
    ),
    (
        "stock-us",
        "미국 주식",
        ["미국증시", "주식", "S&P", "나스닥", "매그니피센트", "Fed"],
        [
            (
                "yahoo-finance",
                "Yahoo Finance",
                "미국·글로벌 주식 시세, 재무 지표, 뉴스, 차트와 워치리스트를 제공하는 대표적인 투자 정보 사이트입니다.",
                "https://finance.yahoo.com/",
            ),
            (
                "investing",
                "Investing.com",
                "주식·환율·원자재·경제 캘린더를 한데 모은 글로벌 시황 플랫폼. 지표 발표 일정과 국제 시장 흐름 파악에 쓰입니다.",
                "https://www.investing.com/",
            ),
            (
                "bloomberg",
                "Bloomberg",
                "매크로·마켓·기업 뉴스를 다루는 글로벌 금융 미디어. 금리·환율·증시 이슈의 깊이 있는 보도로 유명합니다.",
                "https://www.bloomberg.com/markets",
            ),
            (
                "reuters",
                "Reuters",
                "글로벌 통신사 로이터의 비즈니스·마켓 뉴스. 속보성 경제·금융·기업 기사를 빠르게 확인할 때 씁니다.",
                "https://www.reuters.com/business/",
            ),
            (
                "cnbc",
                "CNBC",
                "미국 비즈니스·증시 속보와 방송 기반 마켓 뉴스를 전하는 채널. 장중 이슈와 기업 헤드라인을 빠르게 봅니다.",
                "https://www.cnbc.com/markets/",
            ),
            (
                "reddit-stocks",
                "r/stocks",
                "레딧의 주식 토론 커뮤니티. 종목·매크로·실적에 대한 개인 투자자 의견과 링크가 실시간으로 올라옵니다.",
                "https://www.reddit.com/r/stocks/",
            ),
            (
                "seeking-alpha",
                "Seeking Alpha",
                "애널리스트·투자자가 올리는 종목 분석, 실적 리뷰, 투자 아이디어 기고가 많은 미국 주식 리서치 커뮤니티입니다.",
                "https://seekingalpha.com/",
            ),
        ],
    ),
    (
        "crypto",
        "크립토 · 거래소",
        ["크립토", "코인", "비트코인", "이더리움", "거래소", "블록체인"],
        [
            (
                "coindesk",
                "CoinDesk",
                "암호화폐·블록체인 전문 미디어. 시장 동향, 규제, 프로젝트·거래소 이슈를 다루는 영문 크립토 뉴스입니다.",
                "https://www.coindesk.com/",
            ),
            (
                "cointelegraph",
                "CoinTelegraph",
                "비트코인·알트코인·DeFi·NFT 등 크립토 전반의 속보와 분석을 전하는 글로벌 암호화폐 미디어입니다.",
                "https://cointelegraph.com/",
            ),
            (
                "the-block",
                "The Block",
                "디지털 자산 시장·인프라·기관 동향을 리서치 톤으로 다루는 크립토 전문 매체입니다.",
                "https://www.theblock.co/",
            ),
        ],
    ),
    (
        "quant",
        "퀀트 · 알고 트레이딩",
        ["퀀트", "알고", "트레이딩", "알고리즘", "백테스트"],
        [
            (
                "quantstart",
                "QuantStart",
                "알고리즘 트레이딩·퀀트 파이낸스 학습 사이트. 전략, 백테스트, 파이썬 퀀트 튜토리얼이 많습니다.",
                "https://www.quantstart.com/",
            ),
            (
                "reddit-algotrading",
                "r/algotrading",
                "레딧의 알고 트레이딩 커뮤니티. 전략 아이디어, 데이터, 실행·인프라 경험 공유가 올라옵니다.",
                "https://www.reddit.com/r/algotrading/",
            ),
        ],
    ),
    (
        "it",
        "IT · 개발",
        ["IT", "개발", "AI", "프론트", "백엔드", "제품", "디자인", "데이터"],
        [
            (
                "hn",
                "Hacker News",
                "Y Combinator가 운영하는 테크 뉴스·토론 보드. 스타트업·오픈소스·엔지니어링 이슈가 프론트페이지에 모입니다.",
                "https://news.ycombinator.com/",
            ),
            (
                "lobsters",
                "Lobsters",
                "초청제 테크 링크 커뮤니티. HN보다 조용하고 엔지니어링·보안·시스템 글 비중이 높은 편입니다.",
                "https://lobste.rs/",
            ),
            (
                "github-trending",
                "GitHub Trending",
                "GitHub에서 오늘·이번 주 급상승 중인 오픈소스 저장소를 언어별로 보여 주는 트렌딩 목록입니다.",
                "https://github.com/trending",
            ),
            (
                "stackoverflow",
                "Stack Overflow",
                "개발 Q&A 플랫폼. 태그별 최신 질문·답변으로 특정 언어·프레임워크 이슈를 추적할 때 씁니다.",
                "https://stackoverflow.com/",
            ),
            (
                "geeksforgeeks",
                "GeeksforGeeks",
                "자료구조·알고리즘·면접·언어 튜토리얼이 많은 개발 학습 사이트입니다.",
                "https://www.geeksforgeeks.org/",
            ),
            (
                "velog",
                "velog",
                "국내 개발자가 기술 글을 올리는 블로그 플랫폼. 프론트·백엔드·인프라 학습 아티클을 한글로 많이 찾습니다.",
                "https://velog.io/",
            ),
            (
                "okky",
                "OKKY",
                "한국 개발자 커뮤니티. 구인·구직, 기술 Q&A, 생활·커리어 글을 나누는 국내 IT 포럼입니다.",
                "https://okky.kr/",
            ),
            (
                "techblogposts",
                "TechBlogPosts",
                "국내 기업·팀 기술 블로그를 한곳에 모아 보여주는 애그리게이터입니다.",
                "https://techblogposts.com/",
            ),
            (
                "naver-d2",
                "NAVER D2",
                "네이버 개발자 기술 블로그. 검색·프론트·인프라·AI 실전기가 올라옵니다.",
                "https://d2.naver.com/",
            ),
            (
                "qiita",
                "Qiita",
                "일본 개발자 지식 공유 플랫폼. 언어·프레임워크 실무 글이 많습니다.",
                "https://qiita.com/",
            ),
            (
                "zenn",
                "Zenn",
                "엔지니어가 기술 글·책을 발행하는 일본 테크 퍼블리싱 플랫폼입니다.",
                "https://zenn.dev/",
            ),
            (
                "producthunt",
                "Product Hunt",
                "새로 나온 프로덕트·툴을 매일 소개하고 투표하는 런칭 커뮤니티입니다.",
                "https://www.producthunt.com/",
            ),
        ],
    ),
    (
        "arch",
        "백엔드 · 시스템 아키텍처",
        ["백엔드", "아키텍처", "분산", "인프라", "DB", "스케일"],
        [
            (
                "infoq",
                "InfoQ",
                "소프트웨어 아키텍처·분산 시스템·실무 엔지니어링 뉴스와 심층 아티클을 다루는 기술 미디어입니다.",
                "https://www.infoq.com/",
            ),
            (
                "high-scalability",
                "High Scalability",
                "대규모 서비스의 아키텍처 사례, DB·캐시·큐 설계 패턴을 모아 둔 스케일아웃 전문 블로그입니다.",
                "https://highscalability.com/",
            ),
            (
                "netflix-tech",
                "Netflix Tech Blog",
                "넷플릭스 엔지니어링 블로그. 스트리밍·데이터·플랫폼·운영 경험을 공개 기술글로 공유합니다.",
                "https://netflixtechblog.com/",
            ),
            (
                "uber-eng",
                "Uber Engineering",
                "우버 엔지니어링 블로그. 대규모 분산 처리, 데이터 인프라, 모바일·맵 관련 실전기를 다룹니다.",
                "https://www.uber.com/blog/engineering/",
            ),
            (
                "cloudflare-blog",
                "Cloudflare Blog",
                "네트워크·보안·엣지·성능 이슈를 Cloudflare 관점에서 풀어 쓰는 엔지니어링 블로그입니다.",
                "https://blog.cloudflare.com/",
            ),
        ],
    ),
    (
        "kr-it",
        "국내 IT · 애그리게이터",
        ["IT", "개발", "뉴스", "스타트업", "하드웨어", "반도체"],
        [
            (
                "geeknews",
                "긱뉴스",
                "해외 테크·스타트업·개발 이슈를 한글로 빠르게 모아 주는 국내 테크 뉴스 애그리게이터입니다.",
                "https://news.hada.io/",
            ),
            (
                "clien",
                "클리앙",
                "국내 IT·생활 커뮤니티. 새로운소식·사용기 등에서 국내 테크 이슈와 제품 반응을 볼 수 있습니다.",
                "https://www.clien.net/",
            ),
            (
                "hardbattle",
                "하드웨어배틀",
                "PC·부품·주변기기 뉴스와 벤치, 커뮤니티 토론이 모이는 국내 하드웨어 전문 사이트입니다.",
                "https://www.hwbattle.com/",
            ),
            (
                "itchosun",
                "IT조선",
                "조선일보 계열 IT 전문 매체. 국내 ICT·통신·스타트업·정책 뉴스를 다룹니다.",
                "https://it.chosun.com/",
            ),
            (
                "bloter",
                "블로터",
                "국내 디지털·미디어·테크 전문 언론. 스타트업과 플랫폼 산업 이슈를 자주 다룹니다.",
                "https://www.bloter.net/",
            ),
            (
                "outstanding",
                "아웃스탠딩",
                "비즈니스·테크·콘텐츠 트렌드를 다루는 국내 미디어. 실무형 인사이트 아티클이 많습니다.",
                "https://outstanding.kr/",
            ),
            (
                "innoforest",
                "혁신의 숲",
                "국내 스타트업 투자·성장 데이터를 모아 보는 스타트업 인텔리전스 플랫폼입니다.",
                "https://www.innoforest.co.kr/",
            ),
            (
                "eo-planet",
                "EO 플래닛",
                "스타트업·창업가 인터뷰와 비즈니스 스토리를 전하는 EO 미디어입니다.",
                "https://www.eopla.net/",
            ),
            (
                "disquiet",
                "디스콰이엇",
                "국내 메이커가 제품을 올리고 피드백을 주고받는 프로덕트 커뮤니티입니다.",
                "https://disquiet.io/",
            ),
        ],
    ),
    (
        "design",
        "디자인 · UX",
        ["디자인", "UX", "UI", "프로덕트", "프론트엔드", "브랜딩"],
        [
            (
                "design-compass",
                "Design Compass",
                "국내 디자인·브랜딩·트렌드 뉴스와 아티클을 모으는 디자인 미디어입니다.",
                "https://www.designcompass.org/",
            ),
            (
                "uibowl",
                "UIBowl",
                "실제 서비스 UI 패턴과 화면 레퍼런스를 모아 보는 갤러리입니다.",
                "https://www.uibowl.com/",
            ),
            (
                "surfit",
                "Surfit",
                "디자인·기획·개발 실무 콘텐츠를 큐레이션하는 국내 커리어 미디어입니다.",
                "https://www.surfit.io/",
            ),
            (
                "behance",
                "Behance",
                "전 세계 디자이너 포트폴리오와 프로젝트 쇼케이스가 모이는 Adobe 커뮤니티입니다.",
                "https://www.behance.net/",
            ),
            (
                "dribbble",
                "Dribbble",
                "UI·브랜딩·일러스트 샷이 올라오는 디자이너 커뮤니티입니다.",
                "https://dribbble.com/",
            ),
            (
                "mobbin",
                "Mobbin",
                "모바일 앱 화면을 플로우 단위로 모아 둔 UI 레퍼런스 라이브러리입니다.",
                "https://mobbin.com/",
            ),
        ],
    ),
    (
        "life",
        "라이프 · 연애",
        ["라이프", "연애", "건강", "자기계발"],
        [
            (
                "youtube-life",
                "YouTube",
                "라이프스타일·연애·자기계발 채널. YouTube Data API(youtube-mcp와 동일)로 최신 영상을 가져오고, 키가 없으면 RSS로 폴백합니다.",
                "https://www.youtube.com/",
            ),
            (
                "brunch",
                "브런치",
                "작가·전문가가 에세이·라이프·커리어 글을 연재하는 카카오 계열 퍼블리싱 플랫폼입니다.",
                "https://brunch.co.kr/",
            ),
        ],
    ),
    (
        "career-news",
        "커리어 · 뉴스",
        ["커리어", "뉴스", "이직", "사회"],
        [
            (
                "wanted",
                "원티드",
                "IT·스타트업 중심 채용 플랫폼. 포지션 공고와 함께 커리어 아티클·이벤트·이직 정보를 제공합니다.",
                "https://www.wanted.co.kr/",
            ),
            (
                "rocketpunch",
                "RocketPunch",
                "스타트업·IT 채용과 회사 정보를 다루는 국내 커리어 플랫폼입니다.",
                "https://www.rocketpunch.com/",
            ),
            (
                "naver-news",
                "네이버 뉴스",
                "정치·경제·사회·세계 등 국내 종합 뉴스를 언론사별로 모아 보여주는 네이버 뉴스 홈입니다.",
                "https://news.naver.com/",
            ),
        ],
    ),
]

MEGA_TO_GROUPS: dict[str, list[str]] = {
    "IT": ["it", "arch", "kr-it", "design"],
    "경제": ["stock-kr", "stock-us", "crypto", "quant"],
    "연애": ["life"],
    "라이프": ["life"],
    "커리어": ["career-news"],
    "뉴스": ["career-news", "kr-it"],
    "반도체": ["kr-it", "stock-kr"],
    "의학": ["kr-it", "career-news"],
}


def all_site_ids() -> list[str]:
    ids: list[str] = []
    for _, _, _, sites in _GROUPS:
        for sid, *_ in sites:
            ids.append(sid)
    return ids


def site_label(site_id: str) -> str:
    for _, _, _, sites in _GROUPS:
        for sid, label, *_ in sites:
            if sid == site_id:
                return label
    return site_id


def groups_for_mega(mega_id: str) -> list[dict[str, Any]]:
    wanted = MEGA_TO_GROUPS.get(mega_id, ["stock-kr", "stock-us"])
    return [g for g in catalog_groups() if g["id"] in wanted]


def catalog_groups() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for gid, label, match, sites in _GROUPS:
        out.append(
            {
                "id": gid,
                "label": label,
                "match": list(match),
                "sites": [
                    {"id": sid, "label": lab, "blurb": blurb, "url": url}
                    for sid, lab, blurb, url in sites
                ],
            }
        )
    return out


def catalog_payload() -> dict[str, Any]:
    return {
        "groups": catalog_groups(),
        "mega_map": {k: list(v) for k, v in MEGA_TO_GROUPS.items()},
    }
