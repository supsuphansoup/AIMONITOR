"""Operational endpoints: readiness, runtime configuration, pricing coverage."""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

from backend.app.core.config import settings
from backend.app.core.security import require_read_auth, require_write_auth
from backend.app.db.session import check_db, get_session
from backend.app.services.cost import (
    LOCAL_PROVIDERS,
    MODEL_PRICING_USD_PER_1M,
    PRICING_AS_OF,
    PRICING_SOURCES,
    PROVIDER_CACHE_MULTIPLIERS,
)
from backend.app.services.retention import purge_older_than
from backend.app.services.scheduler import scheduler

# Starlette renamed its 422 constant between releases; the literal works on both.
HTTP_422_UNPROCESSABLE = 422

router = APIRouter(prefix="/system", tags=["System"])


@router.get("/info", dependencies=[Depends(require_read_auth)])
def system_info() -> Dict[str, Any]:
    """Runtime configuration, for the dashboard and for support requests."""
    return {
        "app": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
        "auth_enabled": settings.auth_enabled,
        "auth_required_for_reads": settings.REQUIRE_AUTH_FOR_READS,
        "cors_origins": settings.cors_origin_list,
        "max_batch_size": settings.MAX_BATCH_SIZE,
        "retention_days": settings.RETENTION_DAYS,
        "scheduler": scheduler.status(),
        "priced_models": len(MODEL_PRICING_USD_PER_1M),
    }


@router.get("/pricing", dependencies=[Depends(require_read_auth)])
def pricing_table() -> Dict[str, Any]:
    """The rate card used for cost calculation, so numbers can be audited."""
    return {
        "unit": "USD per 1,000,000 tokens",
        "as_of": PRICING_AS_OF,
        "sources": PRICING_SOURCES,
        # Self-hosted inference is free by provider, whatever the model name.
        "local_free_providers": sorted(LOCAL_PROVIDERS),
        "provider_cache_multipliers": {
            provider: {"cache_write": write, "cache_read": read}
            for provider, (write, read) in PROVIDER_CACHE_MULTIPLIERS.items()
        },
        "models": {
            name: {
                "input": pricing.input_per_1m,
                "output": pricing.output_per_1m,
                "cache_write": pricing.cache_write_per_1m,
                "cache_read": pricing.cache_read_per_1m,
            }
            for name, pricing in MODEL_PRICING_USD_PER_1M.items()
        },
    }


@router.post("/purge", dependencies=[Depends(require_write_auth)])
def purge_old_data(
    days: int,
    session: Session = Depends(get_session),
) -> Dict[str, int]:
    """Delete traces and alert history older than `days`.

    Irreversible, so it is an explicit call rather than something the dashboard
    triggers on its own.
    """
    if days < 1:
        raise HTTPException(
            status_code=HTTP_422_UNPROCESSABLE,
            detail="days must be at least 1.",
        )
    return purge_older_than(session, days)


@router.get("/ready", tags=["Health"])
def readiness() -> Dict[str, Any]:
    """Readiness probe: fails when the database is unreachable."""
    if not check_db():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not reachable.",
        )
    return {"status": "ready", "database": "ok"}
