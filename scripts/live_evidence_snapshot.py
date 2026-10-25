#!/usr/bin/env python3
"""Append one secret-free live-execution evidence snapshot to CSV."""
from __future__ import annotations

import argparse
import csv
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
)


def get_json(url: str, token: str = "", timeout: float = 20.0) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["X-API-Token"] = token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        raise RuntimeError(f"snapshot request failed: {type(exc).__name__}") from exc
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
    try:
        row = collect(args.url, os.getenv("NBX_API_TOKEN", ""))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    append_row(args.csv, row)
    print(f"snapshot appended: samples={row['samples']} days={row['days']} ready={row['ready']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
