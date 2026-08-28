import concurrent.futures
import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from backend.app.db.session import get_session
from backend.app.main import app


@pytest.fixture(name="client")
def client_fixture():
    # Use temporary file-based SQLite database with WAL and busy timeout for true concurrency testing
    temp_dir = tempfile.mkdtemp()
    db_path = os.path.join(temp_dir, "test_stress.db")
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30.0},
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=15000;")
        cursor.close()

    SQLModel.metadata.create_all(engine)

    def get_session_override():
        with Session(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = get_session_override
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()
    SQLModel.metadata.drop_all(engine)
    engine.dispose()
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass


def test_concurrent_trace_ingestion_and_aggregation(client: TestClient):
    """Test sending 50 concurrent requests to verify thread safety and aggregation consistency."""
    total_calls = 50

    def send_trace(i: int):
        payload = {
            "trace_id": f"stress-trace-{i}",
            "provider": "openai" if i % 2 == 0 else "anthropic",
            "model_name": "gpt-4o" if i % 2 == 0 else "claude-3-5-sonnet",
            "prompt_tokens": 1000 + i,
            "completion_tokens": 500 + i,
            "latency_ms": 100.0 + (i * 2),
            "status": "SUCCESS" if i % 10 != 0 else "ERROR",
            "tags": ["stress-test"],
        }
        res = client.post("/api/v1/traces", json=payload)
        return res.status_code, res.text

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(send_trace, i) for i in range(total_calls)]
        results = [f.result() for f in futures]

    failed = [r for r in results if r[0] != 201]
    assert len(failed) == 0, f"Some requests failed: {failed}"

    # Verify overview counts match exactly
    overview_res = client.get("/api/v1/metrics/overview")
    assert overview_res.status_code == 200
    overview = overview_res.json()
    assert overview["total_requests"] == total_calls
    assert overview["error_count"] == 5  # 5 out of 50 failed
    assert overview["success_count"] == 45
    assert overview["error_rate_pct"] == 10.0
    assert overview["total_cost_usd"] > 0
    assert overview["p95_latency_ms"] > 0
