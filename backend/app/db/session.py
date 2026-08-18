from typing import Generator
from sqlalchemy import event
from sqlmodel import SQLModel, create_engine, Session
from backend.app.core.config import settings

# SQLite 연결 설정 with WAL mode & busy timeout for concurrent writes
connect_args = {
    "check_same_thread": False,
    "timeout": 30.0,
} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args=connect_args,
)

# Enable WAL mode and busy_timeout for SQLite to handle concurrent requests seamlessly
if settings.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=10000;")
        cursor.close()


def init_db() -> None:
    """Initialize database tables."""
    SQLModel.metadata.create_all(engine)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency for yielding database session with expire_on_commit=False."""
    with Session(engine, expire_on_commit=False) as session:
        yield session
