"""Source gather + catalog parity tests."""

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


def test_catalog_payload_shape():
    payload = catalog_payload()
    assert payload["groups"]
    assert "IT" in payload["mega_map"]
    assert all("sites" in g for g in payload["groups"])


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


def test_catalog_unknown_id_echoes_and_mega_has_groups():
    assert site_label("not-a-site") == "not-a-site"
    assert groups_for_mega("IT")
