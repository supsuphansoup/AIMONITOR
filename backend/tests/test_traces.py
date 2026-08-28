import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from backend.app.db.session import get_session
from backend.app.main import app

# In-memory test SQLite DB
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


def test_ingest_single_trace(client: TestClient):
    payload = {
        "provider": "openai",
        "model_name": "gpt-4o",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "latency_ms": 320.5,
        "status": "SUCCESS",
        "tags": ["production", "chatbot"],
        "metadata": {"user_id": "usr_123"},
    }
    response = client.post("/api/v1/traces", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["provider"] == "openai"
    assert data["model_name"] == "gpt-4o"
    assert data["total_tokens"] == 1500
    assert data["latency_ms"] == 320.5
    assert data["cost_usd"] > 0
    assert "trace_id" in data
    assert data["tags"] == "production,chatbot"


def test_ingest_batch_traces(client: TestClient):
    batch_payload = {
        "traces": [
            {
                "provider": "anthropic",
                "model_name": "claude-3-5-sonnet",
                "prompt_tokens": 2000,
                "completion_tokens": 1000,
                "latency_ms": 650.0,
                "status": "SUCCESS",
            },
            {
                "provider": "openai",
                "model_name": "gpt-4o-mini",
                "prompt_tokens": 500,
                "completion_tokens": 200,
                "latency_ms": 150.0,
                "status": "ERROR",
                "error_message": "Rate limit exceeded (429)",
            },
        ]
    }
    response = client.post("/api/v1/traces/batch", json=batch_payload)
    assert response.status_code == 201
    data = response.json()
    assert len(data) == 2
    assert data[1]["status"] == "ERROR"
    assert data[1]["error_message"] == "Rate limit exceeded (429)"


def test_list_and_filter_traces(client: TestClient):
    # Ingest 3 traces
    client.post(
        "/api/v1/traces",
        json={"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 100, "completion_tokens": 50, "status": "SUCCESS"},
    )
    client.post(
        "/api/v1/traces",
        json={"provider": "anthropic", "model_name": "claude-3-5-sonnet", "prompt_tokens": 200, "completion_tokens": 100, "status": "SUCCESS"},
    )
    client.post(
        "/api/v1/traces",
        json={"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 300, "completion_tokens": 150, "status": "ERROR"},
    )

    # Filter by provider=openai
    res = client.get("/api/v1/traces?provider=openai")
    assert res.status_code == 200
    assert len(res.json()) == 2

    # Filter by status=ERROR
    res_err = client.get("/api/v1/traces?status=ERROR")
    assert res_err.status_code == 200
    assert len(res_err.json()) == 1


def test_get_trace_by_id(client: TestClient):
    res_create = client.post(
        "/api/v1/traces",
        json={"trace_id": "test-trace-unique-001", "provider": "gemini", "model_name": "gemini-1.5-pro", "prompt_tokens": 100, "completion_tokens": 50},
    )
    assert res_create.status_code == 201

    res_get = client.get("/api/v1/traces/test-trace-unique-001")
    assert res_get.status_code == 200
    assert res_get.json()["trace_id"] == "test-trace-unique-001"

    # Non-existent ID
    res_404 = client.get("/api/v1/traces/non-existent-id")
    assert res_404.status_code == 404
