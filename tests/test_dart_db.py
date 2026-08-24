import sqlite3
from pathlib import Path

from app.services.dart_db import (
    PdfHit,
    XbrlFact,
    get_fact,
    init_dart_db,
    insert_paragraphs,
    search_paragraphs,
    upsert_facts,
)


def test_dart_db_wal_seed_and_xbrl_lookup(tmp_path: Path):
    db = tmp_path / "dart_financials.db"
    init_dart_db(db)
    conn = sqlite3.connect(db)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
        fts = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pdf_fts'"
        ).fetchone()
        assert fts is not None
    finally:
        conn.close()

    fact = get_fact("005930", "revenue", 2024, path=db)
    assert fact is not None
    assert fact.value == 300_870_903_000_000
    assert fact.account_name == "매출액"
    assert get_fact("삼성전자", "revenue", 2024, path=db) is None
    assert get_fact("005930", "revenue", 2010, path=db) is None


def test_fts5_paragraph_search_does_not_write_xbrl(tmp_path: Path):
    db = tmp_path / "dart_financials.db"
    init_dart_db(db)
    insert_paragraphs(
        [
            (
                "doc-1",
                "005930",
                3,
                "환율 영향은 제한적이었고 반도체 수요가 회복되었습니다.",
                "환율 반도체",
            )
        ],
        path=db,
    )
    hits = search_paragraphs("005930", "반도체 수요", path=db)
    assert hits
    assert all(isinstance(h, PdfHit) for h in hits)
    assert any("반도체" in h.paragraph_text for h in hits)
    before = get_fact("005930", "revenue", 2024, path=db)
    assert before is not None
    upsert_facts(
        [XbrlFact("005930", 2024, 0, "revenue", "매출액", before.value)],
        path=db,
    )
    after = get_fact("005930", "revenue", 2024, path=db)
    assert after is not None
    assert after.value == before.value
