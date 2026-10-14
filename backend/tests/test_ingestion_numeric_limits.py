"""Bound floating inputs before they can overflow aggregate cost or latency."""
import math
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.models.trace import TraceEvent

LIMIT = 1_000_000_000
TRACE = {"provider": "custom", "model_name": "bounded-model"}


def _post(client: TestClient, field: str, value, batch: bool):
    payload = {**TRACE, field: value}
    if batch:
        return client.post("/api/v1/traces/batch", json={"traces": [TRACE, payload]})
    return client.post("/api/v1/traces", json=payload)


@pytest.mark.parametrize("field", ["cost_usd", "latency_ms"])
@pytest.mark.parametrize("value", [math.nextafter(LIMIT, math.inf), LIMIT + 1, 1e308])
@pytest.mark.parametrize("batch", [False, True])
def test_float_limit_rejects_excess_without_storage(client: TestClient, session: Session, field, value, batch):
    response = _post(client, field, value, batch)
    assert response.status_code == 422
    assert session.exec(select(TraceEvent)).all() == []


@pytest.mark.parametrize("field", ["cost_usd", "latency_ms"])
@pytest.mark.parametrize("value", [math.nextafter(LIMIT, 0.0), LIMIT])
@pytest.mark.parametrize("batch", [False, True])
def test_float_limit_accepts_boundary(client: TestClient, field, value, batch):
    response = _post(client, field, value, batch)
    assert response.status_code == 201
    event = response.json()[-1] if batch else response.json()
    assert event[field] == pytest.approx(value)


@pytest.mark.parametrize("field", ["cost_usd", "latency_ms"])
@pytest.mark.parametrize("batch", [False, True])
def test_float_limit_preserves_negative_clamping(client: TestClient, field, batch):
    response = _post(client, field, -1e308, batch)
    assert response.status_code == 201
    event = response.json()[-1] if batch else response.json()
    assert event[field] == 0.0
    if field == "latency_ms":
        assert event["latency_measured"] is True


@pytest.mark.parametrize("endpoint", ["overview", "breakdown", "timeseries"])
def test_maximum_floats_remain_measured_and_priced_in_metrics(client: TestClient, monkeypatch, endpoint):
    import backend.app.routers.metrics as metrics_router
    import backend.app.services.aggregator as aggregator

    now = datetime(2026, 10, 14, 12)
    monkeypatch.setattr(metrics_router, "utc_now", lambda: now)
    monkeypatch.setattr(aggregator, "utc_now", lambda: now)
    payload = {
        **TRACE, "cost_usd": LIMIT, "latency_ms": LIMIT,
        "timestamp": (now - timedelta(minutes=5)).isoformat() + "+00:00",
    }
    assert client.post("/api/v1/traces/batch", json={"traces": [payload] * 3}).status_code == 201
    response = client.get("/api/v1/metrics/" + endpoint)
    assert response.status_code == 200
    points = [response.json()] if endpoint == "overview" else response.json()
    assert len(points) == 1
    point = points[0]
    assert point["total_cost_usd"] == 3 * LIMIT
    assert point["cost_unknown_count"] == 0
    assert point["avg_latency_ms"] == LIMIT
    assert math.isfinite(point["total_cost_usd"])
    assert math.isfinite(point["avg_latency_ms"])
    count_field = {"overview": "total_requests", "breakdown": "count", "timeseries": "request_count"}[endpoint]
    assert point[count_field] == 3
    if endpoint == "overview":
        assert point["latency_measured_count"] == 3
        assert point["p95_latency_ms"] == LIMIT
