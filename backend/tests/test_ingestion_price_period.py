"""A trace is priced at the rate in effect when it happened (CST-05 wired through ingestion)."""
import pytest

PROMO_END = "2027-01-01T00:00:00Z"


def _post(client, **extra):
    payload = {"provider": "gemini", "model_name": "gemini-3.8-flash", "prompt_tokens": 1000, **extra}
    response = client.post("/api/v1/traces", json=payload)
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize("timestamp,expected", [
    ("2026-12-31T23:59:59Z", 0.00075),   # promotional input rate, 0.75 USD/1M
    (PROMO_END, 0.0015),                 # standard rate from 2027-01-01 00:00 UTC
    ("2027-01-01T08:30:00+09:00", 0.00075),  # still 2026-12-31 in UTC
    ("2027-01-01T09:30:00+09:00", 0.0015),
])
def test_record_time_selects_the_price_period(client, timestamp, expected):
    body = _post(client, timestamp=timestamp)
    assert body["cost_source"] == "pricing_table"
    assert body["cost_usd"] == pytest.approx(expected)


def test_batch_prices_each_record_by_its_own_time(client):
    traces = [
        {"provider": "gemini", "model_name": "gemini-3.8-flash", "prompt_tokens": 1000,
         "timestamp": "2026-12-31T12:00:00Z"},
        {"provider": "gemini", "model_name": "gemini-3.8-flash", "prompt_tokens": 1000,
         "timestamp": "2027-01-02T12:00:00Z"},
    ]
    response = client.post("/api/v1/traces/batch", json={"traces": traces})
    assert response.status_code == 201
    assert [row["cost_usd"] for row in response.json()] == pytest.approx([0.00075, 0.0015])


def test_missing_timestamp_prices_and_stores_the_same_arrival_time(client, monkeypatch):
    from datetime import datetime

    from backend.app.routers import traces

    monkeypatch.setattr(traces, "utc_now", lambda: datetime(2027, 3, 1, 12, 0, 0))
    body = _post(client)
    assert body["timestamp"].startswith("2027-03-01T12:00:00")
    assert body["cost_usd"] == pytest.approx(0.0015)
