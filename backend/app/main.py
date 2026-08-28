import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.core.config import settings
from backend.app.core.logging import configure_logging
from backend.app.core.security import warn_if_insecure
from backend.app.db.session import engine, init_db
from backend.app.routers.alerts import router as alerts_router
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
        expose_headers=["X-Request-ID"],
    )


@app.middleware("http")
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
app.include_router(system_router, prefix=settings.API_V1_STR)


# Optional static frontend mounting, for the single-container deployment.
frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if not frontend_dist.exists():
    frontend_dist = Path("frontend/dist")

if frontend_dist.exists() and (frontend_dist / "index.html").exists():
    assets_dir = frontend_dist / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        if (full_path.startswith("api/") or full_path == "api"
                or full_path in ("health", "docs", "redoc", "openapi.json")):
            raise HTTPException(status_code=404, detail="API endpoint not found")

        # Resolve inside dist so a crafted path cannot escape the build output.
        candidate = (frontend_dist / full_path).resolve()
        root = frontend_dist.resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(root / "index.html")
