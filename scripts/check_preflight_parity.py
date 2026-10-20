#!/usr/bin/env python3
"""Compare `risk_engine.preflight` (Python) with `NBX.Risk.preflight` (JS).

Both brokers run the same gates; this script is the proof. It builds a sweep of
inputs that lands on both sides of every threshold, asks each implementation,
and reports the first gate that disagrees — id, `ok`, value or limit.

    python3 scripts/check_preflight_parity.py          # exit 1 on any divergence
    python3 scripts/check_preflight_parity.py -v       # print every case

The sweep is deterministic: no random sampling, so a failure is reproducible.
"""
from __future__ import annotations

import argparse
import json
import itertools
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from risk_engine import RiskConfig, preflight  # noqa: E402

DEFAULTS = RiskConfig(equity=100_000.0)
TOL = 1e-6


def build_cases() -> list[dict]:
    """A grid that touches every gate from both sides."""
    cases: list[dict] = []

    def add(**kw):
        kw.setdefault("concurrent_limit", DEFAULTS.max_concurrent)
        kw.setdefault("max_heat_pct", DEFAULTS.max_heat_pct)
        kw.setdefault("min_cash_pct", DEFAULTS.min_cash_pct)
        kw.setdefault("max_single_asset_pct", DEFAULTS.max_single_asset_pct)
        kw.setdefault("risk_pct_cap", DEFAULTS.risk_pct)
        kw.setdefault("halted", False)
        kw.setdefault("min_notional_usd", 0.0)
        kw.setdefault("market_closed", False)
        kw["id"] = f"c{len(cases):03d}"
        cases.append(kw)

    equity = 100_000.0
    # A clean order, a size exactly on the 25 % cap, and one just over it.
    add(equity=equity, cash=equity, open_heat=0.0, open_positions=0,
        notional_usd=10_000.0, fee_usd=20.0, risk_usd=100.0)
    add(equity=equity, cash=equity, open_heat=0.0, open_positions=0,
        notional_usd=25_000.0, fee_usd=50.0, risk_usd=125.0)
    add(equity=equity, cash=equity, open_heat=0.0, open_positions=0,
        notional_usd=25_000.01, fee_usd=50.0, risk_usd=125.0)

    # Every boolean gate, flipped on its own.
    add(equity=equity, cash=equity, open_heat=0.0, open_positions=0,
        notional_usd=5_000.0, fee_usd=10.0, risk_usd=50.0, halted=True)
    add(equity=equity, cash=equity, open_heat=0.0, open_positions=0,
        notional_usd=5_000.0, fee_usd=10.0, risk_usd=50.0, market_closed=True)
    add(equity=equity, cash=equity, open_heat=0.0, open_positions=5,
        notional_usd=5_000.0, fee_usd=10.0, risk_usd=50.0)
    add(equity=equity, cash=0.0, open_heat=0.0, open_positions=0,
        notional_usd=5_000.0, fee_usd=10.0, risk_usd=50.0)
    add(equity=equity, cash=equity, open_heat=0.0, open_positions=0,
        notional_usd=5_000.0, fee_usd=10.0, risk_usd=50.0, min_notional_usd=11.0)
    add(equity=equity, cash=equity, open_heat=0.0, open_positions=0,
        notional_usd=0.5, fee_usd=0.001, risk_usd=0.02, min_notional_usd=11.0)

    # Risk, heat, cash floor and funds, each side of the line.
    add(equity=equity, cash=equity, open_heat=0.0, open_positions=0,
        notional_usd=5_000.0, fee_usd=10.0, risk_usd=750.0)          # exactly 0.75 %
    add(equity=equity, cash=equity, open_heat=0.0, open_positions=0,
        notional_usd=5_000.0, fee_usd=10.0, risk_usd=788.0)          # 0.788 % (within 5 % slack)
    add(equity=equity, cash=equity, open_heat=0.0, open_positions=0,
        notional_usd=5_000.0, fee_usd=10.0, risk_usd=800.0)          # 0.80 % (outside slack)
    add(equity=equity, cash=equity, open_heat=5.99, open_positions=0,
        notional_usd=5_000.0, fee_usd=10.0, risk_usd=100.0)
    add(equity=equity, cash=equity, open_heat=6.0, open_positions=0,
        notional_usd=5_000.0, fee_usd=10.0, risk_usd=100.0)
    add(equity=equity, cash=10_500.0, open_heat=0.0, open_positions=0,
        notional_usd=500.0, fee_usd=1.0, risk_usd=10.0)              # right at the 10 % floor
    add(equity=equity, cash=10_400.0, open_heat=0.0, open_positions=0,
        notional_usd=500.0, fee_usd=1.0, risk_usd=10.0)
    add(equity=equity, cash=1_000.0, open_heat=0.0, open_positions=0,
        notional_usd=5_000.0, fee_usd=10.0, risk_usd=50.0)           # funds

    # A small account where the exchange minimum dominates, and the bounds.
    add(equity=200.0, cash=200.0, open_heat=0.0, open_positions=0,
        notional_usd=3.0, fee_usd=0.006, risk_usd=0.5, min_notional_usd=2.95)
    add(equity=1e-12, cash=0.0, open_heat=0.0, open_positions=0,
        notional_usd=0.0, fee_usd=0.0, risk_usd=0.0)

    # Ratio-gate sweep: any (equity, notional) pair must give the same answer.
    for eq, notional in itertools.product((1_000.0, 100_000.0, 1e6),
                                          (100.0, 250.0, 250_000.0)):
        add(equity=eq, cash=eq, open_heat=0.0, open_positions=0,
            notional_usd=notional, fee_usd=notional * 0.002, risk_usd=notional * 0.01)
    return cases


