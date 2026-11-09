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


def reject_counts(orders: list[dict], *, since: float | None = None) -> dict:
    """Same shape as server/diagnostics.reject_counts; duplicated so the script
    stays runnable on a copy of the state file without the server on sys.path."""
    by_reason: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_rs: dict[str, dict[str, int]] = {}
    by_day: dict[str, int] = {}
    total = 0
    for ev in orders:
        if not isinstance(ev, dict) or ev.get("kind") != "reject":
            continue
        try:
            ts = float(ev.get("ts"))
        except (TypeError, ValueError):
            continue
        if since is not None and ts < since:
            continue
        extra = ev.get("extra") if isinstance(ev.get("extra"), dict) else {}
        reason = str(extra.get("reason") or "?")
        source = str(extra.get("source") or "?").upper()
        total += 1
        by_reason[reason] = by_reason.get(reason, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1
        by_rs.setdefault(reason, {})
        by_rs[reason][source] = by_rs[reason].get(source, 0) + 1
        day = datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d")
        by_day[day] = by_day.get(day, 0) + 1
    def order(d):
        return dict(sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))
    return {"total": total, "by_reason": order(by_reason), "by_source": order(by_source),
            "by_reason_source": {k: order(v) for k, v in by_rs.items()}, "by_day": dict(sorted(by_day.items()))}


def stop_slip_stats(orders: list[dict], *, since: float | None = None) -> dict:
    """Stop slippage from paper `close`/`partial` events with a stop reason.

    Reads `stop_slip_pct` / `stop_slip_r` written by the broker since 2.5.2;
    older close events (no field) are counted in `legacy` and skipped.
    """
    pct: list[float] = []
    rr: list[float] = []
    legacy = 0
    by_reason: dict[str, list[float]] = {}
    for ev in orders:
        if not isinstance(ev, dict) or ev.get("kind") not in ("close", "partial"):
            continue
        extra = ev.get("extra") if isinstance(ev.get("extra"), dict) else {}
        if extra.get("reason") not in ("sl-hit", "trail"):
            continue
        try:
            ts = float(ev.get("ts"))
        except (TypeError, ValueError):
            continue
        if since is not None and ts < since:
            continue
        p = extra.get("stop_slip_pct")
        if not isinstance(p, (int, float)):
            legacy += 1
            continue
        pct.append(float(p))
        by_reason.setdefault(str(extra.get("reason")), []).append(float(p))
        r = extra.get("stop_slip_r")
        if isinstance(r, (int, float)):
            rr.append(float(r))

    def q(v: list[float], f: float) -> float | None:
        if not v:
            return None
        v = sorted(v)
        k = min(len(v) - 1, max(0, round(f * (len(v) - 1))))
        return v[k]

    return {"n": len(pct), "legacy": legacy,
            "pct": {"mean": st.mean(pct) if pct else None, "median": q(pct, 0.5), "p90": q(pct, 0.9),
                    "worse_share": (sum(1 for x in pct if x > 0) / len(pct)) if pct else None},
            "r": {"mean": st.mean(rr) if rr else None, "median": q(rr, 0.5), "p90": q(rr, 0.9)},
            "by_reason": {k: {"n": len(v), "mean": st.mean(v)} for k, v in by_reason.items()}}


def print_stop_slip(orders: list[dict], since: int | None) -> None:
    c = stop_slip_stats(orders, since=since)
    print(f"\n7) stop slippage (item 9): exit fill vs model stop on sl-hit/trail closes — n={c['n']}"
          + (f", {c['legacy']} older close(s) without the field skipped" if c["legacy"] else ""))
    if not c["n"]:
        print("  none")
        return
    p, r = c["pct"], c["r"]
    def f(x: float | None, d: int = 3) -> str:
        return "—" if x is None else f"{x:+.{d}f}"
    print(f"  in % of stop:  mean {f(p['mean'])}  median {f(p['median'])}  p90 {f(p['p90'])}  "
          f"worse-than-stop share {100 * p['worse_share']:.0f}%")
    print(f"  in R (entry→stop): mean {f(r['mean'])}  median {f(r['median'])}  p90 {f(r['p90'])}")
    for k, v in c["by_reason"].items():
        print(f"  {k:8s} n={v['n']:3d}  mean {v['mean']:+.3f}%")
    print("  read: positive = filled past the stop (a 1 R plan lost 1+slip R); add mean R to every stop-rate × 1 R in sections 3/5 to see the realised cost.")


