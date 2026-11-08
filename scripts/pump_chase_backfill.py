#!/usr/bin/env python3
"""Read-only: would a "do not chase" filter and an enforced depth gate have helped?

Three questions asked of the existing journal, nothing written:

1. **Chase filter** — a LONG issued after the price already ran: the signal's own
   price series (one point per scan cycle, per price contract) gives the return
   over the previous 1h/4h; the stored `atr` normalises it. Split live LONGs by
   `run_4h / ATR%` buckets and compare forward 1h/4h/24h returns and the H1 stop
   rate. Also `%B` from the stored diagnostics (above 1 = outside the upper
   Bollinger band).
2. **Depth gate** — `gate_shadow.depth_usd` recorded for the same symbol within
   ±10 min of the signal: forward returns by depth bucket, and the p05/p10/p25 of
   depth across all scanned rows (the thresholds the gate could use).
3. **Stop slippage** — not in the journal; the paper ledger's close events are
   the source. Reported here only as the formula and the one observed case.

Usage: python3 scripts/pump_chase_backfill.py /path/journal.db [--since ISO] [--md out.md]
"""
from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shadow_backfill import _epoch, attach_forward_returns, load, mean  # noqa: E402

LOOKBACKS = {"1h": 3600, "4h": 4 * 3600}
TOL = 20 * 60


def attach_lookback(rows: list[dict]) -> None:
    by: dict[tuple[str, str], list[tuple[int, float]]] = defaultdict(list)
    for r in rows:
        by[(r["symbol"], r["contract"])].append((r["ts"], r["entry"]))
    series = {k: ([p[0] for p in sorted(v)], [p[1] for p in sorted(v)]) for k, v in by.items()}
    for r in rows:
        ts_list, px = series[(r["symbol"], r["contract"])]
        r["back"] = {}
        for name, secs in LOOKBACKS.items():
            target = r["ts"] - secs
            i = bisect.bisect_left(ts_list, target - TOL)
            best = None
            while i < len(ts_list) and ts_list[i] <= target + TOL:
                if best is None or abs(ts_list[i] - target) < abs(ts_list[best] - target):
                    best = i
                i += 1
            r["back"][name] = None if best is None else (r["entry"] / px[best] - 1) * 100
        atr = r.get("atr")
        r["atr_pct"] = (float(atr) / r["entry"] * 100) if atr and r["entry"] else None
        b4 = r["back"].get("4h")
        r["run_atr"] = (b4 / r["atr_pct"]) if (b4 is not None and r["atr_pct"]) else None


def attach_depth(db: Path, rows: list[dict], tf: str) -> list[float]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    pts: dict[str, list[tuple[int, float]]] = defaultdict(list)
    all_depth: list[float] = []
    for sym, ts, d in conn.execute(
        "SELECT symbol, ts, depth_usd FROM gate_shadow WHERE timeframe=? AND depth_usd IS NOT NULL", (tf,)
    ):
        pts[sym].append((int(ts), float(d)))
        all_depth.append(float(d))
    conn.close()
    series = {k: ([p[0] for p in sorted(v)], [p[1] for p in sorted(v)]) for k, v in pts.items()}
    for r in rows:
        r["depth"] = None
        s = series.get(r["symbol"])
        if not s:
            continue
        ts_list, dv = s
        i = bisect.bisect_left(ts_list, r["ts"] - 600)
        best = None
        while i < len(ts_list) and ts_list[i] <= r["ts"] + 600:
            if best is None or abs(ts_list[i] - r["ts"]) < abs(ts_list[best] - r["ts"]):
                best = i
            i += 1
        r["depth"] = None if best is None else dv[best]
    return all_depth


def pctB(r: dict) -> float | None:
    return r.get("pctB")


def summarise(rows: list[dict]) -> dict:
    def stat(sub: list[dict]) -> dict:
        out: dict = {"n": len(sub)}
        for h in ("1h", "4h", "24h"):
            v = [x["fwd"][h] for x in sub if x.get("fwd", {}).get(h) is not None]
            out[h] = round(mean(v), 3) if v else None
            out[f"n{h}"] = len(v)
        h1 = [x["outcomes"]["H1"] for x in sub if x["outcomes"].get("H1")]
        out["H1_stop"] = round(sum(1 for o in h1 if o["hit_sl"]) / len(h1), 2) if h1 else None
        out["H1_R"] = round(mean([o["r_multiple"] for o in h1 if o["r_multiple"] is not None]), 3) if h1 else None
        return out
    return {"all": stat(rows)}


