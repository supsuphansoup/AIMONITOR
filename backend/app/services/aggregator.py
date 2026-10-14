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
from backend.app.core.timeutil import local_period_start_utc, to_aware_utc, to_naive_utc, utc_now
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
    "latency_measured_count": 0,
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


#: Cost that is actually known. Rows marked 'unknown' are stored with 0, except
#: v0.1.0 rows upgraded in place, which kept a guessed cost; summing cost_usd
#: directly would mix those guesses back into every total.
KNOWN_COST = case((TraceEvent.cost_source == SOURCE_UNKNOWN, 0.0), else_=col(TraceEvent.cost_usd))


def _apply_filters(
    query: Select,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    provider: Optional[str] = None,
    model_name: Optional[str] = None,
    account_id: Optional[str] = None,
    account_unknown: bool = False,
) -> Select:
    if start_time is not None:
        query = query.where(TraceEvent.timestamp >= start_time)
    if end_time is not None:
        query = query.where(TraceEvent.timestamp <= end_time)
    if provider:
        query = query.where(TraceEvent.provider == provider)
    if model_name:
        query = query.where(TraceEvent.model_name == model_name)
    if account_id is not None:
        query = query.where(TraceEvent.account_id == account_id)
    if account_unknown:
        query = query.where(col(TraceEvent.account_id).is_(None))
    return query


WATCHER_TAGS = {
    "claude": "claude-code", "antigravity": "antigravity", "copilot": "copilot",
    "codex": "codex", "cursor": "cursor",
}


def _watcher_key() -> Any:
    tags = "," + func.coalesce(col(TraceEvent.tags), "") + ","
    # replace is case-sensitive on SQLite and PostgreSQL; SQLite LIKE is not.
    return case(*[
        (func.replace(tags, f",{tag},", "") != tags, key)
        for key, tag in WATCHER_TAGS.items()
    ], else_=None)


def _ensure_read_snapshot(session: Session) -> None:
    """Start a real SQLite snapshot; the caller's Session owns its lifetime."""
    connection = session.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        # sqlite3 legacy mode does not BEGIN for SELECT, even inside Session.begin().
        connection.exec_driver_sql("BEGIN")


