"""Canonical list of alertable metrics.

Lives in `core` rather than `services` so both the alert models (which validate
`metric_type` on write) and the alert evaluator (which resolves it on read) can
share one definition without importing each other.
"""
from typing import Dict

#: Accepted `metric_type` aliases -> the key returned by the overview aggregator.
METRIC_FIELD_MAP: Dict[str, str] = {
    "error_rate": "error_rate_pct",
    "error_rate_pct": "error_rate_pct",
    "error_count": "error_count",
    "errors": "error_count",
    "total_requests": "total_requests",
    "requests": "total_requests",
    "request_count": "total_requests",
    "success_count": "success_count",
    "latency_p95": "p95_latency_ms",
    "p95_latency": "p95_latency_ms",
    "p95_latency_ms": "p95_latency_ms",
    "p95": "p95_latency_ms",
    "latency_p50": "p50_latency_ms",
    "p50_latency": "p50_latency_ms",
    "p50_latency_ms": "p50_latency_ms",
    "p50": "p50_latency_ms",
    "latency_p99": "p99_latency_ms",
    "p99_latency": "p99_latency_ms",
    "p99_latency_ms": "p99_latency_ms",
    "p99": "p99_latency_ms",
    "avg_latency": "avg_latency_ms",
    "avg_latency_ms": "avg_latency_ms",
    "latency": "avg_latency_ms",
    "cost_hourly": "total_cost_usd",
    "total_cost": "total_cost_usd",
    "total_cost_usd": "total_cost_usd",
    "cost": "total_cost_usd",
    "total_tokens": "total_tokens",
    "tokens": "total_tokens",
    "prompt_tokens": "prompt_tokens",
    "completion_tokens": "completion_tokens",
    "cache_read_tokens": "cache_read_tokens",
    "cache_write_tokens": "cache_write_tokens",
}

#: Units, for rendering thresholds in the dashboard.
METRIC_UNITS: Dict[str, str] = {
    "error_rate_pct": "%",
    "p50_latency_ms": "ms",
    "p95_latency_ms": "ms",
    "p99_latency_ms": "ms",
    "avg_latency_ms": "ms",
    "total_cost_usd": "USD",
    "total_requests": "count",
    "success_count": "count",
    "error_count": "count",
    "total_tokens": "tokens",
    "prompt_tokens": "tokens",
    "completion_tokens": "tokens",
    "cache_read_tokens": "tokens",
    "cache_write_tokens": "tokens",
}

VALID_METRIC_TYPES = frozenset(METRIC_FIELD_MAP.keys())

#: Overview keys that are undefined - not zero - when a window has no requests:
#: there is no latency to rank and no error rate to divide out. Counts and sums
#: (requests, cost, tokens) really are 0, so "traffic stopped" rules still work.
UNDEFINED_WITHOUT_TRAFFIC = frozenset(
    {"error_rate_pct", "p50_latency_ms", "p95_latency_ms", "p99_latency_ms", "avg_latency_ms"}
)

#: Comparisons a lower bound can settle: if the known part is already over the
#: line, the full total is too. Anything else needs the full total.
LOWER_BOUND_OPERATORS = frozenset({">", ">="})


def resolve_metric_key(metric_type: str) -> str:
    """Map a user-supplied metric name onto an overview field key."""
    normalized = str(metric_type).lower().strip()
    return METRIC_FIELD_MAP.get(normalized, normalized)


def unit_for(metric_type: str) -> str:
    return METRIC_UNITS.get(resolve_metric_key(metric_type), "")
