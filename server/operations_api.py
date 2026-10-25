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

ROOT = Path(__file__).resolve().parents[1]
AUDIT = Path(os.getenv("NBX_OPERATIONS_AUDIT") or Path(__file__).with_name("operations-audit.jsonl"))
HELPER = os.getenv("NBX_INSTALL_HELPER", "")
_ARCHIVE = re.compile(r"nobitrader-state-[0-9]{8}T[0-9]{6}Z\.zip\Z")
_DOMAIN = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")
router = APIRouter(prefix="/api/operations", tags=["owner operations"])


def _paths() -> tuple[Path, Path, Path, Path]:
    values = install_api.read_managed()
    export = Path(values.get("NBX_EXPORT_DIR") or ROOT / "exports").resolve()
    journal = Path(values.get("NBX_JOURNAL_DB") or ROOT / "server" / "journal.db").resolve()
    auth = Path(values.get("NBX_AUTH_DB") or ROOT / "server" / "auth.db").resolve()
    live = Path(values.get("NBX_LIVE_DB") or ROOT / "server" / "live_execution.db").resolve()
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


def _helper(action: str, payload: dict[str, str]) -> dict[str, Any]:
    helper = Path(HELPER)
    if not HELPER or not helper.is_absolute() or not helper.is_file():
        raise HTTPException(status_code=503, detail="privileged helper is not configured")
    try:
        proc = subprocess.run(
            [str(helper), action, json.dumps(payload, separators=(",", ":"))],
            cwd=ROOT, env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"},
            text=True, encoding="utf-8", capture_output=True, timeout=30, check=False,
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
    return {"ok": True, "archives": _archives(), "helper_available": bool(HELPER),
            "actions": ["backup", "restore", "apply-config", "restart"]}


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
    user = install_api.require_owner(request, csrf=True)
    if set(payload) != {"archive", "confirmation"}:
        raise HTTPException(status_code=422, detail="archive and confirmation are required")
    name = str(payload["archive"])
    if not _ARCHIVE.fullmatch(name) or payload["confirmation"] != f"RESTORE {name}":
        raise HTTPException(status_code=422, detail="invalid archive or confirmation")
    backup_dir, journal, auth, live = _paths()
    archive = (backup_dir / name).resolve()
    if archive.parent != backup_dir.resolve() or not archive.is_file():
        raise HTTPException(status_code=404, detail="backup not found")
    try:
        _state_backup().restore_backup(archive, journal, auth, live, force=True)
        _audit(user.username, "restore", True, name)
        return {"ok": True, "restart_required": True}
    except Exception as exc:
        _audit(user.username, "restore", False, type(exc).__name__)
        raise HTTPException(status_code=422, detail="backup validation or restore failed") from exc


@router.post("/apply-config")
def apply_config(payload: dict, request: Request):
    user = install_api.require_owner(request, csrf=True)
    if set(payload) != {"domain"} or not _DOMAIN.fullmatch(str(payload["domain"]).lower()):
        raise HTTPException(status_code=422, detail="valid DNS domain required")
    domain = str(payload["domain"]).lower()
    try:
        result = _helper("apply-config", {"domain": domain})
    except HTTPException:
        _audit(user.username, "apply-config", False, domain)
        raise
    _audit(user.username, "apply-config", True, domain)
    return result


@router.post("/restart")
def restart(payload: dict, request: Request):
    user = install_api.require_owner(request, csrf=True)
    if payload != {"confirmation": "RESTART"}:
        raise HTTPException(status_code=422, detail="confirmation must be RESTART")
    try:
        result = _helper("restart", {})
    except HTTPException:
        _audit(user.username, "restart", False)
        raise
    _audit(user.username, "restart", True)
    return result


__all__ = ["router"]
