#!/usr/bin/env python3
"""Archive raw public market payloads so a later composite backtest has history.

Why a separate process
----------------------
Review finding 9.3/2 and 9.3/6: the canonical backtest is TA-only because no
point-in-time liquidity/order-book history exists, and `dataset_hash` alone is
not a replay. The only cure is to start keeping the raw payloads *now*. Doing
that inside the service would need a deploy and restart, which the running
stability soak forbids; this script therefore runs under its own systemd timer,
touches nothing the service owns, and simply records what the exchange said.

What it stores
--------------
One gzip-compressed JSON file per endpoint per run, under
``<root>/YYYY-MM-DD/<endpoint>-<UTC timestamp>.json.gz`` with the raw body
exactly as received, plus ``fetched_at``, ``url``, ``status`` and ``sha256`` of
the body in a small envelope. Nothing is parsed or normalised: a payload that
was later found to be wrong is still evidence of what was seen at the time.

Retention
---------
``--keep-days N`` removes whole day-directories older than N days. It never
deletes inside the current retention window and never deletes anything else.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://apiv2.nobitex.ir"
ENDPOINTS: dict[str, str] = {
    "stats": "/market/stats",
    "orderbook": "/v3/orderbook/all",
}
DEFAULT_UA = "TraderBot/nobi-archive"


def user_agent() -> str:
    raw = (os.getenv("NBX_UA") or "").strip()
    return f"TraderBot/{raw}-archive" if raw and not raw.startswith("TraderBot/") else (raw or DEFAULT_UA)


def fetch(url: str, timeout: float = 20.0) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent(), "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()


def envelope(name: str, url: str, status: int, body: bytes, fetched_at: dt.datetime) -> bytes:
    return json.dumps({
        "schema": "nobi.market-archive.v1",
        "endpoint": name,
        "url": url,
        "status": status,
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "body": body.decode("utf-8", "replace"),
    }, ensure_ascii=False).encode("utf-8")


def write_snapshot(root: Path, name: str, url: str, status: int, body: bytes,
                   fetched_at: dt.datetime) -> Path:
    day = root / fetched_at.strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    path = day / f"{name}-{fetched_at.strftime('%Y%m%dT%H%M%SZ')}.json.gz"
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wb", compresslevel=6) as fh:
        fh.write(envelope(name, url, status, body, fetched_at))
    os.replace(tmp, path)
    return path


def prune(root: Path, keep_days: int, today: dt.date) -> list[Path]:
    """Delete day-directories strictly older than the retention window."""
    removed: list[Path] = []
    if keep_days <= 0 or not root.exists():
        return removed
    cutoff = today - dt.timedelta(days=keep_days)
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        try:
            day = dt.date.fromisoformat(child.name)
        except ValueError:
            continue
        if day < cutoff:
            shutil.rmtree(child)
            removed.append(child)
    return removed


def run(root: Path, *, base: str = BASE, keep_days: int = 0,
        fetcher=fetch, now: dt.datetime | None = None) -> int:
    fetched_at = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    failures = 0
    for name, path in ENDPOINTS.items():
        url = base + path
        try:
            status, body = fetcher(url)
        except Exception as exc:  # network errors are evidence too
            status, body = 0, json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode()
        out = write_snapshot(root, name, url, status, body, fetched_at)
        ok = status == 200 and body[:1] == b"{"
        failures += 0 if ok else 1
        print(f"{name}: status={status} bytes={len(body)} -> {out}")
    for gone in prune(root, keep_days, fetched_at.date()):
        print(f"pruned {gone}")
    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", required=True, type=Path, help="archive directory, e.g. /srv/nobi-data/archive")
    ap.add_argument("--keep-days", type=int, default=0, help="delete day folders older than N days (0 = keep all)")
    ap.add_argument("--base", default=BASE, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    return run(args.root, base=args.base, keep_days=args.keep_days)


if __name__ == "__main__":
    sys.exit(main())
