"""Opaque account ingestion, filtering, and usage separation, including unknown records."""
import csv
import io
from datetime import timedelta

import pytest

from backend.app.core.timeutil import utc_now
from backend.app.services.api_key_manager import spend_service
from desktop import ai_detector, config, context_manager

ACCOUNT_A = "claude_code:0123456789ab"
ACCOUNT_B = "claude_code:dir-fedcba987654"
TRACE = {"provider": "anthropic", "model_name": "claude-opus-5-5", "tags": ["claude-code"]}


@pytest.mark.parametrize("endpoint", ["/traces", "/traces/export.csv", "/metrics/overview",
                                      "/metrics/timeseries", "/metrics/breakdown"])
def test_unknown_account_query_is_exclusive_and_false_is_unfiltered(client, endpoint):
    for aid, tokens in [(ACCOUNT_A, 10), (None, 30)]:
        assert client.post("/api/v1/traces", json={**TRACE, "account_id": aid,
                                                   "prompt_tokens": tokens}).status_code == 201
    path = "/api/v1" + endpoint
    rejected = client.get(path, params={"account_id": ACCOUNT_A, "account_unknown": True})
    assert rejected.status_code == 422
    assert isinstance(rejected.json()["detail"], list)
    assert ACCOUNT_A not in rejected.text
    assert client.get(path, params={"account_unknown": False}).text == client.get(path).text
    response = client.get(path, params={"account_unknown": True})
    assert response.status_code == 200
    if endpoint.endswith(".csv"):
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert len(rows) == 1 and rows[0]["account_id"] == ""
        assert rows[0]["total_tokens"] == "30"
    elif endpoint == "/traces":
        assert response.headers["X-Total-Count"] == "1"
        assert [row["account_id"] for row in response.json()] == [None]
    elif endpoint == "/metrics/overview":
        assert response.json()["total_requests"] == 1
        assert response.json()["total_tokens"] == 30
    elif endpoint == "/metrics/timeseries":
        assert sum(row["request_count"] for row in response.json()) == 1
        assert sum(row["total_tokens"] for row in response.json()) == 30
    else:
        assert len(response.json()) == 1
        assert response.json()[0]["count"] == 1
        assert response.json()[0]["total_tokens"] == 30


@pytest.mark.parametrize("account_id", ["", "unknown:0123456789ab", "api:a@b.com", "api:a b",
                                       "api:", "api:" + "a" * 61, "api:a/b", "api:키", 123])
def test_account_validation_is_safe_and_batch_atomic(client, account_id, caplog):
    body = {**TRACE, "account_id": account_id}
    response = client.post("/api/v1/traces", json=body)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    if isinstance(account_id, str) and account_id:
        assert str(account_id) not in response.text and str(account_id) not in caplog.text
    response = client.post("/api/v1/traces/batch", json={"traces": [TRACE, body]})
    assert response.status_code == 422
    assert client.get("/api/v1/traces").json() == []


@pytest.mark.parametrize("tool", ["claude_code", "codex", "antigravity", "copilot", "cursor", "api"])
def test_supported_account_tools(client, tool):
    response = client.post("/api/v1/traces", json={**TRACE, "account_id": tool + ":0123456789ab"})
    assert response.status_code == 201
    assert response.json()["account_id"] == tool + ":0123456789ab"


def test_accounts_store_filter_csv_and_metrics(client):
    for account_id, tokens, cost in [(ACCOUNT_A, 10, 1), (ACCOUNT_B, 20, 2), (None, 30, 3)]:
        response = client.post("/api/v1/traces", json={
            **TRACE, "account_id": account_id, "prompt_tokens": tokens,
            "cost_usd": cost, "latency_ms": tokens,
        })
        assert response.status_code == 201
        assert response.json()["account_id"] == account_id
        assert client.get("/api/v1/traces/" + response.json()["trace_id"]).json()["account_id"] == account_id

    params = {"account_id": ACCOUNT_B}
    response = client.get("/api/v1/traces", params=params)
    assert response.headers["X-Total-Count"] == "1"
    assert response.json()[0]["prompt_tokens"] == 20
    csv_response = client.get("/api/v1/traces/export.csv", params=params)
    assert csv_response.headers["X-Total-Count"] == "1"
    rows = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert list(rows[0])[-1] == "account_id" and rows[0]["account_id"] == ACCOUNT_B
    all_rows = list(csv.DictReader(io.StringIO(client.get("/api/v1/traces/export.csv").text)))
    assert sum(row["account_id"] == "" for row in all_rows) == 1

    overview = client.get("/api/v1/metrics/overview", params=params).json()
    assert (overview["total_requests"], overview["total_tokens"], overview["total_cost_usd"]) == (1, 20, 2)
    assert overview["p95_latency_ms"] == 20
    timeseries = client.get("/api/v1/metrics/timeseries", params=params).json()
    assert sum(row["request_count"] for row in timeseries) == 1
    assert sum(row["total_tokens"] for row in timeseries) == 20
    breakdown = client.get("/api/v1/metrics/breakdown", params=params).json()
    assert breakdown[0]["count"] == 1 and breakdown[0]["total_cost_usd"] == 2


