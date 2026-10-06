"""Unit and integration tests for API Key Manager & /api/v1/keys endpoints."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.services import secret_store
from backend.app.services.api_key_manager import APIKeyManager, mask_key


def test_mask_key():
    assert mask_key("") == ""
    assert mask_key("1234567") == "••••••••"
    assert mask_key("sk-ant-api03-abcdefgh1234") == "sk-a••••••••1234"


@pytest.mark.skipif(secret_store.AVAILABLE_SCHEME != secret_store.SCHEME_DPAPI, reason="Windows DPAPI")
def test_api_key_manager_crud(tmp_path: Path):
    key_file = tmp_path / "test_keys.json"
    mgr = APIKeyManager(filepath=key_file)

    # Initially empty
    keys = mgr.list_keys()
    assert len(keys) >= 5
    assert not any(k["has_key"] for k in keys if k["source"] == "stored")

    # Set OpenAI key
    res = mgr.set_key("openai", "sk-proj-testkey1234567890")
    assert res["provider"] == "openai"
    assert "sk-p" in res["masked_key"]
    assert mgr.get_registered_key("openai") == "sk-proj-testkey1234567890"

    # Verify listing reflects registered key
    keys = mgr.list_keys()
    openai_entry = next(k for k in keys if k["provider"] == "openai")
    assert openai_entry["has_key"] is True
    assert openai_entry["source"] == "stored"

    # Delete key
    assert mgr.delete_key("openai") is True
    assert mgr.get_registered_key("openai") is None


@pytest.mark.skipif(secret_store.AVAILABLE_SCHEME != secret_store.SCHEME_DPAPI, reason="Windows DPAPI")
def test_api_keys_endpoints(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    key_file = tmp_path / "test_api_keys.json"
    from backend.app.services.api_key_manager import api_key_manager
    monkeypatch.setattr(api_key_manager, "filepath", key_file)

    monkeypatch.setattr(api_key_manager, "verify_key", lambda provider: {"valid": True})

    # 1. GET /api/v1/keys
    resp = client.get("/api/v1/keys")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert any(p["provider"] == "openrouter" for p in data)

    # 2. POST /api/v1/keys
    post_resp = client.post("/api/v1/keys", json={"provider": "deepseek", "key": "sk-deepseek-test-1234"})
    assert post_resp.status_code == 200
    post_data = post_resp.json()
    assert post_data["provider"] == "deepseek"
    assert "sk-d" in post_data["masked_key"]

    # 3. Verify in list
    list_resp = client.get("/api/v1/keys")
    ds = next(k for k in list_resp.json() if k["provider"] == "deepseek")
    assert ds["has_key"] is True

    # 4. DELETE /api/v1/keys/deepseek
    del_resp = client.delete("/api/v1/keys/deepseek")
    assert del_resp.status_code == 200
    assert del_resp.json()["deleted"] is True