class MetricAggregator:
    @staticmethod
    def get_overview_metrics(
        session: Session,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
        account_id: Optional[str] = None,
        account_unknown: bool = False,
    ) -> Dict[str, Any]:
        """Calculate high-level KPI overview metrics."""
        _ensure_read_snapshot(session)
        totals_query = _apply_filters(
            select(
                func.count(col(TraceEvent.id)),
                func.sum(KNOWN_COST),
                func.sum(col(TraceEvent.total_tokens)),
                func.sum(col(TraceEvent.prompt_tokens)),
                func.sum(col(TraceEvent.completion_tokens)),
                func.sum(col(TraceEvent.cache_read_tokens)),
                func.sum(col(TraceEvent.cache_write_tokens)),
                func.avg(case((col(TraceEvent.latency_measured), col(TraceEvent.latency_ms)), else_=None)),
                func.sum(case((col(TraceEvent.latency_measured), 1), else_=0)),
                func.sum(case((TraceEvent.status != "SUCCESS", 1), else_=0)),
                func.sum(case((TraceEvent.cost_source == SOURCE_UNKNOWN, 1), else_=0)),
            ),
            start_time,
            end_time,
            provider,
            model_name,
            account_id,
            account_unknown,
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
            latency_measured_count,
            error_count,
            cost_unknown_count,
        ) = row

        if not total_requests:
            return dict(EMPTY_OVERVIEW)

        # Newest first: when the row cap truncates the sample, percentiles should
        # describe recent behaviour. Without the ordering the database returns
        # rows in storage order, so a capped query reported last month latency as
        # if it were current.
        latency_query = (
            _apply_filters(
                select(col(TraceEvent.latency_ms)).where(col(TraceEvent.latency_measured).is_(True)),
                start_time,
                end_time,
                provider,
                model_name,
                account_id,
                account_unknown,
            )
            .order_by(col(TraceEvent.timestamp).desc(), col(TraceEvent.id).desc())
            .limit(settings.MAX_AGGREGATION_ROWS)
        )
        latencies = list(session.exec(latency_query).all())

        success_count = total_requests - error_count
        return {
            "total_requests": total_requests,
            "success_count": success_count,
            "error_count": error_count,
            "error_rate_pct": round((error_count / total_requests) * 100.0, 2),
            "avg_latency_ms": round(float(avg_latency), 2) if avg_latency is not None else None,
            "p50_latency_ms": round(percentile(latencies, 50), 2) if latencies else None,
            "p95_latency_ms": round(percentile(latencies, 95), 2) if latencies else None,
            "p99_latency_ms": round(percentile(latencies, 99), 2) if latencies else None,
            "total_cost_usd": round(float(total_cost or 0.0), 6),
            "total_tokens": int(total_tokens or 0),
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "cache_read_tokens": int(cache_read_tokens or 0),
            "cache_write_tokens": int(cache_write_tokens or 0),
            "cost_unknown_count": int(cost_unknown_count),
            # True when the percentile sample was truncated, so the dashboard can
            # say so instead of presenting an approximation as exact.
            "latency_sampled": len(latencies) < latency_measured_count,
            "latency_measured_count": int(latency_measured_count or 0),
        }

    @staticmethod
    def get_timeseries_metrics(
        session: Session,
        interval_minutes: int = 5,
        hours_back: int = 24,
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
        account_id: Optional[str] = None,
        account_unknown: bool = False,
    ) -> List[Dict[str, Any]]:
        """Group metrics into fixed time buckets for charting."""
        interval_minutes = max(1, min(interval_minutes, 1440))
        now = utc_now()
        cutoff = now - timedelta(hours=hours_back)

        # Descending, so a truncated window drops the OLDEST events. Ascending
        # order silently amputated the right-hand edge of the chart - the part a
        # monitoring dashboard exists to show.
        query = (
            _apply_filters(
                select(
                    col(TraceEvent.timestamp),
                    col(TraceEvent.status),
                    col(TraceEvent.latency_ms),
                    KNOWN_COST,
                    col(TraceEvent.total_tokens),
                    col(TraceEvent.latency_measured),
                    col(TraceEvent.cost_source) == SOURCE_UNKNOWN,
                ),
                start_time=cutoff,
                end_time=now,
                provider=provider,
                model_name=model_name,
                account_id=account_id,
                account_unknown=account_unknown,
            )
            .order_by(col(TraceEvent.timestamp).desc(), col(TraceEvent.id).desc())
            .limit(settings.MAX_AGGREGATION_ROWS + 1)
        )

        rows = list(session.exec(query).all())
        if not rows:
            return []
        truncated = len(rows) > settings.MAX_AGGREGATION_ROWS
        rows = rows[:settings.MAX_AGGREGATION_ROWS]

        buckets: Dict[datetime, List[Any]] = {}
        for ts, status, latency, cost, tokens, measured, unknown in rows:
            if ts is None:
                continue
            # Bucket on minutes-since-midnight so intervals that do not divide 60
            # still produce evenly spaced, non-overlapping buckets.
            minutes_of_day = ts.hour * 60 + ts.minute
            floored = (minutes_of_day // interval_minutes) * interval_minutes
            bucket_dt = ts.replace(hour=floored // 60, minute=floored % 60, second=0, microsecond=0)
            buckets.setdefault(bucket_dt, []).append(
                (status, latency if measured else None, cost, tokens, unknown)
            )

        ordered = sorted(buckets)
        results: List[Dict[str, Any]] = []
        for index, bucket_dt in enumerate(ordered):
            entries = buckets[bucket_dt]
            count = len(entries)
            lats = [e[1] for e in entries if e[1] is not None]
            aware = to_aware_utc(bucket_dt)
            results.append(
                {
                    "timestamp": aware.isoformat() if aware else "",
                    "request_count": count,
                    "error_count": sum(1 for e in entries if e[0] != "SUCCESS"),
                    "avg_latency_ms": round(sum(lats) / len(lats), 2) if lats else None,
                    "p95_latency_ms": round(percentile(lats, 95), 2) if lats else None,
                    "total_cost_usd": round(sum(e[2] for e in entries), 6),
                    "cost_unknown_count": sum(1 for e in entries if e[4]),
                    "total_tokens": sum(e[3] for e in entries),
                    # The row cap cuts into the oldest bucket, so its counts are a
                    # floor rather than a total. Flagged instead of quietly shipped.
                    "partial": truncated and index == 0,
                }
            )
        return results

    @staticmethod
    def get_model_breakdown(
        session: Session,
        hours_back: int = 24,
        provider: Optional[str] = None,
        account_id: Optional[str] = None,
        account_unknown: bool = False,
    ) -> List[Dict[str, Any]]:
        """Per provider/model usage totals for the given window, grouped in SQL."""
        now = utc_now()
        cutoff = now - timedelta(hours=hours_back)

        query = (
            select(
                col(TraceEvent.provider),
                col(TraceEvent.model_name),
                func.count(col(TraceEvent.id)),
                func.sum(col(TraceEvent.total_tokens)),
                func.sum(KNOWN_COST),
                func.avg(case((col(TraceEvent.latency_measured), col(TraceEvent.latency_ms)), else_=None)),
                func.sum(case((TraceEvent.status != "SUCCESS", 1), else_=0)),
                func.sum(case((TraceEvent.cost_source == SOURCE_UNKNOWN, 1), else_=0)),
            )
            .where(TraceEvent.timestamp >= cutoff)
            .where(TraceEvent.timestamp <= now)
            .group_by(col(TraceEvent.provider), col(TraceEvent.model_name))
            .order_by(func.count(col(TraceEvent.id)).desc())
        )
        if provider:
            query = query.where(TraceEvent.provider == provider)

        if account_id is not None:
            query = query.where(TraceEvent.account_id == account_id)
        if account_unknown:
            query = query.where(col(TraceEvent.account_id).is_(None))

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
            results.append(
                {
                    "provider": row_provider,
                    "model_name": model,
                    "count": int(count or 0),
                    "total_tokens": int(tokens or 0),
                    "total_cost_usd": round(float(cost or 0.0), 6),
                    "error_count": int(error_count or 0),
                    "avg_latency_ms": round(float(avg_latency), 2) if avg_latency is not None else None,
                    "cost_unknown_count": int(unpriced_count or 0),
                }
            )
        return results

    @staticmethod
    def get_active_contexts(session: Session, now: Optional[datetime] = None) -> Dict[str, Any]:
        """Usage per AI on this PC, keyed the way the desktop detector keys AIs.

        Watcher rows are grouped by the tool that produced them (Claude Code,
        Antigravity, Copilot), not by provider: Copilot also calls Claude models,
        and those calls are not Claude Code usage. Every other row is grouped as
        `<provider>_api`.

        Windows: rolling 5 hours, today and this month in the PC's local time,
        and the last 7 days. Cost sums only priced rows; `cost_unknown` counts
        the rest so a caller can say "계산 불가" instead of showing a low total.
        """
        _ensure_read_snapshot(session)
        now_utc = to_aware_utc(now) if now else to_aware_utc(utc_now())
        windows = {
            "5h": now_utc - timedelta(hours=5),
            "today": local_period_start_utc(now_utc),
            "7d": now_utc - timedelta(days=7),
            "month": local_period_start_utc(now_utc, month_start=True),
        }

        source = select(
            func.coalesce(_watcher_key(), col(TraceEvent.provider) + "_api").label("tool_key"),
            col(TraceEvent.account_id), col(TraceEvent.timestamp),
            col(TraceEvent.total_tokens), KNOWN_COST.label("known_cost"),
            case((TraceEvent.cost_source == SOURCE_UNKNOWN, 1), else_=0).label("unknown_cost"),
        ).where(col(TraceEvent.timestamp) <= to_naive_utc(now_utc)).subquery("usage_rows")
        columns = [source.c.tool_key, source.c.account_id]
        for name, since in windows.items():
            included = source.c.timestamp >= to_naive_utc(since)
            columns.extend([
                func.sum(case((included, 1), else_=0)).label(name + "_requests"),
                func.sum(case((included, source.c.total_tokens), else_=0)).label(name + "_tokens"),
                func.sum(case((included, source.c.known_cost), else_=0.0)).label(name + "_cost"),
                func.sum(case((included, source.c.unknown_cost), else_=0)).label(name + "_unknown"),
                func.min(case((included, source.c.timestamp), else_=None)).label(name + "_earliest"),
            ])
        usage_query = select(*columns).where(
            source.c.timestamp >= min(to_naive_utc(since) for since in windows.values()),
        ).group_by(source.c.tool_key, source.c.account_id)
        summaries = {
            (row.tool_key, row.account_id): row._mapping for row in session.exec(usage_query).all()
        }
        latest_times = select(
            source.c.tool_key, source.c.account_id, func.max(source.c.timestamp).label("timestamp"),
        ).group_by(source.c.tool_key, source.c.account_id).subquery("latest_times")
        latest_ids = select(
            latest_times.c.tool_key, latest_times.c.account_id, func.max(col(TraceEvent.id)).label("id"),
        ).select_from(latest_times).join(TraceEvent, (
            (col(TraceEvent.timestamp) == latest_times.c.timestamp)
            & col(TraceEvent.account_id).is_not_distinct_from(latest_times.c.account_id)
            & (func.coalesce(_watcher_key(), col(TraceEvent.provider) + "_api") == latest_times.c.tool_key)
        )).group_by(latest_times.c.tool_key, latest_times.c.account_id).subquery("latest_ids")
        query = select(
            latest_ids.c.tool_key, latest_ids.c.account_id,
            *[col(getattr(TraceEvent, field)).label("latest_" + field) for field in (
                "id", "timestamp", "provider", "model_name", "session_id",
                "prompt_tokens", "cache_read_tokens", "cache_write_tokens",
            )],
        ).select_from(latest_ids).join(TraceEvent, col(TraceEvent.id) == latest_ids.c.id).order_by(
            latest_ids.c.tool_key, latest_ids.c.account_id.is_not(None), latest_ids.c.account_id,
        )

        results: Dict[str, Any] = {}
        latest_ids: Dict[str, tuple] = {}
        for record in session.exec(query).all():
            row = record._mapping
            key = row["tool_key"]
            kind = "watcher" if key in WATCHER_TAGS else "api"
            ts = to_aware_utc(row["latest_timestamp"])
            mins_ago = int((now_utc - ts).total_seconds() // 60)
            context_tokens = None
            if key in ("claude", "codex") or kind == "api":
                context_tokens = sum(row["latest_" + field] or 0 for field in (
                    "prompt_tokens", "cache_read_tokens", "cache_write_tokens",
                ))
            latest = {
                "key": key, "kind": kind, "provider": row["latest_provider"],
                "model_name": row["latest_model_name"], "session_id": row["latest_session_id"],
                "context_tokens": context_tokens, "timestamp": ts.isoformat(),
                "mins_ago": mins_ago, "is_active": mins_ago <= 15,
            }
            if key not in results:
                results[key] = {**latest, "windows": {
                    name: {"since": to_aware_utc(since).isoformat(), "requests": 0, "tokens": 0,
                           "cost_usd": 0.0, "cost_unknown": 0, "earliest": None}
                    for name, since in windows.items()
                }, "accounts": []}
            total = results[key]
            latest_id = (row["latest_timestamp"], row["latest_id"])
            if key not in latest_ids or latest_id > latest_ids[key]:
                total.update(latest)
                latest_ids[key] = latest_id
            account_windows = {}
            usage_row = summaries.get((key, row["account_id"]), {})
            for name in windows:
                earliest = usage_row.get(name + "_earliest")
                usage = {
                    "since": total["windows"][name]["since"],
                    "requests": int(usage_row.get(name + "_requests", 0)),
                    "tokens": int(usage_row.get(name + "_tokens", 0)),
                    "cost_usd": float(usage_row.get(name + "_cost", 0.0)),
                    "cost_unknown": int(usage_row.get(name + "_unknown", 0)),
                    "earliest": to_aware_utc(earliest).isoformat() if earliest else None,
                }
                combined = total["windows"][name]
                for field in ("requests", "tokens", "cost_usd", "cost_unknown"):
                    combined[field] += usage[field]
                if usage["earliest"] and (
                    not combined["earliest"] or usage["earliest"] < combined["earliest"]
                ):
                    combined["earliest"] = usage["earliest"]
                account_windows[name] = {**usage, "cost_usd": round(usage["cost_usd"], 6)}
            total["accounts"].append({"account_id": row["account_id"], **latest, "windows": account_windows})
        for total in results.values():
            for usage in total["windows"].values():
                usage["cost_usd"] = round(usage["cost_usd"], 6)
        return results
