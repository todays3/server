"""Source gather + catalog parity tests."""

from datetime import datetime, timezone

from app.catalog.ref_sites import all_site_ids, catalog_payload, groups_for_mega, site_label
from app.services.digest import _heuristic_pick
from app.services.sources import HtmlListSpec, SourceItem, catalog_site_ids, collector_site_ids, gather_candidates
from app.services import sources as sources_mod


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
    result = gather_candidates(["경제/주식/국내증시"], max_items=6)
    assert isinstance(result, list)


def test_catalog_ids_match_collectors():
    catalog = set(all_site_ids())
    collectors = collector_site_ids()
    missing = catalog - collectors
    assert not missing, f"catalog sites without collector: {sorted(missing)}"
    assert set(catalog_site_ids()) == catalog


def test_catalog_includes_design_and_kr_tech_sites():
    ids = set(all_site_ids())
    for sid in (
        "techblogposts",
        "naver-d2",
        "design-compass",
        "uibowl",
        "surfit",
        "rocketpunch",
        "innoforest",
        "eo-planet",
        "disquiet",
        "qiita",
        "zenn",
        "producthunt",
        "behance",
        "dribbble",
        "mobbin",
    ):
        assert sid in ids
    for sid in (
        "pubmed",
        "nejm",
        "arxiv-cs",
        "acm-dl",
        "ieee-xplore",
        "usenix",
        "dblp",
        "isscc",
        "iedm",
        "spie",
        "sciencedirect",
    ):
        assert sid in ids
    labels = {g["id"]: g["label"] for g in catalog_payload()["groups"]}
    assert "design" in labels
    assert any("디자인" in g["match"] for g in catalog_payload()["groups"] if g["id"] == "design")
    for sid in (
        "playdb",
        "kopis",
        "interpark-ticket",
        "melon-ticket",
        "yes24-ticket",
        "ticketlink",
        "ntok",
        "sejongpac",
        "sac",
        "lgart",
        "themusical",
        "culture-portal",
    ):
        assert sid in ids
    assert "theater-kr" in labels
    assert groups_for_mega("극예술")


def test_catalog_payload_shape():
    payload = catalog_payload()
    assert payload["groups"]
    assert "IT" in payload["mega_map"]
    assert "반도체" in payload["mega_map"]
    assert "의학" in payload["mega_map"]
    assert "design" in payload["mega_map"]["IT"]
    assert "cs-academic" in payload["mega_map"]["IT"]
    assert "medicine-clinical" in payload["mega_map"]["의학"]
    assert "semi-academic" in payload["mega_map"]["반도체"]
    assert all("sites" in g for g in payload["groups"])
    for group in payload["groups"]:
        for site in group["sites"]:
            assert "dau" in site
            assert site["dau"] > 0


def test_catalog_sites_sorted_by_dau_desc():
    payload = catalog_payload()
    for group in payload["groups"]:
        daus = [site["dau"] for site in group["sites"]]
        assert daus == sorted(daus, reverse=True)


def test_all_catalog_sites_have_explicit_dau():
    from app.catalog.ref_sites import SITE_DAU, all_site_ids

    missing = set(all_site_ids()) - set(SITE_DAU)
    assert not missing, f"sites missing SITE_DAU estimate: {sorted(missing)}"


def test_html_list_parser_extracts_anchors(monkeypatch):
    html = """
    <html><body>
      <a href="/owner/awesome-repo">Awesome Repo</a>
      <a href="/topics/python">Topics</a>
      <a href="/foo/bar/extra">Too deep</a>
    </body></html>
    """
    monkeypatch.setattr(sources_mod, "_http_get", lambda url, **kwargs: html)
    spec = HtmlListSpec(
        kind="커뮤니티",
        source="GitHub Trending",
        url="https://github.com/trending",
        href_re=r"^https://github\.com/[^/]+/[^/]+/?$",
        base="https://github.com",
        limit=8,
    )
    items = sources_mod._parse_html_list(spec, query="python")
    assert len(items) == 1
    assert items[0].url == "https://github.com/owner/awesome-repo"
    assert "Awesome" in items[0].title


