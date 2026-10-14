"""Window boundaries and mutually exclusive tool classification."""
from datetime import datetime, timedelta

import pytest

from backend.app.models.trace import TraceEvent

NOW = datetime(2026, 10, 14, 3)


def add_trace(session, name, **kwargs):
    values = {
        "timestamp": NOW, "provider": "openai", "model_name": "m",
        "total_tokens": 10, "cost_source": "custom", "cost_usd": 1.0,
    }
    session.add(TraceEvent(trace_id=name, **{**values, **kwargs}))
    session.commit()


@pytest.mark.parametrize("endpoint", ["overview", "timeseries", "breakdown"])
def test_rolling_window_excludes_future_and_reads_now_once(client, session, monkeypatch, endpoint):
    calls = []

    def now():
        calls.append(True)
        return NOW

    monkeypatch.setattr("backend.app.services.aggregator.utc_now", now)
    monkeypatch.setattr("backend.app.routers.metrics.utc_now", now)
    add_trace(session, "start", timestamp=NOW - timedelta(hours=1))
    add_trace(session, "now")
    add_trace(session, "future", timestamp=NOW + timedelta(microseconds=1))
    add_trace(session, "old", timestamp=NOW - timedelta(hours=1, microseconds=1))
    response = client.get(f"/api/v1/metrics/{endpoint}", params={"hours_back": 1})
    assert response.status_code == 200
    body = response.json()
    count = (body["total_requests"] if endpoint == "overview" else
             sum(row["request_count" if endpoint == "timeseries" else "count"] for row in body))
    assert count == 2
    assert len(calls) == 1


@pytest.mark.parametrize("params", [
    {},
    {"start_time": NOW.isoformat()},
    {"start_time": NOW.isoformat(), "hours_back": 1},
    {"end_time": (NOW + timedelta(days=2)).isoformat(), "hours_back": 1},
])
def test_explicit_and_all_time_overview_contracts(client, session, monkeypatch, params):
    monkeypatch.setattr("backend.app.routers.metrics.utc_now", lambda: NOW)
    add_trace(session, "now")
    add_trace(session, "future", timestamp=NOW + timedelta(days=1))
    response = client.get("/api/v1/metrics/overview", params=params)
    assert response.status_code == 200
    assert response.json()["total_requests"] == 2
