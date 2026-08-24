"""DART XBRL + PDF FTS5 store. Separate from user tokens and finance.db."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from app.config import get_settings

_lock = Lock()

SEED_FACTS: tuple[tuple[str, int, int, str, str, int], ...] = (
    ("005930", 2023, 0, "revenue", "매출액", 258_935_494_000_000),
    ("005930", 2024, 0, "revenue", "매출액", 300_870_903_000_000),
    ("005930", 2023, 0, "operating_profit", "영업이익", 6_569_920_000_000),
    ("005930", 2024, 0, "operating_profit", "영업이익", 32_726_131_000_000),
    ("005930", 2023, 0, "net_income", "당기순이익", 15_487_100_000_000),
    ("005930", 2024, 0, "net_income", "당기순이익", 33_621_351_000_000),
    ("000660", 2024, 0, "revenue", "매출액", 66_192_960_000_000),
    ("000660", 2024, 0, "operating_profit", "영업이익", 23_467_360_000_000),
)

SEED_PARAS: tuple[tuple[str, str, int, str, str], ...] = (
    (
        "seed-005930-2024",
        "005930",
        12,
        "반도체 수요 증가와 HBM 판매 확대가 실적 개선의 주요 배경입니다. 환율 영향은 제한적이었습니다.",
        "반도체 수요 환율",
    ),
)


@dataclass(frozen=True)
class XbrlFact:
    ticker: str
    year: int
    quarter: int
    account: str
    account_name: str
    value: int


@dataclass(frozen=True)
class PdfHit:
    doc_id: str
    page: int
    paragraph_text: str
    context_tags: str


def dart_db_path() -> Path:
    settings = get_settings()
    raw = (settings.dart_db_path or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent.parent.parent / "dart_financials.db"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_dart_db(path: Path | None = None) -> Path:
    db_path = path or dart_db_path()
    with _lock:
        conn = _connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS xbrl_facts (
                    ticker TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    quarter INTEGER NOT NULL DEFAULT 0,
                    account TEXT NOT NULL,
                    account_name TEXT NOT NULL,
                    value INTEGER NOT NULL,
                    PRIMARY KEY (ticker, year, quarter, account)
                );
                CREATE TABLE IF NOT EXISTS pdf_paragraphs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    page INTEGER NOT NULL,
                    paragraph_text TEXT NOT NULL,
                    context_tags TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_pdf_ticker ON pdf_paragraphs(ticker);
                CREATE TABLE IF NOT EXISTS dart_filings (
                    rcept_no TEXT PRIMARY KEY,
                    ticker TEXT NOT NULL,
                    year INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'ok',
                    ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS pdf_fts USING fts5(
                    paragraph_text,
                    context_tags,
                    content='pdf_paragraphs',
                    content_rowid='id',
                    tokenize='unicode61'
                );
                CREATE TRIGGER IF NOT EXISTS pdf_ai AFTER INSERT ON pdf_paragraphs BEGIN
                    INSERT INTO pdf_fts(rowid, paragraph_text, context_tags)
                    VALUES (new.id, new.paragraph_text, new.context_tags);
                END;
                CREATE TRIGGER IF NOT EXISTS pdf_ad AFTER DELETE ON pdf_paragraphs BEGIN
                    INSERT INTO pdf_fts(pdf_fts, rowid, paragraph_text, context_tags)
                    VALUES ('delete', old.id, old.paragraph_text, old.context_tags);
                END;
                """
            )
            count = conn.execute("SELECT COUNT(*) FROM xbrl_facts").fetchone()[0]
            if count == 0:
                conn.executemany(
                    "INSERT INTO xbrl_facts(ticker, year, quarter, account, account_name, value) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    SEED_FACTS,
                )
                conn.executemany(
                    "INSERT INTO pdf_paragraphs(doc_id, ticker, page, paragraph_text, context_tags) "
                    "VALUES (?, ?, ?, ?, ?)",
                    SEED_PARAS,
                )
            conn.commit()
        finally:
            conn.close()
    return db_path


def upsert_facts(facts: list[XbrlFact], *, path: Path | None = None) -> int:
    if not facts:
        return 0
    db_path = path or dart_db_path()
    init_dart_db(db_path)
    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO xbrl_facts(ticker, year, quarter, account, account_name, value) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(f.ticker, f.year, f.quarter, f.account, f.account_name, f.value) for f in facts],
        )
        conn.commit()
        return len(facts)
    finally:
        conn.close()


def insert_paragraphs(
    rows: list[tuple[str, str, int, str, str]],
    *,
    path: Path | None = None,
) -> int:
    if not rows:
        return 0
    db_path = path or dart_db_path()
    init_dart_db(db_path)
    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO pdf_paragraphs(doc_id, ticker, page, paragraph_text, context_tags) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_fact(
    ticker: str,
    account: str,
    year: int,
    quarter: int = 0,
    *,
    path: Path | None = None,
) -> XbrlFact | None:
    code = _norm_ticker(ticker)
    db_path = path or dart_db_path()
    init_dart_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT ticker, year, quarter, account, account_name, value FROM xbrl_facts "
            "WHERE ticker = ? AND account = ? AND year = ? AND quarter = ?",
            (code, account, int(year), int(quarter)),
        ).fetchone()
        if row is None:
            return None
        return XbrlFact(
            ticker=row["ticker"],
            year=int(row["year"]),
            quarter=int(row["quarter"]),
            account=row["account"],
            account_name=row["account_name"],
            value=int(row["value"]),
        )
    finally:
        conn.close()


