"""Desktop compatibility, missing latency, and secrets that must never be echoed."""
import json
import logging
from datetime import timedelta

import httpx
import pytest

from backend.app.core.config import settings
from backend.app.core.timeutil import utc_now
from backend.app.models.trace import TraceEvent
from backend.app.services import secret_store
from backend.app.services.aggregator import MetricAggregator
from backend.app.services.api_key_manager import APIKeyManager
from backend.app.services.cost import SOURCE_UNKNOWN, calculate_cost_detail
from backend.app.services.provider_spend import KIND_UNAVAILABLE, SpendService, fetch_anthropic_cost

TRACE = {"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 10}


def test_missing_and_explicit_zero_latency_have_different_meanings(client):
    missing = client.post("/api/v1/traces", json={**TRACE, "latency_ms": None}).json()
    zero = client.post("/api/v1/traces", json={**TRACE, "latency_ms": 0}).json()
    assert missing["latency_ms"] is None and missing["latency_measured"] is False
    assert zero["latency_ms"] == 0 and zero["latency_measured"] is True
    overview = client.get("/api/v1/metrics/overview").json()
    assert overview["latency_measured_count"] == 1
    assert overview["avg_latency_ms"] == 0
    assert overview["latency_sampled"] is False


def test_latency_cap_counts_only_measured_rows(session, monkeypatch):
    now = utc_now()
    for index, latency in enumerate((100, 200, 300)):
        session.add(TraceEvent(trace_id=str(index), **TRACE, latency_ms=latency,
                               timestamp=now + timedelta(seconds=index)))
    session.add(TraceEvent(trace_id="watcher", **TRACE, latency_ms=0, latency_measured=False,
                           timestamp=now + timedelta(seconds=4)))
    session.commit()
    monkeypatch.setattr(settings, "MAX_AGGREGATION_ROWS", 2)
    overview = MetricAggregator.get_overview_metrics(session)
    assert overview["latency_measured_count"] == 3
    assert overview["avg_latency_ms"] == 200
    assert overview["p50_latency_ms"] == 250
    assert overview["latency_sampled"] is True


@pytest.mark.parametrize("metric", ["avg_latency", "p50", "p95", "p99"])
def test_unmeasured_traffic_does_not_trigger_latency_alert(client, metric):
    assert client.post("/api/v1/traces", json=TRACE).status_code == 201
    assert client.post("/api/v1/alerts/rules", json={
        "name": "missing timing", "metric_type": metric, "operator": "<", "threshold_value": 100,
    }).status_code == 201
    assert client.post("/api/v1/alerts/evaluate").json() == []


def test_unverified_one_hour_rates_are_unknown():
    for provider, model in (("openai", "gpt-4o"), ("anthropic", "claude-3-7-sonnet")):
        assert calculate_cost_detail(provider, model, 0, 0, cache_write_tokens=100,
                                     cache_write_1h_tokens=100).source == SOURCE_UNKNOWN


def test_active_contexts_exact_tags_ties_and_unknown_cost(session):
    now = utc_now()
    session.add(TraceEvent(trace_id="old", **TRACE, tags="not-claude-code", timestamp=now,
                           cost_usd=99, cost_source="unknown"))
    session.add(TraceEvent(trace_id="new", **{**TRACE, "prompt_tokens": 20}, tags="not-claude-code", timestamp=now,
                           cost_usd=1, cost_source="custom"))
    session.add(TraceEvent(trace_id="future", **TRACE, timestamp=now + timedelta(days=1)))
    session.commit()
    contexts = MetricAggregator.get_active_contexts(session, now=now)
    assert set(contexts) == {"openai_api"}
    assert contexts["openai_api"]["context_tokens"] == 20
    month = contexts["openai_api"]["windows"]["month"]
    assert (month["requests"], month["cost_usd"], month["cost_unknown"]) == (2, 1, 1)
    assert contexts["openai_api"]["timestamp"].endswith("+00:00")


def test_key_validation_does_not_echo_secret(client, caplog):
    secret = "test-only-secret-never-echoed"
    response = client.post("/api/v1/keys", json={"key": secret})
    assert response.status_code == 422
    assert secret not in response.text and secret not in caplog.text
    response = client.post("/api/v1/keys", json={"provider": secret, "key": secret})
    assert response.status_code == 400
    assert secret not in response.text and secret not in caplog.text


def test_storage_fails_closed_without_encryption(tmp_path, monkeypatch):
    monkeypatch.setattr(secret_store, "AVAILABLE_SCHEME", secret_store.SCHEME_UNAVAILABLE)
    manager = APIKeyManager(filepath=tmp_path / "keys.json")
    with pytest.raises(ValueError, match="Encrypted key storage"):
        manager.set_key("openai", "test-only-secret")
    assert not manager.filepath.exists()
    assert secret_store.unseal("unknown", "test-only-secret") is None


@pytest.mark.skipif(secret_store.AVAILABLE_SCHEME != secret_store.SCHEME_DPAPI, reason="Windows DPAPI")
def test_updating_a_key_never_rewrites_other_plaintext_keys(tmp_path):
    manager = APIKeyManager(filepath=tmp_path / "keys.json")
    manager.filepath.write_text(json.dumps({"openai": {"key": "test-only-legacy-secret"}}), encoding="utf-8")
    manager.set_key("groq", "test-only-new-secret")
    stored = manager.filepath.read_text(encoding="utf-8")
    assert "test-only-legacy-secret" not in stored and "test-only-new-secret" not in stored
    assert manager.get_registered_key("openai") == "test-only-legacy-secret"


def test_gemini_verification_sends_secret_only_in_header(tmp_path, monkeypatch, caplog):
    manager = APIKeyManager(filepath=tmp_path / "keys.json")
    secret = "test-only-secret"
    monkeypatch.setattr(manager, "get_registered_key", lambda provider: secret)
    real_client = httpx.Client

    def handler(request):
        assert secret not in str(request.url)
        assert request.headers["x-goog-api-key"] == secret
        raise httpx.ConnectError(secret, request=request)

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler)))
    result = manager.verify_key("gemini")
    assert result["valid"] is False
    assert secret not in json.dumps(result) and secret not in caplog.text


