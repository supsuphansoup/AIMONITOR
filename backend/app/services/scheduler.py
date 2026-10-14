"""Background jobs.

Alert rules used to fire only when someone pressed a button in the dashboard,
which meant nothing was actually monitored while nobody was looking. This runs
the evaluation loop and the retention purge for the lifetime of the process.
"""
import asyncio
import logging
import threading
from functools import partial
from typing import Any, Dict, List, Optional, Set

from sqlmodel import Session

from backend.app.core.config import settings
from backend.app.db.session import engine
from backend.app.services.alert import AlertEvaluator
from backend.app.services.retention import purge_older_than

logger = logging.getLogger(__name__)


def _run_alert_pass(stop_event: Optional[threading.Event] = None) -> int:
    with Session(engine, expire_on_commit=False) as session:
        return len(AlertEvaluator.evaluate_rules(
            session=session, should_stop=stop_event.is_set if stop_event is not None else None,
        ))


def _run_retention_pass() -> Dict[str, int]:
    with Session(engine, expire_on_commit=False) as session:
        return purge_older_than(session, settings.RETENTION_DAYS)


class BackgroundScheduler:
    """Owns the periodic tasks and their lifecycle."""

    def __init__(self) -> None:
        self._tasks: List[asyncio.Task[Any]] = []
        self._running_jobs: Set[asyncio.Task[Any]] = set()
        self._stop_event = threading.Event()
        self.started: bool = False
        self.last_alert_error: Optional[str] = None

    async def _loop(self, name: str, interval_seconds: int, job: Any) -> None:
        # Stagger the first run so startup is not competing with initial traffic.
        await asyncio.sleep(min(interval_seconds, 10))
        while not self._stop_event.is_set():
            running = asyncio.create_task(asyncio.to_thread(job), name=f"{name}:running")
            self._running_jobs.add(running)
            running.add_done_callback(self._job_done)
            try:
                # Cancelling the loop must not discard our handle to its real
                # thread. stop() drains these handles before the engine closes.
                await asyncio.shield(running)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A failing pass must not kill the loop, or monitoring stops
                # silently at the first transient database error.
                self.last_alert_error = f"{name}: {exc.__class__.__name__}"
                logger.exception("Scheduled job '%s' failed", name)
            await asyncio.sleep(interval_seconds)

    def _job_done(self, task: asyncio.Task[Any]) -> None:
        self._running_jobs.discard(task)
        if not task.cancelled():
            task.exception()  # Retrieve errors even if the loop was cancelled.

    def start(self) -> None:
        if self.started or not settings.SCHEDULER_ENABLED:
            if not settings.SCHEDULER_ENABLED:
                logger.info("Background scheduler disabled by configuration.")
            return

        self._stop_event.clear()
        loop_specs = [
            ("alert-evaluation", max(10, settings.ALERT_INTERVAL_SECONDS),
             partial(_run_alert_pass, self._stop_event)),
        ]
        if settings.RETENTION_DAYS > 0:
            loop_specs.append(
                ("retention-purge", max(60, settings.RETENTION_INTERVAL_SECONDS), _run_retention_pass)
            )

        for name, interval, job in loop_specs:
            self._tasks.append(asyncio.create_task(self._loop(name, interval, job), name=name))
            logger.info("Started background job '%s' (every %ds)", name, interval)
        self.started = True

    async def stop(self) -> None:
        self._stop_event.set()
        running = list(self._running_jobs)
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        self._tasks.clear()
        self.started = False

    def status(self) -> Dict[str, Any]:
        return {
            "enabled": settings.SCHEDULER_ENABLED,
            "running": self.started,
            "jobs": [t.get_name() for t in self._tasks if not t.done()],
            "alert_interval_seconds": settings.ALERT_INTERVAL_SECONDS,
            "retention_days": settings.RETENTION_DAYS,
            "last_error": self.last_alert_error,
        }


scheduler = BackgroundScheduler()
