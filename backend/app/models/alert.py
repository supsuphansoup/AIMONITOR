from datetime import datetime, timezone
from typing import ClassVar, FrozenSet, Optional
from sqlmodel import SQLModel, Field
from pydantic import BaseModel, ConfigDict, field_validator


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AlertRuleBase(SQLModel):
    name: str = Field(index=True)
    metric_type: str = Field(description="error_rate, latency_p95, cost_hourly, etc.")
    operator: str = Field(default=">", description=">, >=, <, <=, ==, !=")
    threshold_value: float = Field(description="Threshold value to trigger alert")
    window_minutes: int = Field(default=5, description="Rolling time window in minutes")
    webhook_url: Optional[str] = Field(default=None, description="Slack / Discord Webhook URL")
    is_active: bool = Field(default=True, index=True)


class AlertRule(AlertRuleBase, table=True):
    """Configuration table for alert evaluation."""
    __tablename__ = "alert_rules"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=get_utc_now)


class AlertRuleCreate(BaseModel):
    name: str
    metric_type: str
    operator: str = ">"
    threshold_value: float
    window_minutes: int = 5
    webhook_url: Optional[str] = None
    is_active: bool = True

    model_config = ConfigDict(extra="ignore")

    _VALID_OPERATORS: ClassVar[FrozenSet[str]] = frozenset({">", ">=", "<", "<=", "==", "!="})

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        stripped = str(v).strip()
        if not stripped:
            raise ValueError("Alert rule name must not be empty")
        return stripped

    @field_validator("operator", mode="before")
    @classmethod
    def validate_operator(cls, v: str) -> str:
        op = str(v).strip()
        if op not in cls._VALID_OPERATORS:
            raise ValueError(f"Invalid operator '{v}'. Must be one of: {sorted(cls._VALID_OPERATORS)}")
        return op

    @field_validator("webhook_url", mode="before")
    @classmethod
    def normalize_webhook_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        stripped = str(v).strip()
        return stripped if stripped else None



class AlertRuleRead(AlertRuleBase):
    id: int
    created_at: datetime


class AlertHistoryBase(SQLModel):
    rule_id: int = Field(foreign_key="alert_rules.id", index=True)
    rule_name: str
    triggered_value: float
    message: str
    sent_successfully: bool = True


class AlertHistory(AlertHistoryBase, table=True):
    """Log table for triggered alerts."""
    __tablename__ = "alert_histories"

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=get_utc_now, index=True)


class AlertHistoryRead(AlertHistoryBase):
    id: int
    created_at: datetime
