"""Alert rule evaluation and webhook dispatch."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from sqlmodel import Session, col, select

from backend.app.core.metrics_catalog import METRIC_FIELD_MAP, resolve_metric_key, unit_for
from backend.app.core.timeutil import to_aware_utc, utc_now
from backend.app.models.alert import AlertHistory, AlertRule
from backend.app.services.aggregator import MetricAggregator

logger = logging.getLogger(__name__)

__all__ = [
    "METRIC_FIELD_MAP",
    "extract_metric_value",
    "compare_operator",
    "AlertEvaluator",
]

WEBHOOK_TIMEOUT_SECONDS = 5.0


def extract_metric_value(overview: Dict[str, Any], metric_type: str) -> float:
    """Extract and normalize a metric value from the overview metrics dict."""
    val = overview.get(resolve_metric_key(metric_type), 0.0)
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def compare_operator(actual_value: float, operator: str, threshold: float) -> bool:
    """Evaluate a comparison operator."""
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


def _is_discord(url: str) -> bool:
    return "discord.com/api/webhooks" in url or "discordapp.com/api/webhooks" in url


class AlertEvaluator:
    """Evaluation and alerting engine for anomaly detection rules."""

    @staticmethod
    def build_message(rule: AlertRule, metric_value: float) -> str:
        unit = unit_for(rule.metric_type)
        suffix = f" {unit}" if unit and unit != "count" else ""
        return (
            f"[AIMONITOR] Rule '{rule.name}' triggered: {rule.metric_type} = "
            f"{round(metric_value, 4)}{suffix} (threshold: {rule.operator} "
            f"{rule.threshold_value}{suffix}) over the past {rule.window_minutes}m"
        )

    @staticmethod
    def dispatch_webhook(rule: AlertRule, metric_value: float, triggered_at: datetime) -> bool:
        """POST the alert to the configured webhook.

        Returns True on success (or when no webhook is configured). Network and
        HTTP failures are logged and swallowed: a broken webhook must never stop
        the alert from being recorded.
        """
        if not rule.webhook_url or not rule.webhook_url.strip():
            return True

        text = AlertEvaluator.build_message(rule, metric_value)
        aware_ts = to_aware_utc(triggered_at) or datetime.now(timezone.utc)

        if _is_discord(rule.webhook_url):
            # Discord validates its payload shape and ignores Slack-style keys.
            payload: Dict[str, Any] = {"content": text, "username": "AIMONITOR"}
        else:
            payload = {
                "text": text,
                "rule_id": rule.id,
                "rule_name": rule.name,
                "metric_type": rule.metric_type,
                "operator": rule.operator,
                "threshold_value": rule.threshold_value,
                "triggered_value": metric_value,
                "window_minutes": rule.window_minutes,
                "triggered_at": aware_ts.isoformat(),
            }

        try:
            with httpx.Client(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
                response = client.post(rule.webhook_url, json=payload)
                if response.status_code >= 400:
                    logger.warning(
                        "Webhook for alert '%s' returned status %d",
                        rule.name,
                        response.status_code,
                    )
                    return False
                return True
        except Exception as exc:
            logger.warning(
                "Webhook for alert '%s' failed: %s", rule.name, exc.__class__.__name__
            )
            return False

    @staticmethod
    def is_in_cooldown(rule: AlertRule, now: datetime) -> bool:
        """True when this rule fired recently enough to stay quiet.

        Without this, every evaluation pass re-fires an ongoing incident, so a
        one-minute scheduler turns a single outage into sixty webhook messages.
        """
        if rule.cooldown_minutes <= 0 or rule.last_triggered_at is None:
            return False
        return now - rule.last_triggered_at < timedelta(minutes=rule.cooldown_minutes)

    @classmethod
    def evaluate_rules(
        cls, session: Session, rule_ids: Optional[List[int]] = None
    ) -> List[AlertHistory]:
        """Evaluate active rules against recent metrics; log and dispatch hits.

        Returns the AlertHistory records created by this pass.
        """
        query = select(AlertRule).where(col(AlertRule.is_active).is_(True))
        if rule_ids:
            query = query.where(col(AlertRule.id).in_(rule_ids))
        rules: List[AlertRule] = list(session.exec(query).all())
        if not rules:
            return []

        triggered_histories: List[AlertHistory] = []
        now = utc_now()

        for rule in rules:
            if cls.is_in_cooldown(rule, now):
                continue

            window_minutes = max(1, rule.window_minutes)
            overview = MetricAggregator.get_overview_metrics(
                session=session,
                start_time=now - timedelta(minutes=window_minutes),
                end_time=now,
            )

            metric_val = extract_metric_value(overview, rule.metric_type)
            if not compare_operator(metric_val, rule.operator, rule.threshold_value):
                continue

            sent_ok = cls.dispatch_webhook(rule, metric_val, now)
            history = AlertHistory(
                rule_id=rule.id if rule.id is not None else 0,
                rule_name=rule.name,
                triggered_value=round(metric_val, 4),
                message=cls.build_message(rule, metric_val),
                sent_successfully=sent_ok,
                created_at=now,
            )
            rule.last_triggered_at = now
            session.add(rule)
            session.add(history)
            triggered_histories.append(history)

        if triggered_histories:
            session.commit()
            for history in triggered_histories:
                session.refresh(history)

        return triggered_histories
