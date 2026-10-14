"""Real SQLite read snapshots stay consistent while WAL ingestion commits."""
from datetime import datetime

from sqlalchemy import event, func
from sqlmodel import Session, SQLModel, create_engine, select

from backend.app.models.trace import TraceEvent
from backend.app.services.aggregator import MetricAggregator


def test_overview_snapshot_allows_wal_writer_between_totals_and_latency(tmp_path):
    engine = create_engine(
        "sqlite:///" + (tmp_path / "snapshot.db").as_posix(), connect_args={"timeout": 0.5},
    )
    SQLModel.metadata.create_all(engine)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    with Session(engine) as setup:
        setup.add(TraceEvent(
            trace_id="initial", timestamp=datetime(2026, 10, 14), provider="p", model_name="m",
            status="SUCCESS", latency_ms=10, cost_source="custom", cost_usd=1,
        ))
        setup.commit()
    queries = []
    wrote = False

    def write_between_queries(connection, cursor, statement, parameters, context, executemany):
        nonlocal wrote
        if statement.lstrip().upper().startswith("SELECT") and not wrote:
            queries.append(statement)
            if len(queries) == 2:
                wrote = True
                with Session(engine) as writer:
                    for index in range(2):
                        writer.add(TraceEvent(
                            trace_id=f"error-{index}", timestamp=datetime(2026, 10, 14),
                            provider="p", model_name="m", status="ERROR", latency_ms=999,
                            cost_source="unknown", cost_usd=123,
                        ))
                    writer.commit()  # Must succeed with the reader's snapshot still open.

    event.listen(engine, "before_cursor_execute", write_between_queries)
    try:
        with Session(engine) as reader:
            result = MetricAggregator.get_overview_metrics(reader)
            assert wrote
            assert result["total_requests"] == 1
            assert result["error_count"] == 0 and result["success_count"] == 1
            assert result["error_rate_pct"] == 0 and result["cost_unknown_count"] == 0
            assert result["avg_latency_ms"] == result["p95_latency_ms"] == 10
            assert result["latency_measured_count"] == 1 and result["latency_sampled"] is False
            assert reader.connection().connection.driver_connection.in_transaction
        with Session(engine) as after:
            assert after.exec(select(func.count(TraceEvent.id))).one() == 3
    finally:
        event.remove(engine, "before_cursor_execute", write_between_queries)
        engine.dispose()


def test_metrics_does_not_rollback_callers_existing_transaction(session):
    session.add(TraceEvent(trace_id="uncommitted", provider="p", model_name="m"))
    session.flush()
    assert MetricAggregator.get_overview_metrics(session)["total_requests"] == 1
    session.commit()
    assert session.exec(select(func.count(TraceEvent.id))).one() == 1