def test_page_is_missing_drops_http_404_and_soft_404_titles():
    from app.services.sources import page_is_missing

    assert page_is_missing(404, "") is True
    assert page_is_missing(410, "") is True
    assert page_is_missing(200, "<html><title>Market wrap</title></html>") is False
    assert page_is_missing(200, "<html><title>404 Not Found</title><body>gone</body></html>") is True
    assert page_is_missing(200, "<html><title>페이지를 찾을 수 없습니다</title></html>") is True
    assert page_is_missing(200, "<html><title>How to handle 404 errors in FastAPI</title></html>") is False
    assert page_is_missing(403, "") is False


def test_gather_candidates_excludes_missing_destinations(monkeypatch):
    live = SourceItem("아티클", "살아있는 글", "https://live.example/a", "s", "rss")
    gone = SourceItem("아티클", "죽은 글", "https://gone.example/a", "s", "rss")
    video = SourceItem("유튜브", "영상", "https://www.youtube.com/watch?v=abc", "s", "yt")
    monkeypatch.setattr("app.services.youtube.collect_youtube_items", lambda *a, **k: [video])
    monkeypatch.setattr("app.services.sources._parse_feed", lambda *a, **k: [gone, live])
    monkeypatch.setattr("app.services.sources._html_for_sites", lambda *a, **k: [])
    monkeypatch.setattr(
        "app.services.sources.destination_is_missing",
        lambda url: "gone.example" in url,
    )
    got = gather_candidates(["경제"], preferred_sites=["naver-finance"], max_items=10)
    urls = [item.url for item in got]
    assert "https://gone.example/a" not in urls
    assert "https://live.example/a" in urls
    assert "https://www.youtube.com/watch?v=abc" in urls


def test_youtube_rss_keeps_only_videos_over_10k_views():
    raw = """<?xml version="1.0"?>
    <feed xmlns:media="http://search.yahoo.com/mrss/">
      <entry>
        <title>히트</title>
        <link href="https://www.youtube.com/watch?v=hit01"/>
        <media:group><media:community><media:statistics views="10001"/></media:community></media:group>
      </entry>
      <entry>
        <title>저조회</title>
        <link href="https://www.youtube.com/watch?v=low01"/>
        <media:group><media:community><media:statistics views="20"/></media:community></media:group>
      </entry>
      <entry>
        <title>조회수없음</title>
        <link href="https://www.youtube.com/watch?v=none01"/>
      </entry>
    </feed>
    """
    items = sources_mod._parse_feed_body(
        "유튜브", "채널", "https://www.youtube.com/feeds/videos.xml", raw, limit=5
    )
    assert [item.url for item in items] == ["https://www.youtube.com/watch?v=hit01"]


def test_hn_rss_reads_points_past_summary_truncation():
    long_url = "https://example.com/" + ("a" * 180)
    raw = f"""<?xml version="1.0"?>
    <rss version="2.0">
      <channel>
        <item>
          <title>Show HN: widget</title>
          <link>https://news.ycombinator.com/item?id=1</link>
          <description><![CDATA[<p>Article URL: {long_url}</p><p>Comments URL: https://news.ycombinator.com/item?id=1</p><p>Points: 214</p><p># Comments: 87</p>]]></description>
          <pubDate>Mon, 17 Aug 2026 12:00:00 +0000</pubDate>
        </item>
      </channel>
    </rss>
    """
    now = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    items = sources_mod._parse_feed_body(
        "커뮤니티",
        "HN",
        "https://hnrss.org/frontpage",
        raw,
        limit=5,
        now=now,
    )
    assert len(items) == 1
    assert items[0].points == 214
    assert items[0].comments == 87
    assert items[0].list_rank == 1


def test_catalog_unknown_id_echoes_and_mega_has_groups():
    assert site_label("not-a-site") == "not-a-site"
    assert groups_for_mega("IT")
