"""Middleware behaviour: CORS on failures, body limits, pagination headers."""
from fastapi.testclient import TestClient

import backend.app.routers.metrics as metrics_router
from backend.app.core.config import Settings, settings

ORIGIN = {"Origin": "http://localhost:8000"}
TRACE = {"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 10}


def test_default_cors_origins_only_include_local_api(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert Settings(_env_file=None).cors_origin_list == [
        "http://localhost:8000", "http://127.0.0.1:8000",
    ]


def test_cors_headers_present_on_success(client: TestClient):
    res = client.get("/api/v1/metrics/overview", headers=ORIGIN)
    assert res.headers["access-control-allow-origin"] == ORIGIN["Origin"]


def test_cors_headers_present_on_server_error(client: TestClient, monkeypatch):
    """Without these, a cross-origin client sees an opaque network failure.

    The request-logging middleware builds the 500 response itself, so it has to
    sit inside CORSMiddleware for the headers to be applied on the way out.
    """
    def boom(*_args, **_kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(metrics_router.MetricAggregator, "get_overview_metrics", boom)
    res = client.get("/api/v1/metrics/overview", headers=ORIGIN)

    assert res.status_code == 500
    assert res.headers["access-control-allow-origin"] == ORIGIN["Origin"]
    assert res.json()["detail"] == "Internal server error."
    assert "kaboom" not in res.text


def test_error_response_carries_request_id(client: TestClient, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(metrics_router.MetricAggregator, "get_overview_metrics", boom)
    res = client.get("/api/v1/metrics/overview")
    assert res.json()["request_id"] == res.headers["X-Request-ID"]


def test_oversized_body_is_rejected_before_parsing(client: TestClient):
    res = client.post(
        "/api/v1/traces",
        json=TRACE,
        headers={**ORIGIN, "Content-Length": str(settings.MAX_REQUEST_BYTES + 1)},
    )
    assert res.status_code == 413
    assert res.headers["access-control-allow-origin"] == ORIGIN["Origin"]


def test_normal_body_passes(client: TestClient):
    assert client.post("/api/v1/traces", json=TRACE).status_code == 201


def test_malformed_content_length_is_rejected(client: TestClient):
    res = client.post("/api/v1/traces", json=TRACE, headers={"Content-Length": "not-a-number"})
    assert res.status_code == 400


def test_request_id_is_echoed_when_supplied(client: TestClient):
    res = client.get("/api/v1/metrics/overview", headers={"X-Request-ID": "caller-supplied-id"})
    assert res.headers["X-Request-ID"] == "caller-supplied-id"


def test_total_count_header_reflects_all_matches(client: TestClient):
    for _ in range(7):
        client.post("/api/v1/traces", json=TRACE)

    res = client.get("/api/v1/traces?limit=3")
    assert res.status_code == 200
    assert len(res.json()) == 3
    # The page is short of the total, which is exactly what a pager needs.
    assert res.headers["X-Total-Count"] == "7"


def test_total_count_respects_filters(client: TestClient):
    for _ in range(4):
        client.post("/api/v1/traces", json=TRACE)
    for _ in range(2):
        client.post("/api/v1/traces", json={**TRACE, "provider": "anthropic",
                                            "model_name": "claude-3-5-sonnet"})

    assert client.get("/api/v1/traces?provider=anthropic").headers["X-Total-Count"] == "2"
    assert client.get("/api/v1/traces").headers["X-Total-Count"] == "6"


def test_total_count_is_zero_when_empty(client: TestClient):
    assert client.get("/api/v1/traces").headers["X-Total-Count"] == "0"


def test_pagination_is_stable_across_pages(client: TestClient):
    """Same-timestamp rows need a tiebreak or a row can appear on two pages."""
    for i in range(10):
        client.post("/api/v1/traces", json={**TRACE, "trace_id": f"t{i}",
                                            "timestamp": "2026-08-28T00:00:00+00:00"})

    first = [t["trace_id"] for t in client.get("/api/v1/traces?limit=5&offset=0").json()]
    second = [t["trace_id"] for t in client.get("/api/v1/traces?limit=5&offset=5").json()]

    assert len(set(first) & set(second)) == 0
    assert len(set(first) | set(second)) == 10
