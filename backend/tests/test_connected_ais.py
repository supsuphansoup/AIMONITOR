"""Connected-AI API reuses desktop summaries and SQL usage without exposing local secrets."""
from datetime import datetime, timedelta

import pytest

from backend.app.core.config import settings
from backend.app.core.timeutil import utc_now
from backend.app.models.trace import TraceEvent
from backend.app.services.api_key_manager import spend_service
from desktop import ai_detector, config, context_manager


@pytest.fixture(autouse=True)
def isolate_pc(monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda: {"krw_per_usd": 1380})
    monkeypatch.setattr(ai_detector, "detect_connected_ais", lambda: [])
    monkeypatch.setattr(spend_service, "spend_all", lambda keys: {})
    monkeypatch.setattr(context_manager, "windows_for", lambda *args, **kwargs: [{
        "id": "5h", "label": "5시간", "remaining_pct": None, "source": "unavailable",
        "as_of": "2026-10-06T01:00:00", "resets_at": "2026-10-06T03:00:00+09:00",
    }])


def connected(monkeypatch, key="openai_api"):
    monkeypatch.setattr(ai_detector, "detect_connected_ais", lambda: [{
        "key": key, "name": key, "display_name": key, "provider": "openai",
        "plan_name": "API 종량제", "path": "secret-path", "detail": "exception-secret",
    }])


def add_trace(session, timestamp, **kwargs):
    row = TraceEvent(trace_id=str(timestamp), timestamp=timestamp, provider="openai",
                     model_name="gpt-4o", **kwargs)
    session.add(row)
    session.commit()
    return row


def test_empty_connected_ais(client):
    response = client.get("/api/v1/metrics/connected_ais")
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"]["connected_count"] == 0


def test_connected_without_records_has_no_context_or_official_amount(client, monkeypatch):
    connected(monkeypatch)
    item = client.get("/api/v1/metrics/connected_ais").json()["items"][0]
    assert item["context"] is None and item["official_spend"] is None
    assert item["quota_windows"][0]["remaining_pct"] is None
    assert item["last_request_at"] is None


def test_usage_uses_latest_rows_unknown_cost_and_offset_times(client, session, monkeypatch):
    connected(monkeypatch)
    now = utc_now() - timedelta(minutes=2)
    add_trace(session, now - timedelta(minutes=1), prompt_tokens=50, cost_source="unknown", cost_usd=99)
    add_trace(session, now, prompt_tokens=100, cache_read_tokens=200, cache_write_tokens=300,
              cost_source="pricing_table", cost_usd=1)
    add_trace(session, now, prompt_tokens=150, cost_source="unknown", cost_usd=99)
    add_trace(session, utc_now() + timedelta(days=1), prompt_tokens=999, cost_source="custom", cost_usd=999)
    response = client.get("/api/v1/metrics/connected_ais")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["context"]["tokens"] == 150  # timestamp tie resolved by latest id
    assert item["usage"]["5h"]["requests"] == 3
    assert item["usage"]["5h"]["cost_usd"] == 1
    assert item["usage"]["5h"]["cost_unknown"] == 2
    for value in (item["last_request_at"], item["context"]["as_of"],
                  item["quota_windows"][0]["as_of"], item["quota_windows"][0]["resets_at"]):
        assert datetime.fromisoformat(value).utcoffset() == timedelta(0)
        assert value.endswith("+00:00")
    assert "secret-path" not in response.text and "exception-secret" not in response.text
    assert "metadata_json" not in response.text and "session_id" not in response.text


def test_all_unknown_cost_is_null_not_zero(client, session, monkeypatch):
    connected(monkeypatch)
    add_trace(session, utc_now() - timedelta(minutes=1), cost_source="unknown", cost_usd=99)
    item = client.get("/api/v1/metrics/connected_ais").json()["items"][0]
    assert item["usage"]["5h"]["cost_usd"] is None
    assert item["usage"]["5h"]["cost_krw"] is None


def test_spend_error_details_removed_and_times_normalized(client, monkeypatch):
    connected(monkeypatch)
    monkeypatch.setattr(spend_service, "spend_all", lambda keys: {"openai_api": {
        "kind": "unavailable", "amount": None, "error": "exception-secret",
        "as_of": "2026-10-06T01:00:00", "period_start": "2026-10-01T00:00:00+09:00",
    }})
    response = client.get("/api/v1/metrics/connected_ais")
    spend = response.json()["items"][0]["official_spend"]
    assert spend["amount"] is None and "error" not in spend
    assert spend["as_of"].endswith("+00:00") and spend["period_start"].endswith("+00:00")
    assert "exception-secret" not in response.text


def test_connected_ais_respects_read_auth(client, monkeypatch, api_key):
    monkeypatch.setattr(settings, "REQUIRE_AUTH_FOR_READS", True)
    assert client.get("/api/v1/metrics/connected_ais").status_code == 401
    assert client.get("/api/v1/metrics/connected_ais", headers={"X-API-Key": api_key}).status_code == 200
