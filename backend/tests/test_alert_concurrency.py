"""Alert cooldown under concurrency, and manual evaluation side effects.

Both regressions here produce silent misbehaviour rather than errors: duplicate
pages during an incident, and a cooldown that quietly disappears.
"""
import os
import tempfile
import threading
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from backend.app.models.alert import AlertHistory, AlertRule
from backend.app.models.trace import TraceEvent
from backend.app.services.alert import AlertEvaluator

ERROR_TRACE = {"provider": "openai", "model_name": "gpt-4o", "status": "ERROR"}
RULE = {
    "name": "any error",
    "metric_type": "error_rate",
    "operator": ">",
    "threshold_value": 0.0,
    "window_minutes": 60,
    "cooldown_minutes": 60,
}


@pytest.fixture(name="file_engine")
def file_engine_fixture():
    """A real file-backed database - concurrency cannot be tested in :memory:."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False, "timeout": 30.0}
    )

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=15000;")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()
    try:
        os.remove(path)
    except OSError:
        pass


def test_concurrent_evaluators_fire_once(file_engine):
    """Four workers evaluating at once must produce one alert, not four.

    Reproduces `uvicorn --workers N`: every process runs the scheduler, and a
    slow webhook widens the gap between reading the cooldown and writing it.
    """
    with Session(file_engine) as session:
        session.add(TraceEvent(trace_id="t1", provider="openai", model_name="gpt-4o", status="ERROR"))
        session.add(AlertRule(webhook_url="https://hooks.slack.com/services/mock", **RULE))
        session.commit()

    def slow_webhook(*_args, **_kwargs):
        time.sleep(0.3)
        return True

    barrier = threading.Barrier(4)
    errors = []

    def worker():
        try:
            barrier.wait(timeout=5)
            with Session(file_engine, expire_on_commit=False) as session:
                AlertEvaluator.evaluate_rules(session=session)
        except Exception as exc:
            errors.append(exc)

    with patch.object(AlertEvaluator, "dispatch_webhook", side_effect=slow_webhook) as dispatch:
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
            assert not t.is_alive()
        assert not errors
        assert dispatch.call_count == 1

    with Session(file_engine) as session:
        assert len(session.exec(select(AlertHistory)).all()) == 1


def test_claim_rule_is_single_winner(file_engine):
    from backend.app.core.timeutil import utc_now

    with Session(file_engine) as session:
        rule = AlertRule(**RULE)
        session.add(rule)
        session.commit()
        session.refresh(rule)
        now = utc_now()

        assert AlertEvaluator.claim_rule(session, rule, now) is True
        # Second claim inside the cooldown window loses.
        assert AlertEvaluator.claim_rule(session, rule, now) is False


def test_claim_rule_always_wins_without_cooldown(file_engine):
    from backend.app.core.timeutil import utc_now

    with Session(file_engine) as session:
        rule = AlertRule(**{**RULE, "cooldown_minutes": 0})
        session.add(rule)
        session.commit()
        session.refresh(rule)
        now = utc_now()

        assert AlertEvaluator.claim_rule(session, rule, now) is True
        assert AlertEvaluator.claim_rule(session, rule, now) is True


def test_manual_check_does_not_consume_cooldown(client: TestClient, session: Session):
    """`ignore_cooldown` reports what would fire without disturbing the schedule."""
    client.post("/api/v1/traces", json=ERROR_TRACE)
    client.post("/api/v1/alerts/rules", json=RULE)

    assert len(client.post("/api/v1/alerts/evaluate").json()) == 1
    session.expire_all()
    stamped = session.exec(select(AlertRule)).one().last_triggered_at
    assert stamped is not None

    assert len(client.post("/api/v1/alerts/evaluate?ignore_cooldown=true").json()) == 1
    session.expire_all()
    assert session.exec(select(AlertRule)).one().last_triggered_at == stamped

    # The scheduled pass stays suppressed for the rest of the cooldown.
    assert client.post("/api/v1/alerts/evaluate").json() == []


def test_manual_check_does_not_erase_cooldown_of_quiet_rules(
    client: TestClient, session: Session
):
    """A rule whose condition has cleared must keep its cooldown history.

    The previous implementation reset `last_triggered_at` on every active rule
    before evaluating, so any rule that did not re-fire lost its cooldown for
    good and would page again immediately on the next incident.
    """
    client.post("/api/v1/traces", json=ERROR_TRACE)
    rule_id = client.post("/api/v1/alerts/rules", json=RULE).json()["id"]
    client.post("/api/v1/alerts/evaluate")

    session.expire_all()
    stamped = session.exec(select(AlertRule)).one().last_triggered_at
    assert stamped is not None

    # Condition no longer met.
    client.patch(f"/api/v1/alerts/rules/{rule_id}", json={"threshold_value": 9999.0})
    assert client.post("/api/v1/alerts/evaluate?ignore_cooldown=true").json() == []

    session.expire_all()
    assert session.exec(select(AlertRule)).one().last_triggered_at == stamped


def test_rules_sharing_a_window_aggregate_once(client: TestClient, monkeypatch):
    """Ten rules on one window should scan once, not ten times."""
    import backend.app.services.alert as alert_service

    client.post("/api/v1/traces", json=ERROR_TRACE)
    for i in range(10):
        client.post("/api/v1/alerts/rules", json={**RULE, "name": f"rule-{i}", "window_minutes": 30})
    for i in range(3):
        client.post("/api/v1/alerts/rules", json={**RULE, "name": f"wide-{i}", "window_minutes": 120})

    calls = []
    original = alert_service.MetricAggregator.get_overview_metrics

    def counting(*args, **kwargs):
        calls.append(kwargs.get("start_time"))
        return original(*args, **kwargs)

    monkeypatch.setattr(alert_service.MetricAggregator, "get_overview_metrics", counting)
    client.post("/api/v1/alerts/evaluate")

    # Two distinct windows across thirteen rules.
    assert len(calls) == 2
