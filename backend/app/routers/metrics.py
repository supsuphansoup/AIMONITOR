from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from backend.app.core.security import require_read_auth
from backend.app.core.timeutil import to_aware_utc, to_naive_utc, utc_now
from backend.app.db.session import get_session
from backend.app.services.aggregator import MetricAggregator

router = APIRouter(
    prefix="/metrics",
    tags=["Metrics & Analytics"],
    dependencies=[Depends(require_read_auth)],
)


@router.get("/overview", response_model=Dict[str, Any])
def get_metrics_overview(
    session: Session = Depends(get_session),
    provider: Optional[str] = Query(None, description="Filter by provider"),
    model_name: Optional[str] = Query(None, description="Filter by model name"),
    hours_back: Optional[int] = Query(
        None, ge=1, le=8760, description="Rolling window in hours; omit for all time"
    ),
    start_time: Optional[datetime] = Query(None, description="Start ISO timestamp"),
    end_time: Optional[datetime] = Query(None, description="End ISO timestamp"),
):
    """High-level KPI summary over the requested window.

    `hours_back` is a shorthand for `start_time`; an explicit `start_time` wins.
    """
    resolved_start = to_naive_utc(start_time)
    if resolved_start is None and hours_back is not None:
        resolved_start = utc_now() - timedelta(hours=hours_back)

    return MetricAggregator.get_overview_metrics(
        session=session,
        start_time=resolved_start,
        end_time=to_naive_utc(end_time),
        provider=provider,
        model_name=model_name,
    )


@router.get("/timeseries", response_model=List[Dict[str, Any]])
def get_metrics_timeseries(
    session: Session = Depends(get_session),
    interval_minutes: int = Query(5, ge=1, le=1440, description="Bucket interval in minutes"),
    hours_back: int = Query(24, ge=1, le=8760, description="Hours of past data to include"),
    provider: Optional[str] = Query(None, description="Filter by provider"),
    model_name: Optional[str] = Query(None, description="Filter by model name"),
):
    """Time-series metric buckets for rendering charts."""
    return MetricAggregator.get_timeseries_metrics(
        session=session,
        interval_minutes=interval_minutes,
        hours_back=hours_back,
        provider=provider,
        model_name=model_name,
    )


@router.get("/breakdown", response_model=List[Dict[str, Any]])
def get_model_breakdown(
    session: Session = Depends(get_session),
    hours_back: int = Query(24, ge=1, le=8760, description="Hours of past data to include"),
    provider: Optional[str] = Query(None, description="Filter by provider"),
):
    """Per provider/model usage and cost breakdown."""
    return MetricAggregator.get_model_breakdown(
        session=session, hours_back=hours_back, provider=provider
    )


@router.get("/active_contexts", response_model=Dict[str, Any])
def get_active_contexts(session: Session = Depends(get_session)):
    """Per-tool usage windows and the latest context estimate for the desktop."""
    return MetricAggregator.get_active_contexts(session=session)


@router.get("/connected_ais", response_model=Dict[str, Any])
def get_connected_ais(session: Session = Depends(get_session)):
    """Connected tools, sourced quotas and local usage; never credentials or local paths."""
    from backend.app.services.api_key_manager import spend_service
    from desktop.ai_detector import detect_connected_ais
    from desktop.config import load_config
    from desktop.context_manager import get_connected_ai_summary

    config = load_config()
    ais = detect_connected_ais()
    summary = get_connected_ai_summary(
        active_ais=ais,
        usage_by_ai=MetricAggregator.get_active_contexts(session=session),
        krw_rate=float(config.get("krw_per_usd") or 1380.0),
        config=config,
        official_spend=spend_service.spend_all([ai["key"] for ai in ais if ai["key"].endswith("_api")]),
    )
    # The desktop detector's detail can contain filesystem exception strings.
    # Neither it nor a local credential path belongs in the web response.
    for item in summary["items"]:
        item.pop("path", None)
        item.pop("detail", None)
        if item.get("last_request_at"):
            item["last_request_at"] = to_aware_utc(
                datetime.fromisoformat(item["last_request_at"])
            ).isoformat()
        if item.get("context") and item["context"].get("as_of"):
            item["context"]["as_of"] = to_aware_utc(
                datetime.fromisoformat(item["context"]["as_of"])
            ).isoformat()
        for window in item["quota_windows"]:
            for field in ("as_of", "resets_at"):
                if window.get(field):
                    window[field] = to_aware_utc(datetime.fromisoformat(window[field])).isoformat()
        spend = item.get("official_spend")
        if spend:
            spend.pop("error", None)
            for field in ("as_of", "period_start"):
                if spend.get(field):
                    spend[field] = to_aware_utc(datetime.fromisoformat(spend[field])).isoformat()
        # A zero priced subtotal is not a zero bill when every call is unpriced.
        for usage in item["usage"].values():
            if usage["cost_unknown"] and usage["cost_unknown"] == usage["requests"]:
                usage["cost_usd"] = None
                usage["cost_krw"] = None
    return summary
