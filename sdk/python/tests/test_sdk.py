import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from backend.app.db.session import get_session
from backend.app.main import app
from sdk.python.aimonitor.client import AIMonitor

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@pytest.fixture(name="client")
def client_fixture():
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        def get_session_override():
            return session

        app.dependency_overrides[get_session] = get_session_override
        test_client = TestClient(app)
        yield test_client
        app.dependency_overrides.clear()
    SQLModel.metadata.drop_all(engine)


def test_sdk_track_sync(client: TestClient):
    with AIMonitor(api_url="http://testserver/api/v1") as monitor:
        monitor._sync_client = client

        res = monitor.track(
            provider="openai",
            model_name="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            latency_ms=250.0,
            status="SUCCESS",
            tags=["sdk-sync-test"],
        )
        assert res is not None
        assert res["model_name"] == "gpt-4o"
        assert res["tags"] == "sdk-sync-test"


def test_sdk_observe_sync_decorator(client: TestClient):
    monitor = AIMonitor(api_url="http://testserver/api/v1")
    monitor._sync_client = client

    @monitor.observe(provider="anthropic", model_name="claude-3-5-sonnet", tags=["sync-decorator"])
    def mock_llm_call(prompt: str) -> str:
        return f"Sync Response to {prompt}"

    res = mock_llm_call("Hello AI")
    assert res == "Sync Response to Hello AI"

    traces_res = client.get("/api/v1/traces?provider=anthropic")
    assert traces_res.status_code == 200
    traces = traces_res.json()
    assert len(traces) == 1
    assert traces[0]["model_name"] == "claude-3-5-sonnet"
    assert traces[0]["status"] == "SUCCESS"
    monitor.close()


@pytest.mark.asyncio
async def test_sdk_observe_async_decorator():
    """Verify async def wrapper executes cleanly and measures latency."""
    async with AIMonitor(api_url="http://testserver/api/v1") as monitor:
        @monitor.observe(provider="gemini", model_name="gemini-1.5-pro", tags=["async-decorator"])
        async def mock_async_llm_call(prompt: str) -> str:
            await asyncio.sleep(0.01)
            return f"Async Response to {prompt}"

        res = await mock_async_llm_call("Hello Async")
        assert res == "Async Response to Hello Async"
