from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select, col

from backend.app.db.session import get_session
from backend.app.models.alert import (
    AlertRule,
    AlertRuleCreate,
    AlertRuleRead,
    AlertHistory,
    AlertHistoryRead,
)
from backend.app.services.alert import AlertEvaluator

router = APIRouter(prefix="/alerts", tags=["Alerts & Anomaly Detection"])


@router.post("/rules", response_model=AlertRuleRead, status_code=status.HTTP_201_CREATED)
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


@router.get("/rules", response_model=List[AlertRuleRead])
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


@router.get("/rules/{rule_id}", response_model=AlertRuleRead)
def get_alert_rule(
    rule_id: int,
    session: Session = Depends(get_session),
):
    """Get details of a specific alert rule."""
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Alert rule with ID {rule_id} not found")
    return rule


@router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alert_rule(
    rule_id: int,
    session: Session = Depends(get_session),
):
    """Delete an alert rule by ID."""
    rule = session.get(AlertRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Alert rule with ID {rule_id} not found")
    session.delete(rule)
    session.commit()
    return None


@router.get("/history", response_model=List[AlertHistoryRead])
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


@router.post("/evaluate", response_model=List[AlertHistoryRead])
def trigger_alert_evaluation(
    session: Session = Depends(get_session),
):
    """Trigger manual evaluation of all active alert rules."""
    triggered_alerts = AlertEvaluator.evaluate_rules(session=session)
    return triggered_alerts
