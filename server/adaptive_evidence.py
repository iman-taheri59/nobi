"""Would the proposed factor have helped? One paired walk-forward, out-of-sample.

`adaptive.py` measures what the fixed score threshold means on a timeframe and
proposes a factor. Nobody has ever asked the next question in code: *if the bar
had been higher, would the out-of-sample result have been better?* This module
answers that, and it answers it in the only way this repository accepts: the same
windows, the same cost model, the same optimisation budget, one variable.

Design
------
* **One variable.** Both runs share the window boundaries, the parameter grid and
  the cost model. Only `min_score` differs: ``LONG_SCORE_MIN`` (68) versus
  ``round(68 * factor)``. A comparison that changes two things at once measures
  neither.
* **Only evaluation numbers.** The train slice selects parameters; only the eval
  slice is aggregated. That is what ADR-0009 already means by out-of-sample.
* **Nothing is applied.** ``applied`` is ``False`` on every path, including a
  verdict that favours the higher bar. A verdict is evidence for ADR-0021; the
  decision to change a live threshold stays a documented, deliberate act.
* **Reference data cannot promote.** On ``source="reference"`` the verdict is
  capped at ``inconclusive``: simulated candles can demonstrate a method, never
  certify a strategy (ADR-0009, ADR-0014).

Two refusals that exist because the alternative would be a lie
-------------------------------------------------------------
``no-op``
    A factor of exactly 1.0 has nothing to compare; two identical runs reported
    as evidence would be theatre.

``bad-factor``
    A factor outside the measurable range, or scaled bars that cross. Outside the
    range the comparison is not a threshold experiment; crossed bars make NEUTRAL
    unreachable and would quietly turn three states into two.

One claim that had to be withdrawn (kept here because it was nearly written into a
test): *"a lower bar can only add trades."* It is true of the **labels** — a lower
long bar admits a superset of LONG signals — but not of the trade count, because
the walk-forward re-optimises the stop/target grid on the train slice and a
different bar drives it to a different parameter set. The monotonicity that holds
is on signals, and that is what the tests assert.

History worth keeping: until 1.0.16 a factor below 1 was refused outright as
``lower-bar-untestable``. That was correct *then* — ``ta_engine`` derived the
LONG/EXIT label from ``LONG_SCORE_MIN`` itself, so lowering ``min_score`` filtered
nothing and "no difference" would have meant "we changed nothing". 1.0.16 made
the direction thresholds parameters (ADR-0021's prerequisite), so both directions
are now measurable — with the constant still the default, so nothing else moved.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from adaptive import AdaptiveError
from adaptive import report as adaptive_report
from backtest import DEFAULT_PARAM_GRID, MIN_WARMUP, WalkForwardRunner, _auto_windows
from ta_engine import LONG_SCORE_MIN, NEUTRAL_SCORE_MIN, ta_score_series

#: Below this many windows the comparison cannot say anything: one window is an
#: anecdote with a chart.
MIN_WINDOWS = 3

#: Below this many out-of-sample trades in *either* arm, profit factor is noise.
MIN_OOS_TRADES = 30

#: How much better the higher bar has to be before the verdict names it. Both
#: must hold: a profit-factor improvement bought with a collapsed expectancy is
#: not an improvement.
DELTA_PF = 0.10
DELTA_EXP_R = 0.05

#: A factor within this distance of 1.0 is the same bar after rounding.
NOOP_EPS = 1e-9

#: The range worth measuring. ADR-0020 clamps its recommendation to [0.85, 1.25];
#: a factor far outside that is not a threshold experiment any more, it is a
#: different strategy, and calling it evidence would launder the difference.
MIN_FACTOR = 0.5
MAX_FACTOR = 2.0


def _pooled(windows: list[dict]) -> dict[str, Any]:
    """Aggregate only what the gate reads, from the evaluation slices."""
    trades = sum(int(w.get("trades") or 0) for w in windows)
    wins = sum(int(w.get("wins") or 0) for w in windows)
    gp = sum(float(w.get("gross_profit") or 0.0) for w in windows)
    gl = sum(float(w.get("gross_loss") or 0.0) for w in windows)
    exp_num = sum(float((w.get("oos") or {}).get("expectancy_r") or 0.0) * int(w.get("trades") or 0)
                  for w in windows)
    profitable = sum(1 for w in windows
                     if float(w.get("final_equity") or 0) > float(w.get("initial_equity") or 0))
    return {
        "windows": len(windows),
        "trades": trades,
        "wins": wins,
        "hit_rate": round(wins / trades * 100, 2) if trades else 0.0,
        "gross_profit": round(gp, 2),
        "gross_loss": round(gl, 2),
        "profit_factor": round(min(gp / gl, 99.99), 3) if gl > 0 else (99.99 if gp > 0 else 0.0),
        "expectancy_r": round(exp_num / trades, 4) if trades else 0.0,
        "max_drawdown": round(max((float(w.get("max_drawdown") or 0.0) for w in windows), default=0.0), 4),
        "profitable_windows": profitable,
        "ambiguous_exits": sum(int(w.get("ambiguous_exits") or 0) for w in windows),
    }


def _run(df: pd.DataFrame, symbol: str, tf: str, tb: int, eb: int, long_min: float,
         neutral_min: float, cfg: dict | None) -> list[dict]:
    """One arm. The *labels* and the filter use the same bar, deliberately: an arm
    whose signal says LONG at 58 while the backtester filters at 68 is testing
    neither number."""
    base_cfg: dict[str, Any] = {
        "initial_equity": float((cfg or {}).get("initial_equity", 50_000)),
        "risk_pct": float((cfg or {}).get("risk_pct", 0.75)),
        "atr_stop_k": float((cfg or {}).get("atr_stop_k", 2.0)),
        "tp1_r": float((cfg or {}).get("tp1_r", 1.5)),
        "tp2_r": float((cfg or {}).get("tp2_r", 2.5)),
        "max_concurrent": 5,
        "max_single_asset_pct": 25.0,
        "slippage_bps": float((cfg or {}).get("slippage_bps", 6)),
        "min_score": float(long_min),
        "_signals": ta_score_series(df, long_min=long_min, neutral_min=neutral_min),
    }
    runner = WalkForwardRunner(train_bars=tb, eval_bars=eb)
    return runner.run(df, symbol, tf, base_cfg, grid=list(DEFAULT_PARAM_GRID))


def _verdict(baseline: dict, adaptive: dict) -> tuple[str, str]:
    """Name a winner only when the sample can carry the claim."""
    if min(baseline["windows"], adaptive["windows"]) < MIN_WINDOWS:
        return "inconclusive", f"fewer than {MIN_WINDOWS} windows fit in this history"
    if min(baseline["trades"], adaptive["trades"]) < MIN_OOS_TRADES:
        return "inconclusive", (f"out-of-sample trades below {MIN_OOS_TRADES} in at least one arm "
                                f"(base {baseline['trades']}, adaptive {adaptive['trades']})")
    delta_pf = adaptive["profit_factor"] - baseline["profit_factor"]
    delta_exp = adaptive["expectancy_r"] - baseline["expectancy_r"]
    if delta_pf >= DELTA_PF and delta_exp >= DELTA_EXP_R:
        return "favours-adaptive", (f"the higher bar gained +{delta_pf:.2f} PF and "
                                   f"+{delta_exp:.3f}R per trade out-of-sample")
    if -delta_pf >= DELTA_PF and -delta_exp >= DELTA_EXP_R:
        return "favours-baseline", (f"the higher bar lost {delta_pf:.2f} PF and "
                                    f"{delta_exp:.3f}R per trade out-of-sample")
    return "inconclusive", (f"the difference is inside the noise band "
                            f"(ΔPF {delta_pf:+.2f}, Δexpectancy {delta_exp:+.3f}R)")


def compare(
    rows: list[dict],
    tf: str,
    symbol: str = "BTCIRT",
    *,
    factor: float | None = None,
    source: str = "nobitex",
    cfg: dict | None = None,
) -> dict[str, Any]:
    """Paired walk-forward: fixed bar versus the measured bar. Never applies either.

    ``rows`` are candles (oldest first). ``factor`` defaults to the one the
    measurement proposes; pass it explicitly to test a candidate instead.
    """
    if not rows:
        raise AdaptiveError("insufficient-data", "no candles supplied")
    df = pd.DataFrame(rows)
    for col in ("t", "o", "h", "l", "c"):
        if col not in df.columns:
            raise AdaptiveError("bad-candles", f"candle rows have no '{col}' column")
    if len(df) < MIN_WARMUP + 40:
        raise AdaptiveError("insufficient-data",
                            f"need at least {MIN_WARMUP + 40} candles, got {len(df)}")

    measurement = adaptive_report(rows, tf)
    proposed = float((measurement.get("scales") or {}).get("entry") or 1.0)
    used = round(float(factor if factor is not None else proposed), 4)

    base_pair = (measurement.get("thresholds") or {}).get("base") or {}
    base_long = float(base_pair.get("long") or LONG_SCORE_MIN)
    base_neutral = float(base_pair.get("neutral") or NEUTRAL_SCORE_MIN)
    scaled_long = round(base_long * used, 2)
    scaled_neutral = round(base_neutral * used, 2)

    out: dict[str, Any] = {
        "symbol": symbol,
        "tf": tf,
        "candles_source": source,
        "applied": False,
        "factor": {"proposed": proposed, "tested": used},
        "thresholds": {
            "base_long": base_long,
            "scaled_long": scaled_long,
            "base_neutral": base_neutral,
            "scaled_neutral": scaled_neutral,
        },
        "bars": len(df),
        "measurement": {
            "regime": measurement.get("regime"),
            "percentile": (measurement.get("volatility") or {}).get("percentile"),
            "why": measurement.get("why"),
        },
    }

    if abs(used - 1.0) <= NOOP_EPS:
        return {**out, "ok": False, "error": "no-op",
                "message": "the tested factor is 1.0: the bar is unchanged, so there is "
                           "nothing to compare"}

    if not (MIN_FACTOR <= used <= MAX_FACTOR):
        return {
            **out, "ok": False, "error": "bad-factor",
            "message": f"factor {used} is outside the measurable range "
                       f"[{MIN_FACTOR}, {MAX_FACTOR}]; outside it this is a different "
                       "strategy, not a threshold experiment",
            "meta": {"min_factor": MIN_FACTOR, "max_factor": MAX_FACTOR},
        }

    if not (scaled_neutral < scaled_long):
        # Crossed bars make NEUTRAL unreachable: three states would become two,
        # and the comparison would silently be about a different decision rule.
        return {
            **out, "ok": False, "error": "bad-factor",
            "message": f"scaled bars cross (neutral {scaled_neutral} >= long {scaled_long}); "
                       "NEUTRAL would be unreachable",
            "meta": {"scaled_neutral": scaled_neutral, "scaled_long": scaled_long},
        }

    tb, eb = _auto_windows(len(df))
    baseline = _pooled(_run(df, symbol, tf, tb, eb, base_long, base_neutral, cfg))
    adaptive = _pooled(_run(df, symbol, tf, tb, eb, scaled_long, scaled_neutral, cfg))
    verdict, why = _verdict(baseline, adaptive)

    # A simulated or reference feed may demonstrate the method; it may not
    # certify a strategy. Same rule as the promotion gate, same words.
    capped = None
    if source != "nobitex":
        if verdict != "inconclusive":
            capped = verdict
        verdict = "inconclusive"
        why = (f"{why} — but this ran on the {source} feed, which can never certify a "
               "strategy (ADR-0009/ADR-0014)")

    return {
        **out,
        "ok": True,
        "windows": {"train_bars": tb, "eval_bars": eb, "count": baseline["windows"],
                    "paired": True},
        "baseline": baseline,
        "adaptive": adaptive,
        "delta": {
            "profit_factor": round(adaptive["profit_factor"] - baseline["profit_factor"], 3),
            "expectancy_r": round(adaptive["expectancy_r"] - baseline["expectancy_r"], 4),
            "trades": adaptive["trades"] - baseline["trades"],
            "hit_rate": round(adaptive["hit_rate"] - baseline["hit_rate"], 2),
        },
        "verdict": verdict,
        "verdict_capped": capped,
        "why": why,
        "applied": False,
        "promotable": False,
    }


def _finite(x: Any) -> bool:  # pragma: no cover - tiny helper, kept for readability
    return isinstance(x, (int, float)) and math.isfinite(float(x))
