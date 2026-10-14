import ipaddress
import re
from datetime import datetime
from socket import SOCK_STREAM, gaierror, getaddrinfo
from typing import Any, ClassVar, FrozenSet, Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, computed_field, field_serializer, field_validator
from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel

from backend.app.core.config import settings
from backend.app.core.metrics_catalog import VALID_METRIC_TYPES
from backend.app.core.timeutil import to_aware_utc, utc_now

VALID_OPERATORS: FrozenSet[str] = frozenset({">", ">=", "<", "<=", "==", "!="})

MAX_WINDOW_MINUTES = 1440
MAX_COOLDOWN_MINUTES = 10_080  # one week


class AlertRuleBase(SQLModel):
    name: str = Field(index=True)
    metric_type: str = Field(description="error_rate, latency_p95, cost_hourly, etc.")
    operator: str = Field(default=">", description=">, >=, <, <=, ==, !=")
    threshold_value: float = Field(description="Threshold value to trigger alert")
    window_minutes: int = Field(default=5, description="Rolling time window in minutes")
    cooldown_minutes: int = Field(
        default=15,
        description="Minimum gap between two firings of this rule; 0 fires on every evaluation",
    )
    webhook_url: Optional[str] = Field(default=None, description="Slack / Discord Webhook URL")
    is_active: bool = Field(default=True, index=True)


class AlertRule(AlertRuleBase, table=True):
    """Configuration table for alert evaluation."""
    __tablename__ = "alert_rules"

    id: Optional[int] = Field(default=None, primary_key=True)
    # sa_type keeps naive-UTC storage on SQLModel>=0.0.48 (see models/trace.py).
    created_at: datetime = Field(default_factory=utc_now, sa_type=DateTime)
    last_triggered_at: Optional[datetime] = Field(
        default=None, sa_type=DateTime,
        description="Naive UTC time of the most recent firing; drives the cooldown",
    )


def _validate_operator(v: str) -> str:
    op = str(v).strip()
    if op not in VALID_OPERATORS:
        raise ValueError(f"Invalid operator '{v}'. Must be one of: {sorted(VALID_OPERATORS)}")
    return op


def _validate_metric_type(v: str) -> str:
    metric = str(v).strip().lower()
    if metric not in VALID_METRIC_TYPES:
        raise ValueError(
            f"Unknown metric_type '{v}'. Must be one of: {sorted(VALID_METRIC_TYPES)}"
        )
    return metric


