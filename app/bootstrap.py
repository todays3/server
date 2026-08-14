"""Startup helpers: schema patch for SQLite + seed accounts."""

from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import get_settings
from app.db import SessionLocal, engine
from app.models import Preference, User


def ensure_schema() -> None:
    """Add new columns to existing SQLite DBs created before approval/OAuth flow."""
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

        dig_rows = conn.execute(text("PRAGMA table_info(digests)")).fetchall()
        if dig_rows:
            dig_cols = {row[1] for row in dig_rows}
            if "items_json" not in dig_cols:
                conn.execute(text("ALTER TABLE digests ADD COLUMN items_json TEXT DEFAULT '[]'"))


def _ensure_user(
    db: Session,
    *,
    email: str,
    password: str,
    display_name: str,
    is_admin: bool,
) -> None:
    settings = get_settings()
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user is None:
        user = User(
            email=email.lower(),
            display_name=display_name,
            password_hash=hash_password(password),
            status="approved",
            is_admin=is_admin,
            approved_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.flush()
        db.add(
            Preference(
                user_id=user.id,
                topics="경제/주식/국내증시,경제/주식/미국증시",
                tone="",
                timezone=settings.default_timezone,
            )
        )
        db.commit()
        return

    changed = False
    if user.is_admin != is_admin:
        user.is_admin = is_admin
        changed = True
    if user.status != "approved":
        user.status = "approved"
        user.approved_at = datetime.now(timezone.utc)
        changed = True
    if changed:
        db.commit()


def seed_accounts() -> None:
    settings = get_settings()
    db: Session = SessionLocal()
    try:
        _ensure_user(
            db,
            email=settings.admin_email,
            password=settings.admin_password,
            display_name=settings.admin_name,
            is_admin=True,
        )
        _ensure_user(
            db,
            email=settings.test_user_email,
            password=settings.test_user_password,
            display_name=settings.test_user_name,
            is_admin=False,
        )
    finally:
        db.close()
