"""Verification results belong to the exact secret that was checked."""
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from backend.app.services import secret_store
from backend.app.services.api_key_manager import KNOWN_PROVIDERS, APIKeyManager, api_key_manager

REAL_CLIENT = httpx.Client


@pytest.fixture
def manager(tmp_path, monkeypatch):
    for info in KNOWN_PROVIDERS:
        for name in info["env_vars"]:
            monkeypatch.delenv(name, raising=False)
    if secret_store.AVAILABLE_SCHEME != secret_store.SCHEME_DPAPI:
        secrets = {}

        def seal(secret):
            token = str(len(secrets))
            secrets[token] = secret
            return "test", token

        monkeypatch.setattr(secret_store, "AVAILABLE_SCHEME", "test")
        monkeypatch.setattr(secret_store, "seal", seal)
        monkeypatch.setattr(secret_store, "unseal", lambda scheme, token: secrets.get(token))
    instance = APIKeyManager(tmp_path / "keys.json")
    monkeypatch.setattr(api_key_manager, "filepath", instance.filepath)
    return instance


def mock_http(monkeypatch, handler):
    monkeypatch.setattr(httpx, "Client", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))


def test_verification_of_explicit_key_updates_only_that_key(manager, monkeypatch):
    first = manager.add_key("groq", "synthetic-one")
    second = manager.add_key("groq", "synthetic-two")
    calls = []

    def handler(request):
        calls.append(request.headers["authorization"])
        return httpx.Response(200, json={})

    mock_http(monkeypatch, handler)
    assert manager.verify_key("groq", second["key_id"])["valid"]
    assert calls == ["Bearer synthetic-two"]
    assert manager.list_keys("groq")[0]["key_id"] == first["key_id"]
    assert [k["status"] for k in manager.list_keys("groq")] == ["connected", "valid"]
    assert not manager.verify_key("groq", "missing")["valid"]
    assert len(calls) == 1


@pytest.mark.parametrize("status,expected", [(401, "invalid"), (403, "invalid"), (429, "verification_unavailable")])
@pytest.mark.parametrize("provider", [p["key"] for p in KNOWN_PROVIDERS])
def test_failed_verification_replaces_stale_valid_state(manager, monkeypatch, status, expected, provider):
    manager.add_key(provider, "synthetic-one")
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={
        "data": {"usage": 1}, "balance_infos": [{"currency": "CNY", "total_balance": "1"}]}))
    assert manager.verify_key(provider)["valid"]
    assert manager.list_keys(provider)[0]["status"] == "valid"
    mock_http(monkeypatch, lambda request: httpx.Response(status))
    assert not manager.verify_key(provider)["valid"]
    assert manager.list_keys(provider)[0]["status"] == expected


@pytest.mark.parametrize("change", ["replace", "delete"])
def test_inflight_verification_cannot_certify_replacement_or_deleted_key(manager, monkeypatch, change):
    manager.add_key("groq", "synthetic-old")
    ready, release = threading.Event(), threading.Event()

    def handler(request):
        ready.set()
        assert release.wait(3)
        return httpx.Response(200)

    mock_http(monkeypatch, handler)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(manager.verify_key, "groq")
        assert ready.wait(3)
        if change == "replace":
            manager.set_key("groq", "synthetic-new")
        else:
            manager.delete_key("groq")
        release.set()
        assert result.result(timeout=3)["valid"]
    keys = manager.list_keys("groq")
    assert keys == [] if change == "delete" else keys[0]["last_verified"] is None


def test_representative_verification_tracks_usable_identity(manager, monkeypatch):
    manager.add_key("groq", "synthetic-unreadable")
    second = manager.add_key("groq", "synthetic-usable")
    real_unseal = secret_store.unseal
    monkeypatch.setattr(secret_store, "unseal", lambda *args:
                        None if real_unseal(*args) == "synthetic-unreadable" else real_unseal(*args))
    mock_http(monkeypatch, lambda request: httpx.Response(200))
    assert manager.verify_key("groq")["valid"]
    assert next(k for k in manager.list_keys("groq") if k["key_id"] == second["key_id"])["status"] == "valid"


@pytest.mark.parametrize("provider,body", [("deepseek", {}), ("openrouter", {"data": {}})])
def test_missing_verification_balance_is_unknown(manager, monkeypatch, provider, body):
    manager.add_key(provider, "synthetic-key")
    mock_http(monkeypatch, lambda request: httpx.Response(200, json=body))
    result = manager.verify_key(provider)
    assert result["valid"] and "확인 불가" in result["balance"]
    assert "0.00" not in result["balance"]


def test_deepseek_verification_displays_currencies_separately(manager, monkeypatch):
    manager.add_key("deepseek", "synthetic-key")
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={"balance_infos": [
        {"currency": "CNY", "total_balance": "10"}, {"currency": "USD", "total_balance": "2"}]}))
    balance = manager.verify_key("deepseek")["balance"]
    assert "10.00 CNY" in balance and "2.00 USD" in balance and "12.00" not in balance


def test_openrouter_zero_limit_is_not_unlimited(manager, monkeypatch):
    manager.add_key("openrouter", "synthetic-key")
    mock_http(monkeypatch, lambda request: httpx.Response(200, json={"data": {"usage": 0, "limit": 0}}))
    assert manager.verify_key("openrouter")["balance"] == "사용: $0.00 / 한도: $0.00"


def test_key_specific_route_requires_write_auth(client, manager, api_key, monkeypatch):
    item = manager.add_key("groq", "synthetic-key")
    calls = []
    monkeypatch.setattr(api_key_manager, "verify_key", lambda provider, key_id=None:
                        calls.append((provider, key_id)) or {"valid": True})
    path = f"/api/v1/keys/groq/{item['key_id']}/verify"
    assert client.post(path).status_code == 401
    assert calls == []
    assert client.post(path, headers={"X-API-Key": api_key}).status_code == 200
    assert calls == [("groq", item["key_id"])]
