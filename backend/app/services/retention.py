"""Data retention.

An observability store that only ever grows eventually fills the disk it runs
on. Retention is opt-in (`RETENTION_DAYS=0` keeps everything) so no existing
deployment silently loses history on upgrade.
"""
import logging
from datetime import timedelta
from typing import Dict

from sqlalchemy import delete
from sqlmodel import Session

from backend.app.core.timeutil import utc_now
from backend.app.models.alert import AlertHistory
from backend.app.models.trace import TraceEvent

logger = logging.getLogger(__name__)


def purge_older_than(session: Session, days: int) -> Dict[str, int]:
    """Delete traces and alert history older than `days`.

    Returns the per-table delete counts. A non-positive `days` is a no-op.
    """
    if days <= 0:
        return {"trace_events": 0, "alert_histories": 0}

    cutoff = utc_now() - timedelta(days=days)

    trace_result = session.exec(
        delete(TraceEvent).where(TraceEvent.timestamp < cutoff)
    )
    history_result = session.exec(
        delete(AlertHistory).where(AlertHistory.created_at < cutoff)
    )
    session.commit()

    counts = {
        "trace_events": int(trace_result.rowcount or 0),
        "alert_histories": int(history_result.rowcount or 0),
    }
    if counts["trace_events"] or counts["alert_histories"]:
        logger.info(
            "Retention purge removed %d traces and %d alert history rows older than %d days",
            counts["trace_events"],
            counts["alert_histories"],
            days,
        )
    return counts
