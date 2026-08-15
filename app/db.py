from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def apply_sqlite_pragmas(bind=engine) -> None:
    """WAL + busy timeout so 2–3 concurrent digest writers do not stall the tick."""
    url = str(bind.url)
    if not url.startswith("sqlite"):
        return
    memory = url in {"sqlite://", "sqlite:///:memory:"} or ":memory:" in url
    with bind.begin() as conn:
        conn.execute(text("PRAGMA busy_timeout=5000"))
        if not memory:
            conn.execute(text("PRAGMA journal_mode=WAL"))


apply_sqlite_pragmas()


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
