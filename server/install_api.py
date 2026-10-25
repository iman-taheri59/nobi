"""Owner-only non-secret installation settings for the web wizard (ADR-0035 W2)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

import auth
import identity

MANAGED_ENV = Path(os.getenv("NBX_MANAGED_ENV") or Path(__file__).with_name("managed.env"))
PATH_KEYS = {
    "NBX_JOURNAL_DB", "NBX_AUTH_DB", "NBX_LIVE_DB", "NBX_CANDLE_CACHE",
    "NBX_BT_DIR", "NBX_EXPORT_DIR",
}
INT_RANGES = {
    "NBX_SCAN_LOOP_S": (0, 86400),
    "NBX_GATE_PREFETCH": (0, 20),
    "NBX_BOOK_TTL_S": (10, 3600),
    "NBX_BOOK_GATE_TTL_S": (10, 7200),
    "NBX_BOOK_KEEP_S": (60, 86400),
}
CHOICES = {
    "NBX_SCAN_LOOP_TF": {"15m", "1h", "4h", "D"},
    "NBX_LIVE_MODE": {"disabled", "dry-run"},
}
ALLOWED = PATH_KEYS | set(INT_RANGES) | set(CHOICES)
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
    values: dict[str, str] = {}
    for raw in MANAGED_ENV.read_text(encoding="utf-8").splitlines():
        if "=" not in raw or raw.lstrip().startswith("#"):
            continue
        key, value = raw.split("=", 1)
        if key in ALLOWED:
            values[key] = value
    return values


def validate(values: dict[str, Any], *, create_dirs: bool = False) -> dict[str, str]:
    unknown = sorted(set(values) - ALLOWED)
    if unknown:
        raise ValueError(f"unsupported setting(s): {', '.join(unknown)}")
    clean: dict[str, str] = {}
    for key, value in values.items():
        text = str(value).strip()
        if key in PATH_KEYS:
            path = Path(text).expanduser()
            if not path.is_absolute():
                raise ValueError(f"{key} must be an absolute path")
            directory = path if key.endswith(("CACHE", "DIR")) else path.parent
            if create_dirs:
                directory.mkdir(parents=True, exist_ok=True)
            if not directory.exists() or not os.access(directory, os.W_OK):
                raise ValueError(f"{key} directory is not writable")
            clean[key] = str(path)
        elif key in INT_RANGES:
            number = int(text)
            low, high = INT_RANGES[key]
            if not low <= number <= high:
                raise ValueError(f"{key} must be between {low} and {high}")
            if key == "NBX_SCAN_LOOP_S" and 0 < number < 60:
                raise ValueError("NBX_SCAN_LOOP_S must be 0 or at least 60")
            clean[key] = str(number)
        elif key in CHOICES:
            if text not in CHOICES[key]:
                raise ValueError(f"{key} has an unsupported value")
            clean[key] = text
    # The two TTL meanings must remain ordered.
    merged = {**read_managed(), **clean}
    if int(merged.get("NBX_BOOK_TTL_S", 120)) >= int(merged.get("NBX_BOOK_GATE_TTL_S", 300)):
        raise ValueError("NBX_BOOK_TTL_S must be smaller than NBX_BOOK_GATE_TTL_S")
    return clean


def write_managed(values: dict[str, str]) -> None:
    MANAGED_ENV.parent.mkdir(parents=True, exist_ok=True)
    partial = MANAGED_ENV.with_suffix(MANAGED_ENV.suffix + ".partial")
    lines = ["# Managed by the nobitrader owner installation wizard. No secrets."]
    lines.extend(f"{key}={values[key]}" for key in sorted(values))
    partial.write_text("\n".join(lines) + "\n", encoding="utf-8")
    partial.chmod(0o600)
    os.replace(partial, MANAGED_ENV)


@router.get("/settings")
def settings(request: Request):
    require_owner(request)
    return {"ok": True, "values": read_managed(), "allowed": sorted(ALLOWED),
            "restart_required": False}


@router.post("/probe")
def probe(payload: dict, request: Request):
    require_owner(request, csrf=True)
    try:
        clean = validate(payload or {}, create_dirs=True)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "values": clean, "writable": True}


@router.post("/settings")
def save_settings(payload: dict, request: Request):
    user = require_owner(request, csrf=True)
    try:
        clean = validate(payload or {}, create_dirs=True)
        merged = {**read_managed(), **clean}
        write_managed(merged)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "values": merged, "restart_required": True,
            "saved_by": user.username}


__all__ = ["ALLOWED", "MANAGED_ENV", "read_managed", "require_operator", "require_operator_mutation", "require_owner", "require_owner_mutation", "router", "validate", "write_managed"]
