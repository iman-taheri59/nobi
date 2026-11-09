#!/usr/bin/env python3
"""Read-only summary of paper_state.json for the Status workflow.

Prints equity, drawdown from the saved curve, open/partial positions, halt
state, and the last N order events. Never writes. Exits 0 even when the file is
missing (the report says so), because a missing paper book is a finding, not a
crash of the report.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime


def _ts(v) -> str:
    try:
        return datetime.fromtimestamp(float(v), UTC).strftime("%Y-%m-%d %H:%MZ")
    except (TypeError, ValueError, OSError):
        return str(v)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path")
    ap.add_argument("--events", type=int, default=15, help="last N order events to list")
    a = ap.parse_args()
    try:
        with open(a.path, encoding="utf-8") as fh:
            s = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"paper state: cannot read {a.path}: {e}")
        return 0
    start = float(s.get("start_equity") or 0)
    eq = float(s.get("equity") or 0)
    curve = [c for c in (s.get("equity_curve") or []) if isinstance(c, (list, tuple)) and len(c) >= 2]
    peak = max((float(c[1]) for c in curve), default=eq)
    dd = (eq / peak - 1) * 100 if peak else 0.0
    print(f"paper state  written {s.get('ts')}  file age {time.time() - datetime.fromisoformat(str(s.get('ts')).replace('Z', '+00:00')).timestamp():.0f}s"
          if s.get("ts") else "paper state  (no ts)")
    print(f"  equity {eq:,.2f} / start {start:,.2f}  ({(eq / start - 1) * 100 if start else 0:+.2f}%)  cash {float(s.get('cash') or 0):,.2f}"
          f"  peak {peak:,.2f}  drawdown {dd:+.2f}%  curve points {len(curve)}")
    print(f"  halted {s.get('halted')}  reasons {s.get('halt_reasons') or []}")
    pos = s.get("positions") or {}
    open_ = {k: v for k, v in pos.items() if isinstance(v, dict) and v.get("state") in ("OPEN", "PARTIAL", "PENDING")}
    closed = sum(1 for v in pos.values() if isinstance(v, dict) and v.get("state") == "CLOSED")
    print(f"  positions: {len(open_)} open/partial/pending, {closed} closed in file")
    for _k, v in sorted(open_.items(), key=lambda kv: -float(kv[1].get("opened_at") or 0)):
        print(f"    {v.get('symbol'):<12} {v.get('side'):<4} {v.get('state'):<8} qty {v.get('filled_qty')}  entry {v.get('entry_price')}  stop {v.get('stop')}"
              f"  tp1 {v.get('tp1')} tp2 {v.get('tp2')}  src {v.get('source')}  opened {_ts(v.get('opened_at'))}  pnl {v.get('realized_pnl')}")
    orders = s.get("orders") or []
    kinds: dict[str, int] = {}
    for ev in orders:
        kinds[str(ev.get("kind"))] = kinds.get(str(ev.get("kind")), 0) + 1
    print(f"  events kept {len(orders)}  by kind {dict(sorted(kinds.items()))}")
    for ev in orders[-a.events:]:
        ex = ev.get("extra") if isinstance(ev.get("extra"), dict) else {}
        bits = [f"{k}={ex[k]}" for k in ("reason", "source", "pre_broker", "pnl", "stop_slip_r") if k in ex]
        print(f"    {_ts(ev.get('ts'))}  {ev.get('kind')!s:<8} {ev.get('symbol')!s:<12} {ev.get('side')!s:<4} qty {ev.get('qty')} px {ev.get('price')}  {' '.join(bits)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
