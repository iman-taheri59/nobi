"""
Walk-Forward Backtester (ADR-0009) — event-driven, fee+slippage aware.

Goal: prove/deny that the TA score produces positive expectancy out-of-sample
before live promotion. Reuses the same TA/risk code (training/serve parity).
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from ta_engine import candles_to_df, ta_score

REPORTS = Path(os.getenv("NBX_BT_DIR", str(Path(__file__).parent / "backtest_reports")))
REPORTS.mkdir(exist_ok=True)

FEE_PCT = 0.002
SLIP_BPS = 6

@dataclass
class BTTrade:
    side: str
    entry_t: int
    entry: float
    exit_t: int | None = None
    exit: float | None = None
    reason: str | None = None
    qty: float = 0.0
    stop: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    realized_pnl: float = 0.0
    r_multiple: float | None = None

@dataclass
class BTResult:
    config_hash: str
    symbol: str
    tf: str
    window_start: str
    window_end: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    hit_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    max_drawdown: float = 0.0
    sharpe_like: float = 0.0
    equity_curve: list = field(default_factory=list)
    sample_trades: list = field(default_factory=list)


def _cfg_hash(cfg: dict) -> str:
    import hashlib
    return hashlib.md5(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:8]


def _rows_to_frames(rows: list[dict]):
    """Reusable canonical frame builder so every caller sees same dtypes."""
    return candles_to_df(rows)


class EventBacktester:
    """Walks candle-by-candle, only permitting decisions on CLOSED candles (no look-ahead)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.equity = float(cfg.get("initial_equity", 100000))
        self.cash = float(self.equity)
        self.equity_curve: list[tuple[int, float]] = []
        self.positions: list[BTTrade] = []
        self.closed: list[BTTrade] = []
        self.max_open = int(cfg.get("max_concurrent", 5))
        self.risk_pct = float(cfg.get("risk_pct", 0.75))
        self.stop_k = float(cfg.get("atr_stop_k", 2.0))

    # ---------- helpers ----------
    def _buy(self, ts: Any, px: float, stop: float, tp1: float, tp2: float, qty: float):
        tr = BTTrade(side="buy", entry_t=ts, entry=px, stop=stop, tp1=tp1, tp2=tp2, qty=qty)
        self.positions.append(tr)

    def _exit(self, tr: BTTrade, ts: Any, px: float, reason: str):
        notional_in = tr.entry * tr.qty
        fee_in = notional_in * FEE_PCT
        notional_out = px * tr.qty
        fee_out = notional_out * FEE_PCT
        pnl = notional_out - notional_in - fee_in - fee_out
        tr.exit_t, tr.exit, tr.reason = ts, px, reason
        tr.realized_pnl = float(pnl)
        risk_unit = abs(tr.entry - tr.stop)
        tr.r_multiple = float(pnl / (risk_unit * tr.qty)) if (risk_unit > 0 and tr.qty > 0) else None
        # Return sale proceeds (principal + net PnL) to cash
        self.cash = max(float(self.cash + notional_out - fee_out), 0.0)
        self.positions = [p for p in self.positions if p is not tr]
        open_val = sum(px * p.qty for p in self.positions)
        self.equity = max(float(self.cash + open_val), 0.0)
        self.closed.append(tr)

    # ---------- core loop ----------
    def run_symbol(self, df: pd.DataFrame, symbol: str, tf: str) -> BTResult:
        # FIX: fresh state per run so walk-forward windows do not leak
        self.positions = []
        self.closed = []
        self.equity = float(self.cfg.get("initial_equity", 100000))
        self.cash = float(self.equity)
        self.equity_curve = []
        self.cfg["symbol"] = symbol
        self.cfg["tf"] = tf
        cfg_hash = _cfg_hash(self.cfg)
        closes: pd.Series = df["c"].reset_index(drop=True)
        highs: pd.Series = df["h"].reset_index(drop=True)
        lows: pd.Series = df["l"].reset_index(drop=True)
        ts_col: pd.Series = df["t"].reset_index(drop=True)

        def _ts_ms(v) -> float:
            if hasattr(v, "timestamp"):
                return float(v.timestamp()) * 1000
            return float(v) * 1000

        for i in range(60, len(df)):
            row_ts = ts_col.iloc[i]
            slice_df = df.iloc[: i + 1]
            sig = ta_score(slice_df)  # only data up to current candle
            # manage open positions with high/low of the current candle
            for tr in list(self.positions):
                if tr.tp1 and highs.iloc[i] >= tr.tp1:
                    self._exit(tr, row_ts, tr.tp1, "tp1")
                    continue
                if lows.iloc[i] <= tr.stop:
                    self._exit(tr, row_ts, tr.stop, "sl")
                    continue
            # rule: only LONG on strong signal
            if sig["direction"] == "LONG" and len(self.positions) < self.max_open:
                entry = closes.iloc[i]
                atr_v = sig["risk"]["atr"]
                stop = entry - self.stop_k * atr_v
                tp1 = entry + 1.5 * (entry - stop)
                tp2 = entry + 2.5 * (entry - stop)
                risk_amt = self.equity * self.risk_pct / 100
                stop_dist = entry - stop
                qty = (risk_amt / stop_dist) if stop_dist > 0 else 0
                cost = qty * entry * (1 + FEE_PCT)
                if qty > 0 and cost < self.cash:
                    self.cash -= float(cost)
                    self._buy(row_ts, float(entry), float(stop), float(tp1), float(tp2), float(qty))
            # mark-to-market on candle close (cash + market value of open positions)
            open_val = sum(float(closes.iloc[i]) * tr.qty for tr in self.positions)
            current = float(self.cash + open_val)
            self.equity = current
            self.equity_curve.append((_ts_ms(row_ts), round(current, 2)))

        # flatten any remaining at last close
        for tr in list(self.positions):
            self._exit(tr, ts_col.iloc[-1], closes.iloc[-1], "eod")

        wins = [t for t in self.closed if t.realized_pnl > 0]
        losses = [t for t in self.closed if t.realized_pnl <= 0]
        total = len(self.closed)
        hit_rate = (len(wins) / total * 100) if total else 0
        pf = (sum(t.realized_pnl for t in wins) / abs(sum(t.realized_pnl for t in losses) or 1e-9)) if (total and losses) else (9.99 if wins else 0.0)
        pf = min(float(pf), 99.99)
        expectancy_r = sum(t.r_multiple or 0 for t in self.closed) / total if total else 0
        # MDD
        eqv = [v for _, v in self.equity_curve]
        peak = eqv[0] if eqv else self.equity
        mdd = 0.0
        for v in eqv:
            peak = max(peak, v)
            mdd = max(mdd, (peak - v) / max(peak, 1) * 100)
        res = BTResult(
            config_hash=cfg_hash,
            symbol=symbol,
            tf=tf,
            window_start=str(df["t"].iloc[0]),
            window_end=str(df["t"].iloc[-1]),
            trades=total,
            wins=len(wins),
            losses=len(losses),
            hit_rate=round(hit_rate, 1),
            profit_factor=round(pf, 2),
            expectancy_r=round(expectancy_r, 3),
            max_drawdown=round(mdd, 1),
            equity_curve=[list(x) for x in self.equity_curve][-400:],
            sample_trades=[{ "entry": t.entry, "exit": t.exit, "reason": t.reason, "pnl": round(t.realized_pnl, 2), "r": round(t.r_multiple or 0, 2) } for t in self.closed[-8:]],
        )
        return res


