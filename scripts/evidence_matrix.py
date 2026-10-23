#!/usr/bin/env python3
"""Run the A/B evidence matrix and write one artefact with its provenance.

    python3 scripts/evidence_matrix.py                        # reference feed, 4 symbols x 3 timeframes
    python3 scripts/evidence_matrix.py --source nobitex       # the venue — the only run that can matter
    python3 scripts/evidence_matrix.py --out docs/evidence/x.json --factor 1.25

Why a matrix instead of one number
----------------------------------
A single `favours-adaptive` on one symbol and one timeframe is an anecdote. The
question ADR-0021 asks — *is the higher bar better?* — is only interesting across
symbols and timeframes, because the whole premise of ADR-0020 is that the fixed
bar behaves differently on each of them. So this runs 4 × 3 cells and writes down
every cell, including the refusals.

What the artefact says about itself
-----------------------------------
Every cell carries `applied: false`, `promotable: false` and its source. A run on
the reference feed is stamped `REFERENCE_DATA` and its verdicts are capped at
`inconclusive` by `adaptive_evidence` — a simulated or foreign feed can
demonstrate the method, never certify the strategy (ADR-0009/ADR-0014). The file
therefore records what was measured and what it may not be used to claim.

Exit codes: 0 when every cell produced an answer (a named refusal counts as an
answer), 1 when at least one cell raised something unnamed — an exception is not
evidence, and hiding it behind a table would be the worst of both.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Persian and em dashes on a console that is not UTF-8 (Windows cp1252, POSIX C):
#: printing them raised `UnicodeEncodeError` and the script "failed" for a reason
#: that had nothing to do with what it was checking. Reconfigure, do not escape.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import adaptive_evidence
import reference_feed

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
TIMEFRAMES = ("15m", "1h", "4h")
VERDICTS = {"favours-adaptive", "favours-baseline", "inconclusive"}


async def fetch(symbol: str, tf: str, source: str, bars: int, toman: float | None):
    if source == "reference":
        return await reference_feed.fetch_candles(symbol, tf, limit=bars, fx=toman)
    import main  # local: only the venue path needs the app

    return await main.client.get_candles_ex(symbol, tf, bars)


def _stamp(source: str) -> dict[str, Any]:
    """What this run may and may not be used to say."""
    promotable = source == "nobitex"
    return {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "label": "VENUE_DATA" if promotable else "REFERENCE_DATA",
        "promotable": promotable,
        "applied": False,
        "note": (
            "venue candles — a result here can support an ADR-0021 decision, and still "
            "changes nothing by itself"
            if promotable else
            "reference feed — demonstrates the method only; ADR-0009/ADR-0014 forbid "
            "certifying a strategy on it, so every verdict stays inconclusive"
        ),
    }


async def run(args: argparse.Namespace) -> int:
    if args.source == "reference" and args.bars < 900:
        # The walk-forward needs three windows; a short history turns every cell
        # into "inconclusive" for a reason that has nothing to do with the bar.
        args.bars = 900

    cells: list[dict[str, Any]] = []
    unnamed_failures = 0
    for symbol in args.symbols:
        for tf in args.timeframes:
            try:
                rows, _meta = await fetch(symbol, tf, args.source, args.bars, args.toman)
                out = adaptive_evidence.compare(
                    rows, tf, symbol, factor=args.factor, source=args.source)
            except Exception as exc:                      # a refusal is a result; a crash is not
                code = getattr(exc, "code", None)
                message = getattr(exc, "message", str(exc))
                if code is None:
                    unnamed_failures += 1
                cells.append({
                    "symbol": symbol, "tf": tf, "ok": False,
                    "error": code or type(exc).__name__,
                    "message": str(message)[:300],
                    "named": code is not None,
                })
                print(f"{symbol:>9} {tf:>4}  refused: {code or type(exc).__name__} — {str(message)[:60]}")
                continue

            if out.get("ok"):
                cells.append({
                    "symbol": symbol,
                    "tf": tf,
                    "ok": True,
                    "bars": out.get("bars"),
                    "factor": out.get("factor"),
                    "thresholds": out.get("thresholds"),
                    "windows": out.get("windows"),
                    "baseline": out.get("baseline"),
                    "adaptive": out.get("adaptive"),
                    "delta": out.get("delta"),
                    "verdict": out.get("verdict"),
                    "verdict_capped": out.get("verdict_capped"),
                    "why": out.get("why"),
                    "applied": False,
                    "promotable": False,
                    "measurement": out.get("measurement"),
                })
                b, a = out["baseline"], out["adaptive"]
                print(f"{symbol:>9} {tf:>4}  PF {b['profit_factor']:>5.2f}→{a['profit_factor']:<5.2f} "
                      f"trades {b['trades']:>3}→{a['trades']:<3} {out['verdict']:>16}")
            else:
                cells.append({
                    "symbol": symbol, "tf": tf, "ok": False,
                    "error": out.get("error"), "message": out.get("message"),
                    "thresholds": out.get("thresholds"), "named": True,
                })
                print(f"{symbol:>9} {tf:>4}  refused: {out.get('error')} — {str(out.get('message'))[:60]}")

    artefact = {
        "kind": "adaptive-evidence-matrix",
        "version": (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        if (ROOT / "VERSION").is_file() else None,
        "provenance": _stamp(args.source),
        "matrix": {
            "symbols": list(args.symbols),
            "timeframes": list(args.timeframes),
            "bars": args.bars,
            "factor": args.factor,
            "cells": len(cells),
        },
        "summary": _summary(cells),
        "cells": cells,
    }

    out_path = Path(args.out) if args.out else (
        ROOT / "docs" / "evidence" / f"adaptive-evidence-{datetime.now(UTC):%Y-%m-%d}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artefact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    s = artefact["summary"]
    print()
    print(f"{artefact['provenance']['label']} · {s['answered']}/{s['cells']} cells answered · "
          f"favours-adaptive {s['favours_adaptive']} · favours-baseline {s['favours_baseline']} · "
          f"inconclusive {s['inconclusive']}")
    shown = out_path.relative_to(ROOT) if out_path.is_relative_to(ROOT) else out_path
    print(f"applied: false — evidence for ADR-0021, not a retune. wrote {shown}")
    if not artefact["provenance"]["promotable"]:
        print("reference feed: these numbers cannot certify a strategy (ADR-0009/ADR-0014).")
    return 0 if unnamed_failures == 0 else 1


def _summary(cells: list[dict[str, Any]]) -> dict[str, int]:
    ok = [c for c in cells if c.get("ok")]
    verdicts = [c.get("verdict") for c in ok]
    return {
        "cells": len(cells),
        "answered": len(ok),
        "refused": sum(1 for c in cells if not c.get("ok")),
        "unnamed_failures": sum(1 for c in cells if not c.get("ok") and not c.get("named")),
        "favours_adaptive": verdicts.count("favours-adaptive"),
        "favours_baseline": verdicts.count("favours-baseline"),
        "inconclusive": verdicts.count("inconclusive"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=("reference", "nobitex"), default="reference")
    ap.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    ap.add_argument("--timeframes", nargs="+", default=list(TIMEFRAMES))
    ap.add_argument("--bars", type=int, default=900)
    ap.add_argument("--factor", type=float, default=None,
                    help="test this factor instead of the one adaptive.py proposes")
    ap.add_argument("--toman", type=float, default=None,
                    help="toman per USDT, needed for an IRT symbol on the reference feed")
    ap.add_argument("--out", default=None, help="artefact path (default: docs/evidence/…)")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
