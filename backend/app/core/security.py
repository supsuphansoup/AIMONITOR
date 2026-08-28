"""API key authentication.

AIMONITOR ingests data from application servers, so the credential model is a
shared secret rather than a user session. Set ``API_KEY`` and every mutating
endpoint requires it; leave it unset and the server runs open, which is only
appropriate on a loopback interface.
"""
import hmac
import logging
from typing import Optional

from fastapi import Header, HTTPException, status

from backend.app.core.config import settings

logger = logging.getLogger(__name__)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Missing or invalid API key.",
    headers={"WWW-Authenticate": "Bearer"},
)


def _extract_key(x_api_key: Optional[str], authorization: Optional[str]) -> Optional[str]:
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    if authorization:
        scheme, _, credential = authorization.partition(" ")
        if scheme.lower() == "bearer" and credential.strip():
            return credential.strip()
    return None


def _verify(x_api_key: Optional[str], authorization: Optional[str]) -> None:
    expected = settings.API_KEY
    if not expected:
        # Open mode: startup already warned about it.
        return
    provided = _extract_key(x_api_key, authorization)
    if provided is None or not hmac.compare_digest(provided, expected):
        raise _UNAUTHORIZED


async def require_write_auth(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
) -> None:
    """Guard for endpoints that create, modify, or delete data."""
    _verify(x_api_key, authorization)


async def require_read_auth(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None),
) -> None:
    """Guard for query endpoints, enforced only when REQUIRE_AUTH_FOR_READS is on."""
    if not settings.REQUIRE_AUTH_FOR_READS:
        return
    _verify(x_api_key, authorization)


def warn_if_insecure() -> None:
    """Emit startup warnings for configurations that are unsafe off localhost."""
    if not settings.auth_enabled:
        logger.warning(
            "API_KEY is not set - every endpoint is unauthenticated. "
            "Bind to localhost only, or set API_KEY before exposing this server."
        )
    if settings.cors_allows_wildcard:
        logger.warning(
            "CORS_ORIGINS allows '*'; credentialed cross-origin requests are disabled "
            "because browsers reject a wildcard origin together with credentials."
        )
