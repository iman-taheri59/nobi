"""Owner-only non-secret installation settings for the web wizard (ADR-0035 W2)."""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

import auth
import identity
from runtime_settings import ALLOWED as ALLOWED
from runtime_settings import PATH_KEYS, clean_values, describe, parse_managed, serialize_managed

MANAGED_ENV = Path(os.getenv("NBX_MANAGED_ENV") or Path(__file__).with_name("managed.env"))

_MANAGED_LOCK = threading.Lock()
router = APIRouter(prefix="/api/install", tags=["installation"])


def require_owner(request: Request, *, csrf: bool = False) -> auth.User:
    session = identity.session_of(request)
    if session is None:
        raise HTTPException(status_code=401, detail="owner session required")
    if not session.user.is_owner:
        raise HTTPException(status_code=403, detail="owner role required")
    if csrf and not identity.csrf_ok(request, session):
        raise HTTPException(status_code=403, detail="CSRF token required")
    return session.user


def require_owner_mutation(request: Request) -> auth.User:
    return require_owner(request, csrf=True)


def require_operator(request: Request) -> auth.User:
    session = identity.session_of(request)
    if session is None:
        raise HTTPException(status_code=401, detail="operator session required")
    if not session.user.can_mutate:
        raise HTTPException(status_code=403, detail="operator or owner role required")
    return session.user


def require_operator_mutation(request: Request) -> auth.User:
    user = require_operator(request)
    session = identity.session_of(request)
    assert session is not None
    if not identity.csrf_ok(request, session):
        raise HTTPException(status_code=403, detail="CSRF token required")
    return user


def read_managed() -> dict[str, str]:
    if not MANAGED_ENV.exists():
        return {}
    return parse_managed(MANAGED_ENV.read_text(encoding="utf-8"))


def validate(values: dict[str, Any], *, create_dirs: bool = False,
             allow_missing: bool = False) -> dict[str, str]:
    clean = clean_values(values)
    merged = {**read_managed(), **clean}
    if int(merged.get("NBX_BOOK_TTL_S", 120)) >= int(merged.get("NBX_BOOK_GATE_TTL_S", 300)):
        raise ValueError("NBX_BOOK_TTL_S must be smaller than NBX_BOOK_GATE_TTL_S")
    for key, text in clean.items():
        if key not in PATH_KEYS:
            continue
        path = Path(text)
        directory = path if key.endswith(("CACHE", "DIR")) else path.parent
        if create_dirs:
            directory.mkdir(parents=True, exist_ok=True)
        probe = directory
        if allow_missing:
            while not probe.exists() and probe != probe.parent:
                probe = probe.parent
        if not probe.is_dir() or not os.access(probe, os.W_OK):
            raise ValueError(f"{key} directory or existing parent is not writable")
    return clean


def write_managed(values: dict[str, str]) -> None:
    # Validate/serialize BEFORE mkdir/open, even when called outside the handler.
    content = serialize_managed(values)
    MANAGED_ENV.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".managed-", dir=MANAGED_ENV.parent)
    partial = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(partial, MANAGED_ENV)
    finally:
        partial.unlink(missing_ok=True)


@router.get("/settings")
def settings(request: Request):
    require_owner(request)
    try:
        values = read_managed()
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail="managed settings are invalid or unreadable") from exc
    return {"ok": True, "values": values, "fields": describe(values),
            "allowed": sorted(ALLOWED), "restart_required": False}


@router.post("/probe")
def probe(payload: dict, request: Request):
    require_owner(request, csrf=True)
    try:
        clean = validate(payload or {}, allow_missing=True)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "values": clean, "writable": True, "created_directories": False}


@router.post("/settings")
def save_settings(payload: dict, request: Request):
    user = require_owner(request, csrf=True)
    try:
        with _MANAGED_LOCK:
            clean = validate(payload or {}, create_dirs=True)
            merged = {**read_managed(), **clean}
            write_managed(merged)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "values": merged, "restart_required": True,
            "saved_by": user.username, "fields": describe(merged)}


__all__ = ["ALLOWED", "MANAGED_ENV", "read_managed", "require_operator", "require_operator_mutation", "require_owner", "require_owner_mutation", "router", "validate", "write_managed"]
