import logging
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, col, select

from backend.app.core.security import require_read_auth, require_write_auth
from backend.app.core.timeutil import to_naive_utc
from backend.app.db.session import get_session
from backend.app.models.trace import (
    VALID_STATUSES,
    BatchTraceCreate,
    TraceEvent,
    TraceEventCreate,
    TraceEventRead,
)
from backend.app.services.cost import calculate_cost_detail

logger = logging.getLogger(__name__)

# Starlette renamed its 422 constant between releases; the literal works on both.
HTTP_422_UNPROCESSABLE = 422

router = APIRouter(prefix="/traces", tags=["Traces"])


def process_single_trace_create(payload: TraceEventCreate) -> TraceEvent:
    """Transform a TraceEventCreate into a TraceEvent entity with derived fields."""
    prompt_tokens = max(0, payload.prompt_tokens)
    completion_tokens = max(0, payload.completion_tokens)
    cache_read_tokens = max(0, payload.cache_read_tokens)
    cache_write_tokens = max(0, payload.cache_write_tokens)

    total_tokens = (
        payload.total_tokens
        if payload.total_tokens is not None
        else prompt_tokens + completion_tokens + cache_read_tokens + cache_write_tokens
    )

    cost = calculate_cost_detail(
        provider=payload.provider,
        model_name=payload.model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        custom_cost_usd=payload.cost_usd,
    )

    kwargs = {
        "trace_id": payload.trace_id or str(uuid.uuid4()),
        "session_id": payload.session_id,
        "provider": payload.provider,
        "model_name": payload.model_name,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "total_tokens": max(0, total_tokens),
        "latency_ms": max(0.0, payload.latency_ms),
        "cost_usd": cost.cost_usd,
        "cost_source": cost.source,
        "status": payload.status.upper(),
        "error_message": payload.error_message,
        "tags": payload.tags_as_string(),
        "metadata_json": payload.metadata_as_json(),
    }

    if payload.timestamp:
        kwargs["timestamp"] = payload.timestamp

    return TraceEvent(**kwargs)


@router.post(
    "",
    response_model=TraceEventRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_write_auth)],
)
def ingest_trace(
    payload: TraceEventCreate,
    session: Session = Depends(get_session),
):
    """Ingest a single LLM trace event."""
    event = process_single_trace_create(payload)
    try:
        session.add(event)
        session.commit()
        session.refresh(event)
        return TraceEventRead.model_validate(event)
    except Exception:
        session.rollback()
        # The exception text can echo the submitted payload, so it is logged
        # server-side and never returned to the caller.
        logger.exception("Failed to ingest trace")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to ingest trace.",
        ) from None


@router.post(
    "/batch",
    response_model=List[TraceEventRead],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_write_auth)],
)
def ingest_batch_traces(
    payload: BatchTraceCreate,
    session: Session = Depends(get_session),
):
    """Ingest multiple LLM trace events in a single transaction."""
    events = [process_single_trace_create(item) for item in payload.traces]
    try:
        session.add_all(events)
        session.commit()
        for event in events:
            session.refresh(event)
        return [TraceEventRead.model_validate(e) for e in events]
    except Exception:
        session.rollback()
        logger.exception("Failed to ingest batch of %d traces", len(events))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to ingest batch traces.",
        ) from None


@router.get("", response_model=List[TraceEventRead], dependencies=[Depends(require_read_auth)])
def list_traces(
    session: Session = Depends(get_session),
    provider: Optional[str] = Query(None, description="Filter by provider"),
    model_name: Optional[str] = Query(None, description="Filter by model name"),
    status_filter: Optional[str] = Query(
        None, alias="status", description="Filter by status (SUCCESS, ERROR, TIMEOUT, RATE_LIMITED)"
    ),
    session_id: Optional[str] = Query(None, description="Filter by session ID"),
    start_time: Optional[datetime] = Query(None, description="Only traces at or after this ISO timestamp"),
    end_time: Optional[datetime] = Query(None, description="Only traces at or before this ISO timestamp"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Query recent trace events with pagination and filters."""
    query = select(TraceEvent).order_by(col(TraceEvent.timestamp).desc())

    if provider:
        query = query.where(TraceEvent.provider == provider)
    if model_name:
        query = query.where(TraceEvent.model_name == model_name)
    if status_filter:
        normalized = status_filter.strip().upper()
        if normalized not in VALID_STATUSES:
            raise HTTPException(
                status_code=HTTP_422_UNPROCESSABLE,
                detail=f"Invalid status filter. Must be one of: {sorted(VALID_STATUSES)}",
            )
        query = query.where(TraceEvent.status == normalized)
    if session_id:
        query = query.where(TraceEvent.session_id == session_id)
    if start_time:
        query = query.where(TraceEvent.timestamp >= to_naive_utc(start_time))
    if end_time:
        query = query.where(TraceEvent.timestamp <= to_naive_utc(end_time))

    query = query.offset(offset).limit(limit)
    return session.exec(query).all()


@router.get(
    "/{trace_id}", response_model=TraceEventRead, dependencies=[Depends(require_read_auth)]
)
def get_trace_detail(
    trace_id: str,
    session: Session = Depends(get_session),
):
    """Fetch the most recent trace event carrying the given trace_id."""
    query = (
        select(TraceEvent)
        .where(TraceEvent.trace_id == trace_id)
        .order_by(col(TraceEvent.timestamp).desc())
    )
    event = session.exec(query).first()
    if not event:
        raise HTTPException(status_code=404, detail=f"Trace with ID '{trace_id}' not found")
    return event