def test_claude_quota_exception_does_not_log_oauth_token(tmp_path, monkeypatch, caplog):
    from desktop.claude_quota_syncer import ClaudeQuotaSyncer

    secret = "test-only-oauth-secret"
    syncer = ClaudeQuotaSyncer()
    syncer.cred_file = tmp_path / "credentials.json"
    syncer.cred_file.write_text(json.dumps({"claudeAiOauth": {"accessToken": secret}}), encoding="utf-8")
    caplog.set_level(logging.DEBUG)

    def fail(*args, **kwargs):
        raise httpx.ConnectError(secret)

    monkeypatch.setattr(httpx, "get", fail)
    assert syncer.fetch_fresh() is None
    assert secret not in caplog.text


def test_spend_cache_is_invalidated_when_key_changes(monkeypatch):
    from backend.app.services import provider_spend

    current_key = ["test-account-one"]
    monkeypatch.setitem(provider_spend.FETCHERS, "openai_api", ("openai_admin", lambda key, client: {"account": key}))
    service = SpendService(lambda provider: current_key[0])
    assert service.spend_for("openai_api")["account"] == "test-account-one"
    current_key[0] = "test-account-two"
    assert service.spend_for("openai_api")["account"] == "test-account-two"


def test_spend_page_cap_reports_unavailable(monkeypatch):
    from backend.app.services import provider_spend

    monkeypatch.setattr(provider_spend, "MAX_PAGES", 1)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "data": [{"results": [{"amount": "100", "currency": "USD"}]}],
        "has_more": True, "next_page": "another-page",
    }))
    with httpx.Client(transport=transport) as client:
        result = fetch_anthropic_cost("test-key", client)
    assert result["kind"] == KIND_UNAVAILABLE and result["amount"] is None


def test_missing_provider_amount_is_unavailable(monkeypatch):
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": {}}))
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(transport=transport))
    result = SpendService(lambda provider: "test-key").spend_for("openrouter_api")
    assert result["kind"] == KIND_UNAVAILABLE and result["amount"] is None
