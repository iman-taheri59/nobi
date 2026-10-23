"""
Walk-Forward Backtester (ADR-0009) — event-driven, fee-aware, no look-ahead.

Design
------
1. Signals are computed once per bar with `ta_score_series` (causal, identical
   to `ta_score(df.iloc[:i+1])`).
2. The frame is cut into rolling **train / evaluation** windows. For every
   window the parameter set is chosen on the *train* slice only, then applied
   unchanged to the *evaluation* slice. Only evaluation numbers are aggregated:
   that is what "out-of-sample" means.
3. Intrabar ambiguity (both stop and target inside one candle) is resolved
   pessimistically — we cannot know the path, so we assume the stop was hit
   first.
4. Position size follows the same guards as the live/paper engine: risk per
   trade from `risk_pct` *and* the 25 % single-asset cap.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from fees import FEE_PCT
from storage import writable_dir
from ta_engine import candles_to_df, ta_score_series

#: Where walk-forward reports are written. Outside the checkout on purpose: the
#: deployed unit has a read-only source tree, and `mkdir` at import time turned
#: that into a startup failure (see `storage.py`).
REPORTS = writable_dir("NBX_BT_DIR", Path(__file__).parent / "backtest_reports",
                       label="backtest-reports")

#: Default cost model. A backtest that ignores the spread is not a cheap
#: backtest, it is a different strategy: every target looks closer than it is.
#: `slippage_bps=0` is still allowed, but the promotion gate then refuses to
#: certify the result (see `costs_modeled`).
SLIP_BPS = 6

#: What the live decision actually blends (ADR-0007): TA 0.55, fundamentals
#: 0.25, liquidity 0.20, then vetoes can cap the composite below promotion.
#: Duplicated here on purpose — `server/main.py` imports this module, so the
#: values cannot live there, and importing main from here would be a cycle.
#: `tests/test_backtest_basis.py` asserts these equal `main.TA_W/FA_W/LIQ_W`.
LIVE_COMPOSITE_WEIGHTS: dict[str, float] = {"ta": 0.55, "fa": 0.25, "liq": 0.20}
LIVE_COMPOSITE = "ta+fa+liq"

#: What a walk-forward report is *able* to test. Fundamentals and liquidity have
#: no historical series in this repository (both are fetched point-in-time), and
#: inventing one would be worse than admitting the gap — so the backtest runs on
#: the TA component alone and says so in every report.
BACKTEST_COMPONENTS: tuple[str, ...] = ("ta",)


def decision_basis() -> dict[str, Any]:
    """Declare which strategy the numbers below were produced on.

    A report that cannot answer this is a report about an unnamed strategy. The
    live engine blends three components and applies vetoes; the walk-forward
    simulator reproduces the TA component only, because the other two have no
    history to replay.
    """
    weights = dict.fromkeys(BACKTEST_COMPONENTS, 1.0)
    return {
        "components": list(BACKTEST_COMPONENTS),
        "weights": weights,
        "vetoes": False,
        "live_composite": LIVE_COMPOSITE,
        "live_weights": dict(LIVE_COMPOSITE_WEIGHTS),
        "matches_live": tuple(LIVE_COMPOSITE_WEIGHTS) == BACKTEST_COMPONENTS,
    }


#: Appended to every gate verdict built on a TA-only run, so the sentence the
#: operator reads is never stronger than the evidence behind it.
BASIS_CAVEAT = (
    "This report measures the technical component only. The live decision also "
    "weights liquidity and fundamentals and can be capped by vetoes, so a PASS "
    "here certifies the TA rules, not the full live composite."
)
MIN_WARMUP = 60

# Strategy parameters that actually change trade decisions. `risk_pct` is not
# optimised: it scales position size but leaves R-multiples and PF unchanged.
DEFAULT_PARAM_GRID: list[dict[str, float]] = [
    {"atr_stop_k": k, "tp1_r": a, "tp2_r": b}
    for k in (1.5, 2.0, 2.5)
    for (a, b) in ((1.0, 2.0), (1.5, 2.5), (2.0, 3.5))
]

WF_MIN_WINDOWS = 3
WF_MIN_OOS_TRADES = 30

# Sensitivity sweep used by the heatmap (position size vs stop distance).
DEFAULT_GRID = {"risk_pct": [0.5, 1.0, 1.5], "atr_stop_k": [1.5, 2.0, 2.5]}


@dataclass
class BTTrade:
    side: str
    entry_t: Any
    entry: float
    qty: float
    initial_qty: float
    stop: float
    tp1: float
    tp2: float
    risk_amount: float = 0.0
    exit_t: Any = None
    exit: float | None = None
    reason: str | None = None
    realized_pnl: float = 0.0
    r_multiple: float | None = None
    tp1_done: bool = False
    ambiguous: bool = False

    @property
    def entry_ts(self) -> str:
        return str(self.entry_t)


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
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    expectancy_r: float = 0.0
    max_drawdown: float = 0.0
    sharpe_like: float = 0.0
    initial_equity: float = 0.0
    final_equity: float = 0.0
    ambiguous_exits: int = 0
    # [[ms, equity], …] — lists, not tuples: this value goes straight to JSON
    equity_curve: list[list[float]] = field(default_factory=list)
    sample_trades: list = field(default_factory=list)
    params: dict = field(default_factory=dict)


def _cfg_hash(cfg: dict) -> str:
    payload = {
        "atr_stop_k": cfg.get("atr_stop_k", 2.0),
        "tp1_r": cfg.get("tp1_r", 1.5),
        "tp2_r": cfg.get("tp2_r", 2.5),
        "fee": FEE_PCT,
        "slippage_bps": cfg.get("slippage_bps", SLIP_BPS),
        "max_single_asset_pct": cfg.get("max_single_asset_pct", 25.0),
        "basis": list(BACKTEST_COMPONENTS),
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:8]


def _rows_to_frames(rows: list[dict]):
    """Reusable canonical frame builder so every caller sees same dtypes."""
    return candles_to_df(rows)


class EventBacktester:
    """Candle-by-candle simulation; decisions only on closed candles."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.initial_equity = float(cfg.get("initial_equity", 100_000))
        self.equity = self.initial_equity
        self.cash = self.initial_equity
        # x is epoch **milliseconds** as a float (`_ts_ms` keeps the fraction)
        self.equity_curve: list[list[float]] = []
        self.positions: list[BTTrade] = []
        self.closed: list[BTTrade] = []
        self.max_open = int(cfg.get("max_concurrent", 5))
        self.risk_pct = float(cfg.get("risk_pct", 0.75))
        self.stop_k = float(cfg.get("atr_stop_k", 2.0))
        self.tp1_r = float(cfg.get("tp1_r", 1.5))
        self.tp2_r = float(cfg.get("tp2_r", 2.5))
        self.min_score = float(cfg.get("min_score", 0.0))
        self.max_single_asset_pct = float(cfg.get("max_single_asset_pct", 25.0))
        self.slippage = float(cfg.get("slippage_bps", SLIP_BPS)) / 10_000.0

    # ---------------- helpers ----------------
    def _ts_ms(self, v) -> float:
        if hasattr(v, "timestamp"):
            return float(v.timestamp()) * 1000
        return float(v) * 1000

    def _close_part(self, tr: BTTrade, ts: Any, px: float, frac: float, reason: str):
        qty = tr.qty * frac if frac < 1 else tr.qty
        if qty <= 0:
            return
        fill = px * (1 - self.slippage) if tr.side == "buy" else px * (1 + self.slippage)
        notional_out = fill * qty
        fee_out = notional_out * FEE_PCT
        cost_basis = tr.entry * qty
        pnl = notional_out - fee_out - cost_basis * (1 + FEE_PCT)
        tr.realized_pnl += pnl
        tr.qty -= qty
        self.cash += notional_out - fee_out
        tr.exit_t, tr.exit = ts, fill
        tr.reason = reason
        if tr.qty <= 1e-12:
            tr.qty = 0.0
            self.positions = [p for p in self.positions if p is not tr]
            tr.r_multiple = (tr.realized_pnl / tr.risk_amount) if tr.risk_amount > 0 else None
            self.closed.append(tr)

    # ---------------- core loop ----------------
    def run_symbol(
        self,
        df: pd.DataFrame,
        symbol: str,
        tf: str,
        signals: list[dict | None] | None = None,
        start: int | None = None,
        end: int | None = None,
    ) -> BTResult:
        n = len(df)
        start = MIN_WARMUP if start is None else max(0, int(start))
        end = n if end is None else min(n, int(end))
        if end - start <= 1:
            raise ValueError("backtest window is empty")

        self.positions = []
        self.closed = []
        self.equity_curve = []
        self.equity = self.initial_equity
        self.cash = self.initial_equity

        closes = df["c"].reset_index(drop=True)
        highs = df["h"].reset_index(drop=True)
        lows = df["l"].reset_index(drop=True)
        ts_col = df["t"].reset_index(drop=True)
        if signals is None:
            signals = ta_score_series(df)

        for i in range(start, end):
            row_ts = ts_col.iloc[i]
            hi, lo, cl = float(highs.iat[i]), float(lows.iat[i]), float(closes.iat[i])

            # ---- 1) manage open positions on this candle (conservative) ----
            for tr in list(self.positions):
                if not tr.tp1_done:
                    hit_tp1 = hi >= tr.tp1
                    hit_sl = lo <= tr.stop
                    if hit_sl and hit_tp1:
                        # Unknown intrabar path -> assume the adverse one.
                        tr.ambiguous = True
                        self._close_part(tr, row_ts, tr.stop, 1.0, "sl-ambiguous")
                        continue
                    if hit_sl:
                        self._close_part(tr, row_ts, tr.stop, 1.0, "sl")
                        continue
                    if hit_tp1:
                        self._close_part(tr, row_ts, tr.tp1, 0.5, "tp1")
                        tr.tp1_done = True
                        tr.stop = tr.entry  # break-even after TP1
                        continue
                else:
                    if lo <= tr.stop:
                        self._close_part(tr, row_ts, tr.stop, 1.0, "stop-after-tp1")
                        continue
                    if hi >= tr.tp2:
                        self._close_part(tr, row_ts, tr.tp2, 1.0, "tp2")

            # ---- 2) entries on the close of the signal candle ----
            sig = signals[i] if signals and i < len(signals) else None
            if (sig and sig.get("direction") == "LONG" and sig.get("score", 0) >= self.min_score
                    and len(self.positions) < self.max_open):
                    entry = cl
                    atr_v = float(sig["risk"]["atr"])
                    stop = entry - self.stop_k * atr_v
                    stop_dist = entry - stop
                    if stop_dist > 0:
                        risk_amt = self.equity * self.risk_pct / 100
                        qty = risk_amt / stop_dist
                        # ADR-0008: never exceed the single-asset cap.
                        cap_qty = (self.equity * self.max_single_asset_pct / 100) / entry
                        qty = min(qty, cap_qty)
                        rr = stop_dist
                        tp1 = entry + self.tp1_r * rr
                        tp2 = entry + self.tp2_r * rr
                        fill = entry * (1 + self.slippage)
                        cost = qty * fill * (1 + FEE_PCT)
                        if qty > 0 and cost < self.cash:
                            self.cash -= float(cost)
                            self.positions.append(
                                BTTrade(
                                    side="buy",
                                    entry_t=row_ts,
                                    entry=fill,
                                    qty=float(qty),
                                    initial_qty=float(qty),
                                    stop=float(stop),
                                    tp1=float(tp1),
                                    tp2=float(tp2),
                                    risk_amount=float(stop_dist * qty),
                                )
                            )

            open_val = sum(cl * tr.qty for tr in self.positions)
            current = float(self.cash + open_val)
            self.equity = current
            self.equity_curve.append([self._ts_ms(row_ts), round(current, 2)])

        # flatten anything still open at the window edge
        for tr in list(self.positions):
            self._close_part(tr, ts_col.iloc[end - 1], float(closes.iat[end - 1]), 1.0, "eod")

        return self._result(df, symbol, tf, start, end)

    # ---------------- reporting ----------------
    def _result(self, df, symbol, tf, start, end) -> BTResult:
        wins = [t for t in self.closed if t.realized_pnl > 0]
        losses = [t for t in self.closed if t.realized_pnl <= 0]
        total = len(self.closed)
        hit_rate = (len(wins) / total * 100) if total else 0
        gross_profit = float(sum(t.realized_pnl for t in wins))
        gross_loss = abs(float(sum(t.realized_pnl for t in losses)))
        pf = (gross_profit / gross_loss) if gross_loss > 0 else (99.99 if gross_profit > 0 else 0.0)
        pf = min(float(pf), 99.99)
        expectancy_r = sum(t.r_multiple or 0 for t in self.closed) / total if total else 0.0

        eqv = [v for _, v in self.equity_curve]
        peak = eqv[0] if eqv else self.equity
        mdd = 0.0
        for v in eqv:
            peak = max(peak, v)
            mdd = max(mdd, (peak - v) / max(peak, 1) * 100)

        # Per-trade R dispersion -> Sharpe-like ratio of the R stream.
        rs = [t.r_multiple or 0.0 for t in self.closed]
        if len(rs) > 1:
            mean_r = sum(rs) / len(rs)
            var = sum((x - mean_r) ** 2 for x in rs) / (len(rs) - 1)
            sd = math.sqrt(var)
            sharpe = (mean_r / sd * math.sqrt(len(rs))) if sd > 0 else 0.0
        else:
            sharpe = 0.0

        return BTResult(
            config_hash=_cfg_hash(self.cfg),
            symbol=symbol,
            tf=tf,
            window_start=str(df["t"].iloc[start]),
            window_end=str(df["t"].iloc[end - 1]),
            trades=total,
            wins=len(wins),
            losses=len(losses),
            hit_rate=round(hit_rate, 1),
            profit_factor=round(pf, 2),
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
            expectancy_r=round(expectancy_r, 3),
            max_drawdown=round(mdd, 1),
            sharpe_like=round(sharpe, 2),
            initial_equity=round(self.initial_equity, 2),
            final_equity=round(self.initial_equity + sum(t.realized_pnl for t in self.closed), 2),
            ambiguous_exits=sum(1 for t in self.closed if t.ambiguous),
            equity_curve=[list(x) for x in self.equity_curve],
            sample_trades=[
                {
                    "entry": round(t.entry, 8),
                    "exit": round(t.exit or 0, 8),
                    "reason": t.reason,
                    "pnl": round(t.realized_pnl, 2),
                    "r": round(t.r_multiple or 0, 2),
                    "ambiguous": t.ambiguous,
                }
                for t in self.closed[-8:]
            ],
            params={
                "atr_stop_k": self.stop_k,
                "tp1_r": self.tp1_r,
                "tp2_r": self.tp2_r,
                "risk_pct": self.risk_pct,
                "min_score": self.min_score,
            },
        )


