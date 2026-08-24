from app.config import get_settings
from app.services.faiss_store import reset_faiss_for_tests, search_ticker, warmup_faiss


def test_hash_search_finds_seed_ticker(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DB_PATH", str(tmp_path / "finance.db"))
    monkeypatch.setenv("AGENT_FAISS_ENABLED", "false")
    get_settings.cache_clear()
    try:
        reset_faiss_for_tests()
        warmup_faiss()
        hit = search_ticker("005930")
        assert hit is not None
        assert hit.name == "삼성전자"
        named = search_ticker("삼성전자")
        assert named is not None
        assert named.ticker == "005930"
    finally:
        reset_faiss_for_tests()
        get_settings.cache_clear()