def bucket(rows: list[dict], key, edges: list[float], labels: list[str]) -> dict:
    out = {}
    groups: dict[str, list[dict]] = {lab: [] for lab in labels}
    for r in rows:
        v = key(r)
        if v is None:
            continue
        for i, e in enumerate(edges):
            if v < e:
                groups[labels[i]].append(r)
                break
        else:
            groups[labels[-1]].append(r)
    for lab, sub in groups.items():
        out[lab] = summarise(sub)["all"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("db", type=Path)
    ap.add_argument("--tf", default="15m")
    ap.add_argument("--since")
    ap.add_argument("--md", type=Path)
    ap.add_argument("--exclude", default="", help="comma-separated symbols to drop (e.g. a single pump that dominates 24h)")
    a = ap.parse_args()
    since = _epoch(a.since) if a.since else 0

    rows = load(a.db, a.tf, since=since)
    if a.exclude:
        drop = {x.strip().upper() for x in a.exclude.split(",") if x.strip()}
        rows = [r for r in rows if r["symbol"].upper() not in drop]
    # diagnostics.%B lives in features; load() drops it, so read it again cheaply
    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    pb = {}
    for sid, feat in conn.execute(
        "SELECT id, features FROM signal_log WHERE timeframe=? AND ts>=? AND direction='LONG'", (a.tf, since)
    ):
        try:
            pb[sid] = (json.loads(feat or "{}").get("diagnostics") or {}).get("pctB")
        except json.JSONDecodeError:
            pass
    conn.close()
    for r in rows:
        r["pctB"] = pb.get(r["id"])

    attach_forward_returns(rows)
    attach_lookback(rows)
    all_depth = attach_depth(a.db, rows, a.tf)
    longs = [r for r in rows if r["direction"] == "LONG"]

    rep = {
        "rows": len(rows), "longs": len(longs),
        "base": summarise(longs)["all"],
        "run_atr": bucket(longs, lambda r: r["run_atr"], [0, 1, 2, 3], ["<0 (down)", "0–1 ATR", "1–2 ATR", "2–3 ATR", "≥3 ATR"]),
        "back_4h_pct": bucket(longs, lambda r: r["back"].get("4h"), [0, 3, 6, 10], ["<0%", "0–3%", "3–6%", "6–10%", "≥10%"]),
        "pctB": bucket(longs, pctB, [0.8, 1.0, 1.1], ["<0.8", "0.8–1.0", "1.0–1.1", "≥1.1"]),
        "depth": bucket(longs, lambda r: r["depth"], [1000, 2500, 5000, 20000], ["<1k", "1k–2.5k", "2.5k–5k", "5k–20k", "≥20k"]),
        "depth_quantiles": {
            q: round(statistics.quantiles(all_depth, n=100)[int(q[1:]) - 1], 0) for q in ("p05", "p10", "p25", "p50")
        } if len(all_depth) > 100 else {},
        "depth_rows": len(all_depth),
    }
    # what a combined rule would have removed
    for name, pred in {
        "chase: run_4h ≥ 2 ATR": lambda r: (r["run_atr"] or 0) >= 2,
        "chase: %B ≥ 1.0": lambda r: (r["pctB"] or 0) >= 1.0,
        "depth < p10": lambda r: r["depth"] is not None and rep["depth_quantiles"] and r["depth"] < rep["depth_quantiles"]["p10"],
        "either chase rule": lambda r: (r["run_atr"] or 0) >= 2 or (r["pctB"] or 0) >= 1.0,
        "either chase or depth<p10": lambda r: (r["run_atr"] or 0) >= 2 or (r["pctB"] or 0) >= 1.0
        or (r["depth"] is not None and rep["depth_quantiles"] and r["depth"] < rep["depth_quantiles"]["p10"]),
    }.items():
        removed = [r for r in longs if pred(r)]
        kept = [r for r in longs if not pred(r)]
        rep.setdefault("rules", {})[name] = {"removed": summarise(removed)["all"], "kept": summarise(kept)["all"]}

    md = render(rep, str(a.db), a.since)
    if a.md:
        a.md.write_text(md, encoding="utf-8")
    print(md)
    return 0


def fmt(s: dict) -> str:
    return (f"{s['n']} | {s['1h']} | {s['4h']} | {s['24h']} (n={s['n24h']}) | "
            f"{s['H1_stop']} | {s['H1_R']}")


def render(rep: dict, db: str, since: str | None) -> str:
    L = [f"# Chase / depth backfill — `{db}`" + (f" since {since}" if since else ""), ""]
    L.append(f"rows {rep['rows']} · live LONGs **{rep['longs']}** · depth rows {rep['depth_rows']}")
    L.append("")
    hdr = "| bucket | n | 1h % | 4h % | 24h % (n) | H1 stop | H1 R̄ |\n|---|---|---|---|---|---|---|"
    L += ["## baseline (all live LONGs)", hdr, f"| all | {fmt(rep['base'])} |", ""]
    for key, title in (("run_atr", "## run-up over the previous 4h, in ATRs"),
                       ("back_4h_pct", "## run-up over the previous 4h, in %"),
                       ("pctB", "## Bollinger %B at issue"),
                       ("depth", "## order-book depth ±1% (USD) at issue")):
        L += [title, hdr]
        for lab, s in rep[key].items():
            L.append(f"| {lab} | {fmt(s)} |")
        L.append("")
    if rep["depth_quantiles"]:
        L.append("depth quantiles over every scanned row: " + ", ".join(f"{k}={v:.0f}" for k, v in rep["depth_quantiles"].items()))
        L.append("")
    L += ["## rules — what each would have removed vs kept", "| rule | side | n | 1h % | 4h % | 24h % (n) | H1 stop | H1 R̄ |", "|---|---|---|---|---|---|---|---|"]
    for name, rk in rep.get("rules", {}).items():
        L.append(f"| {name} | removed | {fmt(rk['removed'])} |")
        L.append(f"| | kept | {fmt(rk['kept'])} |")
    L += ["", "Forward returns come from the journal's own price series (±20 min, per price contract); "
          "H1 stop/R̄ from `signal_outcomes`. Read-only; nothing here changes behaviour."]
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
