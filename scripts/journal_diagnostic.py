#!/usr/bin/env python3
"""Read-only diagnostic over a copy of ``journal.db``.

Answers "is the edge slow or wrong?" with the four views that mattered on
2026-11-06 (docs/analysis/journal-diagnostic-2026-11-06.md):

1. forward return of the signal price series by engine label — separates
   "the market fell" from "our LONG picks were worse than the rest";
2. Spearman IC of each score component against the 1h return;
3. exit-reason mix per horizon and the stop-hit share under wider stops
   (re-simulated from the recorded MAE, H1 only);
4. per-symbol 1h return after LONG.

Run it on a **copy**, never on the live file::

    python3 scripts/journal_diagnostic.py /path/to/journal.db [--since 2026-11-04T15:00Z]
    python3 scripts/journal_diagnostic.py journal.db --hash e00e9f17844da0c7 --hash 0337bc4e6ff384ff

Only SELECTs are issued; the connection is opened read-only.
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime


def spearman(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: v[i])
        out = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2
            for k in range(i, j + 1):
                out[order[k]] = avg
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def parse_since(text: str | None) -> int | None:
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db")
    ap.add_argument("--since", help="ISO time; ignore signals before it")
    ap.add_argument("--hash", action="append", default=[], help="config_hash to include (repeatable)")
    ap.add_argument("--tf", default="15m")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    since = parse_since(args.since)

    where = ["timeframe = ?", "entry > 0"]
    params: list[object] = [args.tf]
    if since:
        where.append("ts >= ?")
        params.append(since)
    if args.hash:
        where.append("config_hash IN ({})".format(",".join("?" * len(args.hash))))
        params.extend(args.hash)
    sel = " AND ".join(where)

    rows = conn.execute(
        f"SELECT id, ts, symbol, direction, entry, stop, composite, ta_score, fa_score, liquidity_score,"
        f" regime, rank_in_cycle, voided_at FROM signal_log WHERE {sel} ORDER BY symbol, ts", params
    ).fetchall()
    if not rows:
        print("no signals match", file=sys.stderr)
        return 1
    lo, hi = min(r["ts"] for r in rows), max(r["ts"] for r in rows)
    print(f"signals: {len(rows)}  range: {datetime.fromtimestamp(lo, UTC):%Y-%m-%d %H:%MZ} → "
          f"{datetime.fromtimestamp(hi, UTC):%Y-%m-%d %H:%MZ}  symbols: {len({r['symbol'] for r in rows})}")

    # 1. forward return of the signal price series (every emitted row carries the close)
    series: dict[str, dict[int, float]] = defaultdict(dict)
    all_rows = conn.execute("SELECT ts, symbol, entry FROM signal_log WHERE timeframe=? AND entry>0", (args.tf,)).fetchall()
    for r in all_rows:
        series[r["symbol"]][int(r["ts"])] = float(r["entry"])

    def fwd(sym: str, ts: int, h: int) -> float | None:
        p0, p1 = series[sym].get(ts), series[sym].get(ts + h)
        return None if not (p0 and p1) else (p1 / p0 - 1) * 100

    print("\n1) forward return of the signal price series (close-to-close, no stop/TP)")
    print(f"{'horizon':8s} " + " ".join(f"{d:>22s}" for d in ("EXIT", "NEUTRAL", "LONG")))
    for h, lbl in ((3600, "1h"), (14400, "4h"), (86400, "24h")):
        cells = []
        for d in ("EXIT", "NEUTRAL", "LONG"):
            v = [f for f in (fwd(r["symbol"], r["ts"], h) for r in rows if r["direction"] == d and not r["voided_at"]) if f is not None]
            cells.append(f"{st.mean(v):+.3f}% (n={len(v)})" if v else "—")
        print(f"{lbl:8s} " + " ".join(f"{c:>22s}" for c in cells))

    # 2. IC per component (1h forward return, LONG rows)
    print("\n2) Spearman IC vs 1h forward return, LONG rows")
    longs = [r for r in rows if r["direction"] == "LONG" and not r["voided_at"]]
    pairs = [(r, fwd(r["symbol"], r["ts"], 3600)) for r in longs]
    pairs = [(r, f) for r, f in pairs if f is not None]
    for comp in ("ta_score", "fa_score", "liquidity_score", "composite"):
        xs = [float(r[comp]) for r, _ in pairs if r[comp] is not None]
        ys = [f for r, f in pairs if r[comp] is not None]
        ic = spearman(xs, ys)
        print(f"  {comp:16s} IC={ic:+.3f}  n={len(xs)}" if ic is not None else f"  {comp:16s} n/a")
    by_reg: dict[str, list[float]] = defaultdict(list)
    for r, f in pairs:
        by_reg[str(r["regime"])].append(f)
    print("  by regime: " + ", ".join(f"{k} {st.mean(v):+.3f}% (n={len(v)})" for k, v in by_reg.items()))

    # 3. outcomes
    ids = [r["id"] for r in longs]
    out_by: dict[str, list[sqlite3.Row]] = defaultdict(list)
    if ids:
        q = ("SELECT o.*, s.entry AS s_entry, s.stop AS s_stop FROM signal_outcomes o JOIN signal_log s ON s.id=o.signal_id "
             "WHERE o.signal_id IN ({})".format(",".join("?" * len(ids))))
        for o in conn.execute(q, ids):
            out_by[o["horizon_key"]].append(o)
    print("\n3) outcomes per horizon (resolved LONG rows)")
    for hk in sorted(out_by):
        v = out_by[hk]
        rs = [o["r_multiple"] for o in v if o["r_multiple"] is not None]
        mix = Counter(o["exit_reason"] or "legacy" for o in v)
        print(f"  {hk}: n={len(v)} R̄={st.mean(rs):+.3f} tp1={st.mean(o['hit_tp1'] or 0 for o in v):.2f} "
              f"sl={st.mean(o['hit_sl'] or 0 for o in v):.2f} timeout={st.mean(o['timeout'] or 0 for o in v):.2f}  {dict(mix)}")
    h1 = [o for o in out_by.get("H1", []) if o["mae_pct"] is not None and o["s_entry"] and o["s_stop"]]
    if h1:
        dists = [abs(o["s_entry"] - o["s_stop"]) / o["s_entry"] * 100 for o in h1]
        print(f"  stop distance median {st.median(dists):.2f}% — H1 stop-hit share if stop were k×ATR (from recorded MAE):")
        for k in (1.0, 1.5, 2.0, 3.0, 4.0):
            hit = sum(1 for o, d in zip(h1, dists, strict=True) if o["mae_pct"] <= -(k * d / 2.0)) / len(h1)
            print(f"    {k:.1f}×ATR → {hit:.2f}")

    # 3b. ADR-0050: the same outcomes split by the market regime recorded at issue
    print("\n3b) LONG 1h return by market regime recorded at issue (ADR-0050 shadow)")
    import json as _json
    feat = {r["id"]: r["features"] for r in conn.execute(
        f"SELECT id, features FROM signal_log WHERE {sel} AND direction='LONG'", params)}
    by_regime: dict[str, list[float]] = defaultdict(list)
    for r, f in pairs:
        try:
            state = (_json.loads(feat.get(r["id"]) or "{}").get("market_regime")) or "not-recorded"
        except ValueError:
            state = "not-recorded"
        by_regime[str(state)].append(f)
    for k, v in sorted(by_regime.items(), key=lambda kv: -len(kv[1])):
        print(f"  {k:14s} n={len(v):4d} mean={st.mean(v):+.3f}%  win={sum(1 for x in v if x > 0) / len(v):.2f}")
    if set(by_regime) == {"not-recorded"}:
        print("  (rows predate the regime gate; nothing to split yet)")

    # 4. symbols
    print("\n4) 1h return after LONG by symbol (n ≥ 15)")
    by_sym: dict[str, list[float]] = defaultdict(list)
    for r, f in pairs:
        by_sym[r["symbol"]].append(f)
    table = sorted(((s, v) for s, v in by_sym.items() if len(v) >= 15), key=lambda kv: st.mean(kv[1]))
    for s, v in table[:8]:
        print(f"  {s:12s} n={len(v):3d} mean={st.mean(v):+.3f}%")
    if len(table) > 8:
        print("  …")
        for s, v in table[-4:]:
            print(f"  {s:12s} n={len(v):3d} mean={st.mean(v):+.3f}%")
    print("\nfees are not included in any number above; the clean window may be short — read with the date range in mind.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
