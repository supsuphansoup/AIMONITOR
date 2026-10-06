"""Alert evaluation when a window has no traffic, or traffic with unknown cost."""
import pytest
from fastapi.testclient import TestClient

PRICED_TRACE = {
    "provider": "openai", "model_name": "gpt-4o",
    "prompt_tokens": 1_000_000, "completion_tokens": 0, "latency_ms": 10,
}  # $2.50
UNPRICED_TRACE = {"provider": "acme", "model_name": "mystery-1", "prompt_tokens": 1000, "latency_ms": 10}


def _rule(client: TestClient, metric_type: str, operator: str, threshold: float) -> None:
    res = client.post("/api/v1/alerts/rules", json={
        "name": f"{metric_type} {operator} {threshold}",
        "metric_type": metric_type,
        "operator": operator,
        "threshold_value": threshold,
        "window_minutes": 15,
        "cooldown_minutes": 0,
    })
    assert res.status_code == 201, res.text


def _evaluate(client: TestClient) -> list:
    return client.post("/api/v1/alerts/evaluate").json()


@pytest.mark.parametrize("metric_type", [
    "error_rate", "error_rate_pct",
    "latency_p50", "p50", "latency_p95", "p95", "p95_latency_ms", "latency_p99", "p99",
    "avg_latency", "latency",
])
def test_rate_and_latency_rules_stay_silent_without_traffic(client: TestClient, metric_type):
    """No requests means no latency and no error rate - not a latency of 0 ms."""
    _rule(client, metric_type, "<", 1_000_000)
    assert _evaluate(client) == []


@pytest.mark.parametrize("metric_type,operator,threshold", [
    ("total_requests", "<", 1),   # "traffic stopped" must keep working
    ("requests", "==", 0),
    ("total_cost", "<", 1),
    ("total_tokens", "<=", 0),
])
def test_count_and_sum_rules_still_see_zero_without_traffic(client: TestClient, metric_type, operator, threshold):
    _rule(client, metric_type, operator, threshold)
    assert len(_evaluate(client)) == 1


def test_rate_rule_fires_once_there_is_traffic(client: TestClient):
    client.post("/api/v1/traces", json=PRICED_TRACE)
    _rule(client, "latency_p95", "<", 1_000_000)
    assert len(_evaluate(client)) == 1


def test_cost_above_threshold_fires_on_known_cost_and_says_it_is_a_floor(client: TestClient):
    """Known cost is a lower bound; if the floor is over the line, so is the real total."""
    client.post("/api/v1/traces", json=PRICED_TRACE)
    client.post("/api/v1/traces", json=UNPRICED_TRACE)
    _rule(client, "total_cost", ">", 1.0)

    fired = _evaluate(client)
    assert len(fired) == 1
    assert "1 call" in fired[0]["message"]
    assert "unknown pricing" in fired[0]["message"]


@pytest.mark.parametrize("operator", ["<", "<=", "==", "!="])
def test_cost_rules_that_need_the_full_total_skip_while_cost_is_unknown(client: TestClient, operator):
    client.post("/api/v1/traces", json=PRICED_TRACE)
    client.post("/api/v1/traces", json=UNPRICED_TRACE)
    _rule(client, "total_cost", operator, 100.0)
    assert _evaluate(client) == []


def test_cost_below_threshold_fires_when_every_call_is_priced(client: TestClient):
    client.post("/api/v1/traces", json=PRICED_TRACE)
    _rule(client, "total_cost", "<", 100.0)
    fired = _evaluate(client)
    assert len(fired) == 1
    assert "unknown pricing" not in fired[0]["message"]
