"""Owner-only backup, restore and constrained OS-helper operations (ADR-0037)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

import install_api
import install_wizard
from runtime_settings import effective_values

ROOT = Path(__file__).resolve().parents[1]
AUDIT = Path(os.getenv("NBX_OPERATIONS_AUDIT") or Path(__file__).with_name("operations-audit.jsonl"))
# One transport for both the wizard and the operations card.
HELPER = os.getenv("NBX_INSTALL_HELPER") or str(install_wizard.HELPER)
_ARCHIVE = re.compile(r"nobitrader-state-[0-9]{8}T[0-9]{6}Z\.zip\Z")
router = APIRouter(prefix="/api/operations", tags=["owner operations"])


def _paths() -> tuple[Path, Path, Path, Path]:
    values = effective_values()
    export = Path(values["NBX_EXPORT_DIR"]).resolve()
    journal = Path(values["NBX_JOURNAL_DB"]).resolve()
    auth = Path(values["NBX_AUTH_DB"]).resolve()
    live = Path(values["NBX_LIVE_DB"]).resolve()
    return export / "backups", journal, auth, live


def _audit(user: str, action: str, ok: bool, detail: str = "") -> None:
    AUDIT.parent.mkdir(parents=True, exist_ok=True)
    record = {"at": int(time.time()), "user": user, "action": action,
              "ok": ok, "detail": detail[:300]}
    fd = os.open(AUDIT, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, (json.dumps(record, ensure_ascii=False) + "\n").encode())
    finally:
        os.close(fd)


def _archives() -> list[dict[str, Any]]:
    backup_dir, *_ = _paths()
    if not backup_dir.exists():
        return []
    return [{"name": p.name, "bytes": p.stat().st_size, "mtime": int(p.stat().st_mtime)}
            for p in sorted(backup_dir.iterdir(), reverse=True)
            if p.is_file() and _ARCHIVE.fullmatch(p.name)][:50]


def _state_backup():
    # Imported lazily so the API has no startup dependency on a script entrypoint.
    from scripts import state_backup
    return state_backup


def _helper(action: str) -> dict[str, Any]:
    """Call the same protocol-1 client the browser installer uses.

    The old code passed ``[action, JSON]`` to a second, incompatible helper
    (``scripts/nobi_privileged_helper.py``). That helper is gone: on a 1.9.0
    host the call either hit the exact-argv sudoers rule (denied) or the new
    argparse CLI (rejected). Both routes are now one allowlist.
    """
    if action not in install_wizard.ACTIONS:
        raise HTTPException(status_code=422, detail="unsupported helper action")
    helper = Path(HELPER)
    if not HELPER or not helper.is_absolute() or not helper.is_file():
        raise HTTPException(status_code=503, detail="privileged helper is not configured")
    try:
        proc = subprocess.run(
            [str(helper), "--protocol", str(install_wizard.PROTOCOL), action],
            cwd=ROOT, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
            text=True, encoding="utf-8", capture_output=True, timeout=100, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="helper timeout") from exc
    output = (proc.stdout + "\n" + proc.stderr).strip()[-4000:]
    if proc.returncode:
        raise HTTPException(status_code=502, detail=f"helper failed: {output}")
    return {"ok": True, "output": output}


@router.get("")
def status(request: Request):
    install_api.require_owner(request)
    helper_ready = bool(HELPER) and Path(HELPER).is_file()
    backup_dir, journal, auth, live = _paths()
    return {"ok": True, "archives": _archives(), "helper_available": helper_ready,
            "actions": ["backup", "restore", "restart"],
            "helper_protocol": install_wizard.PROTOCOL,
            "restore_available": False,
            "restore_reason": "online-restore-disabled; stop the service and use the offline CLI",
            "path_source": "effective-running-process",
            "backup_directory": str(backup_dir),
            "databases": {"journal": str(journal), "auth": str(auth), "live": str(live)}}


@router.post("/backup")
def backup(request: Request):
    user = install_api.require_owner(request, csrf=True)
    backup_dir, journal, auth, live = _paths()
    try:
        output = _state_backup().create_backup(backup_dir, journal, auth, live)
        _audit(user.username, "backup", True, output.name)
        return {"ok": True, "archive": output.name, "archives": _archives()}
    except Exception as exc:
        _audit(user.username, "backup", False, type(exc).__name__)
        raise HTTPException(status_code=500, detail="backup failed") from exc


@router.post("/restore")
def restore(payload: dict, request: Request):
    install_api.require_owner(request, csrf=True)
    if set(payload) != {"archive", "confirmation"}:
        raise HTTPException(status_code=422, detail="archive and confirmation are required")
    name = str(payload["archive"])
    if not _ARCHIVE.fullmatch(name) or payload["confirmation"] != f"RESTORE {name}":
        raise HTTPException(status_code=422, detail="invalid archive or confirmation")
    # Replacing SQLite files while the API/writers are alive is unsafe. An
    # exact confirmation is necessary, but never a substitute for quiescence.
    raise HTTPException(status_code=409, detail={
        "error": "online-restore-disabled",
        "note": "Stop the service and all writers; use state_backup.py restore --force --offline.",
    })


@router.post("/apply-config")
def apply_config(payload: dict, request: Request):
    """Retired: the domain is now a root-owned file, never a request argument.

    ``NBX_INSTALL_HELPER`` used to call ``apply-config <JSON>`` on a second
    helper that accepted a domain from the browser. The protocol-1 helper
    deliberately does not: it reads /etc/nobi/domain (root:root 0444) and
    replaces a placeholder in a root-owned nginx template. Use the Settings
    -> Install wizard ``install-proxy`` action instead.
    """
    user = install_api.require_owner(request, csrf=True)
    _audit(user.username, "apply-config", False, "retired endpoint")
    raise HTTPException(status_code=410, detail={
        "error": "moved",
        "where": "/api/install/wizard/action",
        "action": "install-proxy",
        "note": "set /etc/nobi/domain as root, then run install-proxy",
    })


@router.post("/restart")
def restart(payload: dict, request: Request):
    user = install_api.require_owner(request, csrf=True)
    if payload != {"confirmation": "RESTART"}:
        raise HTTPException(status_code=422, detail="confirmation must be RESTART")
    try:
        result = _helper("restart-service")
    except HTTPException:
        _audit(user.username, "restart", False)
        raise
    _audit(user.username, "restart", True)
    return result


__all__ = ["router"]
