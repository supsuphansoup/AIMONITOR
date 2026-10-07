from datetime import datetime
from typing import Any, ClassVar, Dict, FrozenSet, List, Optional

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator
from pydantic import Field as PydanticField
from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from backend.app.core.accounts import ACCOUNT_ID_MAX_LENGTH, ACCOUNT_ID_PATTERN
from backend.app.core.config import settings
from backend.app.core.sanitize import (
    normalize_tags,
    serialize_metadata,
    truncate_error_message,
)
from backend.app.core.timeutil import to_aware_utc, to_naive_utc, utc_now
from backend.app.services.cost import SOURCE_UNKNOWN

VALID_STATUSES: FrozenSet[str] = frozenset({"SUCCESS", "ERROR", "TIMEOUT", "RATE_LIMITED"})


class TraceEventBase(SQLModel):
    """Base schema for an LLM trace event."""
    trace_id: str = Field(
        index=True, description="Unique trace/request ID from client or auto-generated"
    )
    session_id: Optional[str] = Field(
        default=None, index=True, description="User or session identifier"
    )
    account_id: Optional[str] = Field(default=None, index=True, max_length=ACCOUNT_ID_MAX_LENGTH)
    provider: str = Field(
        index=True, description="LLM provider (openai, anthropic, gemini, ollama, custom)"
    )
    model_name: str = Field(
        index=True, description="Specific model name (e.g. gpt-4o, claude-3-5-sonnet)"
    )
    prompt_tokens: int = Field(default=0, ge=0, description="Uncached input tokens")
    completion_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(
        default=0, ge=0, description="Input tokens served from the provider prompt cache"
    )
    cache_write_tokens: int = Field(default=0, ge=0, description="Input tokens written into the prompt cache")
    cache_write_1h_tokens: int = Field(default=0, ge=0, description="1-hour subset of cache_write_tokens")
    latency_measured: bool = Field(default=True, description="Whether the source measured latency")
    total_tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0, description="End-to-end latency in milliseconds")
    cost_usd: float = Field(default=0.0, ge=0.0, description="Cost in USD; 0.0 when cost_source is 'unknown'")
    cost_source: str = Field(
        default=SOURCE_UNKNOWN,
        index=True,
        description="How cost_usd was derived: custom, pricing_table, local_free, or unknown",
    )
    status: str = Field(default="SUCCESS", index=True, description="SUCCESS, ERROR, TIMEOUT, RATE_LIMITED")
    error_message: Optional[str] = Field(default=None, description="Truncated upstream error string")
    tags: Optional[str] = Field(default=None, description="Comma-separated tags for categorization")
    metadata_json: Optional[str] = Field(
        default=None, description="Redacted JSON metadata (never prompt/response text)"
    )


class TraceEvent(TraceEventBase, table=True):
    """Database table model for storing trace events."""
    __tablename__ = "trace_events"
    __table_args__ = (
        Index("ix_trace_events_measured_timestamp", "latency_measured", "timestamp", "id"),
        Index("ix_trace_events_account_timestamp", "account_id", "timestamp"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    # Stored as naive UTC; see backend.app.core.timeutil for why.
    timestamp: datetime = Field(default_factory=utc_now, index=True)


class TraceEventCreate(BaseModel):
    """Payload schema for single trace event ingestion."""
    trace_id: Optional[str] = None
    session_id: Optional[str] = None
    account_id: Optional[str] = PydanticField(
        default=None, max_length=ACCOUNT_ID_MAX_LENGTH, pattern=ACCOUNT_ID_PATTERN
    )
    timestamp: Optional[datetime] = None
    provider: str = Field(min_length=1, max_length=64)
    model_name: str = Field(min_length=1, max_length=200)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cache_write_1h_tokens: int = 0
    total_tokens: Optional[int] = None
    latency_ms: Optional[float] = None
    cost_usd: Optional[float] = None
    status: str = "SUCCESS"
    error_message: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="ignore")

    _VALID_STATUSES: ClassVar[FrozenSet[str]] = VALID_STATUSES

    @field_validator("status", mode="before")
    @classmethod
    def normalize_status(cls, v: str) -> str:
        """Normalize status to uppercase and validate against allowed values."""
        upper = str(v).strip().upper()
        if upper not in cls._VALID_STATUSES:
            raise ValueError(
                f"Invalid status '{v}'. Must be one of: {sorted(cls._VALID_STATUSES)}"
            )
        return upper

    @field_validator("timestamp", mode="after")
    @classmethod
    def normalize_timestamp(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Convert a client timestamp to naive UTC so offsets are not dropped.

        Without this, `2026-08-28T10:00:00+09:00` was persisted as 10:00 UTC
        instead of 01:00 UTC, shifting the event nine hours into the future.
        """
        return to_naive_utc(v)

    @field_validator("error_message", mode="after")
    @classmethod
    def cap_error_message(cls, v: Optional[str]) -> Optional[str]:
        return truncate_error_message(v)

    def tags_as_string(self) -> Optional[str]:
        return normalize_tags(self.tags)

    def metadata_as_json(self) -> Optional[str]:
        return serialize_metadata(self.metadata)


class BatchTraceCreate(BaseModel):
    """Payload schema for batch trace event ingestion."""
    traces: List[TraceEventCreate] = Field(min_length=1)

    @field_validator("traces", mode="after")
    @classmethod
    def enforce_batch_limit(cls, v: List[TraceEventCreate]) -> List[TraceEventCreate]:
        limit = settings.MAX_BATCH_SIZE
        if len(v) > limit:
            raise ValueError(f"Batch too large: {len(v)} traces (max {limit} per request)")
        return v


class TraceEventRead(TraceEventBase):
    """Response schema for trace event queries."""
    id: int
    timestamp: datetime
    latency_ms: Optional[float] = None

    @field_serializer("latency_ms")
    def serialize_latency(self, value: float, _info: Any) -> Optional[float]:
        return value if self.latency_measured else None

    @field_serializer("timestamp")
    def serialize_timestamp(self, value: datetime, _info: Any) -> str:
        """Emit an explicit UTC offset so browsers do not read it as local time."""
        aware = to_aware_utc(value)
        return aware.isoformat() if aware else ""
