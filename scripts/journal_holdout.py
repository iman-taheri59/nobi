#!/usr/bin/env python3
"""Freeze or show the journal holdout cutoff (ADR-0052).

    journal_holdout.py show   [--db PATH]
    journal_holdout.py freeze --at 2026-11-10T07:20:00Z [--note "..."] [--force] [--db PATH]

The cutoff is one timestamp in `journal_meta`. Everything before it is the
in-sample window the diagnostic reports were written on; everything after it
is held out. The diagnostic prints both sides separately. Moving the cutoff
requires `--force` and the previous value is kept inside the new record.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import journal


def _ts(raw: str) -> int:
    s = raw.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("show", "freeze"))
    ap.add_argument("--db", default=None, help="journal.db path (default: the service's)")
    ap.add_argument("--at", help="ISO-8601 cutoff, e.g. 2026-11-10T07:20:00Z")
    ap.add_argument("--note", default="")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    conn = journal.connect(args.db)
    try:
        if args.cmd == "show":
            h = journal.holdout(conn)
            if not h:
                print("holdout: not frozen")
                return 1
            print(f"holdout cutoff: {datetime.fromtimestamp(h['cutoff_ts'], UTC).isoformat()}  "
                  f"(frozen {datetime.fromtimestamp(h['frozen_at'], UTC).isoformat()}, config {h.get('config_hash')})")
            if h.get("note"):
                print(f"note: {h['note']}")
            if h.get("previous"):
                print(f"previous: {h['previous']}")
            return 0
        if not args.at:
            ap.error("freeze needs --at")
        try:
            v = journal.freeze_holdout(conn, _ts(args.at), note=args.note, force=args.force)
        except ValueError as exc:
            print(f"refused: {exc}")
            return 2
        print(f"frozen at {datetime.fromtimestamp(v['cutoff_ts'], UTC).isoformat()}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
