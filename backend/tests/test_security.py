"""API key authentication."""
from fastapi.testclient import TestClient

from backend.app.core.config import settings

TRACE = {"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 10, "completion_tokens": 5}


def test_open_mode_allows_writes_when_no_key_configured(client: TestClient):
    assert settings.API_KEY is None
    assert client.post("/api/v1/traces", json=TRACE).status_code == 201


def test_write_requires_key_when_configured(client: TestClient, api_key: str):
    assert client.post("/api/v1/traces", json=TRACE).status_code == 401


def test_write_accepts_x_api_key_header(client: TestClient, api_key: str):
    res = client.post("/api/v1/traces", json=TRACE, headers={"X-API-Key": api_key})
    assert res.status_code == 201


def test_write_accepts_bearer_token(client: TestClient, api_key: str):
    res = client.post("/api/v1/traces", json=TRACE, headers={"Authorization": f"Bearer {api_key}"})
    assert res.status_code == 201


def test_wrong_key_is_rejected(client: TestClient, api_key: str):
    res = client.post("/api/v1/traces", json=TRACE, headers={"X-API-Key": "not-the-key"})
    assert res.status_code == 401


def test_reads_stay_open_by_default(client: TestClient, api_key: str):
    """Reads are only gated when REQUIRE_AUTH_FOR_READS is switched on."""
    assert client.get("/api/v1/traces").status_code == 200
    assert client.get("/api/v1/metrics/overview").status_code == 200


def test_reads_can_be_gated(client: TestClient, api_key: str, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_AUTH_FOR_READS", True)
    assert client.get("/api/v1/traces").status_code == 401
    assert client.get("/api/v1/traces", headers={"X-API-Key": api_key}).status_code == 200


def test_alert_and_purge_endpoints_are_guarded(client: TestClient, api_key: str):
    assert client.post("/api/v1/alerts/evaluate").status_code == 401
    assert client.post("/api/v1/system/purge?days=30").status_code == 401
    assert client.delete("/api/v1/alerts/rules/1").status_code == 401


def test_health_is_never_gated(client: TestClient, api_key: str, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_AUTH_FOR_READS", True)
    assert client.get("/health").status_code == 200