def search_paragraphs(
    ticker: str,
    query: str,
    *,
    limit: int = 3,
    path: Path | None = None,
) -> list[PdfHit]:
    code = _norm_ticker(ticker)
    db_path = path or dart_db_path()
    init_dart_db(db_path)
    conn = _connect(db_path)
    try:
        match = _fts_query(query)
        rows = []
        if match:
            try:
                rows = conn.execute(
                    "SELECT p.doc_id, p.page, p.paragraph_text, p.context_tags "
                    "FROM pdf_fts f JOIN pdf_paragraphs p ON p.id = f.rowid "
                    "WHERE p.ticker = ? AND pdf_fts MATCH ? LIMIT ?",
                    (code, match, int(limit)),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            like = f"%{(query or '').strip()[:40]}%"
            rows = conn.execute(
                "SELECT doc_id, page, paragraph_text, context_tags FROM pdf_paragraphs "
                "WHERE ticker = ? AND (paragraph_text LIKE ? OR context_tags LIKE ?) LIMIT ?",
                (code, like, like, int(limit)),
            ).fetchall()
        if not rows:
            for tok in [t.strip('"') for t in match.split(" AND ") if t.strip()]:
                if len(tok) < 2:
                    continue
                like = f"%{tok}%"
                rows = conn.execute(
                    "SELECT doc_id, page, paragraph_text, context_tags FROM pdf_paragraphs "
                    "WHERE ticker = ? AND (paragraph_text LIKE ? OR context_tags LIKE ?) LIMIT ?",
                    (code, like, like, int(limit)),
                ).fetchall()
                if rows:
                    break
        if not rows:
            rows = conn.execute(
                "SELECT doc_id, page, paragraph_text, context_tags FROM pdf_paragraphs "
                "WHERE ticker = ? LIMIT ?",
                (code, int(limit)),
            ).fetchall()
        return [
            PdfHit(
                doc_id=row["doc_id"],
                page=int(row["page"]),
                paragraph_text=row["paragraph_text"],
                context_tags=row["context_tags"],
            )
            for row in rows
        ]
    finally:
        conn.close()


def list_facts(
    ticker: str,
    *,
    year: int | None = None,
    limit: int = 12,
    path: Path | None = None,
) -> list[XbrlFact]:
    code = _norm_ticker(ticker)
    db_path = path or dart_db_path()
    init_dart_db(db_path)
    conn = _connect(db_path)
    try:
        if year is None:
            rows = conn.execute(
                "SELECT ticker, year, quarter, account, account_name, value FROM xbrl_facts "
                "WHERE ticker = ? ORDER BY year DESC, account LIMIT ?",
                (code, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ticker, year, quarter, account, account_name, value FROM xbrl_facts "
                "WHERE ticker = ? AND year = ? ORDER BY account LIMIT ?",
                (code, int(year), int(limit)),
            ).fetchall()
        return [
            XbrlFact(
                ticker=row["ticker"],
                year=int(row["year"]),
                quarter=int(row["quarter"]),
                account=row["account"],
                account_name=row["account_name"],
                value=int(row["value"]),
            )
            for row in rows
        ]
    finally:
        conn.close()


def is_ingested(rcept_no: str, *, path: Path | None = None) -> bool:
    if not (rcept_no or "").strip():
        return False
    db_path = path or dart_db_path()
    init_dart_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM dart_filings WHERE rcept_no = ?",
            (rcept_no.strip(),),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def mark_ingested(rcept_no: str, ticker: str, year: int, *, path: Path | None = None) -> None:
    if not (rcept_no or "").strip():
        return
    db_path = path or dart_db_path()
    init_dart_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO dart_filings(rcept_no, ticker, year, status) VALUES (?, ?, ?, 'ok')",
            (rcept_no.strip(), _norm_ticker(ticker), int(year)),
        )
        conn.commit()
    finally:
        conn.close()


def _norm_ticker(ticker: str) -> str:
    raw = (ticker or "").strip().upper()
    if raw.isdigit():
        return raw.zfill(6)
    return raw


_FTS_STOP = {
    "때문에",
    "왜",
    "어떻게",
    "알려줘",
    "관련",
    "그리고",
    "으로",
    "에서",
    "대한",
    "있나",
    "좋아졌나",
    "실적이",
}


def _fts_query(raw: str) -> str:
    tokens = [tok for tok in (raw or "").replace('"', " ").split() if len(tok) >= 2]
    cleaned: list[str] = []
    for tok in tokens[:8]:
        if tok in _FTS_STOP:
            continue
        safe = "".join(ch for ch in tok if ch.isalnum() or ("\uac00" <= ch <= "\ud7a3"))
        if safe and safe not in _FTS_STOP:
            cleaned.append(f'"{safe}"')
        if len(cleaned) >= 4:
            break
    return " AND ".join(cleaned)
