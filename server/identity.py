"""The bridge between a raw HTTP request and an account.

Kept apart from `auth.py` so the storage layer stays framework-free (the CLI
imports it without dragging FastAPI in) and apart from `security.py` so the
token logic there does not need to know how cookies work.

Three things live here, and each one is a decision:

* **Cookie flags.** `HttpOnly` keeps the session away from JavaScript (an XSS
  cannot read it), `SameSite=Lax` stops cross-site POSTs from carrying it, and
  `Secure` is set whenever the request arrived over HTTPS — not unconditionally,
  because a local `http://127.0.0.1` development run would then never store it.
* **CSRF.** The session cookie is not readable by scripts, so a second value is
  sent in a *readable* cookie and must come back in `X-CSRF-Token` on every
  mutation. An attacker on another origin can make the browser send the session
  cookie but cannot read the CSRF cookie to echo it.
* **Both cookies are cleared by name and path**, because a cookie deleted with
  the wrong `path` stays in the browser and keeps logging the user back in.
"""
from __future__ import annotations

import hashlib
import hmac

import auth

SESSION_COOKIE = "nbx_session"
CSRF_COOKIE = "nbx_csrf"
CSRF_HEADER = "x-csrf-token"


def session_of(request) -> auth.Session | None:
    """The account behind this request's cookie, or None."""
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    return auth.session_from_token(token)


def is_secure(request) -> bool:
    """HTTPS as the *client* experienced it, which is what the browser checks.

    ``X-Forwarded-Proto`` is only believable from a peer named in
    ``NBX_TRUSTED_PROXIES`` — the same rule ``security.client_host`` applies to
    ``X-Forwarded-For``. Previously any client could send
    ``X-Forwarded-Proto: http`` on an HTTPS login and receive a session cookie
    without the ``Secure`` flag. A forged ``https`` from an untrusted peer can
    only make the cookie stricter, never weaker.
    """
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if forwarded in ("http", "https"):
        from security import trusted_peer  # lazy: security imports this module

        if trusted_peer(request):
            return forwarded == "https"
    return request.url.scheme == "https" or forwarded == "https"


def csrf_ok(request, session: auth.Session) -> bool:
    """Header must match the per-session token. Missing header = refusal.

    `session.csrf` is the *hash* stored in the database, so the header is hashed
    the same way before comparison. Both sides are fixed-length hex, which is
    what makes `compare_digest` meaningful here.
    """
    presented = request.headers.get(CSRF_HEADER, "")
    if not presented:
        return False
    return hmac.compare_digest(hashlib.sha256(presented.encode()).hexdigest(), session.csrf_hash)


def set_session_cookies(response, *, token: str, csrf: str, max_age: int,
                        secure: bool) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True,
                        samesite="lax", secure=secure, path="/")
    # Readable by the page on purpose: the JS has to echo it in a header.
    response.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, httponly=False,
                        samesite="lax", secure=secure, path="/")


def clear_session_cookies(response) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/")


__all__ = ["CSRF_COOKIE", "CSRF_HEADER", "SESSION_COOKIE", "clear_session_cookies",
           "csrf_ok", "is_secure", "session_of", "set_session_cookies"]
