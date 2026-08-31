import json
from datetime import datetime, timezone

from app.models import CrawlRun, Digest
from app.routers.digests import _digest_out


def test_digest_out_exposes_crawl_summary_and_sample():
    digest = Digest(
        id=7,
        title="하루만장 · 9/1",
        body="반도체 브리핑",
        status="sent",
        delivery_channel="kakao_me",
        error_message="",
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        sent_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        items_json=json.dumps(
            [{"kind": "아티클", "title": "HBM 동향", "url": "https://example.com/hbm"}],
            ensure_ascii=False,
        ),
    )
    crawl = CrawlRun(
        id=8,
        digest_id=7,
        total_count=42,
        kinds_json=json.dumps({"아티클": 40, "커뮤니티": 2}, ensure_ascii=False),
        crawled_items_json=json.dumps(
            [
                {
                    "kind": "아티클",
                    "title": "HBM 동향",
                    "source": "IEEE",
                    "site_id": "ieee-xplore-semi",
                    "url": "https://example.com/hbm",
                }
            ],
            ensure_ascii=False,
        ),
    )

    out = _digest_out(digest, crawl)

    assert out.crawl_recorded is True
    assert out.crawled_total == 42
    assert out.crawled_kinds == {"아티클": 40, "커뮤니티": 2}
    assert out.crawled_items[0].source == "IEEE"


def test_digest_out_marks_test_send_without_crawl():
    digest = Digest(
        id=9,
        title="테스트",
        body="테스트",
        status="sent",
        delivery_channel="kakao_me",
        error_message="",
        created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        sent_at=None,
        items_json="[]",
    )

    out = _digest_out(digest)

    assert out.crawl_recorded is False
    assert out.crawled_total == 0
    assert out.crawled_items == []
