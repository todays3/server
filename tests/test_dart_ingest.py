import io
import zipfile
from pathlib import Path

from app.services.dart_db import get_fact, search_paragraphs
from app.services.dart_ingest import ingest_zip_bytes, run_dart_ingest_sync


def _zip_with(xml: str, pdf_name: str = "report.pdf") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ifrs_2024.xml", xml)
        zf.writestr(pdf_name, b"%PDF-1.4 fake")
    return buf.getvalue()


XML = """<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
            xmlns:ifrs="http://xbrl.ifrs.org/taxonomy">
  <ifrs:Revenue>111000</ifrs:Revenue>
</xbrli:xbrl>
"""


def test_ingest_zip_xbrl_only_for_numbers_pdf_to_fts(tmp_path: Path, monkeypatch):
    db = tmp_path / "dart_financials.db"

    def fake_pdf(_data, *, doc_id, ticker):
        return [(doc_id, ticker, 1, "반도체 수요 증가가 실적을 이끌었습니다.", "반도체")]

    monkeypatch.setattr("app.services.dart_ingest.extract_paragraphs_from_bytes", fake_pdf)
    result = ingest_zip_bytes(
        _zip_with(XML),
        ticker="005930",
        year=2025,
        rcept_no="20250000000001",
        path=db,
    )
    assert result.fact_count == 1
    assert result.paragraph_count == 1
    fact = get_fact("005930", "revenue", 2025, path=db)
    assert fact is not None
    assert fact.value == 111000
    hits = search_paragraphs("005930", "반도체 수요", path=db)
    assert hits
    assert "111000" not in hits[0].paragraph_text


def test_offpeak_ingest_noops_when_disabled(monkeypatch):
    monkeypatch.setenv("DART_INGEST_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        assert run_dart_ingest_sync() == "disabled"
    finally:
        get_settings.cache_clear()
