import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
import httpx
from sqlmodel import Session, select, col

from backend.app.models.alert import AlertRule, AlertHistory
from backend.app.services.aggregator import MetricAggregator

logger = logging.getLogger(__name__)

# Map commonly used metric identifiers to MetricAggregator overview field keys
METRIC_FIELD_MAP: Dict[str, str] = {
    "error_rate": "error_rate_pct",
    "error_rate_pct": "error_rate_pct",
    "error_count": "error_count",
    "errors": "error_count",
    "total_requests": "total_requests",
    "requests": "total_requests",
    "request_count": "total_requests",
    "success_count": "success_count",
    "latency_p95": "p95_latency_ms",
    "p95_latency": "p95_latency_ms",
    "p95_latency_ms": "p95_latency_ms",
    "p95": "p95_latency_ms",
    "latency_p50": "p50_latency_ms",
    "p50_latency": "p50_latency_ms",
    "p50_latency_ms": "p50_latency_ms",
    "p50": "p50_latency_ms",
    "latency_p99": "p99_latency_ms",
    "p99_latency": "p99_latency_ms",
    "p99_latency_ms": "p99_latency_ms",
    "p99": "p99_latency_ms",
    "avg_latency": "avg_latency_ms",
    "avg_latency_ms": "avg_latency_ms",
    "latency": "avg_latency_ms",
    "cost_hourly": "total_cost_usd",
    "total_cost": "total_cost_usd",
    "total_cost_usd": "total_cost_usd",
    "cost": "total_cost_usd",
    "total_tokens": "total_tokens",
    "tokens": "total_tokens",
    "prompt_tokens": "prompt_tokens",
    "completion_tokens": "completion_tokens",
}


def extract_metric_value(overview: Dict[str, Any], metric_type: str) -> float:
    """Extract and normalize metric value from overview metrics dictionary."""
    normalized_type = metric_type.lower().strip()
    target_key = METRIC_FIELD_MAP.get(normalized_type, normalized_type)
    val = overview.get(target_key, 0.0)
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def compare_operator(actual_value: float, operator: str, threshold: float) -> bool:
    """Evaluate comparison operator."""
    op = operator.strip()
    if op == ">":
        return actual_value > threshold
    elif op == ">=":
        return actual_value >= threshold
    elif op == "<":
        return actual_value < threshold
    elif op == "<=":
        return actual_value <= threshold
    elif op in ("==", "="):
        return actual_value == threshold
    elif op == "!=":
        return actual_value != threshold
    else:
        logger.warning("Unknown operator '%s', defaulting to '>' comparison", operator)
        return actual_value > threshold


class AlertEvaluator:
    """Evaluation and alerting engine for anomaly detection rules."""

    @staticmethod
    def dispatch_webhook(rule: AlertRule, metric_value: float, triggered_at: datetime) -> bool:
        """Send webhook notification via HTTP POST.

        Returns True if successful, False otherwise.
        Catches any connection or HTTP errors gracefully.
        """
        if not rule.webhook_url or not rule.webhook_url.strip():
            return True

        payload = {
            "text": f"🚨 [AIMONITOR ALERT] Rule '{rule.name}' triggered: {rule.metric_type} is {metric_value} (threshold: {rule.operator} {rule.threshold_value})",
            "rule_id": rule.id,
            "rule_name": rule.name,
            "metric_type": rule.metric_type,
            "operator": rule.operator,
            "threshold_value": rule.threshold_value,
            "triggered_value": metric_value,
            "window_minutes": rule.window_minutes,
            "triggered_at": triggered_at.isoformat(),
        }

        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.post(rule.webhook_url, json=payload)
                if response.status_code >= 400:
                    logger.warning(
                        "Webhook dispatch for alert '%s' returned status %d: %s",
                        rule.name,
                        response.status_code,
                        response.text,
                    )
                    return False
                return True
        except Exception as exc:
            logger.warning(
                "Webhook dispatch for alert '%s' to '%s' failed gracefully: %s",
                rule.name,
                rule.webhook_url,
                exc,
            )
            return False

    @classmethod
    def evaluate_rules(cls, session: Session) -> List[AlertHistory]:
        """Query all active rules, evaluate against recent metrics, log and dispatch alerts.

        Returns a list of created AlertHistory records.
        """
        query = select(AlertRule).where(AlertRule.is_active == True)
        rules: List[AlertRule] = list(session.exec(query).all())

        if not rules:
            return []

        triggered_histories: List[AlertHistory] = []
        now = datetime.now(timezone.utc)

        for rule in rules:
            window_minutes = max(1, rule.window_minutes)
            start_time = now - timedelta(minutes=window_minutes)

            overview = MetricAggregator.get_overview_metrics(
                session=session,
                start_time=start_time,
                end_time=now,
            )

            metric_val = extract_metric_value(overview, rule.metric_type)
            is_triggered = compare_operator(metric_val, rule.operator, rule.threshold_value)

            if is_triggered:
                sent_ok = cls.dispatch_webhook(rule, metric_val, now)

                history = AlertHistory(
                    rule_id=rule.id if rule.id is not None else 0,
                    rule_name=rule.name,
                    triggered_value=round(metric_val, 4),
                    message=(
                        f"Rule '{rule.name}' triggered: {rule.metric_type} = {round(metric_val, 4)} "
                        f"(threshold: {rule.operator} {rule.threshold_value}) over past {rule.window_minutes}m"
                    ),
                    sent_successfully=sent_ok,
                    created_at=now,
                )
                session.add(history)
                triggered_histories.append(history)

        if triggered_histories:
            session.commit()
            for history in triggered_histories:
                session.refresh(history)

        return triggered_histories
