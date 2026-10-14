"""Collector retries are idempotent only when they supply a validated dedupe key."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex
from sqlmodel import Session, SQLModel, create_engine, select

from backend.app.db.session import ensure_schema
from backend.app.models.trace import BatchTraceCreate, TraceEvent, TraceEventCreate
from backend.app.routers import traces as router

TRACE = {"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 10}


def test_single_dedupe_returns_original_record(client, session):
    first = client.post("/api/v1/traces", json={**TRACE, "dedupe_key": "collector:abc-1"})
    again = client.post("/api/v1/traces", json={**TRACE, "dedupe_key": "collector:abc-1", "prompt_tokens": 20})
    assert first.status_code == again.status_code == 201
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["prompt_tokens"] == 10
    assert again.json()["dedupe_key"] == "collector:abc-1"
    assert len(session.exec(select(TraceEvent)).all()) == 1


def test_batch_dedupe_preserves_order_length_and_optional_duplicates(client, session):
    first = client.post("/api/v1/traces", json={**TRACE, "dedupe_key": "old"}).json()
    res = client.post("/api/v1/traces/batch", json={"traces": [
        {**TRACE, "dedupe_key": "new"}, {**TRACE, "dedupe_key": "old"},
        {**TRACE, "dedupe_key": "new"}, {**TRACE, "trace_id": "no-key"},
        {**TRACE, "trace_id": "no-key"},
    ]})
    assert res.status_code == 201
    rows = res.json()
    assert len(rows) == 5
    assert rows[0]["id"] == rows[2]["id"]
    assert rows[1]["id"] == first["id"]
    assert rows[3]["id"] != rows[4]["id"]
    assert len(session.exec(select(TraceEvent)).all()) == 4


@pytest.mark.parametrize("key", ["", "x" * 129, "white space", "한글", "slash/key", "line\nend"])
@pytest.mark.parametrize("batch", [False, True])
def test_invalid_dedupe_key_is_422_without_storage(client, session, key, batch):
    payload = {**TRACE, "dedupe_key": key}
    res = client.post("/api/v1/traces/batch" if batch else "/api/v1/traces",
                      json={"traces": [TRACE, payload]} if batch else payload)
    assert res.status_code == 422
    assert session.exec(select(TraceEvent)).all() == []


def test_partial_delivery_retry_keeps_201_events_not_401(client, session, monkeypatch):
    events = [{**TRACE, "dedupe_key": f"collector:{i}"} for i in range(201)]
    first = {"traces": events[:200]}
    last = {"traces": events[200:]}
    assert client.post("/api/v1/traces/batch", json=first).status_code == 201

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic second batch failure")

    with monkeypatch.context() as patch:
        patch.setattr(session, "refresh", fail)
        assert client.post("/api/v1/traces/batch", json=last).status_code == 500
    assert client.post("/api/v1/traces/batch", json=first).status_code == 201
    assert client.post("/api/v1/traces/batch", json=last).status_code == 201
    assert len(session.exec(select(TraceEvent)).all()) == 201


def test_deduped_batch_refresh_failure_rolls_back_new_rows(client, session, monkeypatch):
    assert client.post("/api/v1/traces", json={**TRACE, "dedupe_key": "old"}).status_code == 201

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic response failure")
    monkeypatch.setattr(session, "refresh", fail)
    res = client.post("/api/v1/traces/batch", json={"traces": [
        {**TRACE, "dedupe_key": "new"}, {**TRACE, "dedupe_key": "old"}, TRACE,
    ]})
    assert res.status_code == 500
    rows = session.exec(select(TraceEvent)).all()
    assert len(rows) == 1
    assert rows[0].dedupe_key == "old"


@pytest.mark.parametrize("batch", [False, True])
def test_dedupe_race_uses_one_persisted_row(tmp_path, batch):
    engine = create_engine("sqlite:///" + (tmp_path / "race.db").as_posix(),
                           connect_args={"check_same_thread": False, "timeout": 5})
    SQLModel.metadata.create_all(engine)
    barrier = threading.Barrier(8)

    def send(i):
        with Session(engine) as session:
            payload = {**TRACE, "dedupe_key": "shared"}
            barrier.wait(timeout=3)
            if batch:
                result = router.ingest_batch_traces(BatchTraceCreate(traces=[
                    payload, {**TRACE, "dedupe_key": f"unique:{i}"},
                ]), session)
                return result[0].id
            return router.ingest_trace(TraceEventCreate(**payload), session).id

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(send, range(8)))
        assert len(set(ids)) == 1
        with Session(engine) as session:
            assert len(session.exec(select(TraceEvent)).all()) == (9 if batch else 1)
    finally:
        engine.dispose()


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()], ids=["sqlite", "postgresql"])
def test_dedupe_index_and_insert_compile_for_supported_dialects(dialect):
    index = next((index for index in TraceEvent.__table__.indexes
                  if index.name == "ix_trace_events_dedupe_key"), None)
    assert index is not None
    ddl = str(CreateIndex(index).compile(dialect=dialect))
    assert "UNIQUE INDEX" in ddl
    assert "WHERE dedupe_key IS NOT NULL" in ddl
    event = router.process_single_trace_create(TraceEventCreate(**TRACE, dedupe_key="key"))
    statement = router._dedupe_insert_statement(event, dialect.name)
    sql = str(statement.compile(dialect=dialect))
    assert "ON CONFLICT (dedupe_key) WHERE dedupe_key IS NOT NULL DO NOTHING" in sql


def test_v05_upgrade_adds_nullable_dedupe_column_and_unique_partial_index(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "v05.db").as_posix())
    try:
        with engine.begin() as conn:
            conn.execute(text('''CREATE TABLE trace_events (
                id INTEGER PRIMARY KEY, timestamp DATETIME NOT NULL, trace_id VARCHAR NOT NULL,
                session_id VARCHAR, account_id VARCHAR(64), provider VARCHAR NOT NULL, model_name VARCHAR NOT NULL,
                prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,
                cache_read_tokens INTEGER NOT NULL DEFAULT 0, cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                cache_write_1h_tokens INTEGER NOT NULL DEFAULT 0, total_tokens INTEGER NOT NULL DEFAULT 0,
                latency_measured BOOLEAN NOT NULL DEFAULT 1, latency_ms FLOAT NOT NULL DEFAULT 0,
                cost_usd FLOAT NOT NULL DEFAULT 0, cost_source VARCHAR NOT NULL DEFAULT 'unknown',
                status VARCHAR NOT NULL DEFAULT 'SUCCESS', error_message VARCHAR, tags VARCHAR, metadata_json VARCHAR
            )'''))
            conn.execute(text("""INSERT INTO trace_events (id, timestamp, trace_id, provider, model_name)
                              VALUES (1, '2026-01-01 00:00:00', 'old', 'custom', 'old-model')"""))
        ensure_schema(engine)
        ensure_schema(engine)
        columns = {column["name"]: column for column in inspect(engine).get_columns("trace_events")}
        assert columns["dedupe_key"]["nullable"] is True
        indexes = {index["name"]: index for index in inspect(engine).get_indexes("trace_events")}
        assert indexes["ix_trace_events_dedupe_key"]["unique"] == 1
        with engine.connect() as conn:
            assert conn.execute(text("SELECT id, dedupe_key FROM trace_events")).all() == [(1, None)]
            ddl = conn.execute(text("SELECT sql FROM sqlite_master WHERE name='ix_trace_events_dedupe_key'")).scalar()
            assert "WHERE dedupe_key IS NOT NULL" in ddl
    finally:
        engine.dispose()