# --------------------------------------------------------------------------- #
# Walk-forward
# --------------------------------------------------------------------------- #
def _objective(a: dict, min_trades: int) -> float:
    """Selection score on the *train* slice."""
    if a["trades"] < min_trades:
        # Not enough evidence: rank by trade count only, and keep it negative.
        return -5.0 + a["trades"] * 0.01
    return (
        a["expectancy_r"]
        + 0.25 * (a["profit_factor"] - 1.0)
        - 0.01 * a["max_drawdown"]
    )


def _auto_windows(n_bars: int) -> tuple[int, int]:
    """Pick train/eval sizes that yield >= 3 windows for the available history."""
    eb = max(48, min(240, n_bars // 7))
    tb = 3 * eb
    if tb + eb > n_bars:
        tb = max(MIN_WARMUP + 24, n_bars - eb - 1)
    return tb, eb


class WalkForwardRunner:
    """Rolling train/eval windows with per-window parameter selection."""

    def __init__(self, train_bars: int = 288, eval_bars: int = 144):
        self.tb, self.eb = train_bars, eval_bars

    def run(
        self,
        df: pd.DataFrame,
        symbol: str,
        tf: str,
        base_cfg: dict,
        grid: list[dict] | None = None,
    ) -> list[dict]:
        grid = grid or [{}]
        n = len(df)
        signals = base_cfg.get("_signals") or ta_score_series(df)
        min_train_trades = int(base_cfg.get("min_train_trades", 3))
        min_score = float(base_cfg.get("min_score", 0.0))

        results: list[dict] = []
        start = 0
        idx = 0
        while start + self.tb + self.eb <= n:
            tr_a, tr_b = start, start + self.tb
            ev_a, ev_b = tr_b, tr_b + self.eb
            idx += 1

            # --- 1) choose parameters on the train slice only ---
            best = None
            for params in grid:
                cfg = {**base_cfg, **params}
                bt = EventBacktester(cfg)
                res = bt.run_symbol(df, symbol, tf, signals=signals, start=tr_a, end=tr_b)
                score = _objective(res.__dict__, min_train_trades)
                cand: dict[str, Any] = {"params": params, "score": score, "result": res}
                if best is None or cand["score"] > best["score"]:
                    best = cand
            assert best is not None
            chosen = best["params"]

            # --- 2) apply them unchanged to the evaluation slice ---
            bt_oos = EventBacktester({**base_cfg, **chosen})
            oos = bt_oos.run_symbol(df, symbol, tf, signals=signals, start=ev_a, end=ev_b)
            iso = best["result"]

            results.append(
                {
                    "index": idx,
                    "train_start": str(df["t"].iloc[tr_a]),
                    "train_end": str(df["t"].iloc[tr_b - 1]),
                    "eval_start": str(df["t"].iloc[ev_a]),
                    "eval_end": str(df["t"].iloc[ev_b - 1]),
                    "params": chosen,
                    "is": _summary(iso),
                    "oos": _summary(oos),
                    # flattened OOS fields keep the existing dashboard contract
                    "trades": oos.trades,
                    "wins": oos.wins,
                    "hit_rate": oos.hit_rate,
                    "profit_factor": oos.profit_factor,
                    "expectancy_r": oos.expectancy_r,
                    "max_drawdown": oos.max_drawdown,
                    "gross_profit": oos.gross_profit,
                    "gross_loss": oos.gross_loss,
                    "initial_equity": oos.initial_equity,
                    "final_equity": oos.final_equity,
                    "window_start": oos.window_start,
                    "window_end": oos.window_end,
                    "equity_curve": oos.equity_curve,
                    "sample_trades": oos.sample_trades,
                    "ambiguous_exits": oos.ambiguous_exits,
                    "params_used": chosen,
                    "params_optimised": len(grid) > 1,
                    "min_score": min_score,
                }
            )
            start += self.eb
        return results


def _summary(res: BTResult) -> dict:
    return {
        "trades": res.trades,
        "wins": res.wins,
        "losses": res.losses,
        "hit_rate": res.hit_rate,
        "gross_profit": res.gross_profit,
        "gross_loss": res.gross_loss,
        "profit_factor": res.profit_factor,
        "expectancy_r": res.expectancy_r,
        "max_drawdown": res.max_drawdown,
        "final_equity": res.final_equity,
        "ambiguous_exits": res.ambiguous_exits,
    }


def _pooled_pf(windows: list[dict]) -> float:
    gp = sum(float(w.get("gross_profit", 0.0)) for w in windows)
    gl = sum(float(w.get("gross_loss", 0.0)) for w in windows)
    if gl > 0:
        return min(gp / gl, 99.99)
    return 99.99 if gp > 0 else 0.0


def _aggregate_windows(windows: list[dict], initial_equity: float = 100_000.0) -> dict:
    """Pool out-of-sample results and chain their equity curves."""
    trades = int(sum(w.get("trades", 0) for w in windows))
    wins = int(sum(w.get("wins", 0) for w in windows))
    weighted_r = sum((w.get("expectancy_r", 0.0) * w.get("trades", 0)) for w in windows)
    curve: list[list[float]] = []
    equity = float(initial_equity)
    peak = equity
    mdd = 0.0
    for w in windows:
        w_start = float(w.get("initial_equity") or initial_equity)
        w_end = float(w.get("final_equity") or w_start)
        ret = (w_end / w_start) - 1.0 if w_start else 0.0
        scale = equity / w_start if w_start else 1.0
        for ts, val in w.get("equity_curve", []):
            v = round(float(val) * scale, 2)
            curve.append([ts, v])
            peak = max(peak, v)
            if peak > 0:
                mdd = max(mdd, (peak - v) / peak * 100)
        equity = equity * (1.0 + ret)
    return {
        "trades": trades,
        "total_wins": wins,
        "hit_rate": float(round(wins / trades * 100, 1)) if trades else 0.0,
        "profit_factor": float(round(_pooled_pf(windows), 2)),
        "expectancy_r": float(round(weighted_r / trades, 3)) if trades else 0.0,
        "max_drawdown": float(round(mdd, 1)),
        "initial_equity": round(float(initial_equity), 2),
        "final_equity": round(equity, 2),
        "ambiguous_exits": int(sum(w.get("ambiguous_exits", 0) for w in windows)),
        "ambiguous_share": float(
            round(sum(w.get("ambiguous_exits", 0) for w in windows) / trades * 100, 1)
            if trades else 0.0
        ),
        "windows": len(windows),
        "equity_curve": curve[-400:],
        "profitable_windows": int(
            sum(1 for w in windows if float(w.get("final_equity") or 0) > float(w.get("initial_equity") or 0))
        ),
    }


def _gate(
    agg: dict,
    windows: list[dict],
    tb: int,
    eb: int,
    source: str = "live",
    slippage_bps: float = SLIP_BPS,
) -> dict:
    """ADR-0009 promotion gate.

    States: PASS / FAIL / INSUFFICIENT_DATA / SYNTHETIC_DATA.

    A gate that certifies a strategy fitted on simulated candles is worse than
    no gate at all, so `source != "live"` can never return PASS — the arithmetic
    verdict is still reported under `would_be` for transparency.
    """
    n_windows = len(windows)
    profitable = agg["profitable_windows"]
    consistency = (profitable / n_windows * 100) if n_windows else 0.0
    is_exp = (
        sum(w["is"]["expectancy_r"] * w["is"]["trades"] for w in windows)
        / max(1, sum(w["is"]["trades"] for w in windows))
    )
    oos_exp = agg["expectancy_r"]
    # Anti-overfit: OOS must retain at least 40 % of the in-sample edge.
    degradation_ok = True if is_exp <= 0 else oos_exp >= 0.4 * is_exp

    checks = {
        "windows_ge_3": {"value": n_windows, "pass": n_windows >= WF_MIN_WINDOWS, "required": WF_MIN_WINDOWS},
        "oos_trades_ge_min": {
            "value": agg["trades"],
            "pass": agg["trades"] >= WF_MIN_OOS_TRADES,
            "required": WF_MIN_OOS_TRADES,
        },
        "profit_factor_ge_1_3": {
            "value": agg["profit_factor"],
            "pass": agg["profit_factor"] >= 1.3,
            "required": 1.3,
        },
        "max_drawdown_lt_15": {
            "value": agg["max_drawdown"],
            "pass": agg["max_drawdown"] < 15,
            "required": 15,
        },
        "expectancy_r_positive": {"value": oos_exp, "pass": oos_exp > 0, "required": 0},
        "is_oos_degradation_ok": {
            "value": round(oos_exp / is_exp, 2) if is_exp > 0 else None,
            "pass": degradation_ok,
            "required": 0.4,
        },
        "profitable_windows_ge_50": {
            "value": round(consistency, 1),
            "pass": consistency >= 50,
            "required": 50,
        },
        # ADR-0009 §3 resolves intrabar ambiguity pessimistically (stop first,
        # because the path is unknowable). That is the honest choice, but it is
        # still an *assumption*: if it decides a large share of the exits, the
        # expectancy describes the assumption as much as the market.
        "ambiguous_share_le_10": {
            "value": agg.get("ambiguous_share", 0.0),
            "pass": agg.get("ambiguous_share", 0.0) <= 10.0,
            "required": "<= 10 %",
        },
        # A run with no slippage is not a pessimistic run, it is an optimistic
        # one: the fee is modelled but the fill is free. Whatever it certifies
        # would not survive the first real order, so it may not certify anything.
        "costs_modeled": {
            "value": slippage_bps,
            "pass": slippage_bps > 0,
            "required": "> 0 bps",
        },
    }

    data_short = n_windows < WF_MIN_WINDOWS or agg["trades"] < WF_MIN_OOS_TRADES
    if data_short:
        would_be = "INSUFFICIENT_DATA"
    elif all(c["pass"] for c in checks.values()):
        would_be = "PASS"
    else:
        would_be = "FAIL"

    # Two sources can produce arithmetic that looks like a result without being
    # one. Simulated candles are the obvious case. The reference feed (1.0.10)
    # is the subtler one: its prices are real, but they are *another venue's*,
    # so a strategy validated on them has not been validated where it trades.
    synthetic = source == "synthetic"
    reference = source == "reference"
    state = "SYNTHETIC_DATA" if synthetic else ("REFERENCE_DATA" if reference else would_be)

    # How much history would make the trade count sufficient?
    avg_trades = agg["trades"] / n_windows if n_windows else 0.0
    bars_needed = tb + WF_MIN_WINDOWS * eb
    if avg_trades > 0 and agg["trades"] < WF_MIN_OOS_TRADES:
        need_windows = math.ceil(WF_MIN_OOS_TRADES / avg_trades)
        bars_needed = max(bars_needed, tb + need_windows * eb)

    failed = [k for k, v in checks.items() if not v["pass"]]
    if synthetic:
        msg = (
            "Candles came from the offline fallback (simulated). The arithmetic verdict "
            f"would be {would_be}, but no promotion decision may rest on simulated data."
        )
    elif reference:
        msg = (
            "Candles came from the reference feed, not from the venue. The arithmetic "
            f"verdict would be {would_be}; it describes another exchange's prices, so it "
            "cannot certify a strategy that executes on this one."
        )
    elif would_be == "INSUFFICIENT_DATA":
        msg = (
            f"Not enough evidence yet: {n_windows} windows / {agg['trades']} OOS trades. "
            f"Need >= {WF_MIN_WINDOWS} windows and >= {WF_MIN_OOS_TRADES} trades — "
            f"request about {bars_needed} bars of history."
        )
    elif would_be == "PASS":
        msg = "Out-of-sample results satisfy every ADR-0009 check."
    else:
        msg = "Failed: " + ", ".join(failed)
    basis = decision_basis()
    if not basis["matches_live"]:
        msg = f"{msg} {BASIS_CAVEAT}"

    return {
        "state": state,
        "would_be": would_be,
        "decision_basis": basis,
        "data_source": source,
        "checks": checks,
        "failed": failed,
        "message": msg,
        "bars_needed": bars_needed,
    }


def _param_stability(windows: list[dict]) -> dict:
    seen: dict[str, int] = {}
    for w in windows:
        key = json.dumps(w.get("params") or {}, sort_keys=True)
        seen[key] = seen.get(key, 0) + 1
    top = max(seen.values()) if seen else 0
    return {
        "distinct_param_sets": len(seen),
        "most_common_share": round(top / len(windows) * 100, 1) if windows else 0.0,
    }


def run_and_save(
    symbol: str,
    tf: str,
    candles_rows: list[dict],
    cfg: dict,
    mode: str = "wf",
    grid: list[dict] | None = None,
    auto_window: bool = True,
    source: str = "live",
) -> dict:
    """Run a walk-forward (or single) backtest and persist the report."""
    df = _rows_to_frames(candles_rows)
    n = len(df)
    if n < MIN_WARMUP + 40:
        raise ValueError(f"need at least {MIN_WARMUP + 40} candles, got {n}")

    requested_tb = int(cfg.get("train_bars", 240))
    requested_eb = int(cfg.get("eval_bars", 120))
    window_mode = "manual"
    tb, eb = requested_tb, requested_eb

    def _n_windows(train: int, ev: int) -> int:
        return max(0, (n - train) // ev) if ev > 0 else 0

    if auto_window and (_n_windows(tb, eb) < 2 or tb + eb > n):
        tb, eb = _auto_windows(n)
        window_mode = "auto"

    base_cfg: dict[str, Any] = {
        "initial_equity": float(cfg.get("initial_equity", cfg.get("equity", 50_000))),
        "risk_pct": float(cfg.get("risk_pct", 0.75)),
        "atr_stop_k": float(cfg.get("atr_stop_k", 2.0)),
        "tp1_r": float(cfg.get("tp1_r", 1.5)),
        "tp2_r": float(cfg.get("tp2_r", 2.5)),
        "max_concurrent": int(cfg.get("max_concurrent", 5)),
        "min_score": float(cfg.get("min_score", 0.0)),
        "max_single_asset_pct": float(cfg.get("max_single_asset_pct", 25.0)),
        "slippage_bps": float(cfg.get("slippage_bps", SLIP_BPS)),
    }

    signals = ta_score_series(df)
    base_cfg["_signals"] = signals

    if mode == "single":
        bt = EventBacktester(base_cfg)
        res = bt.run_symbol(df, symbol, tf, signals=signals, start=MIN_WARMUP, end=n)
        windows = [
            {
                "index": 1,
                "train_start": None,
                "train_end": None,
                "eval_start": res.window_start,
                "eval_end": res.window_end,
                "params": res.params,
                "is": _summary(res),
                "oos": _summary(res),
                **{k: getattr(res, k) for k in (
                    "trades", "wins", "hit_rate", "profit_factor", "expectancy_r",
                    "max_drawdown", "gross_profit", "gross_loss", "initial_equity",
                    "final_equity", "window_start", "window_end", "equity_curve",
                    "sample_trades", "ambiguous_exits",
                )},
                "params_used": res.params,
                "params_optimised": False,
            }
        ]
    else:
        if grid is None:
            # Optimise the three parameters that actually alter trade decisions.
            grid = list(DEFAULT_PARAM_GRID)
            if cfg.get("fixed_params"):
                grid = [
                    {
                        "atr_stop_k": base_cfg["atr_stop_k"],
                        "tp1_r": base_cfg["tp1_r"],
                        "tp2_r": base_cfg["tp2_r"],
                    }
                ]
        runner = WalkForwardRunner(train_bars=tb, eval_bars=eb)
        windows = runner.run(df, symbol, tf, base_cfg, grid=grid)

    if not windows:
        raise ValueError("no walk-forward window fits in the supplied history")

    agg = _aggregate_windows(windows, base_cfg["initial_equity"])
    agg_is = _aggregate_windows(
        [
            {
                "trades": w["is"]["trades"],
                "wins": w["is"]["wins"],
                "hit_rate": w["is"]["hit_rate"],
                "profit_factor": w["is"]["profit_factor"],
                "expectancy_r": w["is"]["expectancy_r"],
                "max_drawdown": w["is"]["max_drawdown"],
                "gross_profit": w["is"].get("gross_profit", 0.0),
                "gross_loss": w["is"].get("gross_loss", 0.0),
                "initial_equity": base_cfg["initial_equity"],
                "final_equity": base_cfg["initial_equity"],
            }
            for w in windows
        ],
        base_cfg["initial_equity"],
    )
    # gross figures for the in-sample pool are approximated from PF & wins
    gate = _gate(agg, windows, tb, eb, source=source,
                slippage_bps=base_cfg["slippage_bps"])
    stability = _param_stability(windows)

    report = {
        "config_hash": _cfg_hash({**base_cfg, **windows[0]["params"]}),
        "symbol": symbol,
        "tf": tf,
        "mode": mode,
        "source": source,
        "generated_at": datetime.now(UTC).isoformat(),
        "bars": n,
        "window_mode": window_mode,
        "train_bars": tb,
        "eval_bars": eb,
        "param_grid_size": len(grid) if (grid and mode != "single") else 1,
        "initial_equity": round(base_cfg["initial_equity"], 2),
        "slippage_bps": base_cfg["slippage_bps"],
        "decision_basis": decision_basis(),
        "costs": {
            "slippage_bps": base_cfg["slippage_bps"],
            "fee_pct": FEE_PCT,
            "modeled": base_cfg["slippage_bps"] > 0,
        },
        "aggregate": agg,
        "aggregate_is": agg_is,
        "degradation": {
            "is_expectancy_r": agg_is["expectancy_r"],
            "oos_expectancy_r": agg["expectancy_r"],
            "retention": round(agg["expectancy_r"] / agg_is["expectancy_r"], 2) if agg_is["expectancy_r"] > 0 else None,
            "is_profit_factor": agg_is["profit_factor"],
            "oos_profit_factor": agg["profit_factor"],
        },
        "param_stability": stability,
        "gate": gate,
        "details": windows,
    }
    fp = REPORTS / f"{symbol}-{tf}-{int(time.time())}.json"
    fp.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    report["report_file"] = fp.name
    return report


# --------------------------------------------------------------------------- #
# Grid search over the same walk-forward harness
# --------------------------------------------------------------------------- #
def grid_search(
    symbol: str,
    tf: str,
    candles_rows: list[dict],
    grid: dict | None = None,
    slippage_bps: float = SLIP_BPS,
) -> dict:
    """Sweep `atr_stop_k` x `risk_pct` across the walk-forward windows."""
    import itertools
    import time as _time

    grid = grid or dict(DEFAULT_GRID)
    t0 = _time.time()
    df = candles_to_df(candles_rows)
    n = len(df)
    tb, eb = _auto_windows(n)
    signals = ta_score_series(df)

    keys = sorted(grid.keys())
    combos = list(itertools.product(*(grid[k] for k in keys)))
    results = []
    for combo in combos:
        params = dict(zip(keys, combo, strict=True))
        cfg = {
            "initial_equity": 50_000,
            "risk_pct": float(params.get("risk_pct", 0.75)),
            "atr_stop_k": float(params.get("atr_stop_k", 2.0)),
            "max_concurrent": 5,
            "max_single_asset_pct": 25.0,
            "slippage_bps": float(slippage_bps),
            "_signals": signals,
        }
        runner = WalkForwardRunner(train_bars=tb, eval_bars=eb)
        windows = runner.run(df, symbol, tf, cfg, grid=[{}])
        agg = _aggregate_windows(windows, 50_000)
        results.append({"params": params, "aggregate": agg})

    best = None
    for r in results:
        a = r["aggregate"]
        score = a["profit_factor"] * 0.5 + a["expectancy_r"] + (a["hit_rate"] / 100) * 0.2 - (a["max_drawdown"] / 100) * 0.5
        if best is None or score > best["_score"]:
            best = {**r, "_score": score}
    best = best or {"params": {}, "aggregate": {}, "_score": 0}

    return {
        "symbol": symbol,
        "tf": tf,
        "slippage_bps": float(slippage_bps),
        "combo_count": len(combos),
        "windows": max(0, (n - tb) // eb),
        "window_bars": {"train": tb, "eval": eb},
        "elapsed_s": round(_time.time() - t0, 2),
        "out_of_sample": True,
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
        for r in paths:
            p = r["params"]
            if p.get("risk_pct") == x and p.get("atr_stop_k") == y:
                return r["aggregate"]
        return {}

    return {
        "x": xax,
        "y": yax,
        "values": [[{"risk_pct": x, "atr_stop_k": y, **val(x, y)} for y in yax] for x in xax],
    }
