"""CSV headers and streamed rows must describe one WAL read snapshot."""
import csv
import io
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func
from sqlmodel import Session, SQLModel, create_engine, select

from backend.app.db.session import get_session
from backend.app.main import app
from backend.app.models.trace import TraceEvent


@pytest.fixture
def wal_engine(tmp_path):
    engine = create_engine(
        "sqlite:///" + (tmp_path / "csv-snapshot.db").as_posix(),
        connect_args={"check_same_thread": False, "timeout": 1},
    )

    @event.listens_for(engine, "connect")
    def enable_wal(connection, _record):
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("PRAGMA busy_timeout=1000")

    SQLModel.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.mark.parametrize("row_count, limit", [(0, 2), (2, 3), (2, 2), (2, 1)])
def test_csv_count_and_stream_share_snapshot_with_concurrent_writer(wal_engine, monkeypatch, row_count, limit):
    start = datetime(2026, 10, 14, 12)
    with Session(wal_engine) as seed:
        for index in range(row_count):
            seed.add(TraceEvent(
                trace_id=f"before-{index}", provider="custom", model_name="snapshot-model",
                timestamp=start + timedelta(seconds=index),
            ))
        seed.commit()

    closed_sessions = []

    def read_session():
        with Session(wal_engine, expire_on_commit=False) as session:
            yield session
        closed_sessions.append(True)

    monkeypatch.setitem(app.dependency_overrides, get_session, read_session)
    writer_committed = []
    read_transactions = []

    def write_between_count_and_stream(connection, _cursor, statement, _params, _context, _many):
        sql = statement.lower().lstrip()
        if sql.startswith("select count(") and "trace_events" in sql and not writer_committed:
            read_transactions.append(connection.connection.driver_connection.in_transaction)
            with Session(wal_engine) as writer:
                writer_connection = writer.connection().connection.driver_connection
                assert writer_connection is not connection.connection.driver_connection
                writer.add(TraceEvent(
                    trace_id="after-count", provider="custom", model_name="snapshot-model",
                    timestamp=start + timedelta(minutes=1),
                ))
                # This commit runs while the CSV reader is open, so it also checks
                # that a WAL writer is not blocked by an exclusive read transaction.
                writer.commit()
            writer_committed.append(True)
        elif sql.startswith("select trace_events."):
            read_transactions.append(connection.connection.driver_connection.in_transaction)

    event.listen(wal_engine, "after_cursor_execute", write_between_count_and_stream)
    client = TestClient(app)
    try:
        response = client.get("/api/v1/traces/export.csv", params={"limit": limit})
    finally:
        client.close()
        event.remove(wal_engine, "after_cursor_execute", write_between_count_and_stream)

    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    expected_ids = [f"before-{index}" for index in reversed(range(row_count))][:limit]
    assert [row["trace_id"] for row in rows] == expected_ids
    assert response.headers["x-total-count"] == str(row_count)
    assert response.headers["x-export-partial"] == str(row_count > limit).lower()
    assert writer_committed == [True]
    assert read_transactions == [True, True]
    assert closed_sessions == [True]
    with Session(wal_engine) as current:
        assert current.exec(select(func.count(TraceEvent.id))).one() == row_count + 1
