"""KOSPI/KOSDAQ financials in a SQLite file separate from user/token data."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from app.config import get_settings

_lock = Lock()
_initialized_path = ""

SEED_ROWS: tuple[tuple[str, str, str, str, int, int], ...] = (
    ("005930", "KOSPI", "삼성전자", "전자", 300_000_000, 32_000_000),
    ("000660", "KOSPI", "SK하이닉스", "반도체", 66_000_000, 23_000_000),
    ("207940", "KOSPI", "삼성바이오로직스", "바이오", 4_000_000, 1_100_000),
    ("373220", "KOSPI", "LG에너지솔루션", "2차전지", 25_000_000, 2_000_000),
    ("005380", "KOSPI", "현대차", "자동차", 150_000_000, 14_000_000),
    ("000270", "KOSPI", "기아", "자동차", 100_000_000, 12_000_000),
    ("035420", "KOSPI", "네이버", "인터넷", 10_000_000, 1_500_000),
    ("035720", "KOSPI", "카카오", "인터넷", 8_000_000, 400_000),
    ("066570", "KOSPI", "LG전자", "전자", 80_000_000, 3_500_000),
    ("012330", "KOSPI", "현대모비스", "자동차부품", 50_000_000, 3_000_000),
    ("247540", "KOSDAQ", "에코프로비엠", "2차전지", 6_000_000, 400_000),
    ("086520", "KOSDAQ", "에코프로", "2차전지", 3_000_000, 200_000),
    ("028300", "KOSDAQ", "HLB", "바이오", 200_000, -50_000),
    ("196170", "KOSDAQ", "알테오젠", "바이오", 150_000, 20_000),
    ("041510", "KOSDAQ", "에스엠", "엔터", 900_000, 150_000),
)


@dataclass(frozen=True)
class FinancialHighlight:
    ticker: str
    market: str
    name: str
    sector: str
    revenue: int
    operating_profit: int

    def blurb(self) -> str:
        return (
            f"{self.name}({self.ticker}, {self.market}) "
            f"매출 {self.revenue:,}백만 · 영업이익 {self.operating_profit:,}백만"
        )


def finance_db_path() -> Path:
    settings = get_settings()
    raw = (settings.finance_db_path or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parent.parent.parent / "finance.db"


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_finance_db(path: Path | None = None) -> Path:
    """Create WAL schema and seed a small KOSPI/KOSDAQ snapshot if empty."""
    global _initialized_path
    db_path = path or finance_db_path()
    with _lock:
        conn = _connect(db_path)
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS companies (
                    ticker TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    name TEXT NOT NULL,
                    sector TEXT NOT NULL DEFAULT '',
                    revenue INTEGER NOT NULL DEFAULT 0,
                    operating_profit INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS digest_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    item_index INTEGER NOT NULL,
                    action_item TEXT NOT NULL,
                    ticker TEXT NOT NULL DEFAULT '',
                    insight TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_digest_actions_user ON digest_actions(user_id, created_at DESC);
                """
            )
            count = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
            if count == 0:
                conn.executemany(
                    "INSERT INTO companies(ticker, market, name, sector, revenue, operating_profit) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    SEED_ROWS,
                )
            conn.commit()
        finally:
            conn.close()
        _initialized_path = str(db_path)
    return db_path


def lookup_ticker(ticker: str, *, path: Path | None = None) -> FinancialHighlight | None:
    code = (ticker or "").strip().upper()
    if not code:
        return None
    if code.isdigit():
        code = code.zfill(6)
    db_path = path or finance_db_path()
    init_finance_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT ticker, market, name, sector, revenue, operating_profit FROM companies "
            "WHERE ticker = ? OR name = ? COLLATE NOCASE",
            (code, ticker.strip()),
        ).fetchone()
        if row is None:
            return None
        return FinancialHighlight(
            ticker=row["ticker"],
            market=row["market"],
            name=row["name"],
            sector=row["sector"],
            revenue=int(row["revenue"]),
            operating_profit=int(row["operating_profit"]),
        )
    finally:
        conn.close()


def list_companies(*, path: Path | None = None) -> list[FinancialHighlight]:
    db_path = path or finance_db_path()
    init_finance_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT ticker, market, name, sector, revenue, operating_profit FROM companies ORDER BY ticker"
        ).fetchall()
        return [
            FinancialHighlight(
                ticker=row["ticker"],
                market=row["market"],
                name=row["name"],
                sector=row["sector"],
                revenue=int(row["revenue"]),
                operating_profit=int(row["operating_profit"]),
            )
            for row in rows
        ]
    finally:
        conn.close()


def replace_digest_actions(user_id: int, actions: list[tuple[int, str, str, str]], *, path: Path | None = None) -> None:
    db_path = path or finance_db_path()
    init_finance_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM digest_actions WHERE user_id = ?", (int(user_id),))
        conn.executemany(
            "INSERT INTO digest_actions(user_id, item_index, action_item, ticker, insight) VALUES (?, ?, ?, ?, ?)",
            [(int(user_id), idx, action, ticker, insight) for idx, action, ticker, insight in actions],
        )
        conn.commit()
    finally:
        conn.close()


def get_digest_action(user_id: int, item_index: int, *, path: Path | None = None) -> dict[str, str] | None:
    db_path = path or finance_db_path()
    init_finance_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT item_index, action_item, ticker, insight FROM digest_actions "
            "WHERE user_id = ? AND item_index = ? ORDER BY id DESC LIMIT 1",
            (int(user_id), int(item_index)),
        ).fetchone()
        if row is None:
            return None
        return {
            "item_index": str(row["item_index"]),
            "action_item": row["action_item"],
            "ticker": row["ticker"],
            "insight": row["insight"],
        }
    finally:
        conn.close()
