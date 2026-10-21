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
``NBX_TRUSTED_PROXIES`` Comma separated IPs/CIDRs of reverse proxies whose
                        ``X-Forwarded-For`` may be believed (``127.0.0.1``,
                        ``::1``, a Docker bridge, Arvan's LB). Without it the
                        header is ignored entirely.

Why the proxy list exists
-------------------------
A reverse proxy on the same host makes **every** request look like it came from
127.0.0.1. With no token set, that meant "loopback only" now described the
proxy, not the caller — so a deployment behind nginx would have accepted
`POST /api/paper/halt` from anyone on the internet. Measured, not theorised:

    auth mode: loopback-only
    POST /api/paper/halt  (+ X-Forwarded-For: 203.0.113.9)  -> 200

Two changes close it: the client address is resolved through the proxy chain
(rightmost hop that is not itself a trusted proxy), and `startup_problem()`
makes a token mandatory as soon as a trusted proxy is configured — the
combination "proxy + no token" no longer has a meaning that is safe.
"""
from __future__ import annotations

import ipaddress
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


def trusted_proxies() -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """The proxies whose ``X-Forwarded-For`` this process will believe."""
    raw = os.getenv("NBX_TRUSTED_PROXIES", "").strip()
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in (x.strip() for x in raw.split(",")):
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            log.warning("security: ignoring NBX_TRUSTED_PROXIES entry %r (not an IP/CIDR)", item)
    return nets


def _in_any(addr: str, nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network]) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in nets)


_warned_unconfigured_proxy = False


def client_host(request: Request) -> str:
    """The address that actually made this request.

    Behind a trusted proxy the TCP peer is the proxy, and the caller is the
    rightmost ``X-Forwarded-For`` hop that is not itself a trusted proxy — a
    client can prepend anything to that header, so reading the leftmost value
    would let it choose its own identity.

    An untrusted peer is reported as itself, headers and all.

    The third case is the one that used to be a hole. A request that arrives
    from loopback **carrying** ``X-Forwarded-For`` was forwarded by a proxy
    sitting on the same machine, and no browser sends that header on its own.
    When no proxy is configured we still refuse to call it local: the caller is
    reported as remote, so a token-less deployment fails closed instead of
    trusting every visitor. Configuring NBX_TRUSTED_PROXIES upgrades the answer
    from "not local" to the real address.
    """
    global _warned_unconfigured_proxy
    peer = (request.client.host if request.client else "") or ""
    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    nets = trusted_proxies()

    if nets and _in_any(peer, nets):
        if not hops:
            return peer
        for hop in reversed(hops):
            if not _in_any(hop, nets):
                return hop
        return hops[0]          # every hop is a proxy: best remaining answer

    if hops and peer in LOOPBACK_HOSTS:
        if not _warned_unconfigured_proxy:
            _warned_unconfigured_proxy = True
            log.warning(
                "security: a loopback peer sent X-Forwarded-For, so this process is "
                "behind a proxy that NBX_TRUSTED_PROXIES does not describe. The request "
                "is treated as remote (fail closed). Set NBX_TRUSTED_PROXIES=%s to "
                "resolve the real client address.", peer)
        return hops[-1]
    return peer


def startup_problem() -> str:
    """A configuration that must not run, in one sentence. Empty = fine."""
    if trusted_proxies() and not API_TOKEN:
        return (
            "NBX_TRUSTED_PROXIES is set but NBX_API_TOKEN is not. Behind a proxy "
            "every request looks like it came from loopback, so the loopback "
            "fallback would let the whole internet call the mutating endpoints. "
            "Set NBX_API_TOKEN (see docs/deploy-arvan.md) or remove "
            "NBX_TRUSTED_PROXIES."
        )
    return ""


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
    host = client_host(request)
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


__all__ = ["API_TOKEN", "auth_mode", "client_host", "cors_origins",
           "require_client", "reset_rate_limits", "startup_problem",
           "trusted_proxies"]