def _validate_webhook_url(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    stripped = str(v).strip()
    if not stripped:
        return None
    if "…" in stripped:
        raise ValueError("A masked webhook_url cannot be saved; omit it to keep the current URL")
    try:
        parsed = urlsplit(stripped)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("webhook_url has an invalid host or port") from None
    if parsed.scheme not in ("http", "https") or not host:
        raise ValueError("webhook_url must be an absolute http:// or https:// URL")
    if any(ord(char) < 32 or ord(char) == 127 for char in stripped):
        raise ValueError("webhook_url must not contain control characters")
    allow_private = settings.ALERT_ALLOW_PRIVATE_WEBHOOKS
    if not allow_private and (parsed.username is not None or parsed.password is not None):
        raise ValueError("webhook_url must not contain userinfo")

    address = _webhook_ip(host.rstrip("."))
    if address is not None:
        addresses = [address]
        canonical_host = str(address)
        if ":" in canonical_host:
            canonical_host = f"[{canonical_host}]"
        userinfo = parsed.netloc.rsplit("@", 1)[0] + "@" if "@" in parsed.netloc else ""
        netloc = userinfo + canonical_host + (f":{port}" if port is not None else "")
        stripped = parsed._replace(netloc=netloc).geturl()
    else:
        try:
            answers = getaddrinfo(host, port or (443 if parsed.scheme == "https" else 80), type=SOCK_STREAM)
            addresses = [ipaddress.ip_address(answer[4][0].split("%", 1)[0]) for answer in answers]
        except (gaierror, ValueError, UnicodeError, OSError):
            raise ValueError("webhook_url host could not be resolved") from None
        if not addresses:
            raise ValueError("webhook_url host could not be resolved")
    if not allow_private:
        for address in addresses:
            address = getattr(address, "ipv4_mapped", None) or address
            if (not address.is_global or address.is_multicast or address.is_reserved
                    or getattr(address, "is_site_local", False)):
                raise ValueError("webhook_url must resolve only to public unicast addresses")
    return stripped


def _webhook_ip(host: str) -> Optional[Any]:
    """Recognize IPv6 and legacy IPv4 forms before the OS resolver can reinterpret them."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    parts = host.split(".")
    if not 1 <= len(parts) <= 4 or not all(re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)", p) for p in parts):
        return None
    try:
        numbers = [int(p, 16 if p.lower().startswith("0x") else 8 if len(p) > 1 and p[0] == "0" else 10)
                   for p in parts]
    except ValueError:
        raise ValueError("webhook_url contains an invalid IP address") from None
    if any(n > 255 for n in numbers[:-1]) or numbers[-1] >= 1 << (8 * (5 - len(numbers))):
        raise ValueError("webhook_url contains an invalid IP address")
    number = numbers[-1]
    for index, part in enumerate(numbers[:-1]):
        number += part << (8 * (3 - index))
    return ipaddress.IPv4Address(number)


class AlertRuleCreate(BaseModel):
    name: str
    metric_type: str
    operator: str = ">"
    threshold_value: float
    window_minutes: int = Field(default=5, ge=1, le=MAX_WINDOW_MINUTES)
    cooldown_minutes: int = Field(default=15, ge=0, le=MAX_COOLDOWN_MINUTES)
    webhook_url: Optional[str] = None
    is_active: bool = True

    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    _VALID_OPERATORS: ClassVar[FrozenSet[str]] = VALID_OPERATORS

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        if v is None:
            raise ValueError("Alert rule name must not be null")
        stripped = str(v).strip()
        if not stripped:
            raise ValueError("Alert rule name must not be empty")
        return stripped[:200]

    @field_validator("operator", mode="before")
    @classmethod
    def validate_operator(cls, v: str) -> str:
        return _validate_operator(v)

    @field_validator("metric_type", mode="before")
    @classmethod
    def validate_metric_type(cls, v: str) -> str:
        return _validate_metric_type(v)

    @field_validator("webhook_url", mode="before")
    @classmethod
    def normalize_webhook_url(cls, v: Optional[str]) -> Optional[str]:
        return _validate_webhook_url(v)


class AlertRuleUpdate(BaseModel):
    """Partial update; omitted fields are left untouched."""
    name: Optional[str] = None
    metric_type: Optional[str] = None
    operator: Optional[str] = None
    threshold_value: Optional[float] = None
    window_minutes: Optional[int] = Field(default=None, ge=1, le=MAX_WINDOW_MINUTES)
    cooldown_minutes: Optional[int] = Field(default=None, ge=0, le=MAX_COOLDOWN_MINUTES)
    webhook_url: Optional[str] = None
    is_active: Optional[bool] = None

    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)

    @field_validator(
        "name", "metric_type", "operator", "threshold_value", "window_minutes",
        "cooldown_minutes", "is_active", mode="before",
    )
    @classmethod
    def reject_explicit_null(cls, v: Any) -> Any:
        if v is None:
            raise ValueError("This field must not be null; omit it to keep the current value")
        return v

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        stripped = str(v).strip()
        if not stripped:
            raise ValueError("Alert rule name must not be empty")
        return stripped[:200]

    @field_validator("operator", mode="before")
    @classmethod
    def validate_operator(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _validate_operator(v)

    @field_validator("metric_type", mode="before")
    @classmethod
    def validate_metric_type(cls, v: Optional[str]) -> Optional[str]:
        return None if v is None else _validate_metric_type(v)

    @field_validator("webhook_url", mode="before")
    @classmethod
    def normalize_webhook_url(cls, v: Optional[str]) -> Optional[str]:
        return _validate_webhook_url(v)


class AlertRuleRead(AlertRuleBase):
    id: int
    created_at: datetime
    last_triggered_at: Optional[datetime] = None

    @computed_field
    @property
    def webhook_configured(self) -> bool:
        return bool(self.webhook_url)

    @field_serializer("webhook_url")
    def mask_webhook_url(self, value: Optional[str], _info: Any) -> Optional[str]:
        if not value:
            return None
        try:
            parsed = urlsplit(value)
            host = parsed.hostname
            if parsed.scheme in ("http", "https") and host:
                host = f"[{host}]" if ":" in host else host
                return f"{parsed.scheme}://{host}/…"
        except ValueError:
            pass
        return "[configured]"

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime, _info: Any) -> str:
        aware = to_aware_utc(value)
        return aware.isoformat() if aware else ""

    @field_serializer("last_triggered_at")
    def serialize_last_triggered_at(self, value: Optional[datetime], _info: Any) -> Optional[str]:
        aware = to_aware_utc(value)
        return aware.isoformat() if aware else None


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
    created_at: datetime = Field(default_factory=utc_now, index=True, sa_type=DateTime)


class AlertHistoryRead(AlertHistoryBase):
    id: int
    created_at: datetime

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime, _info: Any) -> str:
        aware = to_aware_utc(value)
        return aware.isoformat() if aware else ""
