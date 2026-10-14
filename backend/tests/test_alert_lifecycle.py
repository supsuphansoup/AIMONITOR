"""Alert cooldown, rule updates, and validation."""
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.core.timeutil import utc_now
from backend.app.models.alert import AlertRule
from backend.app.services.alert import AlertEvaluator

ERROR_TRACE = {"provider": "openai", "model_name": "gpt-4o", "status": "ERROR", "latency_ms": 10}
ANY_ERROR_RULE = {
    "name": "any error",
    "metric_type": "error_rate",
    "operator": ">",
    "threshold_value": 0.0,
    "window_minutes": 15,
}


def _create_rule(client: TestClient, **overrides) -> dict:
    payload = {**ANY_ERROR_RULE, **overrides}
    res = client.post("/api/v1/alerts/rules", json=payload)
    assert res.status_code == 201, res.text
    return res.json()


def test_cooldown_suppresses_repeat_firing(client: TestClient):
    """A sustained incident should page once per cooldown, not once per pass."""
    client.post("/api/v1/traces", json=ERROR_TRACE)
    _create_rule(client, cooldown_minutes=15)

    assert len(client.post("/api/v1/alerts/evaluate").json()) == 1
    assert client.post("/api/v1/alerts/evaluate").json() == []
    assert client.post("/api/v1/alerts/evaluate").json() == []

    assert len(client.get("/api/v1/alerts/history").json()) == 1


def test_zero_cooldown_fires_every_pass(client: TestClient):
    client.post("/api/v1/traces", json=ERROR_TRACE)
    _create_rule(client, cooldown_minutes=0)

    assert len(client.post("/api/v1/alerts/evaluate").json()) == 1
    assert len(client.post("/api/v1/alerts/evaluate").json()) == 1


def test_cooldown_expires(client: TestClient, session: Session):
    client.post("/api/v1/traces", json=ERROR_TRACE)
    _create_rule(client, cooldown_minutes=5)
    assert len(client.post("/api/v1/alerts/evaluate").json()) == 1

    rule = session.exec(select(AlertRule)).one()
    rule.last_triggered_at = utc_now() - timedelta(minutes=6)
    session.add(rule)
    session.commit()

    assert len(client.post("/api/v1/alerts/evaluate").json()) == 1


def test_ignore_cooldown_forces_evaluation(client: TestClient):
    client.post("/api/v1/traces", json=ERROR_TRACE)
    _create_rule(client, cooldown_minutes=60)
    assert len(client.post("/api/v1/alerts/evaluate").json()) == 1
    assert client.post("/api/v1/alerts/evaluate").json() == []
    assert len(client.post("/api/v1/alerts/evaluate?ignore_cooldown=true").json()) == 1


def test_last_triggered_at_is_exposed(client: TestClient):
    client.post("/api/v1/traces", json=ERROR_TRACE)
    rule = _create_rule(client)
    assert rule["last_triggered_at"] is None

    client.post("/api/v1/alerts/evaluate")
    refreshed = client.get(f"/api/v1/alerts/rules/{rule['id']}").json()
    assert refreshed["last_triggered_at"] is not None


def test_rule_can_be_paused_and_resumed(client: TestClient):
    client.post("/api/v1/traces", json=ERROR_TRACE)
    rule = _create_rule(client, cooldown_minutes=0)

    paused = client.patch(f"/api/v1/alerts/rules/{rule['id']}", json={"is_active": False})
    assert paused.status_code == 200
    assert paused.json()["is_active"] is False
    assert client.post("/api/v1/alerts/evaluate").json() == []

    resumed = client.patch(f"/api/v1/alerts/rules/{rule['id']}", json={"is_active": True})
    assert resumed.json()["is_active"] is True
    assert len(client.post("/api/v1/alerts/evaluate").json()) == 1


def test_patch_updates_threshold_only(client: TestClient):
    rule = _create_rule(client)
    updated = client.patch(f"/api/v1/alerts/rules/{rule['id']}", json={"threshold_value": 42.5}).json()
    assert updated["threshold_value"] == 42.5
    assert updated["name"] == rule["name"]
    assert updated["metric_type"] == rule["metric_type"]


def test_patch_missing_rule_is_404(client: TestClient):
    assert client.patch("/api/v1/alerts/rules/9999", json={"is_active": False}).status_code == 404


def test_unknown_metric_type_is_rejected(client: TestClient):
    res = client.post("/api/v1/alerts/rules", json={**ANY_ERROR_RULE, "metric_type": "vibes"})
    assert res.status_code == 422
    assert res.json()["detail"] == [
        {"loc": ["body", "metric_type"], "type": "value_error", "msg": "Invalid field value"},
    ]
    assert "vibes" not in res.text


def test_invalid_webhook_scheme_is_rejected(client: TestClient):
    res = client.post("/api/v1/alerts/rules", json={**ANY_ERROR_RULE, "webhook_url": "slack.com/hook"})
    assert res.status_code == 422


def test_window_bounds_are_enforced(client: TestClient):
    assert client.post("/api/v1/alerts/rules", json={**ANY_ERROR_RULE, "window_minutes": 0}).status_code == 422
    assert client.post("/api/v1/alerts/rules", json={**ANY_ERROR_RULE, "window_minutes": 99999}).status_code == 422


def test_deleting_a_rule_removes_its_history(client: TestClient):
    client.post("/api/v1/traces", json=ERROR_TRACE)
    rule = _create_rule(client)
    client.post("/api/v1/alerts/evaluate")
    assert len(client.get("/api/v1/alerts/history").json()) == 1

    assert client.delete(f"/api/v1/alerts/rules/{rule['id']}").status_code == 204
    assert client.get("/api/v1/alerts/history").json() == []


def test_supported_metrics_endpoint(client: TestClient):
    body = client.get("/api/v1/alerts/metrics").json()
    keys = {m["key"] for m in body["metrics"]}
    assert {"error_rate", "latency_p95", "total_cost"} <= keys
    assert ">" in body["operators"]
    assert next(m for m in body["metrics"] if m["key"] == "latency_p95")["unit"] == "ms"


def test_is_in_cooldown_helper():
    now = utc_now()
    rule = AlertRule(name="r", metric_type="error_rate", threshold_value=1.0, cooldown_minutes=10)
    assert AlertEvaluator.is_in_cooldown(rule, now) is False

    rule.last_triggered_at = now - timedelta(minutes=3)
    assert AlertEvaluator.is_in_cooldown(rule, now) is True

    rule.last_triggered_at = now - timedelta(minutes=11)
    assert AlertEvaluator.is_in_cooldown(rule, now) is False

    rule.cooldown_minutes = 0
    rule.last_triggered_at = now
    assert AlertEvaluator.is_in_cooldown(rule, now) is False
