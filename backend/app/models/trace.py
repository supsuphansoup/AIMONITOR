from datetime import datetime, timezone
from typing import ClassVar, FrozenSet, Optional, Dict, Any, List
from sqlmodel import SQLModel, Field
from pydantic import BaseModel, ConfigDict, field_validator


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TraceEventBase(SQLModel):
    """Base schema for LLM Trace Event."""
    trace_id: str = Field(index=True, description="Unique trace/request ID from client or auto-generated")
    session_id: Optional[str] = Field(default=None, index=True, description="User or session identifier")
    provider: str = Field(index=True, description="LLM Provider (e.g. openai, anthropic, gemini, ollama, custom)")
    model_name: str = Field(index=True, description="Specific model name (e.g. gpt-4o, claude-3-5-sonnet, etc.)")
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0, description="End-to-end latency in milliseconds")
    cost_usd: float = Field(default=0.0, ge=0.0, description="Calculated or provided cost in USD")
    status: str = Field(default="SUCCESS", index=True, description="SUCCESS, ERROR, TIMEOUT, RATE_LIMITED")
    error_message: Optional[str] = Field(default=None, description="Detailed error log if status is not SUCCESS")
    tags: Optional[str] = Field(default=None, description="Comma-separated or JSON tags for categorization")
    metadata_json: Optional[str] = Field(default=None, description="Raw JSON string for additional metadata")


class TraceEvent(TraceEventBase, table=True):
    """Database table model for storing trace events."""
    __tablename__ = "trace_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=get_utc_now, index=True)


class TraceEventCreate(BaseModel):
    """Payload schema for single trace event ingestion."""
    trace_id: Optional[str] = None
    session_id: Optional[str] = None
    timestamp: Optional[datetime] = None
    provider: str
    model_name: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: Optional[int] = None
    latency_ms: float = 0.0
    cost_usd: Optional[float] = None
    status: str = "SUCCESS"
    error_message: Optional[str] = None
    tags: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(extra="ignore")

    _VALID_STATUSES: ClassVar[FrozenSet[str]] = frozenset({"SUCCESS", "ERROR", "TIMEOUT", "RATE_LIMITED"})

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


class BatchTraceCreate(BaseModel):
    """Payload schema for batch trace events ingestion."""
    traces: List[TraceEventCreate]


class TraceEventRead(TraceEventBase):
    """Response schema for trace event queries."""
    id: int
    timestamp: datetime
