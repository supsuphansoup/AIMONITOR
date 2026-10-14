"""Alert rule evaluation and webhook dispatch."""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

import httpx
from sqlmodel import Session, col, select, update

from backend.app.core.config import settings
from backend.app.core.metrics_catalog import (
    LOWER_BOUND_OPERATORS,
    METRIC_FIELD_MAP,
    UNDEFINED_WITHOUT_TRAFFIC,
    resolve_metric_key,
    unit_for,
)
from backend.app.core.timeutil import to_aware_utc, utc_now
from backend.app.models.alert import DELIVERY_PENDING_PREFIX, AlertHistory, AlertRule, _validate_webhook_url
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
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return parsed.hostname in ("discord.com", "discordapp.com") and bool(
        re.match(r"^/api/(?:v[0-9]+/)?webhooks(?:/|$)", parsed.path)
    )


async def _send_webhook(url: str, payload: Dict[str, Any]) -> bool:
    # Recheck DNS immediately before sending, including rules saved under older settings.
    validated_url = await asyncio.get_running_loop().run_in_executor(None, _validate_webhook_url, url)
    if validated_url is None:
        return False
    async with httpx.AsyncClient(
        timeout=WEBHOOK_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False,
    ) as client:
        # Only the status is needed. Never download or buffer the response body.
        async with client.stream("POST", validated_url, json=payload) as response:
            return 200 <= response.status_code < 300


