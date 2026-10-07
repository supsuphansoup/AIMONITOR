"""Multiple sealed keys, safe labels, legacy migration, and independent billing caches."""
import json
import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.services import provider_spend, secret_store
from backend.app.services.api_key_manager import KNOWN_PROVIDERS, APIKeyManager, api_key_manager
from backend.app.services.provider_spend import SpendService


@pytest.fixture
def manager(tmp_path, monkeypatch):
    for info in KNOWN_PROVIDERS:
        for name in info["env_vars"]:
            monkeypatch.delenv(name, raising=False)
    # Production encryption is untouched; use real DPAPI where available.
    if secret_store.AVAILABLE_SCHEME != secret_store.SCHEME_DPAPI:
        secrets = {}

        def seal(secret):
            token = str(len(secrets))
            secrets[token] = secret
            return "test-sealed", token

        monkeypatch.setattr(secret_store, "seal", seal)
        monkeypatch.setattr(secret_store, "unseal", lambda scheme, token: secrets.get(token))
    path = tmp_path / "keys.json"
    monkeypatch.setattr(api_key_manager, "filepath", path)
    return APIKeyManager(filepath=path)


def test_multiple_key_crud_and_compatibility(manager):
    one = manager.add_key("openrouter", "test-secret-one", "업무")
    two = manager.add_key("openrouter", "test-secret-two", "개인")
    assert one["key_id"] != two["key_id"]
    assert re.fullmatch(r"[A-Za-z0-9_-]{1,32}", one["key_id"])
    assert manager.get_registered_key("openrouter") == "test-secret-one"
    assert manager.get_key("openrouter", two["key_id"]) == "test-secret-two"
    assert manager.get_key("openrouter", "missing") is None
    assert len(manager.list_keys("openrouter")) == 2
    manager.set_key("openrouter", "test-secret-replacement")
    assert manager.get_key("openrouter", one["key_id"]) == "test-secret-replacement"
    assert manager.get_key("openrouter", two["key_id"]) == "test-secret-two"
    assert manager.update_label("openrouter", two["key_id"], "새 이름")["label"] == "새 이름"
    assert manager.update_label("openrouter", "missing", "이름") is None
    assert manager.delete_key("openrouter", one["key_id"])
    assert manager.get_registered_key("openrouter") == "test-secret-two"
    assert manager.delete_key("openrouter")
    assert not manager.delete_key("openrouter")


def test_key_endpoints_and_secret_non_disclosure(client, manager, caplog):
    ids = []
    for label, secret in [("업무", "test-secret-one"), ("개인", "test-secret-two")]:
        response = client.post("/api/v1/keys", json={"provider": "deepseek", "key": secret, "label": label})
        assert response.status_code == 200
        assert secret not in response.text
        item = response.json()
        assert item["label"] == label and item["masked"] == item["masked_key"]
        ids.append(item["key_id"])
    response = client.get("/api/v1/keys")
    card = next(item for item in response.json() if item["provider"] == "deepseek")
    assert {item["key_id"] for item in card["keys"]} == set(ids)
    assert "test-secret-one" not in response.text and "test-secret-two" not in response.text
    response = client.patch(f"/api/v1/keys/deepseek/{ids[1]}", json={"label": "변경"})
    assert response.status_code == 200 and response.json()["label"] == "변경"
    assert client.delete(f"/api/v1/keys/deepseek/{ids[0]}").status_code == 200
    assert len(manager.list_keys("deepseek")) == 1
    assert client.delete(f"/api/v1/keys/deepseek/{ids[0]}").status_code == 404
    assert client.delete("/api/v1/keys/deepseek").status_code == 200
    assert manager.list_keys("deepseek") == []
    assert "test-secret-one" not in caplog.text and "test-secret-two" not in caplog.text


@pytest.mark.parametrize("label", ["", " ", "x" * 41, "sk-secret-123", "Bearer secret",
                                   "test-secret-one", "a@example.com", "hello\nworld"])
def test_key_labels_reject_private_material(client, manager, label, caplog):
    existing = manager.add_key("openrouter", "test-secret-one", "안전")
    response = client.post("/api/v1/keys", json={"provider": "deepseek", "key": "safe-key", "label": label})
    assert response.status_code in (400, 422)
    assert manager.list_keys("deepseek") == []
    response = client.patch(f"/api/v1/keys/openrouter/{existing['key_id']}", json={"label": label})
    assert response.status_code in (400, 422)
    if len(label) > 1 and label.strip():
        assert label not in response.text and label not in caplog.text
    assert manager.list_keys("openrouter")[0]["label"] == "안전"
    assert "test-secret-one" not in manager.filepath.read_text(encoding="utf-8")


