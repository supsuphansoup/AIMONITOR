import json
import uuid
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select, col

from backend.app.db.session import get_session
from backend.app.models.trace import (
    TraceEvent,
    TraceEventCreate,
    TraceEventRead,
    BatchTraceCreate,
)
from backend.app.services.cost import calculate_cost

router = APIRouter(prefix="/traces", tags=["Traces"])


def process_single_trace_create(payload: TraceEventCreate) -> TraceEvent:
    """Transform TraceEventCreate into TraceEvent entity with calculated fields."""
    trace_id = payload.trace_id or str(uuid.uuid4())
    total_tokens = (
        payload.total_tokens
        if payload.total_tokens is not None
        else (payload.prompt_tokens + payload.completion_tokens)
    )
    
    cost_usd = calculate_cost(
        provider=payload.provider,
        model_name=payload.model_name,
        prompt_tokens=payload.prompt_tokens,
        completion_tokens=payload.completion_tokens,
        custom_cost_usd=payload.cost_usd,
    )

    tags_str = ",".join(payload.tags) if payload.tags else None
    metadata_str = json.dumps(payload.metadata) if payload.metadata else None

    kwargs = {
        "trace_id": trace_id,
        "session_id": payload.session_id,
        "provider": payload.provider,
        "model_name": payload.model_name,
        "prompt_tokens": max(0, payload.prompt_tokens),
        "completion_tokens": max(0, payload.completion_tokens),
        "total_tokens": max(0, total_tokens),
        "latency_ms": max(0.0, payload.latency_ms),
        "cost_usd": cost_usd,
        "status": payload.status.upper(),
        "error_message": payload.error_message,
        "tags": tags_str,
        "metadata_json": metadata_str,
    }
    
    if payload.timestamp:
        kwargs["timestamp"] = payload.timestamp

    return TraceEvent(**kwargs)


@router.post("", response_model=TraceEventRead, status_code=status.HTTP_201_CREATED)
def ingest_trace(
    payload: TraceEventCreate,
    session: Session = Depends(get_session),
):
    """Ingest a single LLM trace event with transactional safety."""
    event = process_single_trace_create(payload)
    try:
        session.add(event)
        session.commit()
        session.refresh(event)
        return TraceEventRead.model_validate(event)
    except Exception as e:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to ingest trace: {str(e)}",
        )


@router.post("/batch", response_model=List[TraceEventRead], status_code=status.HTTP_201_CREATED)
def ingest_batch_traces(
    payload: BatchTraceCreate,
    session: Session = Depends(get_session),
):
    """Ingest multiple LLM trace events in a single batch transaction."""
    events = [process_single_trace_create(item) for item in payload.traces]
    try:
        for event in events:
            session.add(event)
        session.commit()
        for event in events:
            session.refresh(event)
        return [TraceEventRead.model_validate(e) for e in events]
    except Exception as e:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to ingest batch traces: {str(e)}",
        )


@router.get("", response_model=List[TraceEventRead])
def list_traces(
    session: Session = Depends(get_session),
    provider: Optional[str] = Query(None, description="Filter by provider"),
    model_name: Optional[str] = Query(None, description="Filter by model name"),
    status: Optional[str] = Query(None, description="Filter by status (SUCCESS, ERROR, etc.)"),
    session_id: Optional[str] = Query(None, description="Filter by session ID"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Query and list recent trace events with pagination and filters."""
    query = select(TraceEvent).order_by(col(TraceEvent.timestamp).desc())

    if provider:
        query = query.where(TraceEvent.provider == provider)
    if model_name:
        query = query.where(TraceEvent.model_name == model_name)
    if status:
        query = query.where(TraceEvent.status == status.upper())
    if session_id:
        query = query.where(TraceEvent.session_id == session_id)

    query = query.offset(offset).limit(limit)
    return session.exec(query).all()


@router.get("/{trace_id}", response_model=TraceEventRead)
def get_trace_detail(
    trace_id: str,
    session: Session = Depends(get_session),
):
    """Fetch details of a specific trace event by trace_id."""
    query = select(TraceEvent).where(TraceEvent.trace_id == trace_id)
    event = session.exec(query).first()
    if not event:
        raise HTTPException(status_code=404, detail=f"Trace with ID '{trace_id}' not found")
    return event
