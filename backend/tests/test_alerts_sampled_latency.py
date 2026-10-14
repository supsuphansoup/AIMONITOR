"""Skip capped percentiles and expose safe evaluation diagnostics (ALR-06)."""
import logging
from datetime import timedelta

import pytest

from backend.app.core.config import settings
from backend.app.core.timeutil import utc_now
from backend.app.models.alert import AlertRule
from backend.app.models.trace import TraceEvent


def sampled_window(session, monkeypatch, metric):
    monkeypatch.setattr(settings, "MAX_AGGREGATION_ROWS", 2)
    now = utc_now()
    for index, latency in enumerate([1000, 1000, 10, 10]):
        session.add(TraceEvent(trace_id=f"sample-{index}", provider="openai", model_name="test", latency_ms=latency,
                               latency_measured=True, timestamp=now - timedelta(seconds=10 - index)))
    rule = AlertRule(name="no secrets in diagnostics", metric_type=metric, operator="<", threshold_value=100)
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule.id


@pytest.mark.parametrize("metric", ["p50", "p95", "p99", "latency_p95", "p95_latency_ms"])
def test_sampled_percentile_rules_skip_without_consuming_cooldown(client, session, monkeypatch, caplog, metric):
    rule_id = sampled_window(session, monkeypatch, metric)
    with caplog.at_level(logging.INFO, logger="backend.app.services.alert"):
        response = client.post("/api/v1/alerts/evaluate")
    assert response.status_code == 200 and response.json() == []
    session.expire_all()
    assert session.get(AlertRule, rule_id).last_triggered_at is None
    assert client.get("/api/v1/alerts/history").json() == []
    assert f"rule_id={rule_id} skipped reason=latency_sampled" in caplog.text


def test_include_skipped_returns_history_and_safe_sample_metadata(client, session, monkeypatch):
    rule_id = sampled_window(session, monkeypatch, "p95")
    response = client.post("/api/v1/alerts/evaluate?include_skipped=true&ignore_cooldown=true")
    assert response.status_code == 200
    assert response.json() == {
        "histories": [],
        "skipped_rules": [{"rule_id": rule_id, "reason": "latency_sampled", "sample_count": 2, "sample_limit": 2}],
    }


def test_sampled_window_does_not_skip_exact_average_or_count(client, session, monkeypatch):
    sampled_window(session, monkeypatch, "p95")
    for metric, threshold in [("avg_latency", 1000), ("total_requests", 5)]:
        session.add(AlertRule(name=metric, metric_type=metric, operator="<", threshold_value=threshold))
    session.commit()
    response = client.post("/api/v1/alerts/evaluate?include_skipped=true")
    assert {row["rule_name"] for row in response.json()["histories"]} == {"avg_latency", "total_requests"}
    assert len(response.json()["skipped_rules"]) == 1


def test_include_skipped_without_sampling_keeps_normal_history(client, session):
    session.add(AlertRule(name="no traffic", metric_type="total_requests", operator="<", threshold_value=1))
    session.commit()
    response = client.post("/api/v1/alerts/evaluate?include_skipped=true")
    assert len(response.json()["histories"]) == 1
    assert response.json()["skipped_rules"] == []
