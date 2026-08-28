"""Timestamp handling.

Regression cover for two defects that made the dashboard show the wrong time:
responses carried no UTC offset (so browsers read them as local time), and a
client timestamp with an offset had its offset discarded rather than converted.
"""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

TRACE = {"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 10, "completion_tokens": 5}


def test_response_timestamp_carries_utc_offset(client: TestClient):
    res = client.post("/api/v1/traces", json=TRACE)
    assert res.status_code == 201
    raw = res.json()["timestamp"]

    assert raw.endswith("+00:00"), f"timestamp {raw!r} has no timezone designator"
    parsed = datetime.fromisoformat(raw)
    assert parsed.tzinfo is not None
    assert abs(parsed - datetime.now(timezone.utc)) < timedelta(minutes=5)


def test_client_offset_is_converted_not_dropped(client: TestClient):
    payload = {**TRACE, "timestamp": "2026-08-28T10:00:00+09:00"}
    res = client.post("/api/v1/traces", json=payload)
    assert res.status_code == 201

    parsed = datetime.fromisoformat(res.json()["timestamp"])
    assert parsed == datetime(2026, 8, 28, 1, 0, tzinfo=timezone.utc)


def test_naive_client_timestamp_is_treated_as_utc(client: TestClient):
    res = client.post("/api/v1/traces", json={**TRACE, "timestamp": "2026-08-28T10:00:00"})
    parsed = datetime.fromisoformat(res.json()["timestamp"])
    assert parsed == datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)


def test_timeseries_buckets_carry_offsets(client: TestClient):
    client.post("/api/v1/traces", json={**TRACE, "latency_ms": 120.0})
    points = client.get("/api/v1/metrics/timeseries?hours_back=1&interval_minutes=5").json()
    assert points
    assert datetime.fromisoformat(points[0]["timestamp"]).tzinfo is not None


def test_alert_history_timestamps_carry_offsets(client: TestClient):
    client.post("/api/v1/traces", json={**TRACE, "status": "ERROR"})
    rule = client.post(
        "/api/v1/alerts/rules",
        json={"name": "any error", "metric_type": "error_rate", "operator": ">", "threshold_value": 0.0},
    ).json()
    assert datetime.fromisoformat(rule["created_at"]).tzinfo is not None

    triggered = client.post("/api/v1/alerts/evaluate").json()
    assert len(triggered) == 1
    assert datetime.fromisoformat(triggered[0]["created_at"]).tzinfo is not None


def test_range_filter_accepts_offset_timestamps(client: TestClient):
    client.post("/api/v1/traces", json=TRACE)
    # Same instant expressed in KST; the filter must convert before comparing.
    future_kst = (datetime.now(timezone.utc) + timedelta(hours=1)).astimezone(
        timezone(timedelta(hours=9))
    )
    # Passed via params so httpx percent-encodes the "+" in the offset.
    res = client.get("/api/v1/traces", params={"end_time": future_kst.isoformat()})
    assert res.status_code == 200
    assert len(res.json()) == 1

    past_kst = (datetime.now(timezone.utc) + timedelta(hours=1)).astimezone(
        timezone(timedelta(hours=9))
    )
    empty = client.get("/api/v1/traces", params={"start_time": past_kst.isoformat()})
    assert empty.json() == []
