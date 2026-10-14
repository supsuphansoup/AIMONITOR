"""Numeric and UTC boundaries, and validation before durable writes."""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlmodel import Session, select

from backend.app.models.trace import BatchTraceCreate, TraceEvent, TraceEventCreate, TraceEventRead

TRACE = {"provider": "openai", "model_name": "gpt-4o"}
TOKEN_FIELDS = (
    "prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_write_tokens",
    "cache_write_1h_tokens", "total_tokens",
)
LIMIT = 1_000_000_000


@pytest.mark.parametrize("field", ["cost_usd", "latency_ms"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e309"])
@pytest.mark.parametrize("batch", [False, True])
def test_nonfinite_float_is_rejected_without_storage(client: TestClient, session: Session, field, value, batch):
    payload = {**TRACE, field: value}
    res = client.post("/api/v1/traces/batch" if batch else "/api/v1/traces",
                      json={"traces": [TRACE, payload]} if batch else payload)
    assert res.status_code == 422
    assert session.exec(select(TraceEvent)).all() == []


@pytest.mark.parametrize("field", [*TOKEN_FIELDS, "cost_usd", "latency_ms"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e309"])
@pytest.mark.parametrize("batch", [False, True])
def test_raw_nonfinite_number_fails_input_schema(field, value, batch):
    raw = json.dumps(TRACE)[:-1] + f',"{field}":{value}' + "}"
    schema = BatchTraceCreate if batch else TraceEventCreate
    if batch:
        raw = '{"traces":[' + json.dumps(TRACE) + "," + raw + "]}"
    with pytest.raises(ValidationError):
        schema.model_validate_json(raw)


@pytest.mark.parametrize("field", ["cost_usd", "latency_ms"])
@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e309"])
@pytest.mark.parametrize("batch", [False, True])
def test_raw_nonfinite_http_never_commits(client: TestClient, session: Session, field, value, batch):
    raw = json.dumps(TRACE)[:-1] + f',"{field}":{value}' + "}"
    if batch:
        raw = '{"traces":[' + json.dumps(TRACE) + "," + raw + "]}"
    res = client.post("/api/v1/traces/batch" if batch else "/api/v1/traces", content=raw,
                      headers={"Content-Type": "application/json"})
    assert res.status_code == 422
    assert all(set(item) == {"loc", "type", "msg"} for item in res.json()["detail"])
    assert session.exec(select(TraceEvent)).all() == []


@pytest.mark.parametrize("field", TOKEN_FIELDS)
@pytest.mark.parametrize("value", [LIMIT + 1, 2**63])
@pytest.mark.parametrize("batch", [False, True])
def test_token_limit_rejects_large_values(client: TestClient, session: Session, field, value, batch):
    payload = {**TRACE, field: value}
    res = client.post("/api/v1/traces/batch" if batch else "/api/v1/traces",
                      json={"traces": [TRACE, payload]} if batch else payload)
    assert res.status_code == 422
    assert session.exec(select(TraceEvent)).all() == []


@pytest.mark.parametrize("field", TOKEN_FIELDS)
def test_exact_token_limit_is_accepted(client: TestClient, field):
    payload = {**TRACE, field: LIMIT}
    if field == "cache_write_1h_tokens":
        payload["cache_write_tokens"] = LIMIT
    res = client.post("/api/v1/traces", json=payload)
    assert res.status_code == 201
    assert res.json()[field] == LIMIT


def test_derived_total_limit_uses_clamped_components(client: TestClient, session: Session):
    res = client.post("/api/v1/traces", json={**TRACE, "prompt_tokens": LIMIT, "completion_tokens": 1})
    assert res.status_code == 422
    assert session.exec(select(TraceEvent)).all() == []
    res = client.post("/api/v1/traces", json={**TRACE, "prompt_tokens": LIMIT, "completion_tokens": -1})
    assert res.status_code == 201
    assert res.json()["total_tokens"] == LIMIT


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("failure", ["refresh", "response"])
def test_precommit_failure_rolls_back_all_rows(client: TestClient, session: Session, monkeypatch, batch, failure):
    def fail(*args, **kwargs):
        raise RuntimeError("SYNTHETIC_PRECOMMIT_FAILURE")
    if failure == "refresh":
        monkeypatch.setattr(session, "refresh", fail)
    else:
        monkeypatch.setattr(TraceEventRead, "model_validate", fail)
    res = client.post("/api/v1/traces/batch" if batch else "/api/v1/traces",
                      json={"traces": [TRACE, TRACE]} if batch else TRACE)
    assert res.status_code == 500
    assert session.exec(select(TraceEvent)).all() == []


@pytest.mark.parametrize("timestamp", ["0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00"])
def test_utc_overflow_is_422_on_ingest(client: TestClient, session: Session, timestamp):
    for path, payload in (("/api/v1/traces", {**TRACE, "timestamp": timestamp}),
                          ("/api/v1/traces/batch", {"traces": [TRACE, {**TRACE, "timestamp": timestamp}]})):
        assert client.post(path, json=payload).status_code == 422
    assert session.exec(select(TraceEvent)).all() == []


@pytest.mark.parametrize("path", ["/api/v1/traces", "/api/v1/traces/export.csv"])
@pytest.mark.parametrize("field", ["start_time", "end_time"])
@pytest.mark.parametrize("timestamp", ["0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00"])
def test_utc_overflow_is_422_on_filters(client: TestClient, path, field, timestamp):
    assert client.get(path, params={field: timestamp}).status_code == 422


@pytest.mark.parametrize("error", [OverflowError, ValueError])
def test_utc_callers_accept_both_normalizer_errors(client: TestClient, monkeypatch, error):
    import backend.app.models.trace as model
    import backend.app.routers.traces as router

    def fail(_value):
        raise error("SYNTHETIC_PRIVATE_TIMESTAMP")
    monkeypatch.setattr(model, "to_naive_utc", fail)
    monkeypatch.setattr(router, "to_naive_utc", fail)
    with pytest.raises(ValueError, match="supported UTC range"):
        model.TraceEventCreate.normalize_timestamp(datetime(2026, 1, 1))
    for path in ("/api/v1/traces", "/api/v1/traces/export.csv"):
        assert client.get(path, params={"start_time": "2026-01-01T00:00:00Z"}).status_code == 422
