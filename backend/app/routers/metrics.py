from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from backend.app.core.accounts import ACCOUNT_ID_MAX_LENGTH, ACCOUNT_ID_PATTERN, validate_account_filter
from backend.app.core.security import require_read_auth
from backend.app.core.timeutil import to_aware_utc, to_naive_utc, utc_now
from backend.app.db.session import get_session
from backend.app.services.account_quotas import get_account_quota_windows
from backend.app.services.aggregator import MetricAggregator

router = APIRouter(
    prefix="/metrics",
    tags=["Metrics & Analytics"],
    dependencies=[Depends(require_read_auth)],
)


def _query_time(value: Optional[datetime], field: str) -> Optional[datetime]:
    try:
        return to_naive_utc(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=[{
            "type": "datetime_parsing", "loc": ["query", field],
            "msg": "Timestamp is outside the UTC range",
        }]) from None


@router.get("/overview", response_model=Dict[str, Any])
def get_metrics_overview(
    session: Session = Depends(get_session),
    provider: Optional[str] = Query(None, description="Filter by provider"),
    account_id: Optional[str] = Query(None, max_length=ACCOUNT_ID_MAX_LENGTH, pattern=ACCOUNT_ID_PATTERN),
    account_unknown: bool = Query(False, description="Only records with unknown account"),
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
    validate_account_filter(account_id, account_unknown)
    resolved_start = _query_time(start_time, "start_time")
    resolved_end = _query_time(end_time, "end_time")
    if resolved_start is None and hours_back is not None:
        now = utc_now()
        resolved_start = now - timedelta(hours=hours_back)
        if resolved_end is None:
            resolved_end = now

    return MetricAggregator.get_overview_metrics(
        session=session,
        start_time=resolved_start,
        end_time=resolved_end,
        provider=provider,
        account_id=account_id,
        account_unknown=account_unknown,
        model_name=model_name,
    )


@router.get("/timeseries", response_model=List[Dict[str, Any]])
def get_metrics_timeseries(
    session: Session = Depends(get_session),
    interval_minutes: int = Query(5, ge=1, le=1440, description="Bucket interval in minutes"),
    hours_back: int = Query(24, ge=1, le=8760, description="Hours of past data to include"),
    provider: Optional[str] = Query(None, description="Filter by provider"),
    account_id: Optional[str] = Query(None, max_length=ACCOUNT_ID_MAX_LENGTH, pattern=ACCOUNT_ID_PATTERN),
    account_unknown: bool = Query(False, description="Only records with unknown account"),
    model_name: Optional[str] = Query(None, description="Filter by model name"),
):
    """Time-series metric buckets for rendering charts."""
    validate_account_filter(account_id, account_unknown)
    return MetricAggregator.get_timeseries_metrics(
        session=session,
        interval_minutes=interval_minutes,
        hours_back=hours_back,
        provider=provider,
        account_id=account_id,
        account_unknown=account_unknown,
        model_name=model_name,
    )


@router.get("/breakdown", response_model=List[Dict[str, Any]])
def get_model_breakdown(
    session: Session = Depends(get_session),
    hours_back: int = Query(24, ge=1, le=8760, description="Hours of past data to include"),
    provider: Optional[str] = Query(None, description="Filter by provider"),
    account_id: Optional[str] = Query(None, max_length=ACCOUNT_ID_MAX_LENGTH, pattern=ACCOUNT_ID_PATTERN),
    account_unknown: bool = Query(False, description="Only records with unknown account"),
):
    """Per provider/model usage and cost breakdown."""
    validate_account_filter(account_id, account_unknown)
    return MetricAggregator.get_model_breakdown(
        session=session, hours_back=hours_back, provider=provider, account_id=account_id,
        account_unknown=account_unknown
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
    active = MetricAggregator.get_active_contexts(session=session)
    account_quotas = get_account_quota_windows(active)
    rate = float(config.get("krw_per_usd") or 1380.0)
    summary = get_connected_ai_summary(
        active_ais=ais,
        usage_by_ai=active,
        krw_rate=rate,
        config=config,
        quota_overrides={
            ai["key"]: [] for ai in ais
            if ai.get("detection_status") == "unavailable" or ai.get("use_default_quota") is False
        },
        official_spend=spend_service.spend_all([
            ai["key"] for ai in ais
            if ai["key"].endswith("_api") and ai.get("detection_status") != "unavailable"
        ]),
    )
    for item in summary["items"]:
        ai = next(ai for ai in ais if ai["key"] == item["key"])
        item["detection_status"] = ai.get("detection_status", "available")
        accounts = []
        for account in active.get(item["key"], {}).get("accounts", []):
            # A default credential's quota/bill cannot be attributed to another account.
            account_ai = {**ai, "plan_name": "플랜 확인 불가"} if item["key"] in ("claude", "codex") else ai
            account_item = get_connected_ai_summary(
                active_ais=[account_ai], usage_by_ai={item["key"]: account}, krw_rate=rate,
                config=config, quota_overrides={item["key"]: []}, official_spend={},
            )["items"][0]
            account_item["account_id"] = account["account_id"]
            account_item["quota_windows"] = account_quotas.get((item["key"], account["account_id"]))
            _sanitize_connected_item(account_item)
            accounts.append(account_item)
        item["accounts"] = accounts
        _sanitize_connected_item(item)
    return summary


def _sanitize_connected_item(item: Dict[str, Any]) -> None:
    """Normalize response times and remove local detector and billing error details."""
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
    for window in item["quota_windows"] or []:
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
