"""Schema upgrades on an existing database.

Columns added after v0.1.0 must appear in a database created by the older
version, otherwise upgrading in place means losing every stored trace.
"""
import os
import tempfile

import pytest
from sqlalchemy import inspect, text
from sqlmodel import create_engine

from backend.app.db.session import ensure_schema

#: The v0.1.0 trace_events table, before cache tokens and cost_source existed.
LEGACY_TRACE_TABLE = """
CREATE TABLE trace_events (
    id INTEGER NOT NULL PRIMARY KEY,
    trace_id VARCHAR NOT NULL,
    session_id VARCHAR,
    provider VARCHAR NOT NULL,
    model_name VARCHAR NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    latency_ms FLOAT NOT NULL,
    cost_usd FLOAT NOT NULL,
    status VARCHAR NOT NULL,
    error_message VARCHAR,
    tags VARCHAR,
    metadata_json VARCHAR,
    timestamp DATETIME NOT NULL
)
"""

LEGACY_ALERT_TABLE = """
CREATE TABLE alert_rules (
    id INTEGER NOT NULL PRIMARY KEY,
    name VARCHAR NOT NULL,
    metric_type VARCHAR NOT NULL,
    operator VARCHAR NOT NULL,
    threshold_value FLOAT NOT NULL,
    window_minutes INTEGER NOT NULL,
    webhook_url VARCHAR,
    is_active BOOLEAN NOT NULL,
    created_at DATETIME NOT NULL
)
"""


@pytest.fixture(name="legacy_engine")
def legacy_engine_fixture():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(text(LEGACY_TRACE_TABLE))
        conn.execute(text(LEGACY_ALERT_TABLE))
        conn.execute(text(
            "INSERT INTO trace_events (trace_id, session_id, provider, model_name, "
            "prompt_tokens, completion_tokens, total_tokens, latency_ms, cost_usd, "
            "status, timestamp) VALUES "
            "('legacy-1', NULL, 'openai', 'gpt-4o', 100, 50, 150, 200.0, 0.001, "
            "'SUCCESS', '2026-01-01 00:00:00')"
        ))
    yield engine
    engine.dispose()
    try:
        os.remove(path)
    except OSError:
        pass


def test_new_columns_are_added_to_an_old_database(legacy_engine):
    ensure_schema(legacy_engine)

    columns = {c["name"] for c in inspect(legacy_engine).get_columns("trace_events")}
    assert {"cache_read_tokens", "cache_write_tokens", "cost_source"} <= columns

    rule_columns = {c["name"] for c in inspect(legacy_engine).get_columns("alert_rules")}
    assert {"cooldown_minutes", "last_triggered_at"} <= rule_columns


def test_existing_rows_survive_and_get_defaults(legacy_engine):
    ensure_schema(legacy_engine)
    with legacy_engine.connect() as conn:
        row = conn.execute(text(
            "SELECT trace_id, cache_read_tokens, cache_write_tokens, cost_source "
            "FROM trace_events"
        )).one()
    assert row[0] == "legacy-1"
    assert row[1] == 0
    assert row[2] == 0
    assert row[3] == "unknown"


def test_ensure_schema_is_idempotent(legacy_engine):
    ensure_schema(legacy_engine)
    ensure_schema(legacy_engine)  # must not raise "duplicate column name"
    with legacy_engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM trace_events")).scalar() == 1
