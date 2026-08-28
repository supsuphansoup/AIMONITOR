import logging
from typing import Any, Generator, Optional

from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from backend.app.core.config import settings

logger = logging.getLogger(__name__)

# SQLite connection settings: WAL mode plus a busy timeout so concurrent
# ingestion does not fail with "database is locked".
connect_args = (
    {"check_same_thread": False, "timeout": 30.0} if settings.is_sqlite else {}
)

engine = create_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args=connect_args,
    pool_pre_ping=True,
)


if settings.is_sqlite:

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=15000;")
        # Off by default in SQLite; without it alert history can outlive its rule.
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()


def _literal_default(column: Any) -> Optional[str]:
    """Render a column scalar default as SQL, or None when there is not one."""
    default = getattr(column, "default", None)
    if default is None or getattr(default, "is_callable", False):
        return None
    value = getattr(default, "arg", None)
    if value is None or callable(value):
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def _add_column_ddl(column: Any, target_engine: Engine) -> Optional[str]:
    """Build an ALTER TABLE ADD COLUMN clause for a newly introduced column."""
    type_sql = column.type.compile(dialect=target_engine.dialect)
    default_sql = _literal_default(column)
    parts = [f'"{column.name}"', type_sql]

    if not column.nullable:
        if default_sql is None:
            # SQLite refuses a NOT NULL column with no default on an existing
            # table; adding it as nullable keeps the upgrade non-destructive.
            logger.warning(
                "Column %s is NOT NULL without a scalar default; adding it as nullable.",
                column.name,
            )
        else:
            parts.append(f"NOT NULL DEFAULT {default_sql}")
    elif default_sql is not None:
        parts.append(f"DEFAULT {default_sql}")

    return " ".join(parts)


def ensure_schema(target_engine: Optional[Engine] = None) -> None:
    """Add columns introduced since a database was first created.

    A lightweight substitute for a migration tool: it never drops or retypes
    anything, so upgrading an existing deployment keeps its history intact.
    """
    target_engine = target_engine or engine
    inspector = inspect(target_engine)
    existing_tables = set(inspector.get_table_names())

    for table in SQLModel.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        present = {col["name"] for col in inspector.get_columns(table.name)}
        missing = [c for c in table.columns if c.name not in present]
        if not missing:
            continue
        with target_engine.begin() as conn:
            for column in missing:
                clause = _add_column_ddl(column, target_engine)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN {clause}'))
                logger.info("Schema upgrade: added %s.%s", table.name, column.name)


def init_db() -> None:
    """Create missing tables, then backfill columns added by later versions."""
    SQLModel.metadata.create_all(engine)
    ensure_schema(engine)


def check_db() -> bool:
    """Readiness probe: confirm the database answers a trivial query."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # pragma: no cover - depends on external state
        logger.error("Database readiness check failed: %s", exc)
        return False


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session with expire_on_commit disabled."""
    with Session(engine, expire_on_commit=False) as session:
        yield session
