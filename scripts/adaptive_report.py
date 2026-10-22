#!/usr/bin/env python3
"""Print what the fixed score thresholds amount to across timeframes.

    python3 scripts/adaptive_report.py BTCUSDT --source reference
    python3 scripts/adaptive_report.py BTCIRT  --source reference --toman 100798.65
    python3 scripts/adaptive_report.py BTCIRT  --source nobitex
    python3 scripts/adaptive_report.py BTCUSDT --source nobitex --evidence
    python3 scripts/adaptive_report.py BTCUSDT --source nobitex --evidence --factor 1.25

`LONG_SCORE_MIN = 68` is one number for every timeframe, so "the strategy is
quiet" and "68 is unreachable at 4h" look identical on the dashboard. This prints
the measurement side by side — ATR percentile, regime, the factor `adaptive.py`
would apply, and how often the *fixed* threshold actually fires — so a tuning
argument can start from numbers instead of taste.

`--evidence` adds the paired walk-forward from `adaptive_evidence.py`: the same
windows, one variable, and a verdict that arrives with its sample size. It is the
same comparison the dashboard shows — and like the dashboard, it prints and does
not apply. `applied` is `false` on every row, including a verdict that favours a
different bar.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))

import adaptive
import adaptive_evidence
import reference_feed

TFS = ("15m", "1h", "4h", "D")


async def fetch(symbol: str, tf: str, source: str, bars: int, toman: float | None):
    if source == "reference":
        return await reference_feed.fetch_candles(symbol, tf, limit=bars, fx=toman)
    import main  # local: only needed for the venue path

    return await main.client.get_candles_ex(symbol, tf, bars)


async def run(args: argparse.Namespace) -> int:
    header = (
        f"{'tf':>4} {'bars':>5} {'ATR%':>7} {'pctile':>7} {'regime':>9} "
        f"{'factor':>7} {'long':>11} {'neutral':>11} {'LONG share':>11}"
    )
    print(f"{args.symbol.upper()} — source: {args.source}")
    print(header)
    print("-" * len(header))
    failures = 0
    for tf in args.timeframes:
        try:
            rows, meta = await fetch(args.symbol, tf, args.source, args.bars, args.toman)
            rep = adaptive.report(rows, tf)
        except Exception as exc:                            # a refusal is a result
            failures += 1
            code = getattr(exc, "code", type(exc).__name__)
            message = getattr(exc, "message", str(exc))
            print(f"{tf:>4} {'—':>5}  refused: {code} — {message[:60]}")
            continue
        vol, rec, base = rep["volatility"], rep["thresholds"]["recommended"], rep["thresholds"]["base"]
        arrow = "→"
        print(
            f"{tf:>4} {rep['frequency']['bars']:>5} {vol['atr_pct_price']:>7.2f} "
            f"{vol['percentile'] * 100:>6.0f}% {rep['regime']:>9} "
            f"{rep['scales']['entry']:>7.2f} "
            f"{base['long']:>4.0f} {arrow} {rec['long']:>4.1f} "
            f"{base['neutral']:>4.0f} {arrow} {rec['neutral']:>4.1f} "
            f"{rep['frequency']['long_share'] * 100:>10.1f}%"
        )
        if args.verbose and meta:
            pair = meta.get("pair", "?")
            derived = " (derived)" if meta.get("derived") else ""
            print(f"      pair {pair}{derived}, bars {rep['frequency']['bars']}, "
                  f"median score {rep['frequency']['median_score']}")
    print()
    print("applied: false — this is a measurement, not a retune. See ADR-0020.")

    if args.evidence:
        print()
        await print_evidence(args)
    return 1 if failures == len(args.timeframes) else 0


async def print_evidence(args: argparse.Namespace) -> None:
    """The A/B walk-forward, on the same candles, printed the same way it is shown."""
    header = (f"{'tf':>4} {'windows':>7} {'PF base→cand':>13} {'Δexp R':>7} "
              f"{'trades':>7} {'verdict':>20}")
    print(f"{args.symbol.upper()} — paired walk-forward, source: {args.source}")
    print(header)
    print("-" * len(header))
    for tf in args.timeframes:
        try:
            rows, _meta = await fetch(args.symbol, tf, args.source, max(args.bars, 900), args.toman)
            out = adaptive_evidence.compare(rows, tf, args.symbol.upper(),
                                            factor=args.factor, source=args.source)
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            message = getattr(exc, "message", str(exc))
            print(f"{tf:>4} refused: {code} — {message[:70]}")
            continue
        if not out.get("ok"):
            print(f"{tf:>4} refused: {out.get('error')} — {str(out.get('message'))[:70]}")
            continue
        b, a, d, w = out["baseline"], out["adaptive"], out["delta"], out["windows"]
        print(f"{tf:>4} {w['count']:>7} {b['profit_factor']:>6.2f}→{a['profit_factor']:<6.2f} "
              f"{d['expectancy_r']:>+7.3f} {b['trades']:>3}→{a['trades']:<3} "
              f"{out['verdict']:>20}")
        print(f"      {out['why']}")
    print()
    print("applied: false — evidence for ADR-0021, not a retune of the engine.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol", nargs="?", default="BTCUSDT")
    ap.add_argument("--source", choices=("reference", "nobitex"), default="reference")
    ap.add_argument("--bars", type=int, default=400)
    ap.add_argument("--toman", type=float, default=None,
                    help="toman per USDT, required to derive a rial market from the reference feed")
    ap.add_argument("--timeframes", nargs="+", default=list(TFS))
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--evidence", action="store_true",
                    help="also run the paired walk-forward (higher/lower bar vs the constant)")
    ap.add_argument("--factor", type=float, default=None,
                    help="test this factor instead of the one adaptive.py proposes")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
