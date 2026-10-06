"""Official spend reports, sealed key storage, and per-AI usage grouping."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.services import secret_store
from backend.app.services.api_key_manager import APIKeyManager
from backend.app.services.provider_spend import (
    KIND_BALANCE,
    KIND_SPEND,
    KIND_UNAVAILABLE,
    SpendService,
    fetch_anthropic_cost,
    fetch_deepseek,
    fetch_openai_cost,
)

NOW = datetime(2026, 10, 5, 3, 0, tzinfo=timezone.utc)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_anthropic_cost_report_is_summed_in_cents_across_pages():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if "page" not in req.url.params:
            return httpx.Response(200, json={
                "data": [{"starting_at": "2026-10-01T00:00:00Z", "results": [
                    {"amount": "1234.5", "currency": "USD"}, {"amount": "100", "currency": "USD"}]}],
                "has_more": True, "next_page": "p2"})
        return httpx.Response(200, json={
            "data": [{"results": [{"amount": "65.5", "currency": "USD"}]}], "has_more": False, "next_page": None})

    r = fetch_anthropic_cost("sk-ant-admin01-x", _client(handler), now=NOW)
    assert (r["kind"], r["amount"], r["currency"]) == (KIND_SPEND, 14.0, "USD")
    assert seen[0].url.params["starting_at"] == "2026-10-01T00:00:00Z"
    assert seen[0].headers["x-api-key"] == "sk-ant-admin01-x"
    assert seen[0].headers["anthropic-version"] == "2023-06-01"


def test_openai_costs_are_summed_in_dollars():
    def handler(req: httpx.Request):
        assert req.url.params["start_time"] == str(int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()))
        assert req.headers["authorization"] == "Bearer sk-admin-x"
        return httpx.Response(200, json={"data": [
            {"results": [{"amount": {"value": 1.25, "currency": "usd"}}]},
            {"results": [{"amount": {"value": 0.75, "currency": "usd"}}]}], "has_more": False})

    r = fetch_openai_cost("sk-admin-x", _client(handler), now=NOW)
    assert (r["kind"], r["amount"], r["currency"]) == (KIND_SPEND, 2.0, "USD")


def test_rejected_admin_key_reports_unavailable_without_body():
    r = fetch_openai_cost("sk-bad", _client(lambda req: httpx.Response(401, text="secret details")), now=NOW)
    assert r["kind"] == KIND_UNAVAILABLE
    assert r["error"] == "HTTP 401"
    assert "secret" not in json.dumps(r)


def test_deepseek_balance_is_a_balance_not_spend():
    def handler(req):
        return httpx.Response(200, json={"balance_infos": [{"currency": "CNY", "total_balance": "12.50"}]})

    r = fetch_deepseek("sk-x", _client(handler))
    assert (r["kind"], r["amount"], r["currency"]) == (KIND_BALANCE, 12.5, "CNY")


def test_spend_service_asks_for_admin_key_and_explains_keyless_providers():
    svc = SpendService(lambda provider: None)
    assert "관리자 키" in svc.spend_for("anthropic_api")["source_label"]
    assert svc.spend_for("gemini_api")["kind"] == KIND_UNAVAILABLE
    assert svc.spend_for("claude") is None


def test_spend_service_caches_results(monkeypatch):
    calls = []

    def fake(key, client, now=None):
        calls.append(key)
        return {"kind": KIND_SPEND, "amount": 1.0}

    import backend.app.services.provider_spend as ps
    monkeypatch.setitem(ps.FETCHERS, "openai_api", ("openai_admin", fake))
    svc = SpendService(lambda p: "sk-admin-x")
    svc.spend_for("openai_api")
    svc.spend_for("openai_api")
    assert len(calls) == 1
    svc.spend_for("openai_api", force=True)
    assert len(calls) == 2


# ---------------------------------------------------------------- key storage
@pytest.mark.skipif(secret_store.AVAILABLE_SCHEME != secret_store.SCHEME_DPAPI, reason="Windows DPAPI")
def test_keys_are_not_stored_in_plain_text(tmp_path: Path):
    f = tmp_path / "keys.json"
    mgr = APIKeyManager(filepath=f)
    mgr.set_key("openai_admin", "sk-admin-supersecret-123")
    raw = f.read_text(encoding="utf-8")
    if secret_store.AVAILABLE_SCHEME == secret_store.SCHEME_DPAPI:
        assert "supersecret" not in raw
    assert mgr.get_registered_key("openai_admin") == "sk-admin-supersecret-123"
    entry = next(k for k in mgr.list_keys() if k["provider"] == "openai_admin")
    assert entry["is_admin_key"] is True and entry["protection"]


@pytest.mark.skipif(secret_store.AVAILABLE_SCHEME != secret_store.SCHEME_DPAPI, reason="Windows DPAPI")
def test_plaintext_keys_from_older_versions_are_migrated(tmp_path: Path):
    f = tmp_path / "keys.json"
    f.write_text(json.dumps({"deepseek": {"key": "sk-old-plain-999", "status": "connected"}}), encoding="utf-8")
    mgr = APIKeyManager(filepath=f)
    assert mgr.migrate_plaintext() == 1
    assert "key" not in json.loads(f.read_text(encoding="utf-8"))["deepseek"]
    assert mgr.get_registered_key("deepseek") == "sk-old-plain-999"
    assert mgr.migrate_plaintext() == 0


def test_unknown_provider_is_rejected(tmp_path: Path):
    with pytest.raises(ValueError):
        APIKeyManager(filepath=tmp_path / "k.json").set_key("evil", "x")


def test_key_endpoints_require_auth_when_enabled(client: TestClient, api_key: str):
    assert client.post("/api/v1/keys", json={"provider": "groq", "key": "gsk_x"}).status_code == 401
    assert client.delete("/api/v1/keys/groq").status_code == 401


# ---------------------------------------------------------------- per-AI grouping
def _trace(client, tags, provider="anthropic", model="claude-opus-5-5", **kw):
    body = {"provider": provider, "model_name": model, "prompt_tokens": 1000, "completion_tokens": 10,
            "tags": tags}
    body.update(kw)
    assert client.post("/api/v1/traces", json=body).status_code == 201


def test_usage_is_grouped_by_tool_not_provider(client: TestClient):
    _trace(client, ["claude-code", "cli"], cache_read_tokens=5000)
    _trace(client, ["copilot", "session"], model="claude-haiku-4-5")   # Claude model, but Copilot usage
    _trace(client, ["antigravity", "ide"], provider="gemini", model="gemini-3.1-pro")
    _trace(client, [], provider="openai", model="gpt-5.4")
    _trace(client, [], provider="openai", model="gpt-5.4",
           timestamp=(datetime.now(timezone.utc) - timedelta(days=40)).isoformat())

    usage = client.get("/api/v1/metrics/active_contexts").json()
    assert set(usage) == {"claude", "copilot", "antigravity", "openai_api"}
    assert usage["claude"]["windows"]["5h"]["requests"] == 1
    assert usage["claude"]["context_tokens"] == 6000
    assert usage["copilot"]["context_tokens"] is None      # session totals say nothing about context
    assert usage["antigravity"]["context_tokens"] is None  # text-length estimates
    assert usage["openai_api"]["windows"]["7d"]["requests"] == 1
    assert usage["openai_api"]["windows"]["month"]["requests"] == 1


def test_unpriced_requests_are_counted_not_added_as_zero(client: TestClient):
    _trace(client, [], provider="openai", model="gpt-9-unknown")
    _trace(client, [], provider="openai", model="gpt-5.4")
    month = client.get("/api/v1/metrics/active_contexts").json()["openai_api"]["windows"]["month"]
    assert month["requests"] == 2
    assert month["cost_unknown"] == 1
    assert month["cost_usd"] == pytest.approx((1000 * 2.5 + 10 * 15) / 1_000_000)
