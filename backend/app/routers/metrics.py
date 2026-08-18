from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from backend.app.db.session import get_session
from backend.app.services.aggregator import MetricAggregator

router = APIRouter(prefix="/metrics", tags=["Metrics & Analytics"])


@router.get("/overview", response_model=Dict[str, Any])
def get_metrics_overview(
    session: Session = Depends(get_session),
    provider: Optional[str] = Query(None, description="Filter by provider"),
    model_name: Optional[str] = Query(None, description="Filter by model name"),
    start_time: Optional[datetime] = Query(None, description="Start ISO timestamp"),
    end_time: Optional[datetime] = Query(None, description="End ISO timestamp"),
):
    """Get high-level summary overview metrics."""
    return MetricAggregator.get_overview_metrics(
        session=session,
        start_time=start_time,
        end_time=end_time,
        provider=provider,
        model_name=model_name,
    )


@router.get("/timeseries", response_model=List[Dict[str, Any]])
def get_metrics_timeseries(
    session: Session = Depends(get_session),
    interval_minutes: int = Query(5, ge=1, le=1440, description="Bucket interval in minutes"),
    hours_back: int = Query(24, ge=1, le=720, description="Hours of past data to include"),
):
    """Get time-series metric data for rendering charts."""
    return MetricAggregator.get_timeseries_metrics(
        session=session,
        interval_minutes=interval_minutes,
        hours_back=hours_back,
    )


@router.get("/breakdown", response_model=List[Dict[str, Any]])
def get_model_breakdown(
    session: Session = Depends(get_session),
    hours_back: int = Query(24, ge=1, le=720, description="Hours of past data to include"),
):
    """Get model and provider breakdown statistics."""
    return MetricAggregator.get_model_breakdown(session=session, hours_back=hours_back)