class AlertEvaluator:
    """Evaluation and alerting engine for anomaly detection rules."""

    @staticmethod
    def build_message(rule: AlertRule, metric_value: float, note: Optional[str] = None) -> str:
        unit = unit_for(rule.metric_type)
        suffix = f" {unit}" if unit and unit != "count" else ""
        message = (
            f"[AIMONITOR] Rule '{rule.name}' triggered: {rule.metric_type} = "
            f"{round(metric_value, 4)}{suffix} (threshold: {rule.operator} "
            f"{rule.threshold_value}{suffix}) over the past {rule.window_minutes}m"
        )
        return f"{message} - {note}" if note else message

    @staticmethod
    def dispatch_webhook(
        rule: AlertRule,
        metric_value: float,
        triggered_at: datetime,
        message: Optional[str] = None,
    ) -> bool:
        """POST the alert to the configured webhook.

        Returns True on success (or when no webhook is configured). Network and
        HTTP failures are logged and swallowed: a broken webhook must never stop
        the alert from being recorded.
        """
        if not rule.webhook_url or not rule.webhook_url.strip():
            return True

        text = message or AlertEvaluator.build_message(rule, metric_value)
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

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(
                asyncio.wait_for(_send_webhook(rule.webhook_url, payload), WEBHOOK_TIMEOUT_SECONDS)
            )
        except Exception as exc:
            logger.warning(
                "Webhook for alert '%s' failed: %s", rule.name, exc.__class__.__name__
            )
            return False
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens())
            # OS DNS lookups cannot be interrupted, but cancelled lookups cannot
            # resume the coroutine or initiate an HTTP request. Do not wait for
            # the resolver executor as asyncio.run() would at shutdown.
            loop.close()

    @staticmethod
    def is_in_cooldown(rule: AlertRule, now: datetime) -> bool:
        """True when this rule fired recently enough to stay quiet.

        Without this, every evaluation pass re-fires an ongoing incident, so a
        one-minute scheduler turns a single outage into sixty webhook messages.
        """
        if rule.cooldown_minutes <= 0 or rule.last_triggered_at is None:
            return False
        return now - rule.last_triggered_at < timedelta(minutes=rule.cooldown_minutes)

    @staticmethod
    def claim_rule(session: Session, rule: AlertRule, now: datetime) -> bool:
        """Atomically reserve the right to fire this rule, honouring its cooldown.

        A compare-and-set on `last_triggered_at`: the UPDATE only matches while
        the cooldown is still expired, so exactly one caller can win. Checking
        the cooldown in Python and writing later leaves a window as wide as the
        webhook call, during which every worker in a multi-process deployment
        reads "not in cooldown" and sends its own copy of the same alert.

        The caller must commit the winning UPDATE with its pending history;
        this method intentionally never commits on its own.
        """
        if rule.id is None:
            return False

        condition = (col(AlertRule.id) == rule.id) & col(AlertRule.is_active).is_(True)
        if rule.cooldown_minutes > 0:
            cutoff = now - timedelta(minutes=rule.cooldown_minutes)
            condition = condition & (
                col(AlertRule.last_triggered_at).is_(None)
                | (col(AlertRule.last_triggered_at) <= cutoff)
            )

        result = session.exec(
            update(AlertRule).where(condition).values(last_triggered_at=now)
        )
        if result.rowcount == 1:
            rule.last_triggered_at = now
            return True
        return False

    @classmethod
    def evaluate_rules(
        cls,
        session: Session,
        rule_ids: Optional[List[int]] = None,
        ignore_cooldown: bool = False,
        skipped_rules: Optional[List[Dict[str, Any]]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> List[AlertHistory]:
        """Evaluate active rules against recent metrics; log and dispatch hits.

        `ignore_cooldown` forces every active rule to be evaluated even if it
        fired recently. It still records history and still dispatches webhooks -
        it is a real evaluation, not a dry run - but it neither consumes nor
        resets any cooldown, so a manual check leaves the schedule untouched.

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
        # Rules sharing a window share an aggregation; a dozen rules on the same
        # five-minute window used to mean a dozen identical scans.
        overview_cache: Dict[int, Dict[str, Any]] = {}

        for rule in rules:
            if should_stop is not None and should_stop():
                break
            if not ignore_cooldown and cls.is_in_cooldown(rule, now):
                continue

            window_minutes = max(1, rule.window_minutes)
            if window_minutes not in overview_cache:
                overview_cache[window_minutes] = MetricAggregator.get_overview_metrics(
                    session=session,
                    start_time=now - timedelta(minutes=window_minutes),
                    end_time=now,
                )
            overview = overview_cache[window_minutes]

            metric_key = resolve_metric_key(rule.metric_type)
            if overview.get("latency_sampled") and metric_key in {
                "p50_latency_ms", "p95_latency_ms", "p99_latency_ms",
            }:
                logger.info("Alert rule_id=%s skipped reason=latency_sampled", rule.id)
                if skipped_rules is not None:
                    skipped_rules.append({
                        "rule_id": rule.id,
                        "reason": "latency_sampled",
                        "sample_count": min(int(overview.get("latency_measured_count") or 0),
                                            settings.MAX_AGGREGATION_ROWS),
                        "sample_limit": settings.MAX_AGGREGATION_ROWS,
                    })
                continue
            if not overview.get("total_requests") and metric_key in UNDEFINED_WITHOUT_TRAFFIC:
                # An empty window has no P95 or error rate; reading it as 0 used
                # to fire every "p95 < 100ms" style rule whenever traffic paused.
                continue

            note: Optional[str] = None
            unpriced = int(overview.get("cost_unknown_count") or 0)
            if metric_key == "total_cost_usd" and unpriced:
                # Known cost is only a floor while some calls are unpriced.
                if rule.operator.strip() not in LOWER_BOUND_OPERATORS:
                    continue
                note = (
                    f"excludes {unpriced} call(s) with unknown pricing; "
                    "the actual cost may be higher"
                )

            if overview.get(metric_key) is None:
                continue

            metric_val = extract_metric_value(overview, rule.metric_type)
            if not compare_operator(metric_val, rule.operator, rule.threshold_value):
                continue
            if should_stop is not None and should_stop():
                break

            message = cls.build_message(rule, metric_val, note)
            try:
                if not ignore_cooldown and not cls.claim_rule(session, rule, now):
                    # A concurrent claim or pause won before this UPDATE.
                    session.rollback()
                    continue
                history = AlertHistory(
                    rule_id=rule.id if rule.id is not None else 0,
                    rule_name=rule.name,
                    triggered_value=round(metric_val, 4),
                    message=DELIVERY_PENDING_PREFIX + message,
                    sent_successfully=False,
                    created_at=now,
                )
                session.add(history)
                # The claim and pending record are one transaction. Never send
                # until both are durable, even for ignore_cooldown evaluations.
                session.commit()
                session.refresh(history)
            except Exception:
                session.rollback()
                raise
            triggered_histories.append(history)

            if should_stop is not None and should_stop():
                break
            sent_ok = cls.dispatch_webhook(rule, metric_val, now, message)
            try:
                history.message = message
                history.sent_successfully = sent_ok
                session.add(history)
                session.commit()
                session.refresh(history)
            except Exception:
                # A pending record survives even if the result cannot be saved.
                session.rollback()
                raise

        return triggered_histories