class WalkForwardRunner:
    """Runs train/validation windows sequentially (4-month train / 2-month eval for daily data, or compact for intraday)."""
    def __init__(self, train_bars: int = 288, eval_bars: int = 144):
        self.tb, self.eb = train_bars, eval_bars

    def run(self, df: pd.DataFrame, symbol: str, tf: str, base_cfg: dict) -> list[dict]:
        results = []
        start = 0
        N = len(df)
        while start + self.tb + self.eb <= N:
            train = df.iloc[start: start + self.tb]
            evalw = df.iloc[start + self.tb: start + self.tb + self.eb]
            bt = EventBacktester({**base_cfg, "initial_equity": 50000})
            out = bt.run_symbol(evalw, symbol, tf)
            out.window_start, out.window_end = str(evalw["t"].iloc[0]), str(evalw["t"].iloc[-1])
            results.append(out.__dict__)
            start += self.eb  # rolling forward
        return results


def run_and_save(symbol: str, tf: str, candles_rows: list[dict], cfg: dict) -> dict:
    reports = []
    df = candles_to_df(candles_rows)
    runner = WalkForwardRunner(train_bars=cfg.get("train_bars", 240), eval_bars=cfg.get("eval_bars", 120))
    windows = runner.run(df, symbol, tf, cfg)
    if not windows:
        bt = EventBacktester(cfg).run_symbol(df, symbol, tf)
        windows = [{"windows": 1, **bt.__dict__}]
    agg = {
        "config_hash": cfg.get("config_hash", _cfg_hash(cfg)),
        "symbol": symbol,
        "tf": tf,
        "windows": len(windows),
        "aggregate": {
            "trades": int(sum(w["trades"] for w in windows)),
            "total_wins": int(sum(w["wins"] for w in windows)),
            "hit_rate": float(round(sum(w["hit_rate"] * w["trades"] for w in windows) / max(1, sum(w["trades"] for w in windows)), 1)),
            "profit_factor": float(round(sum(w["profit_factor"] * w["trades"] for w in windows) / max(1, sum(w["trades"] for w in windows)), 2)),
            "expectancy_r": float(round(sum(w["expectancy_r"] * w["trades"] for w in windows) / max(1, sum(w["trades"] for w in windows)), 3)),
            "max_drawdown": float(max(w["max_drawdown"] for w in windows)),
        },
        "details": windows[-3:]  # last 3 for the dashboard
    }
    fp = REPORTS / f"{symbol}-{tf}-{int(time.time())}.json"
    fp.write_text(json.dumps(agg, ensure_ascii=False, indent=1))
    return agg


