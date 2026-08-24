"""Gather caps and cheap LLM shortlist."""

from app.services.curate_limits import GATHER_FETCH_CAP, GATHER_MAX_ITEMS, LLM_SHORTLIST_MAX
from app.services.shortlist import shortlist_for_llm, topic_tokens
from app.services.sources import SourceItem, overcollect_cap
from app.services.digest import korean_content_score, occupation_tokens


def test_overcollect_cap_allows_one_hundred_after_404_buffer():
    assert overcollect_cap(24) == 48
    assert overcollect_cap(100) == 200
    assert overcollect_cap(6) == 12
    assert GATHER_MAX_ITEMS == 100
    assert GATHER_FETCH_CAP == 200
    assert LLM_SHORTLIST_MAX == 18


def test_gather_candidates_keeps_more_than_old_48_cap(monkeypatch):
    from app.services.sources import gather_candidates

    batch = [
        SourceItem(
            kind="아티클",
            title=f"국내 증시 요약 {i:03d}번",
            url=f"https://live.example/{i}",
            summary="마감",
            source="rss",
        )
        for i in range(80)
    ]
    monkeypatch.setattr("app.services.youtube.collect_youtube_items", lambda *a, **k: [])
    monkeypatch.setattr("app.services.sources._parse_feed", lambda *a, **k: batch)
    monkeypatch.setattr("app.services.sources._html_for_sites", lambda *a, **k: [])
    monkeypatch.setattr("app.services.sources.destination_is_missing", lambda *_a, **_k: False)
    got = gather_candidates(["경제"], preferred_sites=["naver-finance"], max_items=100)
    assert len(got) == 80


def test_shortlist_caps_llm_prompt_and_drops_junk():
    job = occupation_tokens("반도체 연구원")

    topics = topic_tokens(["경제/주식/국내증시"])
    pool: list[SourceItem] = []
    for i in range(90):
        pool.append(
            SourceItem(
                kind="아티클",
                title=f"Buy gadget deal {i}",
                url=f"https://spam.example/{i}",
                summary="shop",
                source="Blog",
            )
        )
    keepers = [
        SourceItem(
            kind="아티클",
            title="코스피 반도체 수출 호조",
            url="https://n.news.naver.com/semi",
            summary="국내 증시",
            source="네이버",
        ),
        SourceItem(
            kind="유튜브",
            title="오늘 시황 브리핑입니다",
            url="https://www.youtube.com/watch?v=kr1",
            summary="국내 시장",
            source="삼프로TV",
        ),
        SourceItem(
            kind="커뮤니티",
            title="국내주식 수급 토론 모음",
            url="https://finance.naver.com/talk",
            summary="수급",
            source="네이버",
        ),
        SourceItem(kind="아티클", title="짧", url="https://tiny.example/x", summary="", source="x"),
    ]
    ranked = keepers + pool
    korean_scores = {item.url: korean_content_score(item) for item in ranked}
    picked = shortlist_for_llm(
        ranked,
        job_tokens=job,
        topic_parts=topics,
        korean_scores=korean_scores,
        limit=18,
    )
    assert len(picked) == 18
    urls = {item.url for item in picked}
    assert "https://n.news.naver.com/semi" in urls
    assert "https://www.youtube.com/watch?v=kr1" in urls
    assert "https://finance.naver.com/talk" in urls
    assert "https://tiny.example/x" not in urls
    assert {item.kind for item in picked[:3]} >= {"아티클", "유튜브", "커뮤니티"}


def test_shortlist_caps_repeats_from_the_same_site():
    from app.services.curate_limits import SHORTLIST_MAX_PER_SITE

    job = occupation_tokens("개발자")
    topics = topic_tokens(["IT"])
    hn = [
        SourceItem(
            kind="아티클",
            title=f"코스피 반도체 기술 소식 {i:02d}번째",
            url=f"https://news.ycombinator.com/{i}",
            summary="국내 IT",
            source="HN",
            site_id="hn",
        )
        for i in range(12)
    ]
    others = [
        SourceItem(
            kind="유튜브",
            title="오늘 시황 브리핑입니다",
            url="https://www.youtube.com/watch?v=kr1",
            summary="국내 시장",
            source="삼프로",
            site_id="sampro",
        ),
        SourceItem(
            kind="커뮤니티",
            title="OKKY 개발 토론 모음입니다",
            url="https://okky.kr/a",
            summary="개발",
            source="OKKY",
            site_id="okky",
        ),
        SourceItem(
            kind="아티클",
            title="네이버 D2 공정 이야기입니다",
            url="https://d2.naver.com/a",
            summary="공정",
            source="D2",
            site_id="naver-d2",
        ),
    ]
    ranked = hn + others
    korean_scores = {item.url: korean_content_score(item) for item in ranked}
    picked = shortlist_for_llm(
        ranked,
        job_tokens=job,
        topic_parts=topics,
        korean_scores=korean_scores,
        limit=18,
    )
    hn_count = sum(1 for item in picked if item.site_id == "hn")
    assert hn_count <= SHORTLIST_MAX_PER_SITE
    assert {item.site_id for item in picked} >= {"hn", "sampro", "okky", "naver-d2"}


def test_developer_shortlist_prefers_verified_latest_trend():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    evergreen = SourceItem(
        kind="아티클",
        title="Python 입문 튜토리얼 기초 정리",
        url="https://blog.example/python-101",
        summary="getting started how to",
        source="RandomBlog",
        site_id="random-blog",
        published_at=now - timedelta(hours=2),
    )
    trend = SourceItem(
        kind="아티클",
        title="LLM 에이전트 도입 트렌드와 마이그레이션",
        url="https://news.ycombinator.com/item?id=1",
        summary="이번 주 채택 동향",
        source="HN",
        site_id="hn",
        published_at=now - timedelta(hours=3),
    )
    person = SourceItem(
        kind="아티클",
        title="OpenAI CTO 키노트에서 에이전트 발언",
        url="https://news.hada.io/topic?id=2",
        summary="트렌드 관련 인터뷰",
        source="GeekNews",
        site_id="geeknews",
        published_at=now - timedelta(hours=5),
    )
    picked = shortlist_for_llm(
        [evergreen, trend, person],
        job_tokens=["개발"],
        topic_parts=["IT"],
        korean_scores={
            evergreen.url: 0,
            trend.url: 0,
            person.url: 0,
        },
        limit=2,
        role="developer",
    )
    urls = [item.url for item in picked]
    assert trend.url in urls
    assert person.url in urls
    assert evergreen.url not in urls
