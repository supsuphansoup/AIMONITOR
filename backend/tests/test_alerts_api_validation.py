"""Webhook secrets and partial-update validation (ALR-01 / ALR-07)."""
import pytest

from backend.app.core.config import settings
from backend.app.models import alert as alert_models
from backend.app.models.alert import AlertRule

RULE = {"name": "safe rule", "metric_type": "total_requests", "threshold_value": 1}
SECRET_URL = "https://hooks.slack.com/services/FAKE/REVIEW/SECRET?token=FAKE"


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(alert_models, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("8.8.8.8", 443))])


def test_rule_responses_mask_webhook_on_create_read_and_update(client, session, api_key, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_AUTH_FOR_READS", False)
    headers = {"X-API-Key": api_key}
    created = client.post("/api/v1/alerts/rules", json={**RULE, "webhook_url": SECRET_URL}, headers=headers)
    assert created.status_code == 201
    rule_id = created.json()["id"]
    patched = client.patch(f"/api/v1/alerts/rules/{rule_id}", json={"name": "renamed"}, headers=headers)
    bodies = [created.json(), patched.json(), client.get(f"/api/v1/alerts/rules/{rule_id}").json(),
              client.get("/api/v1/alerts/rules").json()[0]]
    for body in bodies:
        assert body["webhook_url"] == "https://hooks.slack.com/…"
        assert body["webhook_configured"] is True
        assert "SECRET" not in str(body)
    assert session.get(AlertRule, rule_id).webhook_url == SECRET_URL
    masked = client.patch(f"/api/v1/alerts/rules/{rule_id}", json={"webhook_url": bodies[0]["webhook_url"]}, headers=headers)
    assert masked.status_code == 422
    session.expire_all()
    assert session.get(AlertRule, rule_id).webhook_url == SECRET_URL
    cleared = client.patch(f"/api/v1/alerts/rules/{rule_id}", json={"webhook_url": None}, headers=headers)
    assert cleared.json()["webhook_url"] is None
    assert cleared.json()["webhook_configured"] is False


@pytest.mark.parametrize("field", ["name", "metric_type", "operator", "threshold_value", "window_minutes", "cooldown_minutes", "is_active"])
def test_patch_explicit_null_is_rejected_and_omitted_fields_preserved(client, field):
    rule = client.post("/api/v1/alerts/rules", json=RULE).json()
    response = client.patch(f"/api/v1/alerts/rules/{rule['id']}", json={field: None})
    assert response.status_code == 422
    unchanged = client.patch(f"/api/v1/alerts/rules/{rule['id']}", json={})
    assert unchanged.status_code == 200
    assert unchanged.json()[field] == rule[field]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_threshold_is_rejected_on_create_and_patch(client, value):
    assert client.post("/api/v1/alerts/rules", json={**RULE, "threshold_value": value}).status_code == 422
    rule = client.post("/api/v1/alerts/rules", json=RULE).json()
    assert client.patch(f"/api/v1/alerts/rules/{rule['id']}", json={"threshold_value": value}).status_code == 422
    assert client.get(f"/api/v1/alerts/rules/{rule['id']}").json()["threshold_value"] == 1
