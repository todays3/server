"""pdf-inspector adapter. OCR only on flagged pages, and only when enabled."""

from __future__ import annotations

import logging
import re
from typing import Any

from app.config import get_settings

log = logging.getLogger(__name__)

_PARA_SPLIT = re.compile(r"\n\s*\n")


class InspectorUnavailable(RuntimeError):
    """pdf-inspector is optional and may be missing on CI/16GB hosts."""


def _extract_pages(data: bytes, pages: list[int] | None = None) -> Any:
    try:
        import pdf_inspector
    except ImportError as exc:
        raise InspectorUnavailable("pdf-inspector is not installed") from exc
    return pdf_inspector.extract_pages_markdown_bytes(data, pages=pages)


def ocr_flagged_pages(data: bytes, page_numbers: list[int]) -> dict[int, str]:
    """OCR only 1-indexed pages that pdf-inspector flagged. Never the whole PDF."""
    settings = get_settings()
    if not settings.dart_ocr_enabled or not page_numbers:
        return {}
    try:
        import pdf_inspector

        fn = getattr(pdf_inspector, "process_pdf_with_ocr", None)
        if callable(fn):
            zero = [max(0, int(n) - 1) for n in page_numbers]
            result = fn(data, pages=zero)
            markdown = getattr(result, "markdown", None) or ""
            if markdown.strip():
                return {int(page_numbers[0]): markdown.strip()[:4000]}
    except Exception:
        log.warning("pdf-inspector OCR helper unavailable", exc_info=True)
    return _tesseract_pages(data, page_numbers)


def _tesseract_pages(data: bytes, page_numbers: list[int]) -> dict[int, str]:
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
    except ImportError:
        log.warning("OCR enabled but pytesseract/pdf2image are not installed")
        return {}
    out: dict[int, str] = {}
    for page_no in page_numbers[:3]:
        try:
            images = convert_from_bytes(
                data,
                first_page=int(page_no),
                last_page=int(page_no),
                dpi=150,
            )
            if not images:
                continue
            text = pytesseract.image_to_string(images[0], lang="kor+eng")
            if text.strip():
                out[int(page_no)] = text.strip()[:4000]
        except Exception:
            log.exception("tesseract failed on page %s", page_no)
    return out


def extract_paragraphs_from_bytes(
    data: bytes,
    *,
    doc_id: str,
    ticker: str,
) -> list[tuple[str, str, int, str, str]]:
    """Return FTS rows (doc_id, ticker, page, paragraph_text, context_tags)."""
    try:
        result = _extract_pages(data)
    except InspectorUnavailable:
        log.warning("pdf-inspector missing; skip PDF text for %s", doc_id)
        return []
    except Exception:
        log.exception("pdf-inspector failed for %s", doc_id)
        return []

    flagged = [int(n) for n in (getattr(result, "pages_needing_ocr", None) or [])]
    ocr_text: dict[int, str] = {}
    if get_settings().dart_ocr_enabled and flagged:
        try:
            ocr_text = ocr_flagged_pages(data, flagged)
        except Exception:
            log.exception("selective OCR failed for %s", doc_id)
            ocr_text = {}

    rows: list[tuple[str, str, int, str, str]] = []
    for page in getattr(result, "pages", None) or []:
        page_idx = int(getattr(page, "page", 0) or 0)
        page_no = page_idx + 1
        markdown = (getattr(page, "markdown", None) or "").strip()
        if page_no in ocr_text:
            markdown = ocr_text[page_no]
        for para in _split_paragraphs(markdown):
            rows.append((doc_id, ticker, page_no, para[:2000], _tags(para)))
            if len(rows) >= 400:
                return rows
    for page_no, text in ocr_text.items():
        if any(row[2] == page_no for row in rows):
            continue
        for para in _split_paragraphs(text):
            rows.append((doc_id, ticker, page_no, para[:2000], _tags(para)))
    return rows[:400]


def _split_paragraphs(markdown: str) -> list[str]:
    chunks = [part.strip() for part in _PARA_SPLIT.split(markdown or "") if part.strip()]
    if not chunks and (markdown or "").strip():
        chunks = [markdown.strip()]
    return [c for c in chunks if len(c) >= 4][:40]


def _tags(paragraph: str) -> str:
    tokens = [tok for tok in re.split(r"\s+", paragraph) if len(tok) >= 2][:4]
    return " ".join(tokens)[:80]
