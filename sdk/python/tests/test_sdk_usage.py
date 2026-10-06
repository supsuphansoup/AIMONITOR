"""SDK usage extraction, auth headers, and background delivery."""
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from backend.app.db.session import get_session
from backend.app.main import app
from sdk.python.aimonitor.client import AIMonitor, extract_usage


@pytest.fixture(name="client")
def client_fixture():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        app.dependency_overrides[get_session] = lambda: session
        yield TestClient(app)
        app.dependency_overrides.clear()
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


# --- usage extraction ------------------------------------------------------
def test_extract_openai_shape():
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=120, completion_tokens=45))
    usage = extract_usage(response)
    assert usage["prompt_tokens"] == 120
    assert usage["completion_tokens"] == 45


def test_extract_anthropic_shape_with_cache_counters():
    response = SimpleNamespace(usage=SimpleNamespace(
        input_tokens=300,
        output_tokens=90,
        cache_read_input_tokens=8000,
        cache_creation_input_tokens=1500,
    ))
    usage = extract_usage(response)
    assert usage == {
        "prompt_tokens": 300,
        "completion_tokens": 90,
        "cache_read_tokens": 8000,
        "cache_write_tokens": 1500,
    }


def test_extract_google_shape():
    response = SimpleNamespace(usage_metadata=SimpleNamespace(
        prompt_token_count=500, candidates_token_count=200
    ))
    usage = extract_usage(response)
    assert usage["prompt_tokens"] == 500
    assert usage["completion_tokens"] == 200


def test_extract_openai_cached_tokens_are_split_out_of_prompt():
    """OpenAI's prompt_tokens includes the cached part, reported in a nested field."""
    response = SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=1000,
        completion_tokens=10,
        prompt_tokens_details=SimpleNamespace(cached_tokens=800),
    ))
    usage = extract_usage(response)
    assert usage["prompt_tokens"] == 200
    assert usage["cache_read_tokens"] == 800


def test_extract_openai_responses_api_shape():
    usage = extract_usage({"usage": {
        "input_tokens": 1000,
        "output_tokens": 10,
        "input_tokens_details": {"cached_tokens": 600},
    }})
    assert usage["prompt_tokens"] == 400
    assert usage["completion_tokens"] == 10
    assert usage["cache_read_tokens"] == 600


def test_extract_openai_without_cache_details():
    """The SDKs send prompt_tokens_details=None on models without caching."""
    response = SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=120, completion_tokens=45, prompt_tokens_details=None,
    ))
    usage = extract_usage(response)
    assert usage["prompt_tokens"] == 120
    assert usage["cache_read_tokens"] == 0


def test_extract_google_cached_tokens_are_not_counted_twice():
    """Gemini's prompt_token_count already includes cached_content_token_count."""
    response = SimpleNamespace(usage_metadata=SimpleNamespace(
        prompt_token_count=1000, candidates_token_count=10, cached_content_token_count=800,
    ))
    usage = extract_usage(response)
    assert usage["prompt_tokens"] == 200
    assert usage["cache_read_tokens"] == 800


def test_extract_google_cache_count_none():
    response = SimpleNamespace(usage_metadata=SimpleNamespace(
        prompt_token_count=500, candidates_token_count=200, cached_content_token_count=None,
    ))
    assert extract_usage(response)["prompt_tokens"] == 500


def test_extract_dict_shape():
    usage = extract_usage({"usage": {"prompt_tokens": 7, "completion_tokens": 3}})
    assert usage["prompt_tokens"] == 7


@pytest.mark.parametrize("value", [None, "a string", 42, SimpleNamespace(), {"no": "usage"}])
def test_extract_unknown_shapes_are_zero(value):
    assert extract_usage(value) == {
        "prompt_tokens": 0, "completion_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
    }


# --- decorator -------------------------------------------------------------
def test_observe_records_real_token_counts(client: TestClient):
    """The decorator used to log latency with zero tokens, so cost was always 0."""
    monitor = AIMonitor(api_url="http://testserver/api/v1")
    monitor._sync_client = client

    @monitor.observe(provider="openai", model_name="gpt-4o", tags=["decorator"])
    def call_model(_prompt: str):
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10_000, completion_tokens=5_000))

    call_model("hi")

    trace = client.get("/api/v1/traces").json()[0]
    assert trace["prompt_tokens"] == 10_000
    assert trace["completion_tokens"] == 5_000
    assert trace["cost_usd"] == 0.075
    assert trace["cost_source"] == "pricing_table"
    monitor.close()


