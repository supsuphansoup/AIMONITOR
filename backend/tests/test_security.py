"""API key authentication."""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.core.config import Settings, settings

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


@pytest.mark.parametrize("header,value", [(b"x-api-key", b"\xff"), (b"authorization", b"Bearer \xff")])
def test_non_ascii_credentials_are_unauthorized(client, api_key, header, value):
    response = client.post("/api/v1/traces", json=TRACE, headers=[(header, value)])
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("key", ["PRIVATE_비밀", "PRIVATE_\x00", "PRIVATE_\nsecret", "PRIVATE_ secret"])
def test_invalid_configured_key_fails_without_echoing_it(key):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, API_KEY=key)
    assert "PRIVATE_" not in str(error.value)


@pytest.mark.parametrize("key,expected", [(None, None), ("  ", None), (" valid-key_123 ", "valid-key_123")])
def test_configured_key_normalization(key, expected):
    assert Settings(_env_file=None, API_KEY=key).API_KEY == expected
