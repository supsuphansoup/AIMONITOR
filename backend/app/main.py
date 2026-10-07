import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.app.core.config import settings
from backend.app.core.logging import configure_logging
from backend.app.core.security import warn_if_insecure
from backend.app.db.session import engine, init_db
from backend.app.routers.alerts import router as alerts_router
from backend.app.routers.api_keys import router as api_keys_router
from backend.app.routers.metrics import router as metrics_router
from backend.app.routers.system import router as system_router
from backend.app.routers.traces import router as traces_router
from backend.app.services.scheduler import scheduler

configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    warn_if_insecure()
    scheduler.start()
    logger.info(
        "%s %s ready (auth=%s, retention=%s)",
        settings.PROJECT_NAME,
        settings.VERSION,
        "on" if settings.auth_enabled else "off",
        f"{settings.RETENTION_DAYS}d" if settings.RETENTION_DAYS > 0 else "off",
    )
    yield
    await scheduler.stop()
    engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Real-time AI/LLM Observability and Monitoring Engine",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc",
    lifespan=lifespan,
)

# Middleware order matters, and `add_middleware` makes the LAST registration the
# outermost layer. These are registered innermost-first so that CORS ends up
# outermost: an error response produced inside still picks up its headers on the
# way out, without which a cross-origin client sees an opaque network failure
# instead of the actual status.
async def request_context(request: Request, call_next):
    """Tag every request with an id and log its outcome.

    Only method, path, status, and duration are recorded - never a request body,
    which for this service would mean logging model metadata a caller asked us
    to keep out of persistent storage.
    """
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.exception(
            "request_id=%s %s %s failed after %.1fms",
            request_id,
            request.method,
            request.url.path,
            elapsed_ms,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error.", "request_id": request_id},
            headers={"X-Request-ID": request_id},
        )

    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Request-ID"] = request_id
    if request.url.path.startswith(settings.API_V1_STR):
        logger.info(
            "request_id=%s %s %s -> %d (%.1fms)",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
    return response


async def enforce_body_limit(request: Request, call_next):
    """Reject oversized bodies before they are buffered into memory.

    MAX_BATCH_SIZE only applies once the JSON has been parsed, which is too late
    to stop a single enormous payload from being read in full.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > settings.MAX_REQUEST_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": (
                            f"Request body exceeds the {settings.MAX_REQUEST_BYTES} byte limit."
                        )
                    },
                )
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Malformed Content-Length."})
    return await call_next(request)


app.add_middleware(BaseHTTPMiddleware, dispatch=request_context)
app.add_middleware(BaseHTTPMiddleware, dispatch=enforce_body_limit)
app.add_middleware(GZipMiddleware, minimum_size=1024)

# CORS. A wildcard origin and credentials are mutually exclusive per the CORS
# spec; sending both makes browsers drop the response, so credentials are
# switched off whenever the origin list is a wildcard.
origins = settings.cors_origin_list
if origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=not settings.cors_allows_wildcard,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Total-Count"],
    )


@app.get("/health", tags=["Health"])
async def health_check():
    """Liveness probe. Deliberately does not touch the database."""
    return {
        "status": "ok",
        "app": settings.PROJECT_NAME,
        "version": settings.VERSION,
    }


@app.get(f"{settings.API_V1_STR}/health", tags=["Health"])
async def api_v1_health_check():
    """API v1 liveness probe."""
    return {
        "status": "ok",
        "app": settings.PROJECT_NAME,
        "version": settings.VERSION,
    }


app.include_router(traces_router, prefix=settings.API_V1_STR)
app.include_router(metrics_router, prefix=settings.API_V1_STR)
app.include_router(alerts_router, prefix=settings.API_V1_STR)
app.include_router(api_keys_router, prefix=settings.API_V1_STR)
app.include_router(system_router, prefix=settings.API_V1_STR)
