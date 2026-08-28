import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from backend.app.db.session import get_session
from backend.app.main import app

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@pytest.fixture(name="session")
def session_fixture():
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)


@pytest.fixture(name="client")
def client_fixture(session: Session):
    def get_session_override():
        return session

    app.dependency_overrides[get_session] = get_session_override
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_metrics_overview_empty(client: TestClient):
    res = client.get("/api/v1/metrics/overview")
    assert res.status_code == 200
    data = res.json()
    assert data["total_requests"] == 0
    assert data["total_cost_usd"] == 0.0
    assert data["error_rate_pct"] == 0.0


def test_metrics_overview_with_data(client: TestClient):
    # Ingest 2 success, 1 error
    client.post(
        "/api/v1/traces",
        json={"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 1000, "completion_tokens": 500, "latency_ms": 200.0, "status": "SUCCESS"},
    )
    client.post(
        "/api/v1/traces",
        json={"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 2000, "completion_tokens": 1000, "latency_ms": 400.0, "status": "SUCCESS"},
    )
    client.post(
        "/api/v1/traces",
        json={"provider": "anthropic", "model_name": "claude-3-5-sonnet", "prompt_tokens": 500, "completion_tokens": 200, "latency_ms": 600.0, "status": "ERROR"},
    )

    res = client.get("/api/v1/metrics/overview")
    assert res.status_code == 200
    data = res.json()
    assert data["total_requests"] == 3
    assert data["success_count"] == 2
    assert data["error_count"] == 1
    assert data["error_rate_pct"] == 33.33
    assert data["avg_latency_ms"] == 400.0
    assert data["total_tokens"] == (1500 + 3000 + 700)
    assert data["total_cost_usd"] > 0


def test_metrics_breakdown(client: TestClient):
    client.post(
        "/api/v1/traces",
        json={"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 100, "completion_tokens": 50},
    )
    client.post(
        "/api/v1/traces",
        json={"provider": "anthropic", "model_name": "claude-3-5-haiku", "prompt_tokens": 100, "completion_tokens": 50},
    )

    res = client.get("/api/v1/metrics/breakdown")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 2
    assert any(d["provider"] == "openai" for d in data)
    assert any(d["provider"] == "anthropic" for d in data)


def test_metrics_timeseries(client: TestClient):
    client.post(
        "/api/v1/traces",
        json={"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 100, "completion_tokens": 50, "latency_ms": 120.0},
    )
    res = client.get("/api/v1/metrics/timeseries?hours_back=1&interval_minutes=5")
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    assert data[0]["request_count"] == 1
    assert data[0]["avg_latency_ms"] == 120.0
