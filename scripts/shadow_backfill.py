#!/usr/bin/env python3
"""Retrospective evaluation of the ADR-0051 shadow arms on an existing journal.

Read-only. The point: every input the shadow arms need (`ta_score`, `fa_score`,
`liquidity_score`, `veto_severe`, the live direction) is already stored on each
`signal_log` row, so arms B and D can be re-scored over the whole journal today
instead of waiting for the live recorder to accumulate rows. Forward returns come
from the journal itself as well: the same symbol is re-scored every ~15 minutes,
and its `entry` (plan price ≈ last close) is a price series with gaps. That gives
a horizon return for **every** row — including the NEUTRAL rows an arm would have
promoted, which the LONG-only resolver never touched.

What this can and cannot say
----------------------------
* Can: per arm, how many LONGs it would have issued, their mean forward return at
  1h/4h/24h (gross and net of the 0.4 % round trip), hit rate, and the Spearman IC
  of the arm's composite against forward return. Same bars, same symbols, so the
  arms are directly comparable with the live rule.
* Cannot: simulate a stop path (close-only series). `k=3` is therefore bounded,
  not simulated: among live LONGs that hit the 2×ATR stop, how many never saw an
  MAE of 3×ATR (those a k=3 stop would have survived), and what their 24h return
  was. Arm C (live + market regime) needs the market regime that only ships in
  2.5.1 — it is reported as "not backfillable".
* This is a *development* estimate: the arms were defined on 6 Nov with this
  data in view. The frozen holdout (2026-11-10T07:20Z onward) is the test.

Usage:
    python3 scripts/shadow_backfill.py /path/to/journal.db [--tf 15m] [--md out.md]
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from datetime import UTC

import shadow_arms

FEE_RT_PCT = 0.4
HORIZONS = {"1h": 3600, "4h": 14400, "24h": 86400}
TOLERANCE_S = 20 * 60


def _epoch(iso: str) -> int:
    from datetime import datetime
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UTC).timestamp())


def _spearman(x: list[float], y: list[float]) -> float | None:
    n = len(x)
    if n < 30:
        return None

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r

    rx, ry = ranks(x), ranks(y)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    vy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return cov / (vx * vy) if vx and vy else None


def load(db: Path, tf: str, since: int = 0, until: int | None = None) -> list[dict]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, ts, symbol, direction, ta_score, fa_score, liquidity_score, composite, "
        "entry, stop, atr, features, scan_cycle_id, price_contract FROM signal_log "
        "WHERE timeframe=? AND origin='live' AND voided_at IS NULL AND entry > 0 AND ts >= ? AND ts < ? ORDER BY ts",
        (tf, since, until if until is not None else 2**62),
    ).fetchall()
    out = []
    for r in rows:
        feat = json.loads(r["features"] or "{}")
        out.append({
            "id": r["id"], "ts": int(r["ts"]), "symbol": r["symbol"], "direction": r["direction"],
            "score": r["ta_score"], "fa_score": r["fa_score"], "liquidity_score": r["liquidity_score"],
            "composite": r["composite"], "entry": float(r["entry"]), "stop": r["stop"], "atr": r["atr"],
            "veto_severe": feat.get("veto_severe"), "cycle": r["scan_cycle_id"],
            "contract": r["price_contract"] or "legacy",
        })
    outcomes = {}
    for o in conn.execute(
        "SELECT signal_id, horizon_key, mae_pct, mfe_pct, hit_sl, hit_tp1, r_multiple, return_pct "
        "FROM signal_outcomes"
    ):
        outcomes[(o["signal_id"], o["horizon_key"])] = dict(o)
    conn.close()
    for r in out:
        r["outcomes"] = {h: outcomes.get((r["id"], h)) for h in ("H1", "H2")}
    return out


def attach_forward_returns(rows: list[dict]) -> None:
    series: dict[tuple[str, str], tuple[list[int], list[float]]] = {}
    # Keyed by (symbol, price contract): the IRT quote moved from rial to toman
    # mid-journal, and a return across that seam is a factor of ten, not a move.
    by_sym: dict[tuple[str, str], list[tuple[int, float]]] = defaultdict(list)
    for r in rows:
        by_sym[(r["symbol"], r["contract"])].append((r["ts"], r["entry"]))
    for sym, pts in by_sym.items():
        pts.sort()
        series[sym] = ([p[0] for p in pts], [p[1] for p in pts])
    for r in rows:
        ts_list, px = series[(r["symbol"], r["contract"])]
        r["fwd"] = {}
        for name, secs in HORIZONS.items():
            target = r["ts"] + secs
            i = bisect.bisect_left(ts_list, target - TOLERANCE_S)
            best = None
            while i < len(ts_list) and ts_list[i] <= target + TOLERANCE_S:
                if best is None or abs(ts_list[i] - target) < abs(ts_list[best] - target):
                    best = i
                i += 1
            v = None if best is None else (px[best] / r["entry"] - 1) * 100
            # A 15m crypto bar does not move 50 %: anything beyond is a quote
            # glitch or a contract seam the label missed. Drop, do not clip.
            r["fwd"][name] = None if v is None or abs(v) > 50 else v


def membership_groups(rows: list[dict]) -> dict[str, str]:
    per_cycle: dict[int, set[str]] = defaultdict(set)
    for r in rows:
        if r["cycle"] is not None:
            per_cycle[r["cycle"]].add(r["symbol"])
    full = [c for c, syms in per_cycle.items() if len(syms) >= 37]
    n = len(full)
    count: dict[str, int] = defaultdict(int)
    for c in full:
        for s in per_cycle[c]:
            count[s] += 1
    groups = {}
    for s, v in count.items():
        groups[s] = "core" if v >= 0.9 * n else ("mid" if v >= 0.5 * n else "transient")
    return groups


def arm_directions(r: dict) -> dict[str, tuple[str, float | None]]:
    scored = shadow_arms.score(r, None)
    out = {"live": (r["direction"], r["composite"])}
    for arm in ("B_fa_liq", "D_comp"):
        out[arm] = (scored[arm]["direction"], scored[arm]["composite"])
    out["TA_only"] = ("LONG" if (r["score"] or 0) >= shadow_arms.LONG_MIN and not r["veto_severe"] else "NEUTRAL", r["score"])
    # Baselines: every row (the universe itself) and the live EXIT rows. An arm
    # that does not beat "all rows" has no selection value whatever its sign.
    out["ALL_rows"] = ("LONG", r["composite"])
    out["live_EXIT"] = ("LONG" if r["direction"] == "EXIT" else "NEUTRAL", r["composite"])
    return out


def mean(v: list[float]) -> float:
    return sum(v) / len(v)


def evaluate(rows: list[dict]) -> dict:
    groups = membership_groups(rows)
    arms = ("live", "D_comp", "B_fa_liq", "TA_only", "ALL_rows", "live_EXIT")
    def _fresh() -> dict[str, Any]:
        return {"long": 0, "fwd": {h: [] for h in HORIZONS}, "ic_x": {h: [] for h in HORIZONS},
                "ic_y": {h: [] for h in HORIZONS}, "by_group": defaultdict(lambda: {h: [] for h in HORIZONS})}
    stats: dict[str, dict[str, Any]] = {a: _fresh() for a in arms}
    overlap: dict[tuple[str, ...], int] = defaultdict(int)
    for r in rows:
        dirs = arm_directions(r)
        key = tuple(a for a in arms[:4] if dirs[a][0] == "LONG")
        overlap[key] += 1
        for a in arms:
            d, comp = dirs[a]
            for h, v in r["fwd"].items():
                if v is None or comp is None:
                    continue
                stats[a]["ic_x"][h].append(float(comp))
                stats[a]["ic_y"][h].append(v)
            if d != "LONG":
                continue
            stats[a]["long"] += 1
            for h, v in r["fwd"].items():
                if v is not None:
                    stats[a]["fwd"][h].append(v)
                    stats[a]["by_group"][groups.get(r["symbol"], "?")][h].append(v)
    report: dict[str, Any] = {"rows": len(rows), "arms": {}, "overlap": {}}
    for a in arms:
        s = stats[a]
        report["arms"][a] = {
            "long": s["long"],
            "long_share_pct": round(100 * s["long"] / len(rows), 2),
            "fwd": {h: {"n": len(v), "mean_pct": None if not v else round(mean(v), 3),
                        "net_pct": None if not v else round(mean(v) - FEE_RT_PCT, 3),
                        "hit_rate_pct": None if not v else round(100 * sum(1 for x in v if x > 0) / len(v), 1)}
                    for h, v in s["fwd"].items()},
            "ic": {h: (None if (ic := _spearman(s["ic_x"][h], s["ic_y"][h])) is None else round(ic, 3))
                   for h in HORIZONS},
            "by_group": {g: {h: {"n": len(v), "mean_pct": None if not v else round(mean(v), 3)} for h, v in hv.items()}
                         for g, hv in s["by_group"].items()},
        }
    for key, n in sorted(overlap.items(), key=lambda kv: -kv[1]):
        if key:
            report["overlap"]["+".join(key)] = n
    # k=3 bound from the resolver's MAE/MFE on live LONGs
    stopped_k2 = 0
    would_survive = 0
    survivor_24h: list[float] = []
    survivor_mfe: list[float] = []
    for r in rows:
        o = r["outcomes"].get("H2")
        if r["direction"] != "LONG" or not o or not o["hit_sl"] or not r["stop"] or not r["entry"]:
            continue
        stop_pct = (r["entry"] - r["stop"]) / r["entry"] * 100
        stopped_k2 += 1
        if o["mae_pct"] is not None and -o["mae_pct"] < 1.5 * stop_pct:
            would_survive += 1
            if r["fwd"].get("24h") is not None:
                survivor_24h.append(r["fwd"]["24h"])
            if o["mfe_pct"] is not None:
                survivor_mfe.append(float(o["mfe_pct"]))
    report["k3_bound"] = {
        "stopped_at_k2_H2": stopped_k2,
        "would_survive_k3": would_survive,
        "share_pct": None if not stopped_k2 else round(100 * would_survive / stopped_k2, 1),
        "survivors_24h_return_mean_pct": None if not survivor_24h else round(mean(survivor_24h), 3),
        "survivors_mfe_mean_pct": None if not survivor_mfe else round(mean(survivor_mfe), 3),
        "note": "bound, not a simulation: a k=3 stop also changes TP1/TP2 and size; C_gate not backfillable (market regime ships in 2.5.1)",
    }
    return report


def to_markdown(rep: dict, db: str) -> str:
    L = [f"# Shadow arms — retrospective on `{db}`", "",
         f"rows (15m, live, non-void): **{rep['rows']}** · horizons from the journal's own price series "
         f"(±{TOLERANCE_S // 60} min) · fee round trip {FEE_RT_PCT}%", "",
         "| arm | LONGs | share | 1h mean | 1h net | 4h mean | 24h mean | 24h net | hit 24h | IC 1h | IC 24h |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for a, s in rep["arms"].items():
        f = s["fwd"]
        L.append(f"| {a} | {s['long']} | {s['long_share_pct']}% | {f['1h']['mean_pct']} | {f['1h']['net_pct']} | "
                 f"{f['4h']['mean_pct']} | {f['24h']['mean_pct']} | {f['24h']['net_pct']} | {f['24h']['hit_rate_pct']}% | "
                 f"{s['ic']['1h']} | {s['ic']['24h']} |")
    L += ["", "## LONG overlap between arms", ""]
    for k, n in rep["overlap"].items():
        L.append(f"- `{k}`: {n}")
    L += ["", "## By universe membership (24h mean %, n)", "", "| arm | core | mid | transient |", "|---|---|---|---|"]
    for a, s in rep["arms"].items():
        g = s["by_group"]
        cells = [f"{g[name]['24h']['mean_pct']} ({g[name]['24h']['n']})" if name in g else "—"
                 for name in ("core", "mid", "transient")]
        L.append(f"| {a} | {cells[0]} | {cells[1]} | {cells[2]} |")
    k = rep["k3_bound"]
    L += ["", "## k=3 bound (from resolver MAE on live LONGs stopped at H2)", "",
          f"- stopped at k=2: **{k['stopped_at_k2_H2']}**; never saw MAE ≥ 3×ATR (k=3 would have survived): "
          f"**{k['would_survive_k3']}** ({k['share_pct']}%)",
          f"- survivors' 24h close-to-close return: **{k['survivors_24h_return_mean_pct']}%**; their mean MFE: {k['survivors_mfe_mean_pct']}%",
          f"- {k['note']}", ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db", type=Path)
    ap.add_argument("--tf", default="15m")
    ap.add_argument("--md", type=Path, help="write a Markdown report here")
    ap.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    ap.add_argument("--since", help="ISO-8601 UTC lower bound on signal ts, e.g. 2026-11-05T07:19:52Z")
    ap.add_argument("--until", help="ISO-8601 UTC upper bound (exclusive)")
    a = ap.parse_args()
    rows = load(a.db, a.tf, since=_epoch(a.since) if a.since else 0, until=_epoch(a.until) if a.until else None)
    attach_forward_returns(rows)
    rep = evaluate(rows)
    md = to_markdown(rep, a.db.name)
    if a.md:
        a.md.write_text(md, encoding="utf-8")
    print(json.dumps(rep, indent=1, ensure_ascii=False) if a.json else md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
