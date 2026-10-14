"""Forced billing refresh uses write protection and a minimum interval."""
import pytest

from backend.app.core.config import settings
from backend.app.routers import api_keys
from backend.app.services import provider_spend as ps


@pytest.fixture
def billing(monkeypatch):
    calls = []
    service = ps.SpendService(lambda provider: None)

    def fetch(card, secret, fetcher, deadline):
        calls.append(secret)
        return ps._result("openrouter", "spend", amount=len(calls))

    monkeypatch.setattr(service, "_fetch", fetch)
    monkeypatch.setattr(api_keys, "spend_service", service)
    monkeypatch.setattr(api_keys.api_key_manager, "billing_snapshot", lambda: [
        {"provider": "openrouter", "key_id": "default", "label": "기본", "secret": "synthetic-key"},
    ])
    monkeypatch.setattr(settings, "REQUIRE_AUTH_FOR_READS", False)
    return calls


def test_get_force_cannot_trigger_provider_call(client, api_key, billing):
    response = client.get("/api/v1/keys/spend?force=true", headers={"Origin": "https://attacker.invalid"})
    assert response.status_code == 400
    assert not billing


def test_refresh_requires_write_auth(client, api_key, billing):
    assert client.post("/api/v1/keys/spend/refresh").status_code == 401
    assert not billing
    response = client.post("/api/v1/keys/spend/refresh", headers={"X-API-Key": api_key})
    assert response.status_code == 200 and len(billing) == 1


def test_refresh_rejects_foreign_origin_in_open_mode(client, billing, monkeypatch):
    monkeypatch.setattr(settings, "API_KEY", "")
    response = client.post("/api/v1/keys/spend/refresh", headers={"Origin": "https://attacker.invalid"})
    assert response.status_code == 403 and not billing


def test_refresh_reuses_read_cache_and_limits_forced_requests(client, billing):
    assert client.get("/api/v1/keys/spend").json()["items"][0]["amount"] == 1
    assert client.get("/api/v1/keys/spend").json()["items"][0]["amount"] == 1
    refreshed = client.post("/api/v1/keys/spend/refresh")
    assert refreshed.status_code == 200 and refreshed.json()["items"][0]["amount"] == 2
    assert client.get("/api/v1/keys/spend").json()["items"][0]["amount"] == 2
    limited = client.post("/api/v1/keys/spend/refresh")
    assert limited.status_code == 429 and limited.headers["Retry-After"] == "60"
    assert len(billing) == 2


def test_refresh_interval_expires(client, billing, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(ps.time, "monotonic", lambda: clock[0])
    assert client.post("/api/v1/keys/spend/refresh").status_code == 200
    clock[0] += 59.0
    assert client.post("/api/v1/keys/spend/refresh").status_code == 429
    clock[0] += 1.0
    assert client.post("/api/v1/keys/spend/refresh").status_code == 200
    assert len(billing) == 2
