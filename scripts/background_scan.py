#!/usr/bin/env python3
"""Guarded opt-in to browser-independent scanning on the standard systemd deploy.

Run as root on the SERVER, not a workstation. --check is observational: GETs
health/mode and reads the journal with SQLite mode=ro; never calls /signals,
risk previews, metrics evaluators or orders. --apply changes ONLY two scan keys
in /etc/nobi/nobi.env, backs it up privately, and restarts nobi (not the OS).
No credentials or account balances are printed. Default action is --check.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ENV = Path("/etc/nobi/nobi.env")
BASE = "http://127.0.0.1:8000"
PRIVATE_DIR = Path("/root")
TARGET = {"NBX_SCAN_LOOP_S": "300", "NBX_SCAN_LOOP_TF": "15m"}


def parse_env(text: str) -> dict[str, str]:
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[key.strip()] = value
    return values


def updated_env(text: str) -> str:
    # Duplicate definitions must not override the intended value later.
    lines = [
        line
        for line in text.splitlines()
        if not re.match(r"^\s*(?:export\s+)?NBX_SCAN_LOOP_(?:S|TF)\s*=", line)
    ]
    return "\n".join(lines) + "\n" + "\n".join(k + "=" + v for k, v in TARGET.items()) + "\n"


def run(*args: str) -> str:
    return subprocess.check_output(
        args, text=True, encoding="utf-8", stderr=subprocess.DEVNULL
    ).strip()


def process_env() -> dict[str, str]:
    pid = run("systemctl", "show", "nobi", "-p", "MainPID", "--value")
    if not pid.isdigit() or int(pid) <= 0:
        raise RuntimeError("nobi has no running process")
    return dict(
        item.decode().split("=", 1)
        for item in Path("/proc", pid, "environ").read_bytes().split(b"\0")
        if b"=" in item
    )


def effective_config() -> dict[str, str]:
    proc = process_env()
    config = dict(proc)
    # Same precedence as server/_env.py: real environment, server .env, managed.
    source = Path("/opt/nobi/server/.env")
    if source.exists():
        for key, val in parse_env(source.read_text(encoding="utf-8")).items():
            if not config.get(key):
                config[key] = val
    managed = Path(config.get("NBX_MANAGED_ENV") or "/opt/nobi/server/managed.env")
    if managed.exists():
        for key, val in parse_env(managed.read_text(encoding="utf-8")).items():
            if not config.get(key):
                config[key] = val
    return config


def get_json(path: str, config: dict) -> dict:
    headers = {"Accept": "application/json", "Cache-Control": "no-store"}
    token = config.get("NBX_API_TOKEN")
    if token:
        headers["X-API-Token"] = token
    req = urllib.request.Request(BASE + path, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as response:
        data = json.load(response)
    if not isinstance(data, dict):
        raise RuntimeError("unexpected API response")
    return data


def safe_mode(config: dict) -> dict:
    if config.get("NBX_LIVE_MODE", "disabled").strip().lower() not in {"disabled", "dry-run"}:
        raise RuntimeError("Live mode is not disabled/dry-run; refusing to proceed")
    mode = get_json("/api/exec/mode", config)
    symbols = mode.get("symbols")
    if mode.get("default") != "semi" or not isinstance(symbols, dict):
        raise RuntimeError("expected semi default and a valid symbol-mode map")
    if any(value == "auto" for value in symbols.values()):
        raise RuntimeError("an auto symbol override exists; refusing to enable scans")
    return {"default": mode["default"], "symbols": symbols}


def journal_counts(config: dict) -> dict:
    path = Path(config.get("NBX_JOURNAL_DB") or "/opt/nobi/server/journal.db")
    if not path.is_file():
        return {"available": False, "reason": "journal-file-missing"}
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5) as db:
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            out: dict = {"available": True}
            if "scan_cycles" in tables:
                count, completed, last = db.execute(
                    "SELECT COUNT(*),SUM(status='completed'),MAX(completed_at) FROM scan_cycles WHERE timeframe='15m'"
                ).fetchone()
                out.update(cycles=count, completed_cycles=completed or 0, last_completed_epoch=last)
            if "signal_log" in tables:
                count, bar = db.execute(
                    "SELECT COUNT(*),MAX(ts) FROM signal_log WHERE timeframe='15m' AND origin='live'"
                ).fetchone()
                out.update(live_signal_rows=count, last_signal_bar_close_epoch=bar)
            return out
    except sqlite3.Error:
        return {"available": False, "reason": "journal-read-failed"}


def snapshot(config: dict) -> dict:
    health = get_json("/api/health?scan_check=" + str(time.time_ns()), config)
    return {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "version": health.get("version"),
        "service_active": run("systemctl", "is-active", "nobi"),
        "health_ok": health.get("ok"),
        "scan_loop": health.get("scan_loop"),
        "upstream": health.get("upstream"),
        "journal": journal_counts(config),
    }


def atomic_write(path: Path, content: bytes, stat) -> None:
    if sys.platform != "linux":
        raise RuntimeError("this operation requires Linux/systemd")
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        os.fchmod(fd, stat.st_mode & 0o777)
        os.fchown(fd, stat.st_uid, stat.st_gid)
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def wait_health(config: dict) -> dict:
    until = time.monotonic() + 60
    while True:
        try:
            health = get_json("/api/health?scan_check=" + str(time.time_ns()), config)
            if health.get("ok"):
                return health
        except Exception:
            pass
        if time.monotonic() >= until:
            raise RuntimeError("health did not recover in 60 seconds")
        time.sleep(2)


def apply(config: dict) -> dict:
    before_mode = safe_mode(config)
    before = snapshot(config)
    if not before["journal"].get("available"):
        raise RuntimeError("journal cannot be read; refusing to start an evidence test")
    if config.get("NBX_JOURNAL", "1") == "0":
        raise RuntimeError("journal disabled; refusing to start an evidence test")
    if not ENV.is_file() or ENV.is_symlink():
        raise RuntimeError("expected regular /etc/nobi/nobi.env")
    stat = ENV.stat()
    pid = run("systemctl", "show", "nobi", "-p", "MainPID", "--value")
    owners = {0, Path("/proc", pid).stat().st_uid}
    if stat.st_uid not in owners or stat.st_mode & 0o022:
        raise RuntimeError("environment file owner/permissions are not safe")
    old = ENV.read_bytes()
    new = updated_env(old.decode()).encode()
    already = before.get("scan_loop") or {}
    if (
        already.get("enabled")
        and already.get("interval_s") == 300
        and config.get("NBX_SCAN_LOOP_TF", "15m") == "15m"
    ):
        return {"already_enabled": True, "snapshot": before, "formal_five_day_test_started": False}
    backup = PRIVATE_DIR / (
        "nobi-scan-env-backup-"
        + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        + "-"
        + str(os.getpid())
        + ".env"
    )
    fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as out:
        out.write(old)
    try:
        atomic_write(ENV, new, stat)
        subprocess.run(["systemctl", "restart", "nobi"], check=True, timeout=90)
        health = wait_health(config)
        current = effective_config()
        if safe_mode(current) != before_mode:
            raise RuntimeError("execution modes changed across restart")
        if current.get("NBX_LIVE_MODE", "disabled") != config.get("NBX_LIVE_MODE", "disabled"):
            raise RuntimeError("Live setting changed across restart")
        loop = health.get("scan_loop") or {}
        if (
            not loop.get("enabled")
            or loop.get("interval_s") != 300
            or current.get("NBX_SCAN_LOOP_TF") != "15m"
        ):
            raise RuntimeError("requested scan settings are not effective")
        after = snapshot(current)
    except BaseException:
        atomic_write(ENV, old, stat)
        subprocess.run(["systemctl", "restart", "nobi"], check=False, timeout=90)
        print(
            "FAILED: original environment restored and service restart attempted; inspect service status."
        )
        raise
    result = {
        "configured_at_utc": datetime.now(UTC).isoformat(),
        "interval_seconds": 300,
        "timeframe": "15m",
        "environment_backup": str(backup),
        "before": before,
        "after": after,
        "formal_five_day_test_started": False,
        "next": "Close all dashboard tabs. After 15-20 minutes run --check; scheduled runs and durable journal evidence must advance. Queue fix is separate.",
    }
    record = PRIVATE_DIR / "nobi-background-scan-baseline.json"
    fd = os.open(record, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(result, out, indent=2)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("this tool requires Linux/systemd")
    if os.geteuid() != 0:
        parser.error("run as root on the server")
    if args.apply and args.check:
        parser.error("choose --apply or --check")
    if args.apply and args.confirm != "ENABLE-BACKGROUND-SCAN":
        parser.error("--apply requires --confirm ENABLE-BACKGROUND-SCAN")
    try:
        config = effective_config()
        if args.apply:
            report = apply(config)
        else:
            report = snapshot(config)
            safe_mode(config)
            baseline = PRIVATE_DIR / "nobi-background-scan-baseline.json"
            if baseline.exists():
                old = json.loads(baseline.read_text(encoding="utf-8"))
                report["baseline_configured_at_utc"] = old.get("configured_at_utc")
                report["baseline_journal"] = (old.get("after") or {}).get("journal")
            report["formal_five_day_test_started"] = False
        print(json.dumps(report, indent=2))
        return 0
    except Exception as exc:
        # Never print exception strings or request headers: those may contain
        # credentials. The exception type is enough for a safe first report.
        print("FAILED (" + type(exc).__name__ + "). No credentials printed. Do not retry blindly.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
