"""Resumable owner-only host installation workflow (ADR-0048).

The web process never accepts commands or runs privileged shell text.  It sends
one allowlisted action id to a separately installed sudo-policy helper.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

import install_api
import journal

router = APIRouter(prefix="/api/install/wizard", tags=["installation-wizard"])
HELPER = Path(os.getenv("NBX_PRIVILEGED_HELPER_CLIENT", "/usr/local/bin/nobi-helper-client"))
PROTOCOL = 1
MAX_OUTPUT = 8000
STEPS = (
    "bootstrap-owner", "host-preflight", "storage", "runtime",
    "reverse-proxy", "service-install", "validation", "complete",
)
ACTIONS = {
    "install-service": "service-install",
    "install-proxy": "reverse-proxy",
    "restart-service": "validation",
    "reload-proxy": "validation",
    "rollback": "validation",
}
_MUTATION_LOCK = threading.Lock()
SCHEMA = """
CREATE TABLE IF NOT EXISTS install_wizard_state (
 step TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at INTEGER NOT NULL,
 updated_by TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS install_wizard_runs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, state TEXT NOT NULL,
 actor TEXT NOT NULL, started_at INTEGER NOT NULL, finished_at INTEGER,
 output TEXT NOT NULL DEFAULT '', rollback_available INTEGER NOT NULL DEFAULT 0
);
"""


class ActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(min_length=1, max_length=32)
    confirm: bool = False


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    now = int(time.time())
    for step in STEPS:
        initial = "passed" if step == "bootstrap-owner" else "pending"
        conn.execute(
            "INSERT OR IGNORE INTO install_wizard_state(step,state,updated_at,updated_by) VALUES(?,?,?,'system')",
            (step, initial, now),
        )
    conn.commit()


def _rows() -> list[dict[str, Any]]:
    with journal.connect() as conn:
        ensure(conn)
        return [dict(row) for row in conn.execute(
            "SELECT step,state,updated_at,updated_by,detail FROM install_wizard_state "
            "ORDER BY CASE step " + " ".join(f"WHEN '{s}' THEN {i}" for i, s in enumerate(STEPS)) + " END"
        )]


def _set(step: str, state: str, actor: str, detail: dict[str, Any]) -> None:
    with journal.connect() as conn:
        ensure(conn)
        conn.execute(
            "UPDATE install_wizard_state SET state=?,updated_at=?,updated_by=?,detail=? WHERE step=?",
            (state, int(time.time()), actor, json.dumps(detail, ensure_ascii=False), step),
        )
        journal.audit_record(
            conn, "install_wizard.step", actor=actor, result=state,
            detail={"step": step, "run_id": detail.get("run_id"), "action": detail.get("action")},
        )


def _port_available(port: int) -> bool:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _helper_check() -> tuple[bool, str]:
    """Ask the helper itself, not just the filesystem, whether it can answer.

    A file that exists and is executable proves nothing: the old deployment
    had two incompatible protocols and a sudo path blocked by
    NoNewPrivileges.``check`` is read-only and answers over the same socket
    the real actions use, so a failure here is a failure of the real path.
    """
    if not HELPER.is_file() or not os.access(HELPER, os.X_OK):
        return False, f"helper client missing: {HELPER}"
    try:
        proc = subprocess.run(
            [str(HELPER), "--protocol", str(PROTOCOL), "check"],
            capture_output=True, text=True, encoding="utf-8", timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return False, _redact((proc.stdout or "") + (proc.stderr or ""))[-300:]
    return True, "protocol 1 ok"


def _no_new_privs() -> bool:
    """Informational: the socket transport works with NoNewPrivileges=yes."""
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("NoNewPrivs:"):
                return line.split(":", 1)[1].strip() == "1"
    except OSError:
        return False
    return False


def preflight() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[1]
    helper_ok, helper_why = _helper_check()
    checks = [
        {"id": "os", "ok": os.name == "posix", "value": platform.platform()},
        {"id": "not-root", "ok": not hasattr(os, "geteuid") or os.geteuid() != 0,
         "value": "web process must not run as root"},
        {"id": "python", "ok": sys.version_info >= (3, 11), "value": platform.python_version()},
        {"id": "disk", "ok": shutil.disk_usage(root).free >= 512 * 1024 * 1024,
         "value": shutil.disk_usage(root).free},
        {"id": "data-writable", "ok": os.access(root, os.W_OK), "value": str(root)},
        {"id": "port-80", "ok": _port_available(80), "value": "available"},
        {"id": "helper", "ok": HELPER.is_file() and os.access(HELPER, os.X_OK),
         "value": str(HELPER)},
        {"id": "systemd", "ok": shutil.which("systemctl") is not None, "value": "systemctl"},
        {"id": "nginx", "ok": shutil.which("nginx") is not None, "value": "nginx"},
        {"id": "helper-protocol", "ok": helper_ok, "value": helper_why},
        {"id": "no-new-privs", "ok": True,
         "value": "socket transport; NoNewPrivileges is compatible" if _no_new_privs()
                  else "not set (local/dev)"},
    ]
    required = {"os", "not-root", "python", "disk", "data-writable", "helper",
                "helper-protocol", "systemd", "nginx"}
    ok = all(row["ok"] for row in checks if row["id"] in required)
    return {"ok": ok, "checks": checks, "protocol": PROTOCOL}


def _redact(text: str) -> str:
    lines = []
    for line in text[-MAX_OUTPUT:].splitlines():
        lowered = line.lower()
        if any(word in lowered for word in ("token=", "password=", "secret=", "api_key=")):
            line = "[redacted]"
        lines.append(line)
    return "\n".join(lines)[-MAX_OUTPUT:]


def run_action(action: str, actor: str) -> dict[str, Any]:
    if action not in ACTIONS:
        raise ValueError("unsupported installation action")
    if not _MUTATION_LOCK.acquire(blocking=False):
        raise RuntimeError("an installation action is already running")
    try:
        step = ACTIONS[action]
        started = int(time.time())
        with closing(journal.connect()) as conn:
            ensure(conn)
            cur = conn.execute(
                "INSERT INTO install_wizard_runs(action,state,actor,started_at) VALUES(?,'running',?,?)",
                (action, actor, started),
            )
            run_id = int(cur.lastrowid or 0)
            conn.commit()
        _set(step, "running", actor, {"run_id": run_id, "action": action})
        try:
            proc = subprocess.run(
                [str(HELPER), "--protocol", str(PROTOCOL), action],
                capture_output=True, text=True, encoding="utf-8", timeout=120, check=False,
            )
            output = _redact((proc.stdout or "") + ("\n" if proc.stderr else "") + (proc.stderr or ""))
            state = "passed" if proc.returncode == 0 else "failed"
            rollback = int(action in {"install-service", "install-proxy"})
        except (OSError, subprocess.SubprocessError) as exc:
            output, state, rollback = _redact(f"{type(exc).__name__}: {exc}"), "failed", 0
        with closing(journal.connect()) as conn:
            ensure(conn)
            conn.execute(
                "UPDATE install_wizard_runs SET state=?,finished_at=?,output=?,rollback_available=? WHERE id=?",
                (state, int(time.time()), output, rollback, run_id),
            )
            conn.commit()
        _set(step, state, actor, {"run_id": run_id, "action": action})
        return {"id": run_id, "action": action, "state": state, "output": output,
                "rollback_available": bool(rollback)}
    finally:
        _MUTATION_LOCK.release()


@router.get("/status")
def status(request: Request):
    install_api.require_owner(request)
    with journal.connect() as conn:
        ensure(conn)
        runs = [dict(row) for row in conn.execute(
            "SELECT * FROM install_wizard_runs ORDER BY id DESC LIMIT 10"
        )]
    return {"ok": True, "steps": _rows(), "runs": runs, "actions": sorted(ACTIONS),
            "helper": {"available": HELPER.is_file(), "protocol": PROTOCOL}}


@router.post("/preflight")
def preflight_api(request: Request):
    user = install_api.require_owner_mutation(request)
    result = preflight()
    _set("host-preflight", "passed" if result["ok"] else "blocked", user.username, result)
    return result


@router.post("/action")
def action_api(body: ActionBody, request: Request):
    user = install_api.require_owner_mutation(request)
    if body.action not in ACTIONS:
        raise HTTPException(status_code=422, detail="unsupported installation action")
    if body.action in {"restart-service", "reload-proxy", "rollback"} and not body.confirm:
        raise HTTPException(status_code=409, detail="explicit confirmation required")
    try:
        return run_action(body.action, user.username)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


__all__ = ["ACTIONS", "HELPER", "PROTOCOL", "STEPS", "ensure", "preflight", "router", "run_action"]
