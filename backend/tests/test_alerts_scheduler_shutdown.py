"""Shutdown must drain real worker threads, not only their awaiters (ALR-10)."""
import asyncio
import threading
from functools import partial

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from backend.app.core.config import settings
from backend.app.models.alert import AlertHistory, AlertRule
from backend.app.services import scheduler as scheduler_module
from backend.app.services.alert import AlertEvaluator
from backend.app.services.scheduler import BackgroundScheduler


@pytest.mark.asyncio
async def test_stop_waits_for_running_thread_before_reporting_stopped():
    scheduler = BackgroundScheduler()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def job():
        started.set()
        release.wait(3)
        finished.set()

    scheduler._tasks.append(asyncio.create_task(scheduler._loop("test", 0, job)))
    scheduler.started = True
    stopping = None
    try:
        assert await asyncio.to_thread(started.wait, 1)
        stopping = asyncio.create_task(scheduler.stop())
        done, pending = await asyncio.wait([stopping], timeout=0.05)
        assert done == set() and pending == {stopping}
        assert scheduler.started and not finished.is_set()
    finally:
        release.set()
        if stopping is not None:
            await asyncio.wait_for(stopping, 2)
        await scheduler.stop()
    assert finished.is_set() and not scheduler.started and scheduler._tasks == []


@pytest.mark.asyncio
async def test_stop_drains_current_delivery_and_prevents_next_rule(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'scheduler.db').as_posix()}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        for index in range(2):
            session.add(AlertRule(name=f"rule {index}", metric_type="total_requests", operator="<", threshold_value=1))
        session.commit()
    monkeypatch.setattr(scheduler_module, "engine", engine)
    started = threading.Event()
    release = threading.Event()
    calls = []

    def delivery(*args):
        calls.append(True)
        started.set()
        assert release.wait(3)
        return True

    monkeypatch.setattr(AlertEvaluator, "dispatch_webhook", delivery)
    scheduler = BackgroundScheduler()
    job = partial(scheduler_module._run_alert_pass, scheduler._stop_event)
    scheduler._tasks.append(asyncio.create_task(scheduler._loop("alerts", 0, job)))
    scheduler.started = True
    stopping = None
    try:
        assert await asyncio.to_thread(started.wait, 1)
        stopping = asyncio.create_task(scheduler.stop())
        done, pending = await asyncio.wait([stopping], timeout=0.05)
        assert done == set() and pending == {stopping}
        release.set()
        await asyncio.wait_for(stopping, 2)
        assert calls == [True] and scheduler._running_jobs == set()
        with Session(engine) as session:
            histories = session.exec(select(AlertHistory)).all()
            assert len(histories) == 1 and histories[0].sent_successfully is True
            rules = session.exec(select(AlertRule).order_by(AlertRule.id)).all()
            assert rules[0].last_triggered_at is not None and rules[1].last_triggered_at is None
    finally:
        release.set()
        if stopping is not None:
            await asyncio.wait_for(stopping, 2)
        await scheduler.stop()
        engine.dispose()


def test_stop_after_pending_commit_never_starts_delivery(session, monkeypatch):
    session.add(AlertRule(name="stop", metric_type="total_requests", operator="<", threshold_value=1))
    session.commit()
    stop = threading.Event()
    commit = session.commit
    calls = []

    def commit_then_stop():
        commit()
        stop.set()

    monkeypatch.setattr(session, "commit", commit_then_stop)
    monkeypatch.setattr(AlertEvaluator, "dispatch_webhook", lambda *args: calls.append(True) or True)
    results = AlertEvaluator.evaluate_rules(session, should_stop=stop.is_set)
    assert len(results) == 1 and results[0].message.startswith("[delivery pending] ")
    assert results[0].sent_successfully is False and calls == []


@pytest.mark.asyncio
async def test_job_exception_keeps_loop_alive_and_stop_is_idempotent():
    scheduler = BackgroundScheduler()
    called = []
    recovered = threading.Event()

    def job():
        called.append(True)
        if len(called) == 1:
            raise RuntimeError("test only")
        recovered.set()

    scheduler._tasks.append(asyncio.create_task(scheduler._loop("fake", 0, job)))
    scheduler.started = True
    try:
        assert await asyncio.to_thread(recovered.wait, 1)
    finally:
        await scheduler.stop()
    await scheduler.stop()
    assert len(called) >= 2 and scheduler.last_alert_error == "fake: RuntimeError"
    assert scheduler._tasks == [] and scheduler._running_jobs == set()


@pytest.mark.asyncio
async def test_start_stop_and_restart_do_not_start_jobs_during_initial_delay(monkeypatch):
    monkeypatch.setattr(settings, "SCHEDULER_ENABLED", True)
    monkeypatch.setattr(settings, "RETENTION_DAYS", 0)
    calls = []
    monkeypatch.setattr(scheduler_module, "_run_alert_pass", lambda *args: calls.append(True))
    scheduler = BackgroundScheduler()
    for _ in range(2):
        scheduler.start()
        scheduler.start()
        assert len(scheduler._tasks) == 1 and scheduler.started
        await scheduler.stop()
        assert not scheduler.started
    assert calls == []