def print_paper_rejects(path: str, since: int | None) -> None:
    import json as _pj
    try:
        with open(path, encoding="utf-8") as fh:
            state = _pj.load(fh)
    except (OSError, ValueError) as e:
        print(f"\n6) paper rejects: cannot read {path}: {e}")
        return
    orders = state.get("orders") or []
    c = reject_counts(orders, since=since)
    kept = len(orders)
    print(f"\n6) paper rejects by reason × source (item 7) — {c['total']} reject(s)"
          f"{' since ' + datetime.fromtimestamp(since, UTC).strftime('%Y-%m-%d %H:%MZ') if since else ''};"
          f" the file keeps the last {kept} event(s) only, older rejects are gone")
    if not c["total"]:
        print("  none")
        print_stop_slip(orders, since)   # section 7 does not depend on section 6 having rows
        return
    sources = list(c["by_source"])
    print(f"  {'reason':28s} {'total':>5s}  " + " ".join(f"{src:>7s}" for src in sources))
    for reason, n in c["by_reason"].items():
        row = c["by_reason_source"].get(reason, {})
        print(f"  {reason:28s} {n:5d}  " + " ".join(f"{row.get(src, 0):7d}" for src in sources))
    queue_total = c["by_source"].get("QUEUE", 0)
    pre = sum(1 for ev in orders if isinstance(ev, dict) and ev.get("kind") == "reject"
              and (ev.get("extra") or {}).get("source") == "QUEUE" and (ev.get("extra") or {}).get("pre_broker")
              and (since is None or float(ev.get("ts") or 0) >= since))
    print(f"  approvals that never became orders: {queue_total} (of which {pre} refused before the broker: "
          f"queue clock / stale scan / moved price)")
    print("  by day: " + ", ".join(f"{d} {n}" for d, n in c["by_day"].items()))
    print("  read: '?' source = events written before 2.5.2; counts are from the state file's rolling window, not a ledger.")
    print_stop_slip(orders, since)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db")
    ap.add_argument("--since", help="ISO time; ignore signals before it")
    ap.add_argument("--hash", action="append", default=[], help="config_hash to include (repeatable)")
    ap.add_argument("--tf", default="15m")
    ap.add_argument("--paper", help="copy of paper_state.json: adds section 6, rejects by reason × source (2.5.2, item 7)")
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
    # ADR-0052: say which side of the frozen holdout this run is on. The
    # cutoff lives in journal_meta; the report never moves it.
    _h = conn.execute("SELECT value FROM journal_meta WHERE key='holdout_cutoff'").fetchone()
    if _h:
        import json as _hj
        _cut = int(_hj.loads(_h[0])["cutoff_ts"])
        _since = since
        print(f"holdout cutoff: {datetime.fromtimestamp(_cut, UTC).isoformat()}  "
              f"({'this window is HELD OUT (post-cutoff)' if (_since or 0) >= _cut else 'this window includes IN-SAMPLE rows — compare per side'})")
    else:
        print("holdout cutoff: not frozen (scripts/journal_holdout.py freeze --at …)")
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
        keys = v[0].keys()
        rn = [o["r_net"] for o in v if "r_net" in keys and o["r_net"] is not None]
        net = f" R̄net={st.mean(rn):+.3f} (n={len(rn)})" if rn else " R̄net=— (no net-of-fee rows yet)"
        print(f"  {hk}: n={len(v)} R̄={st.mean(rs):+.3f}{net} tp1={st.mean(o['hit_tp1'] or 0 for o in v):.2f} "
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
    # 5. ADR-0051 shadow arms: identical bars, identical stop geometry, different verdicts
    print("\n5) Shadow arms (ADR-0051): H1 R̄ / stop-rate of the bars each arm called LONG")
    has_shadow = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='signal_shadow_outcomes'").fetchone()
    arm_rows = [] if not has_shadow else conn.execute(
        f"""SELECT s.id, s.direction, s.features,
                   o.r_multiple AS r2, o.hit_sl AS sl2,
                   (SELECT r_multiple FROM signal_shadow_outcomes x
                     WHERE x.signal_id=s.id AND x.horizon_key='H1' AND x.risk_k=2.0) AS r2s,
                   (SELECT hit_sl FROM signal_shadow_outcomes x
                     WHERE x.signal_id=s.id AND x.horizon_key='H1' AND x.risk_k=2.0) AS sl2s,
                   (SELECT r_multiple FROM signal_shadow_outcomes x
                     WHERE x.signal_id=s.id AND x.horizon_key='H1' AND x.risk_k=3.0) AS r3,
                   (SELECT hit_sl FROM signal_shadow_outcomes x
                     WHERE x.signal_id=s.id AND x.horizon_key='H1' AND x.risk_k=3.0) AS sl3
            FROM signal_log s
            LEFT JOIN signal_outcomes o ON o.signal_id=s.id AND o.horizon_key='H1'
            WHERE {sel} AND json_extract(s.features,'$.shadow_arms') IS NOT NULL""", params).fetchall()
    if not arm_rows:
        print("  (no shadow table / no rows with arms yet — they appear with the first deploy of ADR-0051)")
    else:
        import json as _json
        arms: dict[str, dict[str, list]] = defaultdict(lambda: {"r2": [], "sl2": [], "r3": [], "sl3": []})
        for r in arm_rows:
            feats = _json.loads(r["features"] or "{}")
            verdicts = {"A_live": {"direction": r["direction"]}, **(feats.get("shadow_arms") or {})}
            r2 = r["r2"] if r["r2"] is not None else r["r2s"]
            sl2 = r["sl2"] if r["sl2"] is not None else r["sl2s"]
            for name, v in verdicts.items():
                if (v or {}).get("direction") != "LONG":
                    continue
                if r2 is not None:
                    arms[name]["r2"].append(r2)
                    arms[name]["sl2"].append(sl2 or 0)
                if r["r3"] is not None:
                    arms[name]["r3"].append(r["r3"])
                    arms[name]["sl3"].append(r["sl3"] or 0)
        print(f"  {'arm':10s} {'n(k=2)':>7s} {'R̄ k=2':>8s} {'stop%':>6s}   {'n(k=3)':>7s} {'R̄ k=3':>8s} {'stop%':>6s}")
        for name in sorted(arms):
            a = arms[name]
            def _f(v): return f"{st.mean(v):+.3f}" if v else "   —  "
            def _p(v): return f"{100 * st.mean(v):5.0f}%" if v else "   — "
            print(f"  {name:10s} {len(a['r2']):7d} {_f(a['r2']):>8s} {_p(a['sl2']):>6s}   "
                  f"{len(a['r3']):7d} {_f(a['r3']):>8s} {_p(a['sl3']):>6s}")
        print("  read: same bars, same entry/ATR; an arm 'wins' only with ≥500 LONGs and non-overlapping 95% CIs.")

    if args.paper:
        print_paper_rejects(args.paper, since)

    print("\nsection 1/2/4 returns are gross; R̄net (section 3) deducts the round-trip fee assumption recorded per row; the clean window may be short — read with the date range in mind.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