def python_side(cases: list[dict]) -> list[dict]:
    out = []
    for c in cases:
        pf = preflight(
            equity=c["equity"], cash=c["cash"], open_heat=c["open_heat"],
            open_positions=c["open_positions"], concurrent_limit=c["concurrent_limit"],
            max_heat_pct=c["max_heat_pct"], min_cash_pct=c["min_cash_pct"],
            max_single_asset_pct=c["max_single_asset_pct"],
            risk_pct_cap=c["risk_pct_cap"], halted=c["halted"],
            notional_usd=c["notional_usd"], fee_usd=c["fee_usd"],
            risk_usd=c["risk_usd"], min_notional_usd=c["min_notional_usd"],
            market_closed=c["market_closed"],
        )
        out.append({
            "id": c["id"], "blocked": pf.blocked,
            "blocked_by": (pf.first_block().id if pf.first_block() else None),
            "risk_pct": round(pf.risk_pct, 6), "heat_after": round(pf.heat_after, 6),
            "cash_after": pf.cash_after,
            "gates": [{"id": g.id, "ok": g.ok, "value": g.value, "limit": g.limit}
                      for g in pf.gates],
        })
    return out


def js_side(cases: list[dict]) -> dict:
    node = shutil.which("node")
    if not node:
        raise SystemExit("node is not installed; the JS broker cannot be checked")
    proc = subprocess.run(
        [node, str(ROOT / "scripts" / "preflight_parity.js")],
        input=json.dumps(cases), text=True, capture_output=True, timeout=60,
    )
    if proc.returncode != 0:
        raise SystemExit(f"node failed:\n{proc.stderr}")
    return json.loads(proc.stdout)


def same(a, b) -> bool:
    if a is None or b is None:
        return a is b or (a is None and b is None)
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) is bool(b)
    return abs(float(a) - float(b)) <= TOL * max(1.0, abs(float(a)), abs(float(b)))


