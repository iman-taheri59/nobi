"""One CSV row per run: the numbers the S2 decision will be taken on (ADR-0024).

The shadow ledger's acceptance is "≥30 verdicts over ≥5 days" (ADR-0023). The
verdict count fills within a day; the *days* count only fills if the gate keeps
being written on different days, and until 1.1.1 nothing guaranteed that — the
only trigger was an open dashboard tab. This tool cannot fix that by itself, but
it makes the state visible in one line a day, which is the difference between
"we think it is filling" and "here is the row".

    python -m server.tools.daily_snapshot
    python -m server.tools.daily_snapshot --url http://127.0.0.1:8000 --csv a.csv

`GET` only. It writes no secret, changes nothing on the server, and its worst
failure is an empty CSV.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

COLUMNS = ("ts", "version", "rows", "books", "stale_books", "oldest_usable_s",
           "pref_ok", "pref_asked", "pref_failed", "pref_fresh",
           "ok", "no_book", "spread_wide", "book_thin", "days", "ready", "scan_loop")

#: The ledger today knows these codes; anything else a gate grows later is summed
#: into the CSV without a code change, because a column that silently stops
#: counting is worse than a wide file.
KNOWN_CODES = ("ok", "no-book", "spread-wide", "book-thin")


def get_json(url: str, cookie: str = "", timeout: float = 30.0) -> tuple[Any, int]:
    """`(payload, status)`. A non-200 is returned, not raised: a 401 is an answer."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace")), resp.status
    except urllib.error.HTTPError as e:
        return None, int(e.code)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
        print(f"  ! {url}: {type(e).__name__}: {e}", file=sys.stderr)
        return None, 0


def _int(value: Any) -> int | str:
    try:
        return int(value)
    except (TypeError, ValueError):
        return ""


def collect(base: str, tf: str = "15m", cookie: str = "") -> dict[str, Any]:
    """Read the three routes and flatten them into one row.

    The ledger block is `{gate: {verdicts, days, ready}}`, so `days` is the
    *maximum* over gates — the binding constraint for the decision, and the one
    number that cannot be faked by scanning more often.
    """
    health, _ = get_json(base.rstrip("/") + "/api/health", cookie)
    signals, status = get_json(f"{base.rstrip('/')}/api/signals?tf={tf}", cookie)
    if signals is None:
        raise RuntimeError(
            f"/api/signals answered {status or 'nothing'} — is the server up? "
            "(401/403 means the install has accounts: pass --cookie)"
        )
    gates = (signals.get("gates") or {}) if isinstance(signals, dict) else {}
    inputs = gates.get("inputs") or {}
    prefetch = inputs.get("prefetch") or {}
    ledger = (gates.get("ledger") or {}) if isinstance(gates.get("ledger"), dict) else {}
    per_gate = ledger.get("gates") or {}
    ready = ledger.get("ready") or []

    row: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "version": (health or {}).get("version", "") if isinstance(health, dict) else "",
        "rows": _int((gates.get("counts") or {}).get("rows")),
        "books": _int(inputs.get("books")),
        "stale_books": _int(inputs.get("stale_books")),
        # ADR-0024: the age of the oldest *usable* book. `oldest_book_s` counts
        # books that were dropped for being too old, so it can exceed the TTL and
        # read as if the gates used them.
        "oldest_usable_s": _int(inputs.get("oldest_usable_s")),
        "pref_ok": _int(prefetch.get("ok")),
        "pref_asked": _int(prefetch.get("asked")),
        "pref_failed": _int(prefetch.get("failed")),
        "pref_fresh": _int(prefetch.get("fresh")),
        "days": max((_int((row_gate or {}).get("days")) for row_gate in per_gate.values()),
                    key=lambda v: v if isinstance(v, int) else -1, default=0),
        "ready": len(ready) if isinstance(ready, list) else 0,
        "scan_loop": "",
    }
    for code in KNOWN_CODES:
        row[code.replace("-", "_")] = _int((per_gate.get(code) or {}).get("verdicts")) or 0
    loop = (health or {}).get("scan_loop") if isinstance(health, dict) else None
    if isinstance(loop, dict):
        # The whole point of ADR-0024: the evidence window must not depend on an
        # open tab. If this says `off`, `days` is being filled by a browser.
        row["scan_loop"] = "on" if loop.get("enabled") else "off"
    return row


def append_row(row: dict[str, Any], path: Path) -> None:
    new = not path.exists() or path.stat().st_size == 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(COLUMNS))
        if new:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in COLUMNS})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="one CSV row per run: the shadow ledger's state")
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="running dashboard/API base")
    ap.add_argument("--csv", default="nbx-daily.csv", help="file to append to")
    ap.add_argument("--tf", default="15m", help="timeframe whose gates to read")
    ap.add_argument("--cookie", default="", help="cookie header, when the install has accounts")
    args = ap.parse_args(argv)

    try:
        row = collect(args.url, args.tf, args.cookie)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    append_row(row, Path(args.csv))
    print(" | ".join(f"{k}={row.get(k)}" for k in COLUMNS[1:]))
    print(f"appended to {args.csv}")
    days = row.get("days")
    if isinstance(days, int) and days < 5:
        print(f"  -> days={days}/5: the evidence window is not closed yet "
              "(this is what blocks the S2 decision)")
    return 0


if __name__ == "__main__":                      # pragma: no cover - entry point
    raise SystemExit(main())
