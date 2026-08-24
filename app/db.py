from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

connect_args: dict = {}
if settings.database_url.startswith("sqlite"):
    # timeout: sqlite3 waits this many seconds on lock (pairs with busy_timeout).
    connect_args = {"check_same_thread": False, "timeout": 30.0}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def apply_sqlite_pragmas(bind=engine) -> None:
    """WAL + busy timeout so digest prepare does not starve auth / send writers."""
    url = str(bind.url)
    if not url.startswith("sqlite"):
        return
    memory = url in {"sqlite://", "sqlite:///:memory:"} or ":memory:" in url
    with bind.begin() as conn:
        conn.execute(text("PRAGMA busy_timeout=30000"))
        if not memory:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA synchronous=NORMAL"))


apply_sqlite_pragmas()


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