@pytest.mark.parametrize("path", ["traces", "traces/export.csv", "metrics/overview",
                                  "metrics/timeseries", "metrics/breakdown"])
def test_account_query_validation_hides_private_values(client, path):
    response = client.get("/api/v1/" + path, params={"account_id": "api:secret@example.com"})
    assert response.status_code == 422 and "secret@example.com" not in response.text


def test_codex_watcher_accounts_are_separate_from_openai_api(client):
    assert client.post("/api/v1/traces", json={
        "provider": "openai", "model_name": "gpt-5.4", "tags": ["codex", "cli"],
        "account_id": "codex:0123456789ab", "prompt_tokens": 100, "cache_read_tokens": 200,
    }).status_code == 201
    assert client.post("/api/v1/traces", json={
        "provider": "openai", "model_name": "gpt-5.4", "account_id": "api:fedcba987654",
    }).status_code == 201
    usage = client.get("/api/v1/metrics/active_contexts").json()
    assert set(usage) == {"codex", "openai_api"}
    assert usage["codex"]["accounts"][0]["account_id"] == "codex:0123456789ab"
    assert usage["codex"]["context_tokens"] == 300
    assert usage["openai_api"]["windows"]["5h"]["requests"] == 1


def test_account_windows_latest_context_unknown_and_future_separation(client, monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda: {"krw_per_usd": 1380})
    monkeypatch.setattr(ai_detector, "detect_connected_ais", lambda: [{
        "key": "claude", "provider": "anthropic", "plan_name": "Pro", "path": "private-path",
    }])
    monkeypatch.setattr(spend_service, "spend_all", lambda keys: {})
    monkeypatch.setattr(context_manager, "windows_for", lambda *args, **kwargs: [{
        "id": "5h", "remaining_pct": 77, "source": "oauth", "as_of": "2026-10-07T01:00:00",
    }])
    now = utc_now() - timedelta(minutes=1)
    traces = [
        {**TRACE, "account_id": ACCOUNT_A, "prompt_tokens": 10, "cost_usd": 1},
        {**TRACE, "account_id": ACCOUNT_B, "prompt_tokens": 20, "model_name": "unknown"},
        {**TRACE, "account_id": None, "prompt_tokens": 30, "cost_usd": 3},
    ]
    for trace in traces:
        trace["timestamp"] = now.isoformat()
    traces.append({**TRACE, "account_id": "claude_code:aaaaaaaaaaaa", "prompt_tokens": 999,
                   "timestamp": (utc_now() + timedelta(days=1)).isoformat()})
    assert client.post("/api/v1/traces/batch", json={"traces": traces}).status_code == 201
    total = client.get("/api/v1/metrics/active_contexts").json()["claude"]
    assert total["windows"]["5h"]["requests"] == 3 and total["context_tokens"] == 30
    accounts = {a["account_id"]: a for a in total["accounts"]}
    assert set(accounts) == {ACCOUNT_A, ACCOUNT_B, None}
    for account, tokens in [(ACCOUNT_A, 10), (ACCOUNT_B, 20), (None, 30)]:
        assert accounts[account]["context_tokens"] == tokens
        for window in accounts[account]["windows"].values():
            assert window["requests"] == 1 and window["tokens"] == tokens
            assert window["since"].endswith("+00:00")
    response = client.get("/api/v1/metrics/connected_ais")
    item = response.json()["items"][0]
    connected = {a["account_id"]: a for a in item["accounts"]}
    assert set(connected) == set(accounts)
    assert item["usage"]["5h"]["requests"] == 3
    assert response.json()["total"]["requests_today"] == 3
    assert item["quota_windows"][0]["remaining_pct"] == 77
    assert all(a["quota_windows"] is None and a["official_spend"] is None for a in connected.values())
    assert connected[ACCOUNT_B]["usage"]["5h"]["cost_usd"] is None
    assert connected[ACCOUNT_B]["usage"]["5h"]["cost_unknown"] == 1
    assert "private-path" not in response.text and "session_id" not in response.text
    assert response.json()["by_key"]["claude"]["accounts"] == item["accounts"]
