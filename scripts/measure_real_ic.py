#!/usr/bin/env python3
"""Measure the TA score's real-market IC, outside the journal.

The MSI reference in `journal.backtest_ic()` needs candles. This sandbox can
reach Coinbase and Kraken but *not* Nobitex (timeout) or Binance (HTTP 451),
so this script answers a narrower question than the journal does:

    "Does the score predict forward returns in the real market at all?"

It uses the same math as `journal.ic_by_symbol()` — Spearman per symbol, then
an equal-weight mean — on live 15-minute candles from a public venue. It does
**not** prove anything about Nobitex's IRT pairs, whose returns carry an extra
USDTIRT leg and thinner books.

    python3 scripts/measure_real_ic.py --venue coinbase --symbols 10 --bars 1500
    python3 scripts/measure_real_ic.py --venue kraken  --horizons 4,24,96 --bootstrap 0

Exit code 0 always: this is a measurement, not a gate. Read the JSON.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
from ta_engine import ta_score_series  # noqa: E402

COINBASE = {
    "BTC": "BTC-USD", "ETH": "ETH-USD", "SOL": "SOL-USD", "XRP": "XRP-USD",
    "ADA": "ADA-USD", "DOGE": "DOGE-USD", "AVAX": "AVAX-USD", "DOT": "DOT-USD",
    "LINK": "LINK-USD", "LTC": "LTC-USD", "SHIB": "SHIB-USD", "MATIC": "MATIC-USD",
}
# Kraken keeps only 720 bars per interval; 12 of 14 USDT pairs resolve.
KRAKEN = {
    "BTC": "XBTUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT",
    "ADA": "ADAUSDT", "DOGE": "DOGEUSDT", "AVAX": "AVAXUSDT", "DOT": "DOTUSDT",
    "LINK": "LINKUSDT", "LTC": "LTCUSDT", "SHIB": "SHIBUSDT",
}
UA = {"User-Agent": "nobi-research/1.0"}


def _get(url: str, tries: int = 3) -> list:
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                return json.load(r)
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    return []


def fetch_coinbase(pair: str, bars: int, gran: int = 900) -> pd.DataFrame | None:
    """Coinbase returns 300 candles per call and paginates on `end`."""
    rows: list[list] = []
    end = int(time.time())
    while len(rows) < bars:
        url = (f"https://api.exchange.coinbase.com/products/{pair}/candles"
               f"?granularity={gran}&start={end - 300 * gran}&end={end}")
        try:
            page = _get(url)
        except Exception:
            break
        if not page:
            break
        rows += page
        end = min(int(x[0]) for x in page) - gran
        time.sleep(0.9)          # public rate limit: ~3 req/s
    if not rows:
        return None
    by_ts = {int(x[0]): x for x in rows}
    df = pd.DataFrame([by_ts[k] for k in sorted(by_ts)], columns=["ts", "l", "h", "o", "c", "v"])
    return df.assign(ts=df["ts"].astype(int))[["ts", "o", "h", "l", "c", "v"]].astype(float)


def fetch_kraken(pair: str, bars: int, gran: int = 900) -> pd.DataFrame | None:
    """Kraken caps at 720 rows and `since` does not page backwards, so this is
    a one-shot 7.5-day window regardless of `bars`."""
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={gran // 60}"
    try:
        d = _get(url)
    except Exception:
        return None
    if d.get("error"):
        return None
    key = next((k for k in d["result"] if k != "last"), None)
    if not key:
        return None
    df = pd.DataFrame(d["result"][key], columns=["ts", "o", "h", "l", "c", "v", "vwap", "count"])
    return df[["ts", "o", "h", "l", "c", "v"]].astype(float).astype({"ts": int})


def _rank(a: list[float]) -> list[float]:
    order = sorted(range(len(a)), key=lambda i: a[i])
    r = [0.0] * len(a)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and a[order[j + 1]] == a[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def spearman(xs: list[float], ys: list[float]) -> float | None:
    rx, ry = _rank(xs), _rank(ys)
    n = len(rx)
    if n < 3:
        return None
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return None if dx == 0 or dy == 0 else num / (dx * dy)


def load(args) -> dict[str, pd.DataFrame]:
    table = COINBASE if args.venue == "coinbase" else KRAKEN
    fetch = fetch_coinbase if args.venue == "coinbase" else fetch_kraken
    out = {}
    for i, (base, pair) in enumerate(table.items()):
        if i >= args.symbols:
            break
        df = fetch(pair, args.bars)
        if df is None or len(df) < 200:
            print(f"  {base:5s} {pair:10s} skipped (no usable data)", flush=True)
            continue
        out[base] = df
        print(f"  {base:5s} {pair:10s} {len(df)} bars", flush=True)
    return out


def per_symbol_ic(frames: dict[str, pd.DataFrame], horizon: int):
    """Same shape as journal.ic_by_symbol: xs = score, ys = forward return %."""
    per, series = {}, {}
    for base, df in frames.items():
        snaps = ta_score_series(df)
        close = df["c"].to_numpy(float)
        xs, ys, ts = [], [], []
        for i, snap in enumerate(snaps):
            if snap is None or i + horizon >= len(close):
                continue
            xs.append(float(snap["score"]))
            ys.append(float((close[i + horizon] / close[i] - 1) * 100))
            ts.append(int(df["ts"].iloc[i]))
        if len(xs) < 30:
            continue
        per[base] = spearman(xs, ys)
        series[base] = (xs, ys, ts)
    vals = [v for v in per.values() if v is not None]
    mean = sum(vals) / len(vals) if vals else None
    ordered = sorted(vals)
    median = None if not ordered else (
        ordered[len(ordered) // 2] if len(ordered) % 2
        else (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2)
    return per, mean, median, series


def block_bootstrap(series: dict, horizon: int, resamples: int, block: int) -> dict:
    """Overlapping forward returns make consecutive bars non-independent, so
    resample contiguous blocks of time jointly across symbols."""
    common = sorted(set.intersection(*[set(v[2]) for v in series.values()]))
    if len(common) <= block * 2:
        return {"error": "too few common bars"}
    pos = {b: {t: i for i, t in enumerate(v[2])} for b, v in series.items()}
    X = {b: [series[b][0][pos[b][t]] for t in common] for b in series}
    Y = {b: [series[b][1][pos[b][t]] for t in common] for b in series}

    def mean_ic(sel):
        ics = [spearman([X[b][i] for i in sel], [Y[b][i] for i in sel]) for b in X]
        ics = [v for v in ics if v is not None]
        return sum(ics) / len(ics) if ics else None

    point = mean_ic(range(len(common)))
    rnd = random.Random(7)
    K, nblocks, boot = len(common), max(1, len(common) // block), []
    for _ in range(resamples):
        sel = []
        for _ in range(nblocks):
            st = rnd.randrange(0, K - block)
            sel += list(range(st, st + block))
        v = mean_ic(sel)
        if v is not None:
            boot.append(v)
    boot.sort()
    return {
        "common_bars": len(common),
        "point": round(point, 4) if point is not None else None,
        "resamples": len(boot),
        "block": block,
        "ci95": [round(boot[int(0.025 * len(boot))], 4), round(boot[int(0.975 * len(boot))], 4)],
        "share_negative": round(sum(1 for v in boot if v < 0) / len(boot), 3),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", choices=("coinbase", "kraken"), default="coinbase")
    ap.add_argument("--symbols", type=int, default=10)
    ap.add_argument("--bars", type=int, default=1500)
    ap.add_argument("--horizons", default="4,24,96")
    ap.add_argument("--bootstrap", type=int, default=500)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    print(f"venue={args.venue} symbols={args.symbols} bars={args.bars}", flush=True)
    frames = load(args)
    if not frames:
        raise SystemExit("no data: the venue is unreachable from this host")

    report = {"venue": args.venue, "symbols": list(frames),
              "bars": {k: len(v) for k, v in frames.items()},
              "generated_at": int(time.time()), "horizons": {}}

    for h in [int(x) for x in args.horizons.split(",")]:
        per, mean, median, series = per_symbol_ic(frames, h)
        row = {"per_symbol": {k: (round(v, 4) if v is not None else None) for k, v in per.items()},
               "mean": round(mean, 4) if mean is not None else None,
               "median": round(median, 4) if median is not None else None,
               "symbols_scored": sum(1 for v in per.values() if v is not None)}
        if args.bootstrap and series:
            row["bootstrap"] = block_bootstrap(series, h, args.bootstrap, block=h)
        report["horizons"][f"h{h}"] = row
        print(f"  h={h:3d} mean {row['mean']:+.4f} median {row['median']:+.4f} "
              f"({row['symbols_scored']}/{len(frames)} symbols)", flush=True)
        if "bootstrap" in row and "ci95" in row["bootstrap"]:
            b = row["bootstrap"]
            print(f"        95% CI [{b['ci95'][0]:+.4f}, {b['ci95'][1]:+.4f}] "
                  f"share<0 {b['share_negative']:.1%}", flush=True)

    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"\nwrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
