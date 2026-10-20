"""
Access control for the Nobi API.

Two rules, in order of importance:

1. **Mutating endpoints are never open to the world.** Before this module the
   dashboard accepted ``POST /api/paper/order`` and ``POST /api/paper/halt``
   from any origin with ``CORS: *``.
2. **Defaults stay usable in development.** If ``NBX_API_TOKEN`` is not set,
   mutations are allowed only from loopback addresses, so a local dashboard
   keeps working while a public deployment fails closed.

Configuration
-------------
``NBX_API_TOKEN``       Shared secret. When set, every mutating request must
                        send ``X-API-Token: <token>`` (or a Bearer token).
``NBX_CORS_ORIGINS``    Comma separated allow-list. Empty = same-origin only.
                        Use ``null`` to also allow ``file://`` (standalone
                        mobile build).
``NBX_RATE_LIMIT``      Mutating requests per minute per client (default 60).
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from collections import deque

from fastapi import HTTPException, Request, status

log = logging.getLogger("nbx-security")

API_TOKEN = os.getenv("NBX_API_TOKEN", "").strip()
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}


def cors_origins() -> list[str]:
    raw = os.getenv("NBX_CORS_ORIGINS", "").strip()
    if not raw:
        return []
    return [o.strip() for o in raw.split(",") if o.strip()]


def auth_mode() -> str:
    if API_TOKEN:
        return "token"
    return "loopback-only"


def _client_host(request: Request) -> str:
    host = request.client.host if request.client else ""
    return host or ""


def _presented_token(request: Request) -> str:
    token = request.headers.get("x-api-token", "")
    if token:
        return token
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


# --------------------------------------------------------------------------- #
# Rate limiting (per client, per minute, in-process)
# --------------------------------------------------------------------------- #
_RATE_LIMIT = max(1, int(os.getenv("NBX_RATE_LIMIT", "60")))
_hits: dict[str, deque[float]] = {}


def _rate_ok(key: str) -> bool:
    now = time.monotonic()
    window = _hits.setdefault(key, deque())
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= _RATE_LIMIT:
        return False
    window.append(now)
    return True


def reset_rate_limits() -> None:
    _hits.clear()


async def require_client(request: Request) -> None:
    """FastAPI dependency guarding every state-changing endpoint."""
    host = _client_host(request)
    if not _rate_ok(f"{host}:{request.url.path}"):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"rate limit: {_RATE_LIMIT} mutations/minute",
        )

    if API_TOKEN:
        presented = _presented_token(request)
        if not presented or not secrets.compare_digest(presented, API_TOKEN):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing or invalid API token (X-API-Token)",
            )
        return

    if host not in LOOPBACK_HOSTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "mutating endpoints are restricted to loopback because NBX_API_TOKEN "
                "is not set; configure a token to allow remote access"
            ),
        )


__all__ = ["API_TOKEN", "auth_mode", "cors_origins", "require_client", "reset_rate_limits"]
