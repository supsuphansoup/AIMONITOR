"""Dialect portability.

The application is only exercised against SQLite, but `DATABASE_URL` accepts
anything SQLAlchemy supports. Compiling every query the aggregator builds
against the PostgreSQL dialect catches SQLite-only SQL without needing a server
to test against - it is a guardrail, not a substitute for running on Postgres.
"""
import pytest
from sqlalchemy import case, func, select
from sqlalchemy.dialects import postgresql

from backend.app.models.alert import AlertHistory, AlertRule
from backend.app.models.trace import TraceEvent
from backend.app.services.cost import SOURCE_UNKNOWN

DIALECT = postgresql.dialect()


def _compiles(statement) -> str:
    return str(statement.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True}))


def test_overview_totals_compile_for_postgres():
    sql = _compiles(
        select(
            func.count(TraceEvent.id),
            func.sum(TraceEvent.cost_usd),
            func.sum(TraceEvent.total_tokens),
            func.avg(TraceEvent.latency_ms),
        )
    )
    assert "count(" in sql and "avg(" in sql


def test_breakdown_group_by_compiles_for_postgres():
    """The CASE expressions here are the most dialect-sensitive part."""
    sql = _compiles(
        select(
            TraceEvent.provider,
            TraceEvent.model_name,
            func.count(TraceEvent.id),
            func.sum(case((TraceEvent.status != "SUCCESS", 1), else_=0)),
            func.sum(case((TraceEvent.cost_source == SOURCE_UNKNOWN, 1), else_=0)),
        )
        .group_by(TraceEvent.provider, TraceEvent.model_name)
        .order_by(func.count(TraceEvent.id).desc())
    )
    assert "GROUP BY" in sql
    assert "CASE WHEN" in sql


def test_alert_claim_update_compiles_for_postgres():
    """The compare-and-set that makes concurrent evaluation safe."""
    from datetime import datetime, timedelta

    from sqlalchemy import update

    now = datetime(2026, 1, 1)
    cutoff = now - timedelta(minutes=15)
    sql = _compiles(
        update(AlertRule)
        .where(
            (AlertRule.id == 1)
            & (
                AlertRule.last_triggered_at.is_(None)
                | (AlertRule.last_triggered_at <= cutoff)
            )
        )
        .values(last_triggered_at=now)
    )
    assert "UPDATE alert_rules SET last_triggered_at" in sql
    assert "IS NULL" in sql


def test_retention_delete_compiles_for_postgres():
    from datetime import datetime

    from sqlalchemy import delete

    cutoff = datetime(2026, 1, 1)
    assert "DELETE FROM trace_events" in _compiles(
        delete(TraceEvent).where(TraceEvent.timestamp < cutoff)
    )
    assert "DELETE FROM alert_histories" in _compiles(
        delete(AlertHistory).where(AlertHistory.created_at < cutoff)
    )


@pytest.mark.parametrize("dialect_name", ["postgresql", "mysql"])
def test_schema_ddl_compiles_for_other_dialects(dialect_name):
    """Table definitions must not rely on SQLite-specific types."""
    from sqlalchemy.dialects import mysql
    from sqlalchemy.schema import CreateTable

    dialect = {"postgresql": postgresql.dialect(), "mysql": mysql.dialect()}[dialect_name]
    for model in (TraceEvent, AlertRule, AlertHistory):
        ddl = str(CreateTable(model.__table__).compile(dialect=dialect))
        assert model.__tablename__ in ddl
