"""Durable pending delivery and pause races (ALR-04 / ALR-11)."""
import pytest
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine, select, update

from backend.app.models.alert import AlertHistory, AlertRule
from backend.app.models.trace import TraceEvent
from backend.app.services.aggregator import MetricAggregator
from backend.app.services.alert import AlertEvaluator

RULE = {"name": "empty traffic", "metric_type": "total_requests", "operator": "<", "threshold_value": 1}


@pytest.fixture
def file_session(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'alerts.db').as_posix()}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def pragmas(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")

    SQLModel.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        session.add(AlertRule(**RULE))
        session.commit()
        yield session, engine
    engine.dispose()


def test_pending_history_failure_rolls_back_cooldown_and_never_sends(file_session, monkeypatch):
    session, engine = file_session
    dispatched = []
    monkeypatch.setattr(AlertEvaluator, "dispatch_webhook", lambda *args: dispatched.append(True) or True)
    commit = session.commit

    def fail_pending():
        if any(isinstance(row, AlertHistory) for row in session.new):
            raise OperationalError("INSERT alert_histories", {}, RuntimeError("fake disk failure"))
        return commit()

    monkeypatch.setattr(session, "commit", fail_pending)
    with pytest.raises(OperationalError):
        AlertEvaluator.evaluate_rules(session)
    session.rollback()
    with Session(engine) as reader:
        assert reader.exec(select(AlertRule)).one().last_triggered_at is None
        assert reader.exec(select(AlertHistory)).all() == []
    assert dispatched == []


@pytest.mark.parametrize("ignore_cooldown", [False, True])
def test_pending_history_is_durable_before_dispatch(file_session, monkeypatch, ignore_cooldown):
    session, engine = file_session
    checked = []

    def dispatch(*args):
        with Session(engine) as reader:
            history = reader.exec(select(AlertHistory)).one()
            assert history.sent_successfully is False
            assert history.message.startswith("[delivery pending] ")
            stamped = reader.exec(select(AlertRule)).one().last_triggered_at
            assert (stamped is None) is ignore_cooldown
        checked.append(True)
        return True

    monkeypatch.setattr(AlertEvaluator, "dispatch_webhook", dispatch)
    results = AlertEvaluator.evaluate_rules(session, ignore_cooldown=ignore_cooldown)
    assert checked == [True] and results[0].sent_successfully is True
    assert not results[0].message.startswith("[delivery pending] ")


@pytest.mark.parametrize("ignore_cooldown", [False, True])
def test_result_commit_failure_preserves_pending_history(file_session, monkeypatch, ignore_cooldown):
    session, engine = file_session
    attempts = []
    monkeypatch.setattr(AlertEvaluator, "dispatch_webhook", lambda *args: attempts.append(True) or True)
    commit = session.commit
    commits = []

    def fail_result():
        commits.append(True)
        if attempts:  # only the commit that records the webhook result fails
            raise OperationalError("UPDATE alert_histories", {}, RuntimeError("fake disk failure"))
        return commit()

    monkeypatch.setattr(session, "commit", fail_result)
    with pytest.raises(OperationalError):
        AlertEvaluator.evaluate_rules(session, ignore_cooldown=ignore_cooldown)
    session.rollback()
    with Session(engine) as reader:
        history = reader.exec(select(AlertHistory)).one()
        assert history.sent_successfully is False and history.message.startswith("[delivery pending] ")
        stamped = reader.exec(select(AlertRule)).one().last_triggered_at
        assert (stamped is None) is ignore_cooldown
    assert attempts == [True]


def test_pause_committed_before_claim_prevents_firing(file_session, monkeypatch):
    session, engine = file_session
    original = MetricAggregator.get_overview_metrics

    def pause_after_aggregation(*args, **kwargs):
        result = original(*args, **kwargs)
        with Session(engine) as writer:
            writer.exec(update(AlertRule).values(is_active=False))
            writer.commit()
        return result

    monkeypatch.setattr(MetricAggregator, "get_overview_metrics", pause_after_aggregation)
    assert AlertEvaluator.evaluate_rules(session) == []
    with Session(engine) as reader:
        assert reader.exec(select(AlertHistory)).all() == []
        assert reader.exec(select(AlertRule)).one().last_triggered_at is None


@pytest.mark.parametrize("ignore_cooldown", [False, True])
def test_collector_commit_after_aggregation_does_not_block_firing(file_session, monkeypatch, ignore_cooldown):
    session, engine = file_session
    original = MetricAggregator.get_overview_metrics

    def ingest_after_aggregation(*args, **kwargs):
        result = original(*args, **kwargs)
        with Session(engine) as writer:
            writer.add(TraceEvent(trace_id="collector", provider="openai", model_name="gpt-4o"))
            writer.commit()
        return result

    monkeypatch.setattr(MetricAggregator, "get_overview_metrics", ingest_after_aggregation)
    monkeypatch.setattr(AlertEvaluator, "dispatch_webhook", lambda *args: True)
    results = AlertEvaluator.evaluate_rules(session, ignore_cooldown=ignore_cooldown)
    assert len(results) == 1 and results[0].sent_successfully is True
    with Session(engine) as reader:
        stamped = reader.exec(select(AlertRule)).one().last_triggered_at
        assert (stamped is None) is ignore_cooldown


@pytest.mark.parametrize("message,sent,status", [("[delivery pending] test", False, "pending"), ("test", True, "succeeded"), ("test", False, "failed")])
def test_history_api_exposes_delivery_status_without_schema_changes(client, session, message, sent, status):
    rule = AlertRule(**RULE)
    session.add(rule)
    session.commit()
    session.refresh(rule)
    session.add(AlertHistory(rule_id=rule.id, rule_name=rule.name, triggered_value=0, message=message, sent_successfully=sent))
    session.commit()
    assert client.get("/api/v1/alerts/history").json()[0]["delivery_status"] == status
