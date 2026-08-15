from app.services.crawl_log import kind_counts
from app.services.sources import SourceItem


def test_kind_counts_groups_canonical_and_extra():
    items = [
        SourceItem(kind="아티클", title="a", url="https://a", summary="", source="s"),
        SourceItem(kind="아티클", title="b", url="https://b", summary="", source="s"),
        SourceItem(kind="유튜브", title="c", url="https://c", summary="", source="s"),
        SourceItem(kind="커뮤니티", title="d", url="https://d", summary="", source="s"),
        SourceItem(kind="기타", title="e", url="https://e", summary="", source="s"),
    ]
    counts = kind_counts(items)
    assert counts["아티클"] == 2
    assert counts["유튜브"] == 1
    assert counts["커뮤니티"] == 1
    assert counts["기타"] == 1
    assert sum(counts.values()) == 5