def test_observe_can_skip_usage_capture(client: TestClient):
    monitor = AIMonitor(api_url="http://testserver/api/v1")
    monitor._sync_client = client

    @monitor.observe(provider="openai", model_name="gpt-4o", capture_usage=False)
    def call_model():
        return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=999, completion_tokens=999))

    call_model()
    assert client.get("/api/v1/traces").json()[0]["prompt_tokens"] == 0
    monitor.close()


def test_observe_records_errors_and_reraises(client: TestClient):
    monitor = AIMonitor(api_url="http://testserver/api/v1")
    monitor._sync_client = client

    @monitor.observe(provider="openai", model_name="gpt-4o")
    def failing_call():
        raise RuntimeError("upstream 429")

    with pytest.raises(RuntimeError, match="upstream 429"):
        failing_call()

    trace = client.get("/api/v1/traces").json()[0]
    assert trace["status"] == "ERROR"
    assert "upstream 429" in trace["error_message"]
    monitor.close()


def test_usage_extractor_failure_does_not_break_the_call(client: TestClient):
    monitor = AIMonitor(api_url="http://testserver/api/v1")
    monitor._sync_client = client

    def broken_extractor(_response):
        raise ValueError("extractor blew up")

    @monitor.observe(provider="openai", model_name="gpt-4o", usage_extractor=broken_extractor)
    def call_model():
        return "fine"

    assert call_model() == "fine"
    assert client.get("/api/v1/traces").json()[0]["status"] == "SUCCESS"
    monitor.close()


# --- transport -------------------------------------------------------------
def test_api_key_is_sent_as_header():
    monitor = AIMonitor(api_key="secret-123")
    assert monitor._sync_client.headers["X-API-Key"] == "secret-123"
    monitor.close()


def test_no_auth_header_without_key():
    monitor = AIMonitor()
    assert "X-API-Key" not in monitor._sync_client.headers
    monitor.close()


def test_transport_failure_is_swallowed():
    """An unreachable backend must not raise into the calling application."""
    monitor = AIMonitor(api_url="http://127.0.0.1:1/api/v1", timeout=0.2)
    assert monitor.track(provider="openai", model_name="gpt-4o") is None
    monitor.close()


def test_background_mode_batches_and_flushes(client: TestClient):
    monitor = AIMonitor(
        api_url="http://testserver/api/v1", background=True, batch_size=3, flush_interval=0.2
    )
    monitor._sync_client = client

    for i in range(6):
        assert monitor.enqueue(provider="openai", model_name="gpt-4o", prompt_tokens=i) is True

    monitor.flush(timeout=5.0)

    # An empty queue does not yet mean the worker has posted its current batch,
    # so poll. close() is deferred because it closes the shared TestClient.
    deadline = time.monotonic() + 5.0
    traces = []
    while time.monotonic() < deadline:
        traces = client.get("/api/v1/traces").json()
        if len(traces) == 6:
            break
        time.sleep(0.05)
    assert len(traces) == 6
    monitor.close()


def test_enqueue_without_background_falls_back_to_inline(client: TestClient):
    monitor = AIMonitor(api_url="http://testserver/api/v1")
    monitor._sync_client = client

    assert monitor.enqueue(provider="openai", model_name="gpt-4o", prompt_tokens=5) is True
    assert len(client.get("/api/v1/traces").json()) == 1
    monitor.close()


def test_full_queue_drops_instead_of_blocking():
    monitor = AIMonitor(api_url="http://127.0.0.1:1/api/v1", background=False)
    # Simulate a saturated queue without starting the worker thread.
    import queue as queue_module

    monitor._queue = queue_module.Queue(maxsize=1)
    assert monitor.enqueue(provider="openai", model_name="gpt-4o") is True
    assert monitor.enqueue(provider="openai", model_name="gpt-4o") is False
    monitor.close()
