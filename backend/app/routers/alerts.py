import logging
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, col, delete, select

from backend.app.core.metrics_catalog import METRIC_FIELD_MAP, VALID_METRIC_TYPES, unit_for
from backend.app.core.security import require_read_auth, require_write_auth
from backend.app.db.session import get_session
from backend.app.models.alert import (
    VALID_OPERATORS,
    AlertEvaluationRead,
    AlertHistory,
    AlertHistoryRead,
    AlertRule,
    AlertRuleCreate,
    AlertRuleRead,
    AlertRuleUpdate,
)
from backend.app.services.alert import AlertEvaluator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts", tags=["Alerts & Anomaly Detection"])


@router.get("/metrics", dependencies=[Depends(require_read_auth)])
def list_supported_metrics():
    """Metric names and operators the dashboard can offer when building a rule."""
    canonical = sorted(set(METRIC_FIELD_MAP.values()))
    return {
        "metrics": [
            {
                "key": key,
                "resolves_to": METRIC_FIELD_MAP[key],
                "unit": unit_for(key),
            }
            for key in sorted(VALID_METRIC_TYPES)
        ],
        "canonical_metrics": canonical,
        "operators": sorted(VALID_OPERATORS),
    }


@router.post(
    "/rules",
    response_model=AlertRuleRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_write_auth)],
)
def create_alert_rule(
    payload: AlertRuleCreate,
    session: Session = Depends(get_session),
):
    """Create a new alert rule."""
    rule = AlertRule(**payload.model_dump())
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


@router.get("/rules", response_model=List[AlertRuleRead], dependencies=[Depends(require_read_auth)])
def list_alert_rules(
    session: Session = Depends(get_session),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List configured alert rules."""
    query = select(AlertRule).order_by(col(AlertRule.id).desc())
    if is_active is not None:
        query = query.where(AlertRule.is_active == is_active)
    query = query.offset(offset).limit(limit)
    return session.exec(query).all()


@router.get(
    "/rules/{rule_id}", response_model=AlertRuleRead, dependencies=[Depends(require_read_auth)]
)
def get_alert_rule(
    rule_id: int,
    session: Session = Depends(get_session),
):
    """Get details of a specific alert rule."""
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Alert rule with ID {rule_id} not found")
    return rule


@router.patch(
    "/rules/{rule_id}", response_model=AlertRuleRead, dependencies=[Depends(require_write_auth)]
)
def update_alert_rule(
    rule_id: int,
    payload: AlertRuleUpdate,
    session: Session = Depends(get_session),
):
    """Partially update a rule - most often to pause or resume it."""
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Alert rule with ID {rule_id} not found")

    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return rule
    for field, value in changes.items():
        setattr(rule, field, value)

    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


@router.delete(
    "/rules/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_write_auth)],
)
def delete_alert_rule(
    rule_id: int,
    session: Session = Depends(get_session),
):
    """Delete a rule along with the history rows that reference it."""
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Alert rule with ID {rule_id} not found")

    # The history carries a foreign key to the rule, so it has to go first.
    session.exec(delete(AlertHistory).where(AlertHistory.rule_id == rule_id))
    session.delete(rule)
    session.commit()
    return None


@router.get(
    "/history", response_model=List[AlertHistoryRead], dependencies=[Depends(require_read_auth)]
)
def list_alert_history(
    session: Session = Depends(get_session),
    rule_id: Optional[int] = Query(None, description="Filter by rule ID"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List historical alert trigger logs."""
    query = select(AlertHistory).order_by(col(AlertHistory.created_at).desc())
    if rule_id is not None:
        query = query.where(AlertHistory.rule_id == rule_id)
    query = query.offset(offset).limit(limit)
    return session.exec(query).all()


@router.post(
    "/evaluate",
    response_model=Union[List[AlertHistoryRead], AlertEvaluationRead],
    dependencies=[Depends(require_write_auth)],
)
def trigger_alert_evaluation(
    session: Session = Depends(get_session),
    ignore_cooldown: bool = Query(
        False,
        description=(
            "Evaluate even if a rule fired inside its cooldown window. Still records "
            "history and dispatches webhooks, but leaves the cooldown untouched."
        ),
    ),
    include_skipped: bool = Query(
        False, description="Return histories and safe diagnostics for sampled rules",
    ),
):
    """Evaluate all active rules now.

    The scheduler runs this on its own interval; this endpoint exists for manual
    checks and for testing a freshly created rule. `ignore_cooldown` forces a
    real evaluation - history and webhooks included - without consuming or
    resetting the cooldown that governs the scheduled passes.
    """
    skipped: Optional[List[Dict[str, Any]]] = [] if include_skipped else None
    histories = AlertEvaluator.evaluate_rules(
        session=session, ignore_cooldown=ignore_cooldown, skipped_rules=skipped,
    )
    if include_skipped:
        return {"histories": histories, "skipped_rules": skipped}
    return histories
