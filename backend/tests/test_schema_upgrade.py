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
    assert {
        "cache_read_tokens", "cache_write_tokens", "cache_write_1h_tokens", "latency_measured",
        "cost_source", "account_id",
    } <= columns

    rule_columns = {c["name"] for c in inspect(legacy_engine).get_columns("alert_rules")}
    assert {"cooldown_minutes", "last_triggered_at"} <= rule_columns


def test_existing_rows_survive_and_get_defaults(legacy_engine):
    ensure_schema(legacy_engine)
    with legacy_engine.connect() as conn:
        row = conn.execute(text(
            "SELECT trace_id, cache_read_tokens, cache_write_tokens, cost_source, account_id "
            "FROM trace_events"
        )).one()
    assert row[0] == "legacy-1"
    assert row[1] == 0
    assert row[2] == 0
    assert row[3] == "unknown"
    assert row[4] is None


def test_ensure_schema_is_idempotent(legacy_engine):
    ensure_schema(legacy_engine)
    ensure_schema(legacy_engine)  # must not raise "duplicate column name"
    with legacy_engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM trace_events")).scalar() == 1


def test_missing_indexes_are_created(legacy_engine):
    """A new indexed column arrives unindexed unless CREATE INDEX also runs.

    ALTER TABLE ADD COLUMN cannot carry an index, so every query filtering on
    cost_source would fall back to a full scan on an upgraded database.
    """
    before = {i["name"] for i in inspect(legacy_engine).get_indexes("trace_events")}
    assert "ix_trace_events_cost_source" not in before

    ensure_schema(legacy_engine)

    after = {i["name"] for i in inspect(legacy_engine).get_indexes("trace_events")}
    assert "ix_trace_events_cost_source" in after
    # v0.1.0 only ever indexed timestamp; the rest are backfilled too.
    assert {"ix_trace_events_provider", "ix_trace_events_status"} <= after


def test_index_creation_is_idempotent(legacy_engine):
    ensure_schema(legacy_engine)
    first = {i["name"] for i in inspect(legacy_engine).get_indexes("trace_events")}
    ensure_schema(legacy_engine)
    assert {i["name"] for i in inspect(legacy_engine).get_indexes("trace_events")} == first


def test_ensure_schema_works_without_the_models_imported():
    """init_db() must not depend on the caller having imported the models.

    Tables register on SQLModel.metadata as an import side effect, so a script
    that only imports the session module would otherwise operate on empty
    metadata and silently create nothing.
    """
    import pathlib
    import subprocess
    import sys

    # A fresh interpreter is the only way to test this: by the time pytest runs,
    # conftest has already imported the models for every other test.
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    script = (
        "import tempfile, os\n"
        "from sqlalchemy import create_engine, text, inspect\n"
        "from backend.app.db.session import ensure_schema\n"
        "fd, p = tempfile.mkstemp(suffix='.db'); os.close(fd)\n"
        "e = create_engine('sqlite:///' + p)\n"
        f"""with e.begin() as c: c.execute(text('''{LEGACY_TRACE_TABLE}'''))\n"""
        "ensure_schema(e)\n"
        "cols = {c['name'] for c in inspect(e).get_columns('trace_events')}\n"
        "print('RESULT', 'cost_source' in cols)\n"
        "e.dispose(); os.remove(p)\n"
    )
    env = {**os.environ, "PYTHONPATH": str(repo_root)}
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, cwd=str(repo_root), env=env,
    )
    assert "RESULT True" in result.stdout, result.stderr


def test_desktop_columns_defaults_and_index_are_backfilled(legacy_engine):
    ensure_schema(legacy_engine)
    with legacy_engine.connect() as conn:
        row = conn.execute(text(
            "SELECT cache_write_1h_tokens, latency_measured, latency_ms FROM trace_events"
        )).one()
    assert tuple(row) == (0, 1, 200.0)
    indexes = {i["name"]: i["column_names"] for i in inspect(legacy_engine).get_indexes("trace_events")}
    assert indexes["ix_trace_events_measured_timestamp"] == ["latency_measured", "timestamp", "id"]
    assert indexes["ix_trace_events_account_timestamp"] == ["account_id", "timestamp"]
    assert indexes["ix_trace_events_account_id"] == ["account_id"]


def test_upgrade_existing_database_copy_preserves_source(legacy_engine, tmp_path):
    import sqlite3

    source_path = legacy_engine.url.database
    copy_path = tmp_path / "copy.db"
    with sqlite3.connect(f"file:{source_path}?mode=ro", uri=True) as source:
        with sqlite3.connect(copy_path) as destination:
            source.backup(destination)
    copy_engine = create_engine(f"sqlite:///{copy_path}")
    try:
        ensure_schema(copy_engine)
        ensure_schema(copy_engine)
        with copy_engine.connect() as conn:
            row = conn.execute(text("SELECT trace_id, account_id FROM trace_events")).one()
        assert tuple(row) == ("legacy-1", None)
        assert "account_id" not in {c["name"] for c in inspect(legacy_engine).get_columns("trace_events")}
    finally:
        copy_engine.dispose()