def compare(py: list[dict], js: list[dict], verbose: bool) -> list[str]:
    problems: list[str] = []
    if len(py) != len(js):
        return [f"case count differs: {len(py)} python vs {len(js)} js"]
    for p, j in zip(py, js):
        if p["id"] != j["id"]:
            problems.append(f"{p['id']}: js answered for {j['id']}")
            continue
        if p["blocked"] != j["blocked"]:
            problems.append(f"{p['id']}: blocked {p['blocked']} (py) vs {j['blocked']} (js)")
        if p["blocked_by"] != j["blocked_by"]:
            problems.append(f"{p['id']}: blocked_by {p['blocked_by']} vs {j['blocked_by']}")
        for key in ("risk_pct", "heat_after", "cash_after"):
            if not same(p[key], j[key]):
                problems.append(f"{p['id']}: {key} {p[key]} vs {j[key]}")
        pi = {g["id"]: g for g in p["gates"]}
        ji = {g["id"]: g for g in j["gates"]}
        if set(pi) != set(ji):
            problems.append(f"{p['id']}: gate ids {sorted(pi)} vs {sorted(ji)}")
            continue
        for gid, pg in pi.items():
            jg = ji[gid]
            if pg["ok"] != jg["ok"]:
                problems.append(f"{p['id']}/{gid}: ok {pg['ok']} vs {jg['ok']}")
            for key in ("value", "limit"):
                if not same(pg[key], jg[key]):
                    problems.append(f"{p['id']}/{gid}: {key} {pg[key]} vs {jg[key]}")
        if verbose:
            print(f"{p['id']}: {'BLOCK ' + str(p['blocked_by']) if p['blocked'] else 'pass'}"
                  f"  ({len(p['gates'])} gates)")
    return problems


def compare_tables(js: dict, verbose: bool) -> list[str]:
    """The precision tables are part of the contract; the two copies must agree."""
    import units

    problems = []
    amt, price = js["tables"]["AMOUNT_STEP"], js["tables"]["PRICE_STEP"]
    for sym, step in amt.items():
        py = units._AMOUNT_STEP.get(sym)
        if py is None:
            problems.append(f"amount step {sym}: present in js ({step}), missing in python")
        elif not same(py, step):
            problems.append(f"amount step {sym}: python {py} vs js {step}")
    for sym, step in units._AMOUNT_STEP.items():
        if sym not in amt:
            problems.append(f"amount step {sym}: present in python ({step}), missing in js")
    for q, step in price.items():
        py = units._PRICE_STEP.get(q)
        if py is None or not same(py, step):
            problems.append(f"price step {q}: python {py} vs js {step}")
    for q, v in units.MIN_NOTIONAL_QUOTE.items():
        jv = js["tables"]["MIN_NOTIONAL_QUOTE"].get(q)
        if jv is None or not same(jv, v):
            problems.append(f"min notional {q}: python {v} vs js {jv}")

    cfg = RiskConfig(equity=1.0)
    for key in ("risk_pct", "max_concurrent", "max_single_asset_pct",
                "max_heat_pct", "min_cash_pct"):
        py, jv = getattr(cfg, key), js["defaults"].get(key)
        if not same(py, jv):
            problems.append(f"risk default {key}: python {py} vs js {jv}")

    if verbose and not problems:
        print(f"tables: {len(amt)} amount steps, {len(price)} price steps, "
              f"{len(units.MIN_NOTIONAL_QUOTE)} minimums, 5 risk defaults — identical")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    cases = build_cases()
    py = python_side(cases)
    payload = js_side(cases)
    problems = compare(py, payload["results"], args.verbose)
    problems += compare_tables(payload, args.verbose)

    if problems:
        print(f"FAIL — {len(problems)} divergence(s) between the two brokers")
        for p in problems[:20]:
            print(" -", p)
        if len(problems) > 20:
            print(f"   … and {len(problems) - 20} more")
        return 1
    print(f"PASS — {len(cases)} cases × 10 gates: python and js agree, "
          f"tables and defaults identical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
