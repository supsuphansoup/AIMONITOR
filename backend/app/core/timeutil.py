"""UTC time helpers.

SQLite (through SQLAlchemy's ``DATETIME`` type) has no timezone column type, so
an aware ``datetime`` loses its ``tzinfo`` on the way in and comes back naive.
Mixing the two representations is what makes a dashboard render every event
shifted by the viewer's UTC offset.

The rule for this codebase: **persist naive UTC, emit aware UTC.**
"""
from datetime import datetime, timezone
from typing import Optional


def utc_now() -> datetime:
    """Current time as a naive UTC datetime, matching what the DB stores."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Normalize any datetime to naive UTC before it is persisted.

    Aware values are converted to UTC and stripped; naive values are assumed to
    already be UTC (the only thing this application ever writes).
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    try:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    except OverflowError:
        raise ValueError("Timestamp is outside the UTC range") from None


def to_aware_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Stamp a datetime read back from the DB as UTC before serialization."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def local_period_start_utc(value: datetime, month_start: bool = False) -> datetime:
    """Local midnight/month start as naive UTC, using the offset at that boundary."""
    local = to_aware_utc(value).astimezone().replace(tzinfo=None)
    boundary = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if month_start:
        boundary = boundary.replace(day=1)
    # A naive local boundary uses the system's rules for that date, not today's offset.
    return boundary.astimezone(timezone.utc).replace(tzinfo=None)