def test_new_key_cannot_be_its_own_label(client, manager):
    response = client.post("/api/v1/keys", json={"provider": "groq", "key": "opaque-value",
                                               "label": "내 opaque-value"})
    assert response.status_code == 400 and "opaque-value" not in response.text
    assert manager.list_keys("groq") == []


@pytest.mark.parametrize("plaintext", [True, False])
def test_legacy_single_key_migration_keeps_default_and_sealing(manager, plaintext):
    secret = "legacy-key-private"
    legacy = {"key": secret} if plaintext else dict(
        zip(("scheme", "sealed"), secret_store.seal(secret), strict=True)
    )
    manager.filepath.write_text(json.dumps({"openrouter": {**legacy, "status": "valid",
                                                          "last_verified": "2026-10-01T00:00:00+00:00"}}))
    keys = manager.list_keys("openrouter")
    assert len(keys) == 1 and keys[0]["key_id"] == "default" and keys[0]["label"] == "기본"
    assert keys[0]["status"] == "valid" and keys[0]["last_verified"].endswith("+00:00")
    assert manager.get_registered_key("openrouter") == secret
    assert manager.get_key("openrouter", "default") == secret
    stored = json.loads(manager.filepath.read_text(encoding="utf-8"))
    assert "key" not in stored["openrouter"]["default"] and secret not in json.dumps(stored)
    assert manager.migrate_plaintext() == 0


def test_concurrent_additions_preserve_all_keys(manager):
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda i: manager.add_key("openrouter", f"secret-{i}", f"키 {i}"), range(8)))
    assert len({result["key_id"] for result in results}) == 8
    assert len(manager.list_keys("openrouter")) == 8


def test_key_spend_uses_correct_secrets_independent_cache_and_unknown(manager, monkeypatch, client):
    from backend.app.services import api_key_manager as module

    calls = []

    def fetch(secret, client, now=None):
        calls.append(secret)
        return {"kind": "spend", "amount": 1 if secret.endswith("one") else 2, "currency": "USD"}

    monkeypatch.setitem(provider_spend.FETCHERS, "openrouter_api", ("openrouter", fetch))
    service = SpendService(api_key_manager.get_registered_key)
    monkeypatch.setattr(module, "spend_service", service)
    monkeypatch.setattr("backend.app.routers.api_keys.spend_service", service)
    one = manager.add_key("openrouter", "billing-secret-one", "업무")
    two = manager.add_key("openrouter", "billing-secret-two", "개인")
    manager.add_key("openai", "regular-key", "일반")
    response = client.get("/api/v1/keys/spend")
    assert response.status_code == 200
    result = response.json()
    assert result["openrouter_api"]["amount"] == 1  # legacy representative is preserved
    entries = {item["key_id"]: item for item in result["items"]}
    assert entries[one["key_id"]]["amount"] == 1 and entries[two["key_id"]]["amount"] == 2
    assert entries[two["key_id"]]["label"] == "개인"
    unknown = next(item for item in entries.values() if item["provider"] == "openai")
    assert unknown["kind"] == "unavailable" and unknown["amount"] is None
    count = len(calls)
    client.get("/api/v1/keys/spend")
    assert len(calls) == count
    client.get("/api/v1/keys/spend?force=true")
    assert len(calls) > count
    assert "billing-secret" not in response.text and "regular-key" not in response.text


def test_key_spend_cache_invalidation_and_failure_do_not_leak(caplog):
    service = SpendService(lambda provider: None)
    calls = []

    def fetch(secret, client):
        calls.append(secret)
        if secret == "secret-failure":
            raise RuntimeError(secret)
        return {"amount": len(calls), "kind": "spend"}

    first = service._spend_secret("openrouter_api", "secret-one", fetch, False, ("openrouter", "id"))
    assert service._spend_secret("openrouter_api", "secret-one", fetch, False, ("openrouter", "id")) == first
    changed = service._spend_secret("openrouter_api", "secret-two", fetch, False, ("openrouter", "id"))
    assert changed["amount"] == 2
    failed = service._spend_secret("openrouter_api", "secret-failure", fetch, False, ("openrouter", "id"))
    assert failed["kind"] == "unavailable" and failed["amount"] is None
    assert "secret-failure" not in json.dumps(failed) and "secret-failure" not in caplog.text
