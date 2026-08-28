"""Metric aggregation.

Counts and sums are computed by the database; only the latency column is pulled
into Python, and even that is capped. The previous implementation materialized
every matching row as an ORM object to add up four integers, which is fine for a
demo and fatal for a table that has been collecting traffic for a month.
"""
import math
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import Select, case, func
from sqlmodel import Session, col, select

from backend.app.core.config import settings
from backend.app.core.timeutil import to_aware_utc, utc_now
from backend.app.models.trace import TraceEvent
from backend.app.services.cost import SOURCE_UNKNOWN

EMPTY_OVERVIEW: Dict[str, Any] = {
    "total_requests": 0,
    "success_count": 0,
    "error_count": 0,
    "error_rate_pct": 0.0,
    "avg_latency_ms": 0.0,
    "p50_latency_ms": 0.0,
    "p95_latency_ms": 0.0,
    "p99_latency_ms": 0.0,
    "total_cost_usd": 0.0,
    "total_tokens": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "cache_read_tokens": 0,
    "cache_write_tokens": 0,
    "cost_unknown_count": 0,
    "latency_sampled": False,
}


def percentile(data: Sequence[float], p: float) -> float:
    """Linearly interpolated percentile, matching numpy default behaviour."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = (len(sorted_data) - 1) * (p / 100.0)
    floor_idx = math.floor(idx)
    ceil_idx = math.ceil(idx)
    if floor_idx == ceil_idx:
        return float(sorted_data[int(idx)])
    d0 = sorted_data[floor_idx] * (ceil_idx - idx)
    d1 = sorted_data[ceil_idx] * (idx - floor_idx)
    return float(d0 + d1)


def _apply_filters(
    query: Select,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
) -> Select:
    if start_time is not None:
        query = query.where(TraceEvent.timestamp >= start_time)
    if end_time is not None:
        query = query.where(TraceEvent.timestamp <= end_time)
    if provider:
        query = query.where(TraceEvent.provider == provider)
    if model_name:
        query = query.where(TraceEvent.model_name == model_name)
    return query


class MetricAggregator:
    @staticmethod
    def get_overview_metrics(
        session: Session,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Calculate high-level KPI overview metrics."""
        totals_query = _apply_filters(
            select(
                func.count(col(TraceEvent.id)),
                func.sum(col(TraceEvent.cost_usd)),
                func.sum(col(TraceEvent.total_tokens)),
                func.sum(col(TraceEvent.prompt_tokens)),
                func.sum(col(TraceEvent.completion_tokens)),
                func.sum(col(TraceEvent.cache_read_tokens)),
                func.sum(col(TraceEvent.cache_write_tokens)),
                func.avg(col(TraceEvent.latency_ms)),
            ),
            start_time,
            end_time,
            provider,
            model_name,
        )
        row = session.exec(totals_query).one()
        (
            total_requests,
            total_cost,
            total_tokens,
            prompt_tokens,
            completion_tokens,
            cache_read_tokens,
            cache_write_tokens,
            avg_latency,
        ) = row

        if not total_requests:
            return dict(EMPTY_OVERVIEW)

        error_query = _apply_filters(
            select(func.count(col(TraceEvent.id))).where(TraceEvent.status != "SUCCESS"),
            start_time,
            end_time,
            provider,
            model_name,
        )
        error_count = session.exec(error_query).one() or 0

        unpriced_query = _apply_filters(
            select(func.count(col(TraceEvent.id))).where(
                TraceEvent.cost_source == SOURCE_UNKNOWN
            ),
            start_time,
            end_time,
            provider,
            model_name,
        )
        cost_unknown_count = session.exec(unpriced_query).one() or 0

        # Newest first: when the row cap truncates the sample, percentiles should
        # describe recent behaviour. Without the ordering the database returns
        # rows in storage order, so a capped query reported last month latency as
        # if it were current.
        latency_query = _apply_filters(
            select(col(TraceEvent.latency_ms)),
            start_time,
            end_time,
            provider,
            model_name,
        ).order_by(col(TraceEvent.timestamp).desc()).limit(settings.MAX_AGGREGATION_ROWS)
        latencies = list(session.exec(latency_query).all())

        success_count = total_requests - error_count
        return {
            "total_requests": total_requests,
            "success_count": success_count,
            "error_count": error_count,
            "error_rate_pct": round((error_count / total_requests) * 100.0, 2),
            "avg_latency_ms": round(float(avg_latency or 0.0), 2),
            "p50_latency_ms": round(percentile(latencies, 50), 2),
            "p95_latency_ms": round(percentile(latencies, 95), 2),
            "p99_latency_ms": round(percentile(latencies, 99), 2),
            "total_cost_usd": round(float(total_cost or 0.0), 6),
            "total_tokens": int(total_tokens or 0),
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "cache_read_tokens": int(cache_read_tokens or 0),
            "cache_write_tokens": int(cache_write_tokens or 0),
            "cost_unknown_count": int(cost_unknown_count),
            # True when the percentile sample was truncated, so the dashboard can
            # say so instead of presenting an approximation as exact.
            "latency_sampled": len(latencies) < total_requests,
        }

    @staticmethod
    def get_timeseries_metrics(
        session: Session,
        interval_minutes: int = 5,
        hours_back: int = 24,
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Group metrics into fixed time buckets for charting."""
        interval_minutes = max(1, min(interval_minutes, 1440))
        cutoff = utc_now() - timedelta(hours=hours_back)

        # Descending, so a truncated window drops the OLDEST events. Ascending
        # order silently amputated the right-hand edge of the chart - the part a
        # monitoring dashboard exists to show.
        query = _apply_filters(
            select(
                col(TraceEvent.timestamp),
                col(TraceEvent.status),
                col(TraceEvent.latency_ms),
                col(TraceEvent.cost_usd),
                col(TraceEvent.total_tokens),
            ),
            start_time=cutoff,
            provider=provider,
            model_name=model_name,
        ).order_by(col(TraceEvent.timestamp).desc()).limit(settings.MAX_AGGREGATION_ROWS)

        rows = list(session.exec(query).all())
        if not rows:
            return []
        truncated = len(rows) >= settings.MAX_AGGREGATION_ROWS

        buckets: Dict[datetime, List[Any]] = {}
        for ts, status, latency, cost, tokens in rows:
            if ts is None:
                continue
            # Bucket on minutes-since-midnight so intervals that do not divide 60
            # still produce evenly spaced, non-overlapping buckets.
            minutes_of_day = ts.hour * 60 + ts.minute
            floored = (minutes_of_day // interval_minutes) * interval_minutes
            bucket_dt = ts.replace(
                hour=floored // 60, minute=floored % 60, second=0, microsecond=0
            )
            buckets.setdefault(bucket_dt, []).append((status, latency, cost, tokens))

        ordered = sorted(buckets)
        results: List[Dict[str, Any]] = []
        for index, bucket_dt in enumerate(ordered):
            entries = buckets[bucket_dt]
            count = len(entries)
            lats = [e[1] for e in entries]
            aware = to_aware_utc(bucket_dt)
            results.append({
                "timestamp": aware.isoformat() if aware else "",
                "request_count": count,
                "error_count": sum(1 for e in entries if e[0] != "SUCCESS"),
                "avg_latency_ms": round(sum(lats) / count, 2),
                "p95_latency_ms": round(percentile(lats, 95), 2),
                "total_cost_usd": round(sum(e[2] for e in entries), 6),
                "total_tokens": sum(e[3] for e in entries),
                # The row cap cuts into the oldest bucket, so its counts are a
                # floor rather than a total. Flagged instead of quietly shipped.
                "partial": truncated and index == 0,
            })
        return results

    @staticmethod
    def get_model_breakdown(
        session: Session,
        hours_back: int = 24,
        provider: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Per provider/model usage totals for the given window, grouped in SQL."""
        cutoff = utc_now() - timedelta(hours=hours_back)

        query = (
            select(
                col(TraceEvent.provider),
                col(TraceEvent.model_name),
                func.count(col(TraceEvent.id)),
                func.sum(col(TraceEvent.total_tokens)),
                func.sum(col(TraceEvent.cost_usd)),
                func.avg(col(TraceEvent.latency_ms)),
                func.sum(case((TraceEvent.status != "SUCCESS", 1), else_=0)),
                func.sum(case((TraceEvent.cost_source == SOURCE_UNKNOWN, 1), else_=0)),
            )
            .where(TraceEvent.timestamp >= cutoff)
            .group_by(col(TraceEvent.provider), col(TraceEvent.model_name))
            .order_by(func.count(col(TraceEvent.id)).desc())
        )
        if provider:
            query = query.where(TraceEvent.provider == provider)

        results: List[Dict[str, Any]] = []
        for row in session.exec(query).all():
            (
                row_provider,
                model,
                count,
                tokens,
                cost,
                avg_latency,
                error_count,
                unpriced_count,
            ) = row
            results.append({
                "provider": row_provider,
                "model_name": model,
                "count": int(count or 0),
                "total_tokens": int(tokens or 0),
                "total_cost_usd": round(float(cost or 0.0), 6),
                "error_count": int(error_count or 0),
                "avg_latency_ms": round(float(avg_latency or 0.0), 2),
                "cost_unknown_count": int(unpriced_count or 0),
            })
        return results
