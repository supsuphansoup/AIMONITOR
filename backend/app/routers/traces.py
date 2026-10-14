import csv
import io
import logging
import uuid
from datetime import datetime
from typing import Any, Iterator, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, col, select

from backend.app.core.accounts import ACCOUNT_ID_MAX_LENGTH, ACCOUNT_ID_PATTERN, validate_account_filter
from backend.app.core.security import require_read_auth, require_write_auth
from backend.app.core.timeutil import to_aware_utc, to_naive_utc, utc_now
from backend.app.db.session import get_session
from backend.app.models.trace import (
    VALID_STATUSES,
    BatchTraceCreate,
    TraceEvent,
    TraceEventCreate,
    TraceEventRead,
)
from backend.app.services.cost import SOURCE_UNKNOWN, calculate_cost_detail

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/traces", tags=["Traces"])


def _utc_filter_time(value: datetime, field: str) -> Optional[datetime]:
    try:
        return to_naive_utc(value)
    except (OverflowError, ValueError):
        raise RequestValidationError([{"type": "datetime_parsing", "loc": ["query", field]}]) from None


def _query_status(value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in VALID_STATUSES:
        raise RequestValidationError([{"type": "value_error", "loc": ["query", "status"]}])
    return normalized


def process_single_trace_create(payload: TraceEventCreate) -> TraceEvent:
    """Transform a TraceEventCreate into a TraceEvent entity with derived fields."""
    prompt_tokens = max(0, payload.prompt_tokens)
    completion_tokens = max(0, payload.completion_tokens)
    cache_read_tokens = max(0, payload.cache_read_tokens)
    cache_write_tokens = max(0, payload.cache_write_tokens)
    cache_write_1h_tokens = min(max(0, payload.cache_write_1h_tokens), cache_write_tokens)

    total_tokens = (
        payload.total_tokens
        if payload.total_tokens is not None
        else prompt_tokens + completion_tokens + cache_read_tokens + cache_write_tokens
    )

    # One recorded time prices the call and is stored with it, so a backfilled
    # record uses the rate in effect when it happened, not when it arrived.
    recorded_at = payload.timestamp or utc_now()

    cost = calculate_cost_detail(
        provider=payload.provider,
        model_name=payload.model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        cache_write_1h_tokens=cache_write_1h_tokens,
        custom_cost_usd=payload.cost_usd,
        timestamp=recorded_at,
    )

    kwargs = {
        "trace_id": payload.trace_id or str(uuid.uuid4()),
        "session_id": payload.session_id,
        "dedupe_key": payload.dedupe_key,
        "account_id": payload.account_id,
        "provider": payload.provider,
        "model_name": payload.model_name,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "cache_write_1h_tokens": cache_write_1h_tokens,
        "total_tokens": max(0, total_tokens),
        "latency_ms": max(0.0, payload.latency_ms) if payload.latency_ms is not None else 0.0,
        "latency_measured": payload.latency_ms is not None,
        "cost_usd": cost.cost_usd,
        "cost_source": cost.source,
        "status": payload.status.upper(),
        "error_message": payload.error_message,
        "tags": payload.tags_as_string(),
        "metadata_json": payload.metadata_as_json(),
        "timestamp": recorded_at,
    }

    return TraceEvent(**kwargs)


def _dedupe_insert_statement(event: TraceEvent, dialect_name: str) -> Any:
    insert = {"sqlite": sqlite_insert, "postgresql": postgresql_insert}[dialect_name]
    return insert(TraceEvent).values(**event.model_dump(exclude={"id"})).on_conflict_do_nothing(
        index_elements=[TraceEvent.dedupe_key], index_where=col(TraceEvent.dedupe_key).is_not(None),
    )


def _store_trace(payload: TraceEventCreate, session: Session) -> TraceEvent:
    event = process_single_trace_create(payload)
    if event.dedupe_key is None:
        session.add(event)
        return event
    # No read-before-write race or savepoint release outside the batch transaction.
    session.flush()
    session.execute(_dedupe_insert_statement(event, session.get_bind().dialect.name))
    return session.exec(select(TraceEvent).where(TraceEvent.dedupe_key == event.dedupe_key)).one()


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
    try:
        event = _store_trace(payload, session)
        session.flush()
        session.refresh(event)
        response = TraceEventRead.model_validate(event)
        session.commit()
        return response
    except Exception as exc:
        session.rollback()
        logger.error("Ingest failed code=INGEST_FAILED error_type=%s count=1", type(exc).__name__)
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
    try:
        events = [_store_trace(item, session) for item in payload.traces]
        session.flush()
        for event in events:
            session.refresh(event)
        responses = [TraceEventRead.model_validate(e) for e in events]
        session.commit()
        return responses
    except Exception as exc:
        session.rollback()
        logger.error(
            "Ingest failed code=INGEST_BATCH_FAILED error_type=%s count=%d",
            type(exc).__name__, len(payload.traces),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to ingest batch traces.",
        ) from None


@router.get("", response_model=List[TraceEventRead], dependencies=[Depends(require_read_auth)])
def list_traces(
    response: Response,
    session: Session = Depends(get_session),
    provider: Optional[str] = Query(None, description="Filter by provider"),
    model_name: Optional[str] = Query(None, description="Filter by model name"),
    status_filter: Optional[str] = Query(
        None, alias="status", description="Filter by status (SUCCESS, ERROR, TIMEOUT, RATE_LIMITED)"
    ),
    session_id: Optional[str] = Query(None, description="Filter by session ID"),
    account_id: Optional[str] = Query(None, max_length=ACCOUNT_ID_MAX_LENGTH, pattern=ACCOUNT_ID_PATTERN),
    account_unknown: bool = Query(False, description="Only records with unknown account"),
    start_time: Optional[datetime] = Query(None, description="Only traces at or after this ISO timestamp"),
    end_time: Optional[datetime] = Query(None, description="Only traces at or before this ISO timestamp"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Query recent trace events with pagination and filters.

    The total number of matching rows is returned in `X-Total-Count`, so a
    client can render real page numbers instead of guessing from page fullness.
    """
    validate_account_filter(account_id, account_unknown)
    filters = []
    if provider:
        filters.append(TraceEvent.provider == provider)
    if model_name:
        filters.append(TraceEvent.model_name == model_name)
    if status_filter:
        filters.append(TraceEvent.status == _query_status(status_filter))
    if session_id:
        filters.append(TraceEvent.session_id == session_id)
    if account_id is not None:
        filters.append(TraceEvent.account_id == account_id)
    if account_unknown:
        filters.append(col(TraceEvent.account_id).is_(None))
    if start_time:
        filters.append(TraceEvent.timestamp >= _utc_filter_time(start_time, "start_time"))
    if end_time:
        filters.append(TraceEvent.timestamp <= _utc_filter_time(end_time, "end_time"))

    total = session.exec(select(func.count(col(TraceEvent.id))).where(*filters)).one()
    response.headers["X-Total-Count"] = str(total or 0)

    query = (
        select(TraceEvent)
        .where(*filters)
        .order_by(col(TraceEvent.timestamp).desc(), col(TraceEvent.id).desc())
        .offset(offset)
        .limit(limit)
    )
    return session.exec(query).all()


#: Leading characters a spreadsheet treats as the start of a formula.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_text(value: Optional[str]) -> str:
    """Quote caller-chosen text so a spreadsheet shows it instead of running it.

    Model names, tags and IDs come from whoever holds the ingest key; a value
    like `=HYPERLINK(...)` would otherwise execute when the export is opened.
    """
    if not value:
        return ""
    return f"'{value}" if value.startswith(_FORMULA_PREFIXES) else value


CSV_COLUMNS = [
    "id", "trace_id", "timestamp", "session_id", "provider", "model_name",
    "prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_write_tokens",
    "total_tokens", "latency_ms", "cost_usd", "cost_source", "status", "tags", "account_id",
]


@router.get("/export.csv", dependencies=[Depends(require_read_auth)])
def export_traces_csv(
    session: Session = Depends(get_session),
    account_id: Optional[str] = Query(None, max_length=ACCOUNT_ID_MAX_LENGTH, pattern=ACCOUNT_ID_PATTERN),
    account_unknown: bool = Query(False, description="Only records with unknown account"),
    provider: Optional[str] = Query(None),
    model_name: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(
        None, alias="status", description="Filter by status (SUCCESS, ERROR, TIMEOUT, RATE_LIMITED)"
    ),
    start_time: Optional[datetime] = Query(None),
    end_time: Optional[datetime] = Query(None),
    limit: int = Query(10_000, ge=1, le=100_000),
):
    """Stream matching traces as CSV, for cost reporting and spreadsheets.

    `error_message` and `metadata_json` are omitted: an export is the easiest way
    for trace content to escape into a shared file, and neither column is needed
    to reconcile spend.
    """
    validate_account_filter(account_id, account_unknown)
    filters = []
    if account_id is not None:
        filters.append(TraceEvent.account_id == account_id)
    if account_unknown:
        filters.append(col(TraceEvent.account_id).is_(None))
    if provider:
        filters.append(TraceEvent.provider == provider)
    if model_name:
        filters.append(TraceEvent.model_name == model_name)
    if status_filter:
        filters.append(TraceEvent.status == _query_status(status_filter))
    if start_time:
        filters.append(TraceEvent.timestamp >= _utc_filter_time(start_time, "start_time"))
    if end_time:
        filters.append(TraceEvent.timestamp <= _utc_filter_time(end_time, "end_time"))

    # sqlite3 legacy mode does not BEGIN for SELECT. Keep the count and streamed
    # rows in one read snapshot, owned by the request Session until streaming ends.
    connection = session.connection()
    if connection.dialect.name == "sqlite" and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN")

    total = session.exec(select(func.count(col(TraceEvent.id))).where(*filters)).one() or 0
    query = (
        select(TraceEvent)
        .where(*filters)
        .order_by(col(TraceEvent.timestamp).desc(), col(TraceEvent.id).desc())
        .limit(limit)
    )

    def rows() -> Iterator[str]:
        yield "\ufeff"
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        yield buffer.getvalue()

        # Streamed in chunks so a large export never assembles the whole file in
        # memory on either side.
        for event in session.exec(query).yield_per(500):
            buffer.seek(0)
            buffer.truncate(0)
            aware = to_aware_utc(event.timestamp)
            writer.writerow([
                event.id, _csv_text(event.trace_id), aware.isoformat() if aware else "",
                _csv_text(event.session_id), _csv_text(event.provider), _csv_text(event.model_name),
                event.prompt_tokens, event.completion_tokens,
                event.cache_read_tokens, event.cache_write_tokens,
                event.total_tokens, event.latency_ms if event.latency_measured else "",
                # An unpriced call exports as empty, not 0, so a spreadsheet sum
                # cannot quietly treat "we do not know" as "it was free".
                "" if event.cost_source == SOURCE_UNKNOWN else event.cost_usd,
                event.cost_source, event.status, _csv_text(event.tags),
                _csv_text(event.account_id),
            ])
            yield buffer.getvalue()

    filename = f"aimonitor-traces-{utc_now().strftime('%Y%m%d-%H%M%S')}.csv"
    return StreamingResponse(
        rows(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Total-Count": str(total),
            "X-Export-Partial": "true" if total > limit else "false",
        },
    )


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
        .order_by(col(TraceEvent.timestamp).desc(), col(TraceEvent.id).desc())
        .limit(1)
    )
    event = session.exec(query).first()
    if not event:
        raise HTTPException(status_code=404, detail=f"Trace with ID '{trace_id}' not found")
    return event
