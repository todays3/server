"""Source gather + heuristic curate smoke tests."""

from app.services.digest import _heuristic_pick
from app.services.sources import SourceItem, gather_candidates


def test_heuristic_prefers_kind_diversity():
    items = [
        SourceItem("아티클", "A1", "https://a1.example", "s", "src"),
        SourceItem("아티클", "A2", "https://a2.example", "s", "src"),
        SourceItem("유튜브", "Y1", "https://y1.example", "s", "src"),
        SourceItem("커뮤니티", "C1", "https://c1.example", "s", "src"),
    ]
    picked = _heuristic_pick(items, seed="x")
    assert len(picked) == 3
    kinds = {p["kind"] for p in picked}
    assert "유튜브" in kinds
    assert "커뮤니티" in kinds


def test_gather_candidates_returns_something_or_empty_list():
    # Network may be blocked in CI — must not raise
    result = gather_candidates(["경제/주식/국내증시"], max_items=6)
    assert isinstance(result, list)


def test_site_catalog_covers_frontend_ids():
    from app.services.sources import catalog_site_ids

    expected = {
        "naver-finance",
        "toss-securities",
        "kakao-stock",
        "dart",
        "hankyung",
        "mk-stock",
        "sampro",
        "yahoo-finance",
        "investing",
        "bloomberg",
        "cnbc",
        "reddit-stocks",
        "seeking-alpha",
        "hn",
        "github-trending",
        "velog",
        "okky",
        "youtube-life",
        "brunch",
        "wanted",
        "naver-news",
    }
    assert expected.issubset(set(catalog_site_ids()))
