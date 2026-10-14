import json
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from backend.app.db.session import get_session
from backend.app.main import app
from backend.app.models import alert as alert_models
from backend.app.models.alert import AlertRule
from backend.app.services.alert import (
    AlertEvaluator,
    compare_operator,
    extract_metric_value,
)

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(alert_models, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("8.8.8.8", 443))])


@pytest.fixture(name="session")
def session_fixture():
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    SQLModel.metadata.drop_all(engine)


@pytest.fixture(name="client")
def client_fixture(session: Session):
    def get_session_override():
        return session

    app.dependency_overrides[get_session] = get_session_override
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_create_alert_rule(client: TestClient):
    payload = {
        "name": "High Error Rate Alert",
        "metric_type": "error_rate",
        "operator": ">",
        "threshold_value": 5.0,
        "window_minutes": 10,
        "webhook_url": "https://hooks.slack.com/services/test/webhook",
        "is_active": True,
    }
    response = client.post("/api/v1/alerts/rules", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["id"] is not None
    assert data["name"] == "High Error Rate Alert"
    assert data["metric_type"] == "error_rate"
    assert data["operator"] == ">"
    assert data["threshold_value"] == 5.0
    assert data["window_minutes"] == 10
    assert data["webhook_url"] == "https://hooks.slack.com/…"
    assert data["webhook_configured"] is True
    assert data["is_active"] is True
    assert "created_at" in data


def test_list_and_get_alert_rules(client: TestClient):
    # Create active rule
    res1 = client.post(
        "/api/v1/alerts/rules",
        json={
            "name": "Active Rule",
            "metric_type": "error_rate",
            "threshold_value": 10.0,
            "is_active": True,
        },
    )
    rule1_id = res1.json()["id"]

    # Create inactive rule
    res2 = client.post(
        "/api/v1/alerts/rules",
        json={
            "name": "Inactive Rule",
            "metric_type": "latency_p95",
            "threshold_value": 1000.0,
            "is_active": False,
        },
    )
    res2.json()["id"]

    # List all
    res = client.get("/api/v1/alerts/rules")
    assert res.status_code == 200
    all_rules = res.json()
    assert len(all_rules) == 2

    # Filter active only
    res_active = client.get("/api/v1/alerts/rules?is_active=true")
    assert res_active.status_code == 200
    active_rules = res_active.json()
    assert len(active_rules) == 1
    assert active_rules[0]["name"] == "Active Rule"

    # Get single rule
    res_single = client.get(f"/api/v1/alerts/rules/{rule1_id}")
    assert res_single.status_code == 200
    assert res_single.json()["name"] == "Active Rule"

    # Get non-existent rule
    res_not_found = client.get("/api/v1/alerts/rules/99999")
    assert res_not_found.status_code == 404


def test_delete_alert_rule(client: TestClient):
    res = client.post(
        "/api/v1/alerts/rules",
        json={
            "name": "Rule to Delete",
            "metric_type": "cost_hourly",
            "threshold_value": 50.0,
        },
    )
    rule_id = res.json()["id"]

    # Delete existing rule
    del_res = client.delete(f"/api/v1/alerts/rules/{rule_id}")
    assert del_res.status_code == 204

    # Delete non-existent rule
    del_res_404 = client.delete(f"/api/v1/alerts/rules/{rule_id}")
    assert del_res_404.status_code == 404


def test_alert_evaluation_no_trigger(client: TestClient):
    # Create rule: error_rate > 50%
    client.post(
        "/api/v1/alerts/rules",
        json={
            "name": "Error Threshold",
            "metric_type": "error_rate",
            "operator": ">",
            "threshold_value": 50.0,
            "window_minutes": 15,
        },
    )

    # Ingest only SUCCESS traces
    client.post(
        "/api/v1/traces",
        json={"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 100, "completion_tokens": 50, "status": "SUCCESS"},
    )
    client.post(
        "/api/v1/traces",
        json={"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 100, "completion_tokens": 50, "status": "SUCCESS"},
    )

    # Evaluate
    eval_res = client.post("/api/v1/alerts/evaluate")
    assert eval_res.status_code == 200
    assert eval_res.json() == []

    # Check history
    hist_res = client.get("/api/v1/alerts/history")
    assert hist_res.status_code == 200
    assert hist_res.json() == []


def test_alert_evaluation_triggered_error_rate(client: TestClient):
    # Create rule: error_rate > 20%
    rule_res = client.post(
        "/api/v1/alerts/rules",
        json={
            "name": "Critical Error Spike",
            "metric_type": "error_rate",
            "operator": ">",
            "threshold_value": 20.0,
            "window_minutes": 15,
        },
    )
    rule_id = rule_res.json()["id"]

    # Ingest 1 SUCCESS and 1 ERROR trace (error_rate = 50.0%)
    client.post(
        "/api/v1/traces",
        json={"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 100, "completion_tokens": 50, "status": "SUCCESS"},
    )
    client.post(
        "/api/v1/traces",
        json={"provider": "anthropic", "model_name": "claude-3-5-sonnet", "prompt_tokens": 100, "completion_tokens": 50, "status": "ERROR", "error_message": "Rate limit exceeded"},
    )

    # Trigger evaluation
    eval_res = client.post("/api/v1/alerts/evaluate")
    assert eval_res.status_code == 200
    triggered = eval_res.json()
    assert len(triggered) == 1
    assert triggered[0]["rule_id"] == rule_id
    assert triggered[0]["rule_name"] == "Critical Error Spike"
    assert triggered[0]["triggered_value"] == 50.0
    assert triggered[0]["sent_successfully"] is True

    # Check history endpoint
    hist_res = client.get(f"/api/v1/alerts/history?rule_id={rule_id}")
    assert hist_res.status_code == 200
    hist_data = hist_res.json()
    assert len(hist_data) == 1
    assert hist_data[0]["rule_name"] == "Critical Error Spike"


def test_alert_evaluation_latency_p95(client: TestClient):
    # Create rule: latency_p95 >= 500.0 ms
    rule_res = client.post(
        "/api/v1/alerts/rules",
        json={
            "name": "High P95 Latency",
            "metric_type": "latency_p95",
            "operator": ">=",
            "threshold_value": 500.0,
            "window_minutes": 15,
        },
    )
    rule_id = rule_res.json()["id"]

    # Ingest traces with latencies 200ms, 600ms, 800ms
    client.post(
        "/api/v1/traces",
        json={"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 10, "completion_tokens": 10, "latency_ms": 200.0},
    )
    client.post(
        "/api/v1/traces",
        json={"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 10, "completion_tokens": 10, "latency_ms": 600.0},
    )
    client.post(
        "/api/v1/traces",
        json={"provider": "openai", "model_name": "gpt-4o", "prompt_tokens": 10, "completion_tokens": 10, "latency_ms": 800.0},
    )

    eval_res = client.post("/api/v1/alerts/evaluate")
    assert eval_res.status_code == 200
    triggered = eval_res.json()
    assert len(triggered) == 1
    assert triggered[0]["rule_id"] == rule_id
    assert triggered[0]["triggered_value"] >= 500.0


def test_alert_webhook_dispatch_success(session: Session):
    rule = AlertRule(
        name="Webhook Test Rule",
        metric_type="error_rate",
        operator=">",
        threshold_value=10.0,
        webhook_url="https://hooks.slack.com/services/mock",
        is_active=True,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)

    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200)

    original = httpx.AsyncClient
    with patch("httpx.AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs)):
        success = AlertEvaluator.dispatch_webhook(
            rule=rule,
            metric_value=25.0,
            triggered_at=datetime.now(timezone.utc),
        )
        assert success is True
        assert len(requests) == 1
        assert str(requests[0].url) == "https://hooks.slack.com/services/mock"
        assert json.loads(requests[0].content)["triggered_value"] == 25.0


def test_alert_webhook_dispatch_graceful_failure(session: Session):
    rule = AlertRule(
        name="Failing Webhook Rule",
        metric_type="error_rate",
        operator=">",
        threshold_value=10.0,
        webhook_url="https://invalid-non-existent-webhook.example.com",
        is_active=True,
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)

    # Test HTTP 500 status response
    original = httpx.AsyncClient
    with patch("httpx.AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(lambda request: httpx.Response(500)), **kwargs)):
        success = AlertEvaluator.dispatch_webhook(
            rule=rule,
            metric_value=30.0,
            triggered_at=datetime.now(timezone.utc),
        )
        assert success is False

    # Test network exception / timeout
    def failure(request):
        raise httpx.ConnectError("Connection refused", request=request)

    with patch("httpx.AsyncClient", side_effect=lambda **kwargs: original(transport=httpx.MockTransport(failure), **kwargs)):
        success_exc = AlertEvaluator.dispatch_webhook(
            rule=rule,
            metric_value=30.0,
            triggered_at=datetime.now(timezone.utc),
        )
        assert success_exc is False


def test_alert_evaluator_operator_comparison():
    assert compare_operator(10.0, ">", 5.0) is True
    assert compare_operator(5.0, ">", 10.0) is False
    assert compare_operator(10.0, ">=", 10.0) is True
    assert compare_operator(9.9, ">=", 10.0) is False
    assert compare_operator(5.0, "<", 10.0) is True
    assert compare_operator(10.0, "<", 5.0) is False
    assert compare_operator(10.0, "<=", 10.0) is True
    assert compare_operator(10.1, "<=", 10.0) is False
    assert compare_operator(10.0, "==", 10.0) is True
    assert compare_operator(10.0, "!=", 5.0) is True
    # Unknown fallback to >
    assert compare_operator(10.0, "unknown_op", 5.0) is True


def test_extract_metric_value():
    overview = {
        "error_rate_pct": 12.5,
        "p95_latency_ms": 350.2,
        "total_cost_usd": 0.045,
        "total_requests": 100,
        "error_count": 5,
        "avg_latency_ms": 150.0,
        "p50_latency_ms": 120.0,
        "p99_latency_ms": 500.0,
        "total_tokens": 12000,
    }

    assert extract_metric_value(overview, "error_rate") == 12.5
    assert extract_metric_value(overview, "error_rate_pct") == 12.5
    assert extract_metric_value(overview, "latency_p95") == 350.2
    assert extract_metric_value(overview, "p95_latency_ms") == 350.2
    assert extract_metric_value(overview, "cost_hourly") == 0.045
    assert extract_metric_value(overview, "total_cost_usd") == 0.045
    assert extract_metric_value(overview, "total_requests") == 100.0
    assert extract_metric_value(overview, "avg_latency") == 150.0
    assert extract_metric_value(overview, "non_existent_metric") == 0.0
