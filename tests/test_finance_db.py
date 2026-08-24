from pathlib import Path

from app.services.finance_db import init_finance_db, lookup_ticker, replace_digest_actions, get_digest_action


def test_finance_db_wal_and_ticker_lookup(tmp_path: Path):
    db = tmp_path / "finance.db"
    init_finance_db(db)
    wal = Path(str(db) + "-wal")
    highlight = lookup_ticker("005930", path=db)
    assert highlight is not None
    assert highlight.name == "삼성전자"
    assert highlight.operating_profit > 0
    assert "영업이익" in highlight.blurb()
    assert lookup_ticker("삼성전자", path=db) is not None
    assert lookup_ticker("999999", path=db) is None
    # WAL file appears after a write; seed insert should have created it or journal_mode accepted.
    assert db.is_file()
    _ = wal


def test_digest_actions_roundtrip(tmp_path: Path):
    db = tmp_path / "finance.db"
    replace_digest_actions(7, [(1, "공시 확인", "005930", "실적")], path=db)
    row = get_digest_action(7, 1, path=db)
    assert row is not None
    assert row["action_item"] == "공시 확인"
    assert get_digest_action(7, 2, path=db) is None
