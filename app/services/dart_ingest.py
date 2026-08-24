"""Off-peak DART zip ingest. XBRL → facts, PDF → FTS5. Never blocks the ASGI loop."""

from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from app.config import get_settings
from app.services.dart_db import init_dart_db, insert_paragraphs, is_ingested, mark_ingested, upsert_facts
from app.services.dart_pdf import extract_paragraphs_from_bytes
from app.services.xbrl_parser import parse_xbrl_xml

log = logging.getLogger(__name__)

MAX_ZIP_BYTES = 20 * 1024 * 1024
CORP_CODES = {"005930": "00126380", "000660": "00164779"}
LIST_URL = "https://opendart.fss.or.kr/api/list.json"
DOC_URL = "https://opendart.fss.or.kr/api/document.xml"


@dataclass(frozen=True)
class IngestResult:
    fact_count: int
    paragraph_count: int
    skipped: str = ""


def ingest_zip_bytes(
    data: bytes,
    *,
    ticker: str,
    year: int,
    rcept_no: str = "",
    path: Path | None = None,
) -> IngestResult:
    code = ticker.strip().zfill(6) if ticker.strip().isdigit() else ticker.strip().upper()
    if len(data) > MAX_ZIP_BYTES:
        return IngestResult(0, 0, "too_large")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return IngestResult(0, 0, "bad_zip")

    facts = []
    paragraphs: list[tuple[str, str, int, str, str]] = []
    try:
        for name in archive.namelist():
            lower = name.lower()
            if lower.endswith(".xsd") or "schema" in lower:
                continue
            if lower.endswith((".xml", ".xbrl")):
                try:
                    xml = archive.read(name).decode("utf-8", errors="replace")
                    facts.extend(parse_xbrl_xml(xml, ticker=code, year=year))
                except Exception:
                    log.exception("XBRL parse failed for %s", name)
            elif lower.endswith(".pdf"):
                try:
                    pdf = archive.read(name)
                    if len(pdf) > MAX_ZIP_BYTES:
                        continue
                    doc_id = rcept_no or name
                    paragraphs.extend(
                        extract_paragraphs_from_bytes(pdf, doc_id=doc_id, ticker=code)
                    )
                except Exception:
                    log.exception("PDF inspect failed for %s", name)
    finally:
        archive.close()

    facts = facts[:50]
    paragraphs = paragraphs[:400]
    try:
        if facts:
            upsert_facts(facts, path=path)
        if paragraphs:
            insert_paragraphs(paragraphs, path=path)
        if rcept_no:
            mark_ingested(rcept_no, code, year, path=path)
    except Exception:
        log.exception("dart sqlite write failed")
        return IngestResult(0, 0, "db_error")
    return IngestResult(len(facts), len(paragraphs))


def run_dart_ingest_sync() -> str:
    """CPU-bound ingest for the off-peak cron. No-ops unless explicitly enabled."""
    settings = get_settings()
    if not settings.dart_ingest_enabled:
        return "disabled"
    key = (settings.dart_api_key or "").strip()
    if not key:
        return "no_key"
    init_dart_db()
    tickers = [part.strip() for part in (settings.dart_tickers or "").split(",") if part.strip()]
    ingested = 0
    for raw in tickers[:4]:
        ticker = raw.zfill(6) if raw.isdigit() else raw.upper()
        try:
            rcept_no, year = fetch_latest_rcept(ticker, key)
            if not rcept_no:
                continue
            if is_ingested(rcept_no):
                continue
            blob = download_document_zip(rcept_no, key)
            if not blob:
                continue
            ingest_zip_bytes(blob, ticker=ticker, year=year, rcept_no=rcept_no)
            ingested += 1
        except Exception:
            log.exception("dart ingest skipped for %s", ticker)
    return f"ingested:{ingested}"


def fetch_latest_rcept(ticker: str, api_key: str) -> tuple[str, int]:
    corp = CORP_CODES.get(ticker)
    if not corp:
        return "", 0
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    bgn = (now - timedelta(days=400)).strftime("%Y%m%d")
    end = now.strftime("%Y%m%d")
    try:
        with httpx.Client(timeout=httpx.Timeout(20.0, connect=3.0)) as client:
            res = client.get(
                LIST_URL,
                params={
                    "crtfc_key": api_key,
                    "corp_code": corp,
                    "bgn_de": bgn,
                    "end_de": end,
                    "pblntf_detail_ty": "A001",
                    "page_count": 5,
                },
            )
            res.raise_for_status()
            payload = res.json()
    except (httpx.HTTPError, ValueError, OSError) as exc:
        log.warning("dart list failed: %s", exc)
        return "", 0
    rows = payload.get("list") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        return "", 0
    first = rows[0] if isinstance(rows[0], dict) else {}
    rcept = str(first.get("rcept_no") or "")
    rcept_dt = str(first.get("rcept_dt") or now.strftime("%Y%m%d"))
    year = int(rcept_dt[:4]) if rcept_dt[:4].isdigit() else now.year
    return rcept, year


def download_document_zip(rcept_no: str, api_key: str) -> bytes:
    try:
        with httpx.Client(timeout=httpx.Timeout(45.0, connect=3.0)) as client:
            res = client.get(DOC_URL, params={"crtfc_key": api_key, "rcept_no": rcept_no})
            res.raise_for_status()
            blob = res.content
    except (httpx.HTTPError, OSError) as exc:
        log.warning("dart document download failed: %s", exc)
        return b""
    if len(blob) > MAX_ZIP_BYTES:
        log.warning("dart zip too large (%s bytes)", len(blob))
        return b""
    return blob
