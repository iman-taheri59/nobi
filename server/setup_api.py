"""One-time first-run owner bootstrap for the web setup wizard (ADR-0035)."""
from __future__ import annotations

import contextlib
import logging
import os
import secrets
import threading
from pathlib import Path

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

import auth
import identity
from security import client_host, login_rate_ok

log = logging.getLogger("nbx-setup")
router = APIRouter(prefix="/api/setup", tags=["first-run-setup"])
_LOCK = threading.Lock()
TOKEN_FILE = Path(
    os.getenv("NBX_SETUP_TOKEN_FILE")
    or Path(os.getenv("NBX_AUTH_DB") or Path(__file__).with_name("auth.db")).with_name("setup.token")
)


class ClaimBody(BaseModel):
    setup_token: str = Field(min_length=20, max_length=200)
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=12, max_length=200)


def ensure_setup_token() -> str | None:
    """Return/create the console token while this install has no owner."""
    if auth.any_users():
        with contextlib.suppress(OSError):
            TOKEN_FILE.unlink()
        return None
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if TOKEN_FILE.exists():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if len(token) >= 20:
            return token
    token = secrets.token_urlsafe(32)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    with contextlib.suppress(OSError):
        TOKEN_FILE.chmod(0o600)
    log.warning("FIRST-RUN SETUP TOKEN: %s", token)
    return token


@router.get("/status")
def setup_status():
    required = not auth.any_users()
    if required:
        ensure_setup_token()
    return {"ok": True, "setup_required": required}


@router.post("/claim")
def claim(body: ClaimBody, request: Request, response: Response):
    host = client_host(request)
    if not login_rate_ok(host):
        return Response(status_code=429, content='{"detail":"too many setup attempts"}',
                        media_type="application/json")
    with _LOCK:
        if auth.any_users():
            return Response(status_code=409, content='{"detail":"setup is already claimed"}',
                            media_type="application/json")
        expected = ensure_setup_token() or ""
        if not secrets.compare_digest(body.setup_token, expected):
            return Response(status_code=403, content='{"detail":"invalid setup token"}',
                            media_type="application/json")
        try:
            user = auth.create_user(body.username, body.password, role="owner", created_by="setup")
        except auth.AuthError as exc:
            return Response(status_code=exc.status, content=(
                '{"detail":' + json_quote(exc.message) + "}"
            ), media_type="application/json")
        with contextlib.suppress(OSError):
            TOKEN_FILE.unlink()
        token, csrf, _ = auth.create_session(
            user, ip=host, ua=request.headers.get("user-agent", "")
        )
        identity.set_session_cookies(
            response, token=token, csrf=csrf,
            max_age=auth.SESSION_DAYS * 86400, secure=identity.is_secure(request),
        )
        return {"ok": True, "user": user.public(), "is_owner": True}


def json_quote(value: str) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


__all__ = ["TOKEN_FILE", "ensure_setup_token", "router"]
