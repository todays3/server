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
