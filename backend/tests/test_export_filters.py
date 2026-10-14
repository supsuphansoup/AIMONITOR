"""CSV filters match trace listing, including UTC boundaries and newest-first limits."""
import csv
import io

import pytest
from fastapi.testclient import TestClient


def _seed(client: TestClient):
    events = [
        ("before", "2026-10-06T00:59:59.999999+00:00", "ERROR", "openai"),
        ("start", "2026-10-06T01:00:00+00:00", "ERROR", "openai"),
        ("success", "2026-10-06T01:30:00+00:00", "SUCCESS", "openai"),
        ("other-provider", "2026-10-06T01:30:00+00:00", "ERROR", "anthropic"),
        ("end", "2026-10-06T02:00:00+00:00", "ERROR", "openai"),
        ("end-tie", "2026-10-06T02:00:00+00:00", "ERROR", "openai"),
        ("after", "2026-10-06T02:00:00.000001+00:00", "ERROR", "openai"),
        ("timeout", "2026-10-06T01:30:00+00:00", "TIMEOUT", "openai"),
        ("rate-limited", "2026-10-06T01:30:00+00:00", "RATE_LIMITED", "openai"),
    ]
    for trace_id, timestamp, status, provider in events:
        response = client.post("/api/v1/traces", json={
            "trace_id": trace_id, "timestamp": timestamp, "status": status,
            "provider": provider, "model_name": "test-model",
        })
        assert response.status_code == 201


def _rows(response):
    assert response.status_code == 200
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


@pytest.mark.parametrize("status", ["success", " Error ", "timeout", "rate_limited", ""])
def test_export_status_matches_listing(client: TestClient, status: str):
    _seed(client)
    params = {"status": status}
    rows = _rows(client.get("/api/v1/traces/export.csv", params=params))
    listed = client.get("/api/v1/traces", params=params)
    assert listed.status_code == 200
    assert [row["trace_id"] for row in rows] == [row["trace_id"] for row in listed.json()]


@pytest.mark.parametrize("status", ["failed", "   "])
def test_export_rejects_invalid_status_like_listing(client: TestClient, status: str):
    params = {"status": status}
    exported = client.get("/api/v1/traces/export.csv", params=params)
    listed = client.get("/api/v1/traces", params=params)
    assert exported.status_code == listed.status_code == 422
    assert exported.json() == listed.json()


def test_export_combines_filters_with_inclusive_offset_boundaries(client: TestClient):
    _seed(client)
    response = client.get("/api/v1/traces/export.csv", params={
        "provider": "openai", "model_name": "test-model", "status": "error",
        "start_time": "2026-10-06T10:00:00+09:00", "end_time": "2026-10-06T11:00:00+09:00",
    })
    assert [row["trace_id"] for row in _rows(response)] == ["end-tie", "end", "start"]
    assert response.headers["X-Total-Count"] == "3"
    assert response.headers["X-Export-Partial"] == "false"


def test_export_start_time_includes_boundary_and_newer_rows(client: TestClient):
    _seed(client)
    rows = _rows(client.get("/api/v1/traces/export.csv", params={
        "provider": "openai", "status": "ERROR", "start_time": "2026-10-06T01:00:00Z",
    }))
    assert [row["trace_id"] for row in rows] == ["after", "end-tie", "end", "start"]


@pytest.mark.parametrize("limit, partial", [(2, "true"), (3, "false"), (4, "false")])
def test_export_limit_keeps_newest_matches_and_reports_actual_truncation(
    client: TestClient, limit: int, partial: str,
):
    _seed(client)
    response = client.get("/api/v1/traces/export.csv", params={
        "provider": "openai", "status": "ERROR", "start_time": "2026-10-06T01:00:00Z",
        "end_time": "2026-10-06T02:00:00Z", "limit": limit,
    })
    assert [row["trace_id"] for row in _rows(response)] == ["end-tie", "end", "start"][:limit]
    assert response.headers["X-Total-Count"] == "3"
    assert response.headers["X-Export-Partial"] == partial


def test_empty_export_is_not_partial(client: TestClient):
    response = client.get("/api/v1/traces/export.csv", params={"status": "ERROR", "limit": 1})
    assert _rows(response) == []
    assert response.headers["X-Total-Count"] == "0"
    assert response.headers["X-Export-Partial"] == "false"


@pytest.mark.parametrize(
    "latency_ms, expected", [(None, ""), (0.0, "0.0"), (125.5, "125.5")],
    ids=["unmeasured", "measured-zero", "measured-positive"],
)
def test_export_distinguishes_unmeasured_latency_from_measured_zero(
    client: TestClient, latency_ms, expected: str,
):
    response = client.post("/api/v1/traces", json={
        "provider": "openai", "model_name": "test-model", "latency_ms": latency_ms,
    })
    assert response.status_code == 201
    assert response.json()["latency_measured"] is (latency_ms is not None)
    rows = _rows(client.get("/api/v1/traces/export.csv"))
    assert len(rows) == 1
    assert rows[0]["latency_ms"] == expected
