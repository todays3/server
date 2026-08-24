from types import SimpleNamespace

from app.config import get_settings
from app.services.dart_pdf import extract_paragraphs_from_bytes


def test_ocr_skipped_when_disabled_even_if_flagged(monkeypatch):
    monkeypatch.setenv("DART_OCR_ENABLED", "false")
    get_settings.cache_clear()
    ocr_calls: list[int] = []

    def fake_extract(_data, pages=None):
        page = SimpleNamespace(page=0, markdown="본문 문단입니다.", needs_ocr=True)
        return SimpleNamespace(pages=[page], pages_needing_ocr=[1])

    def fake_ocr(_data, page_numbers):
        ocr_calls.extend(page_numbers)
        return {1: "ocr text"}

    monkeypatch.setattr("app.services.dart_pdf._extract_pages", fake_extract)
    monkeypatch.setattr("app.services.dart_pdf.ocr_flagged_pages", fake_ocr)
    try:
        paras = extract_paragraphs_from_bytes(b"%PDF-fake", doc_id="d1", ticker="005930")
        assert paras
        assert ocr_calls == []
        assert all("ocr text" not in p[3] for p in paras)
    finally:
        get_settings.cache_clear()


def test_ocr_only_flagged_pages_when_enabled(monkeypatch):
    monkeypatch.setenv("DART_OCR_ENABLED", "true")
    get_settings.cache_clear()
    ocr_calls: list[int] = []

    def fake_extract(_data, pages=None):
        good = SimpleNamespace(page=0, markdown="텍스트 페이지", needs_ocr=False)
        bad = SimpleNamespace(page=1, markdown="", needs_ocr=True)
        return SimpleNamespace(pages=[good, bad], pages_needing_ocr=[2])

    def fake_ocr(_data, page_numbers):
        ocr_calls.extend(page_numbers)
        return {n: "스캔 복원 문단" for n in page_numbers}

    monkeypatch.setattr("app.services.dart_pdf._extract_pages", fake_extract)
    monkeypatch.setattr("app.services.dart_pdf.ocr_flagged_pages", fake_ocr)
    try:
        paras = extract_paragraphs_from_bytes(b"%PDF-fake", doc_id="d1", ticker="005930")
        assert ocr_calls == [2]
        texts = [p[3] for p in paras]
        assert any("텍스트 페이지" in t for t in texts)
        assert any("스캔 복원" in t for t in texts)
    finally:
        get_settings.cache_clear()
