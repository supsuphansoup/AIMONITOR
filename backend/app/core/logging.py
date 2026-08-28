"""Logging setup.

Deliberately narrow: AIMONITOR must never persist prompt or response text, and
that includes log lines. Only metadata about a request is ever emitted here.
"""
import logging
import sys

from backend.app.core.config import settings

_LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
_configured = False


def configure_logging() -> None:
    """Install a single stdout handler at the configured level (idempotent)."""
    global _configured
    if _configured:
        return

    level = getattr(logging, settings.LOG_LEVEL, logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))

    root = logging.getLogger()
    root.setLevel(level)
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)

    # uvicorn installs its own access log; ours carries the request id instead.
    logging.getLogger("uvicorn.access").propagate = False
    _configured = True
