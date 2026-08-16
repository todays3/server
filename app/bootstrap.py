"""Startup helpers: schema patch for SQLite + seed account cleanup."""

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, engine, apply_sqlite_pragmas
from app.models import CrawlRun, Digest, KakaoAccount, LlmUsage, Preference, PushDevice, StickyNote, User


def ensure_schema() -> None:
    """Add new columns to existing SQLite DBs created before approval/OAuth flow."""
    apply_sqlite_pragmas()
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(users)")).fetchall()
        if not rows:
            return
        cols = {row[1] for row in rows}
        if "status" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN status VARCHAR(32) DEFAULT 'approved'"))
        if "is_admin" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0"))
        if "approved_at" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN approved_at DATETIME"))
        if "occupation" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN occupation VARCHAR(80) DEFAULT ''"))
        if "birth_date" not in cols:
            conn.execute(text("ALTER TABLE users ADD COLUMN birth_date DATE"))
        # SQLite cannot easily ALTER nullability; ORM already treats password_hash as optional.

        pref_rows = conn.execute(text("PRAGMA table_info(preferences)")).fetchall()
        if pref_rows:
            pref_cols = {row[1] for row in pref_rows}
            if "sources" not in pref_cols:
                conn.execute(
                    text(
                        "ALTER TABLE preferences ADD COLUMN sources TEXT "
                        "DEFAULT 'naver-finance,toss-securities,yahoo-finance'"
                    )
                )
            if "send_times" not in pref_cols:
                conn.execute(text("ALTER TABLE preferences ADD COLUMN send_times TEXT DEFAULT '07:30'"))
                # Backfill from legacy single hour/minute columns.
                conn.execute(
                    text(
                        "UPDATE preferences SET send_times = "
                        "printf('%02d:%02d', COALESCE(send_hour, 7), COALESCE(send_minute, 30)) "
                        "WHERE send_times IS NULL OR send_times = ''"
                    )
                )
            if "insight_questions" not in pref_cols:
                conn.execute(
                    text("ALTER TABLE preferences ADD COLUMN insight_questions BOOLEAN DEFAULT 0")
                )

        crawl_rows = conn.execute(text("PRAGMA table_info(crawl_runs)")).fetchall()
        if crawl_rows:
            crawl_cols = {row[1] for row in crawl_rows}
            int_cols = (
                "trigger_ms",
                "crawl_ms",
                "aggregation_ms",
                "llm_ms",
                "format_ms",
                "wait_ms",
                "send_ms",
                "total_ms",
                "lead_ms",
                "cpu_peak_percent",
                "rss_peak_bytes",
                "rss_delta_bytes",
            )
            for col in int_cols:
                if col not in crawl_cols:
                    conn.execute(text(f"ALTER TABLE crawl_runs ADD COLUMN {col} INTEGER DEFAULT 0"))
            if "curator" not in crawl_cols:
                conn.execute(text("ALTER TABLE crawl_runs ADD COLUMN curator VARCHAR(32) DEFAULT ''"))
            if "llm_skip_reason" not in crawl_cols:
                conn.execute(
                    text("ALTER TABLE crawl_runs ADD COLUMN llm_skip_reason VARCHAR(64) DEFAULT ''")
                )
            if "ready_at" not in crawl_cols:
                conn.execute(text("ALTER TABLE crawl_runs ADD COLUMN ready_at DATETIME"))
            if "sent_at" not in crawl_cols:
                conn.execute(text("ALTER TABLE crawl_runs ADD COLUMN sent_at DATETIME"))

        dig_rows = conn.execute(text("PRAGMA table_info(digests)")).fetchall()
        if dig_rows:
            dig_cols = {row[1] for row in dig_rows}
            if "items_json" not in dig_cols:
                conn.execute(text("ALTER TABLE digests ADD COLUMN items_json TEXT DEFAULT '[]'"))

        kakao_rows = conn.execute(text("PRAGMA table_info(kakao_accounts)")).fetchall()
        if kakao_rows:
            kakao_cols = {row[1] for row in kakao_rows}
            if "access_expires_at" not in kakao_cols:
                conn.execute(text("ALTER TABLE kakao_accounts ADD COLUMN access_expires_at DATETIME"))
            if "refresh_expires_at" not in kakao_cols:
                conn.execute(text("ALTER TABLE kakao_accounts ADD COLUMN refresh_expires_at DATETIME"))


def _delete_user_by_email(db: Session, email: str) -> None:
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user is None:
        return
    uid = user.id
    db.query(StickyNote).filter(StickyNote.user_id == uid).delete()
    db.query(PushDevice).filter(PushDevice.user_id == uid).delete()
    db.query(CrawlRun).filter(CrawlRun.user_id == uid).delete()
    db.query(LlmUsage).filter(LlmUsage.user_id == uid).delete()
    db.query(Digest).filter(Digest.user_id == uid).delete()
    db.query(KakaoAccount).filter(KakaoAccount.user_id == uid).delete()
    db.query(Preference).filter(Preference.user_id == uid).delete()
    db.delete(user)
    db.commit()


def seed_accounts() -> None:
    settings = get_settings()
    db: Session = SessionLocal()
    try:
        _delete_user_by_email(db, settings.admin_email)
        _delete_user_by_email(db, settings.test_user_email)
    finally:
        db.close()
