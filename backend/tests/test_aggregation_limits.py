"""Behaviour when the aggregation row cap truncates the sample.

The cap itself is fine; taking the wrong end of the data is not. A monitoring
dashboard that drops the newest events is worse than one that refuses to answer.
"""
from datetime import timedelta

import pytest
from sqlmodel import Session

from backend.app.core.config import settings
from backend.app.core.timeutil import utc_now
from backend.app.models.trace import TraceEvent
from backend.app.services.aggregator import MetricAggregator


@pytest.fixture(name="skewed_history")
def skewed_history_fixture(session: Session):
    """100 slow calls 20 hours ago, then 100 fast calls a minute ago."""
    now = utc_now()
    for i in range(100):
        session.add(TraceEvent(
            trace_id=f"old-{i}", provider="p", model_name="m",
            latency_ms=5000.0, timestamp=now - timedelta(hours=20),
        ))
    for i in range(100):
        session.add(TraceEvent(
            trace_id=f"new-{i}", provider="p", model_name="m",
            latency_ms=10.0, timestamp=now - timedelta(minutes=1),
        ))
    session.commit()
    return session


@pytest.fixture(name="tight_cap")
def tight_cap_fixture(monkeypatch):
    monkeypatch.setattr(settings, "MAX_AGGREGATION_ROWS", 100)


def test_percentiles_sample_the_most_recent_rows(skewed_history, tight_cap):
    """A capped percentile must describe now, not whenever the table started."""
    overview = MetricAggregator.get_overview_metrics(session=skewed_history)

    assert overview["total_requests"] == 200      # counts stay exact, via SQL
    assert overview["latency_sampled"] is True
    assert overview["p95_latency_ms"] == 10.0     # recent, not the 5000ms tail


def test_uncapped_percentiles_use_everything(skewed_history):
    overview = MetricAggregator.get_overview_metrics(session=skewed_history)
    assert overview["latency_sampled"] is False
    assert overview["p95_latency_ms"] == 5000.0


def test_timeseries_keeps_the_newest_bucket(skewed_history, monkeypatch):
    """Truncation must cut the left edge of the chart, never the right.

    A cap of 150 over 200 rows keeps all 100 recent events plus half the old
    ones, so the newest bucket is whole and the oldest one is not.
    """
    monkeypatch.setattr(settings, "MAX_AGGREGATION_ROWS", 150)
    points = MetricAggregator.get_timeseries_metrics(
        session=skewed_history, hours_back=24, interval_minutes=60
    )

    assert len(points) == 2
    assert points[-1]["avg_latency_ms"] == 10.0   # the recent bucket survived intact
    assert points[-1]["request_count"] == 100
    assert points[-1]["partial"] is False
    # The oldest returned bucket is a floor, not a total, and says so.
    assert points[0]["partial"] is True
    assert points[0]["request_count"] == 50


def test_single_truncated_bucket_is_flagged(skewed_history, tight_cap):
    """With one bucket returned there is no way to know it was not cut short."""
    points = MetricAggregator.get_timeseries_metrics(
        session=skewed_history, hours_back=24, interval_minutes=60
    )
    assert len(points) == 1
    assert points[0]["partial"] is True


def test_complete_timeseries_flags_nothing_partial(skewed_history):
    points = MetricAggregator.get_timeseries_metrics(
        session=skewed_history, hours_back=24, interval_minutes=60
    )
    assert all(p["partial"] is False for p in points)
    assert sum(p["request_count"] for p in points) == 200
