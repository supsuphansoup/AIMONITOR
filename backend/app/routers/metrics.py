from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from backend.app.core.security import require_read_auth
from backend.app.core.timeutil import to_naive_utc, utc_now
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