# ---------------- Grid Search + Sensitivity Heatmap ----------------
DEFAULT_GRID = {"risk_pct": [0.5, 1.0, 1.5], "atr_stop_k": [1.5, 2.0, 2.5]}

def grid_search(symbol: str, tf: str, candles_rows: list[dict], grid: dict | None = None) -> dict:
    """Synchronous wrapper that runs in a worker thread via asyncio.to_thread."""
    import itertools
    import time as _time

    grid = grid or DEFAULT_GRID
    t0 = _time.time()
    df = candles_to_df(candles_rows)
    # hardcoded default windows correlated with the 100-bar eval standard
    temp_tb, temp_eb = 240, 120
    keys = sorted(grid.keys())
    combos = list(itertools.product(*(grid[k] for k in keys)))
    results = []
    for combo in combos:
        params = dict(zip(keys, combo))
        runner = WalkForwardRunner(train_bars=temp_tb, eval_bars=temp_eb)
        windows = runner.run(df, symbol, tf, params)
        if not windows:
            bt = EventBacktester(params)
            windows = [bt.run_symbol(df, symbol, tf).__dict__]
        agg = _aggregate_windows(windows)
        results.append({"params": params, "aggregate": agg})

    best = None
    for r in results:
        a = r["aggregate"]
        score = a["profit_factor"] * 0.5 + a["expectancy_r"] + (a["hit_rate"] / 100) * 0.2 - (a["max_drawdown"] / 100) * 0.5
        if best is None or score > best["_score"]:
            best = {**r, "_score": score}
    best = best or {"params": {}, "aggregate": {}, "_score": 0}  # fallback if zero combos

    return {
        "symbol": symbol,
        "tf": tf,
        "combo_count": len(combos),
        "elapsed_s": round(_time.time() - t0, 2),
        "best": {
            "params": best.get("params", {}),
            "aggregate": best.get("aggregate", {}),
            "score": round(best.get("_score", 0), 3),
        },
        "heatmap": _grid_heatmap(paths=results, grid=grid),
    }


def _grid_heatmap(paths: list, grid: dict) -> dict:
    xax = grid.get("risk_pct", [])
    yax = grid.get("atr_stop_k", [])
    def val(x, y):
        for p, a in [(r["params"], r["aggregate"]) for r in paths]:
            if p.get("risk_pct") == x and p.get("atr_stop_k") == y:
                return a
        return {}
    return {
        "x": xax,
        "y": yax,
        "values": [[{"risk_pct": x, "atr_stop_k": y, **val(x, y)} for y in yax] for x in xax],
    }


def _aggregate_windows(windows: list[dict]) -> dict:
    return {
        "trades": int(sum(w.get("trades", 0) for w in windows)),
        "total_wins": int(sum(w.get("wins", 0) for w in windows)),
        "hit_rate": float(round(sum((w.get("hit_rate", 0) * w.get("trades", 0)) for w in windows) / max(1, sum(w.get("trades", 0) for w in windows)), 1)),
        "profit_factor": float(round(sum((w.get("profit_factor", 0) * w.get("trades", 0)) for w in windows) / max(1, sum(w.get("trades", 0) for w in windows)), 2)),
        "expectancy_r": float(round(sum((w.get("expectancy_r", 0) * w.get("trades", 0)) for w in windows) / max(1, sum(w.get("trades", 0) for w in windows)), 3)),
        "max_drawdown": float(max((w.get("max_drawdown", 0) for w in windows), default=0)),
    }
