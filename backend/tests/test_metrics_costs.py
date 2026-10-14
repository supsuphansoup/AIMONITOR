"""Time-series costs retain uncertainty independently of the known subtotal."""
from datetime import datetime

import pytest

from backend.app.models.trace import TraceEvent


@pytest.mark.parametrize("costs,expected_cost,unknown", [
    ([("unknown", 123.0)], 0.0, 1),
    ([("unknown", 123.0), ("custom", 2.0)], 2.0, 1),
    ([("local_free", 0.0)], 0.0, 0),
    ([("custom", 0.0)], 0.0, 0),
])
def test_timeseries_cost_unknown_count(client, session, monkeypatch, costs, expected_cost, unknown):
    now = datetime(2026, 10, 14, 3)
    monkeypatch.setattr("backend.app.services.aggregator.utc_now", lambda: now)
    for index, (source, amount) in enumerate(costs):
        session.add(TraceEvent(
            trace_id=f"cost-{index}", timestamp=now, provider="p", model_name="m",
            cost_source=source, cost_usd=amount,
        ))
    session.commit()
    response = client.get("/api/v1/metrics/timeseries")
    assert response.status_code == 200
    bucket = response.json()[0]
    assert bucket["total_cost_usd"] == expected_cost
    assert bucket["cost_unknown_count"] == unknown
    assert type(bucket["cost_unknown_count"]) is int
