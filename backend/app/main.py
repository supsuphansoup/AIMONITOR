import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request: Request, exc: RequestValidationError):
    """Keep the usual 422 field locations without echoing submitted private values."""
    errors = [{"loc": err["loc"], "type": err["type"], "msg": "Invalid field value"} for err in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})

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
    request_id = request.state.request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.error(
            "request_id=%s %s %s failed after %.1fms (%s)",
            request_id,
            request.method,
            request.url.path,
            elapsed_ms,
            type(exc).__name__,
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


class RequestIdMiddleware:
    """Attach an id even when CORS or an early guard answers without routing.

    This outer observer only annotates responses; all error-producing layers
    remain inside CORS.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = Headers(scope=scope).get("x-request-id") or uuid.uuid4().hex[:16]
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        await self.app(scope, receive, send_with_id)


class RequestBodyLimitMiddleware:
    """Count received bytes before passing chunks to the JSON body buffer."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        detail = f"Request body exceeds the {settings.MAX_REQUEST_BYTES} byte limit."
        declared = Headers(scope=scope).get("content-length")
        if declared is not None:
            try:
                if not declared.isascii() or not declared.isdecimal():
                    raise ValueError
                length = int(declared)
            except ValueError:
                response = JSONResponse(status_code=400, content={"detail": "Malformed Content-Length."})
                await response(scope, receive, send)
                return
            if length > settings.MAX_REQUEST_BYTES:
                response = JSONResponse(status_code=413, content={"detail": detail},
                                        headers={"Connection": "close"})
                await response(scope, receive, send)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > settings.MAX_REQUEST_BYTES:
                    # FastAPI re-raises HTTPException from body parsing so its
                    # exception handler builds the 413 inside CORS. Do not drain
                    # an unbounded stream; close the HTTP/1 connection instead.
                    raise HTTPException(413, detail=detail, headers={"Connection": "close"})
            return message

        await self.app(scope, limited_receive, send)


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOCAL_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def _hostname(host_header: str) -> str:
    """Host header without its port; an IPv6 literal loses its brackets."""
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _host_allowed(headers: Headers) -> bool:
    host = _hostname(headers.get("host", ""))
    return host in LOCAL_HOSTNAMES or host in settings.allowed_host_list


class BrowserCORSMiddleware(CORSMiddleware):
    def preflight_response(self, request_headers: Headers) -> Response:
        response = super().preflight_response(request_headers)
        if not _host_allowed(request_headers):
            headers = {key: value for key, value in response.headers.items()
                       if key not in ("content-length", "content-type")}
            return JSONResponse(status_code=403, content={"detail": "Untrusted Host header."},
                                headers=headers)
        return response


async def reject_browser_attacks(request: Request, call_next):
    """Keep web pages from driving this API through the user's browser.

    CORS only hides responses. Any site can still send a simple POST to
    http://127.0.0.1:8000 (say, a purge), or point its own domain at 127.0.0.1
    and read everything as same-origin. Browsers always send Origin on
    cross-origin writes and keep the attacker's name in Host; the desktop app
    and the SDK send no Origin and address localhost.
    """
    origin = request.headers.get("origin")
    if (
        origin is not None
        and request.method not in SAFE_METHODS
        and not settings.cors_allows_wildcard
        and origin.rstrip("/") not in settings.cors_origin_list
    ):
        logger.warning("Rejected cross-origin %s %s", request.method, request.url.path)
        return JSONResponse(status_code=403, content={"detail": "Cross-origin request rejected."})
    if not _host_allowed(request.headers):
        logger.warning("Rejected %s %s for an untrusted host name", request.method, request.url.path)
        return JSONResponse(status_code=403, content={"detail": "Untrusted Host header."})
    return await call_next(request)


app.add_middleware(RequestBodyLimitMiddleware)
app.add_middleware(BaseHTTPMiddleware, dispatch=request_context)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(BaseHTTPMiddleware, dispatch=reject_browser_attacks)

# CORS. A wildcard origin and credentials are mutually exclusive per the CORS
# spec; sending both makes browsers drop the response, so credentials are
# switched off whenever the origin list is a wildcard.
origins = settings.cors_origin_list
if origins:
    app.add_middleware(
        BrowserCORSMiddleware,
        allow_origins=origins,
        allow_credentials=not settings.cors_allows_wildcard,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-Total-Count"],
    )

app.add_middleware(RequestIdMiddleware)


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
