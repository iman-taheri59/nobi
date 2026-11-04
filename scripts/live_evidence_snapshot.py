#!/usr/bin/env python3
"""Append one secret-free live-execution evidence snapshot to CSV.

`/api/live/status` and `/api/live/evidence` are operator-session routes: a
bare `NBX_API_TOKEN` is not accepted there. For an unattended daily run set
`NBX_SNAPSHOT_USERNAME` / `NBX_SNAPSHOT_PASSWORD` (an Operator or Owner
account) in the timer's environment; the tool logs in once, reads with that
session, logs out, and writes none of it to the CSV.
"""
from __future__ import annotations

import argparse
import csv
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

COLUMNS = (
    "ts", "version", "mode", "configured", "samples", "days", "ambiguous",
    "ready", "applied", "dry_run", "unknown", "cancel_unknown", "rejected",
    "reasons",
    # ADR-0024/0049: scheduler liveness and alert delivery split. Blank when the
    # server predates the field, so a mixed-version soak CSV stays readable.
    "scan_runs", "alert_rules", "alert_runs", "alert_last_ok",
    "alert_sent", "alert_failed", "alert_pending", "alert_oldest_pending_at",
)


def _opt(block: Any, key: str) -> Any:
    if not isinstance(block, dict) or block.get(key) is None:
        return ""
    return block.get(key)


_OPENER = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
)
_HEADERS = {"Accept": "application/json", "User-Agent": "nobi-evidence-snapshot/1.0"}


def _describe(exc: BaseException) -> str:
    # HTTP status helps an operator tell "needs a session" from "server down";
    # the response body is never echoed, it could carry anything.
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTPError {exc.code}"
    return type(exc).__name__


def login(base_url: str, username: str, password: str, timeout: float = 30.0) -> None:
    """Open a real session (the same contract the browser uses)."""
    payload = json.dumps({"username": username, "password": password}).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/auth/login", data=payload,
        headers={**_HEADERS, "Content-Type": "application/json"},
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"snapshot login failed: {_describe(exc)}") from exc


def logout(base_url: str, timeout: float = 10.0) -> None:
    """Best effort: a daily timer must not leave a session per day behind."""
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/auth/logout", data=b"{}",
        headers={**_HEADERS, "Content-Type": "application/json"},
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        pass


def get_json(url: str, token: str = "", timeout: float = 20.0) -> dict[str, Any]:
    headers = dict(_HEADERS)
    if token:
        headers["X-API-Token"] = token
    request = urllib.request.Request(url, headers=headers)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise RuntimeError(f"snapshot request failed: {_describe(exc)}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("snapshot response is not an object")
    return value


def collect(base_url: str, token: str = "") -> dict[str, Any]:
    base = base_url.rstrip("/")
    health = get_json(base + "/api/health", token)
    status = get_json(base + "/api/live/status", token)
    evidence = get_json(base + "/api/live/evidence", token)
    if not evidence.get("ok") or "samples" not in evidence:
        raise RuntimeError("live evidence response is incomplete")
    states = evidence.get("states") if isinstance(evidence.get("states"), dict) else {}
    reasons = evidence.get("reasons") if isinstance(evidence.get("reasons"), list) else []
    alerts = health.get("price_alerts")
    deliveries = alerts.get("deliveries") if isinstance(alerts, dict) else None
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "version": health.get("version", ""),
        "mode": status.get("mode", ""),
        "configured": bool(status.get("configured")),
        "samples": int(evidence.get("samples", 0)),
        "days": int(evidence.get("days", 0)),
        "ambiguous": int(evidence.get("ambiguous", 0)),
        "ready": bool(evidence.get("ready")),
        "applied": bool(evidence.get("applied")),
        "dry_run": int(states.get("dry-run", 0)),
        "unknown": int(states.get("unknown", 0)),
        "cancel_unknown": int(states.get("cancel-unknown", 0)),
        "rejected": int(states.get("rejected", 0)),
        "reasons": ";".join(str(reason) for reason in reasons),
        "scan_runs": _opt(health.get("scan_loop"), "runs"),
        "alert_rules": _opt(alerts, "enabled_rules"),
        "alert_runs": _opt(alerts, "runs"),
        "alert_last_ok": _opt(alerts, "last_ok"),
        "alert_sent": _opt(deliveries, "sent"),
        "alert_failed": _opt(deliveries, "failed"),
        "alert_pending": _opt(deliveries, "pending"),
        "alert_oldest_pending_at": _opt(alerts, "oldest_pending_at"),
    }


def append_row(path: Path, row: dict[str, Any]) -> None:
    new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        if new:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in COLUMNS})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--csv", type=Path, default=Path("live-evidence.csv"))
    args = parser.parse_args(argv)
    username = os.getenv("NBX_SNAPSHOT_USERNAME", "")
    password = os.getenv("NBX_SNAPSHOT_PASSWORD", "")
    session = bool(username and password)
    try:
        if session:
            login(args.url, username, password)
        try:
            row = collect(args.url, os.getenv("NBX_API_TOKEN", ""))
        finally:
            if session:
                logout(args.url)
    except RuntimeError as exc:
        message = str(exc)
        if "HTTPError 401" in message and not session:
            message += (" — live routes need an Operator/Owner session; set "
                        "NBX_SNAPSHOT_USERNAME and NBX_SNAPSHOT_PASSWORD")
        print(message, file=sys.stderr)
        return 1
    append_row(args.csv, row)
    print(f"snapshot appended: samples={row['samples']} days={row['days']} ready={row['ready']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
