"""Timestamp handling.

Regression cover for two defects that made the dashboard show the wrong time:
responses carried no UTC offset (so browsers read them as local time), and a
client timestamp with an offset had its offset discarded rather than converted.
"""
import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.app.core.timeutil import to_naive_utc

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


@pytest.mark.parametrize("zone,cases", [
    ("EST5EDT", [
        ("2026-03-08T07:30:00", "2026-03-08T05:00:00", "2026-03-01T05:00:00"),
        ("2026-11-01T07:30:00", "2026-11-01T04:00:00", "2026-11-01T04:00:00"),
        ("2026-11-14T07:30:00", "2026-11-14T05:00:00", "2026-11-01T04:00:00"),
    ]),
    ("KST-9", [("2025-12-31T18:30:00", "2025-12-31T15:00:00", "2025-12-31T15:00:00")]),
    ("UTC0", [("2026-10-14T03:00:00", "2026-10-14T00:00:00", "2026-10-01T00:00:00")]),
])
def test_local_usage_boundaries_in_child_timezone(zone, cases):
    # A child process applies Windows CRT TZ rules without changing the host timezone.
    code = textwrap.dedent("""
        import json, sys, time
        from datetime import datetime, timedelta
        from sqlmodel import Session, SQLModel, create_engine
        from backend.app.models.trace import TraceEvent
        from backend.app.services.aggregator import MetricAggregator
        if hasattr(time, 'tzset'):
            time.tzset()
        for current, day, month in json.loads(sys.argv[1]):
            now = datetime.fromisoformat(current)
            for name, boundary in [('today', day), ('month', month)]:
                since = datetime.fromisoformat(boundary)
                engine = create_engine('sqlite://')
                SQLModel.metadata.create_all(engine)
                with Session(engine) as session:
                    for index, delta in enumerate([-30, 30]):
                        session.add(TraceEvent(trace_id=str(index), provider='p', model_name='m',
                            timestamp=since + timedelta(minutes=delta)))
                    session.commit()
                    usage = MetricAggregator.get_active_contexts(session, now=now)['p_api']['windows'][name]
                    assert usage['since'] == boundary + '+00:00', usage
                    assert usage['requests'] == 1, usage
                engine.dispose()
    """)
    result = subprocess.run(
        [sys.executable, "-c", code, json.dumps(cases)],
        env={**os.environ, "TZ": zone}, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("field,value", [
    ("start_time", "0001-01-01T00:00:00+14:00"),
    ("end_time", "9999-12-31T23:59:59-01:00"),
])
def test_metrics_datetime_overflow_is_sanitized_422(client, field, value):
    response = client.get("/api/v1/metrics/overview", params={field: value})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["query", field]
    assert value not in response.text and "OverflowError" not in response.text
    assert "input" not in detail[0] and "ctx" not in detail[0]


@pytest.mark.parametrize("value", ["0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-01:00"])
def test_utc_normalization_overflow_raises_fixed_value_error(value):
    with pytest.raises(ValueError, match="^Timestamp is outside the UTC range$"):
        to_naive_utc(datetime.fromisoformat(value))
