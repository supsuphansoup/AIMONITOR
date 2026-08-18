import math
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from sqlmodel import Session, select, col

from backend.app.models.trace import TraceEvent


def percentile(data: List[float], p: float) -> float:
    """Calculate percentile from a list of floats."""
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
        query = select(TraceEvent)
        if start_time:
            query = query.where(TraceEvent.timestamp >= start_time)
        if end_time:
            query = query.where(TraceEvent.timestamp <= end_time)
        if provider:
            query = query.where(TraceEvent.provider == provider)
        if model_name:
            query = query.where(TraceEvent.model_name == model_name)

        events: List[TraceEvent] = list(session.exec(query).all())
        total_requests = len(events)
        
        if total_requests == 0:
            return {
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
            }

        latencies = [e.latency_ms for e in events]
        errors = [e for e in events if e.status != "SUCCESS"]
        error_count = len(errors)
        success_count = total_requests - error_count
        error_rate_pct = round((error_count / total_requests) * 100.0, 2)
        
        total_cost = sum(e.cost_usd for e in events)
        total_tokens = sum(e.total_tokens for e in events)
        prompt_tokens = sum(e.prompt_tokens for e in events)
        completion_tokens = sum(e.completion_tokens for e in events)
        avg_latency = sum(latencies) / total_requests

        return {
            "total_requests": total_requests,
            "success_count": success_count,
            "error_count": error_count,
            "error_rate_pct": error_rate_pct,
            "avg_latency_ms": round(avg_latency, 2),
            "p50_latency_ms": round(percentile(latencies, 50), 2),
            "p95_latency_ms": round(percentile(latencies, 95), 2),
            "p99_latency_ms": round(percentile(latencies, 99), 2),
            "total_cost_usd": round(total_cost, 6),
            "total_tokens": total_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }

    @staticmethod
    def get_timeseries_metrics(
        session: Session,
        interval_minutes: int = 5,
        hours_back: int = 24,
    ) -> List[Dict[str, Any]]:
        """Group metrics by time intervals for charts safely across timezones."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        query = (
            select(TraceEvent)
            .where(TraceEvent.timestamp >= cutoff)
            .order_by(col(TraceEvent.timestamp).asc())
        )
        events: List[TraceEvent] = list(session.exec(query).all())

        if not events:
            return []

        # Group events into interval buckets
        buckets: Dict[str, List[TraceEvent]] = {}
        for event in events:
            ts = event.timestamp
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            else:
                ts = ts.astimezone(timezone.utc)
            
            # Floor timestamp to interval
            minute_bucket = (ts.minute // interval_minutes) * interval_minutes
            bucket_dt = ts.replace(minute=minute_bucket, second=0, microsecond=0)
            bucket_key = bucket_dt.isoformat()

            if bucket_key not in buckets:
                buckets[bucket_key] = []
            buckets[bucket_key].append(event)

        results = []
        for bucket_key, bucket_events in sorted(buckets.items()):
            count = len(bucket_events)
            err_count = sum(1 for e in bucket_events if e.status != "SUCCESS")
            lats = [e.latency_ms for e in bucket_events]
            cost = sum(e.cost_usd for e in bucket_events)
            tokens = sum(e.total_tokens for e in bucket_events)

            results.append({
                "timestamp": bucket_key,
                "request_count": count,
                "error_count": err_count,
                "avg_latency_ms": round(sum(lats) / count, 2) if count > 0 else 0,
                "p95_latency_ms": round(percentile(lats, 95), 2),
                "total_cost_usd": round(cost, 6),
                "total_tokens": tokens,
            })

        return results

    @staticmethod
    def get_model_breakdown(session: Session, hours_back: int = 24) -> List[Dict[str, Any]]:
        """Calculate breakdown by model and provider for the given time window."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        query = select(TraceEvent).where(TraceEvent.timestamp >= cutoff)
        events = session.exec(query).all()
        
        breakdown_map: Dict[str, Dict[str, Any]] = {}
        for e in events:
            key = f"{e.provider}/{e.model_name}"
            if key not in breakdown_map:
                breakdown_map[key] = {
                    "provider": e.provider,
                    "model_name": e.model_name,
                    "count": 0,
                    "total_tokens": 0,
                    "total_cost_usd": 0.0,
                    "error_count": 0,
                    "latencies": [],
                }
            item = breakdown_map[key]
            item["count"] += 1
            item["total_tokens"] += e.total_tokens
            item["total_cost_usd"] += e.cost_usd
            if e.status != "SUCCESS":
                item["error_count"] += 1
            item["latencies"].append(e.latency_ms)

        results = []
        for item in breakdown_map.values():
            lats = item.pop("latencies")
            item["avg_latency_ms"] = round(sum(lats) / len(lats), 2) if lats else 0.0
            item["total_cost_usd"] = round(item["total_cost_usd"], 6)
            results.append(item)

        return sorted(results, key=lambda x: x["count"], reverse=True)
