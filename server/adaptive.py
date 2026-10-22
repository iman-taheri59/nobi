"""Thresholds that fit the timeframe and the regime — measured first, applied later.

``ta_engine.LONG_SCORE_MIN = 68`` and ``NEUTRAL_SCORE_MIN = 55`` are single
numbers applied to every symbol on every timeframe. That is not a tuning choice,
it is an accident of where they were first written: a 0–100 composite score is
built from indicators whose information content per bar is not the same at 15
minutes and at one day. Two consequences follow, and both were invisible:

1. **Signal frequency drifts with the timeframe.** Sweeping the same 68 across
   15m, 1h and 4h produces very different LONG shares. Nothing in the dashboard
   said so, so "the strategy is quiet today" and "this timeframe rarely clears
   68" looked identical.
2. **The stop is timeframe-blind.** ``RISK_K = 2.0`` (stop distance = 2 × ATR)
   is right only because ATR itself scales with the timeframe — but the *score*
   threshold next to it does not, so the two disagree about how much noise a bar
   contains.

This module measures both and proposes a scale factor. It deliberately does
**not** change a decision: ``report()`` always returns ``applied: False``. The
recommendation is evidence for an operator (and for a future ADR), not a silent
behaviour change — the same rule the reference feed follows.

Named refusals, never guessed numbers: ``insufficient-data``, ``flat-market``
(ATR == 0 over the window), ``bad-timeframe``, ``bad-candles``.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from ta_engine import (
    LONG_SCORE_MIN,
    NEUTRAL_SCORE_MIN,
    candles_to_df,
    ta_score_series,
)
from ta_engine import (
    atr as atr_series,
)

#: The timeframe the constants were written for. Every TF factor is relative to it.
REFERENCE_TF = "1h"
TF_MINUTES = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1d": 1440, "D": 1440}

ATR_WINDOW = 14          # same period the engine already uses
LOOKBACK = 200           # how far back the ATR percentile is measured
MIN_BARS = 120           # below this an ATR percentile is noise wearing a number

#: Regime cut on the ATR percentile, not on ATR itself (a price of 80k and a
#: price of 0.5 have meaningless AT numbers in common).
CALM_MAX = 0.25
VOLATILE_MIN = 0.75

#: Shorter timeframes are noisier, so the same score is weaker evidence there.
#: The exponent is deliberately small: this is a nudge that has to survive being
#: measured, not a licence to retune the engine from an environment variable.
TF_EXPONENT = 0.35
REGIME_ENTRY = {"calm": 1.00, "normal": 1.00, "volatile": 1.10}
REGIME_STOP = {"calm": 0.85, "normal": 1.00, "volatile": 1.25}

#: Where the factors pivot. See `report()` for why it is not 0 or the base line.
ANCHOR = 40.0

#: Hard bounds. A factor outside these is a bug, not a regime.
ENTRY_BOUNDS = (0.85, 1.25)
STOP_BOUNDS = (0.75, 1.40)


class AdaptiveError(Exception):
    """A refusal with a stable code, as everywhere else in this repository."""

    def __init__(self, code: str, message: str, meta: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.meta = meta or {}


def tf_minutes(tf: str) -> int:
    key = (tf or "").strip()
    if key not in TF_MINUTES:
        raise AdaptiveError(
            "bad-timeframe",
            f"unknown timeframe {tf!r}; known: {sorted(TF_MINUTES)}",
            {"allowed": sorted(TF_MINUTES)},
        )
    return TF_MINUTES[key]


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    low, high = bounds
    return max(low, min(high, value))


def timeframe_factor(tf: str) -> float:
    """Entry-threshold factor for ``tf`` relative to :data:`REFERENCE_TF`.

    Noise grows roughly with the square root of elapsed time, so a 15-minute bar
    carries about half the information of an hourly one (``sqrt(15/60)``) and
    needs a *higher* bar to clear for the same confidence. Hence the negative
    exponent: ``tf < REFERENCE_TF`` → factor > 1.
    """
    ratio = tf_minutes(tf) / TF_MINUTES[REFERENCE_TF]
    return _clamp(math.exp(-TF_EXPONENT * math.log(math.sqrt(ratio))), ENTRY_BOUNDS)


def _clean(rows: list[dict]) -> pd.DataFrame:
    """Candles as the engine sees them, with rows that cannot be trusted dropped."""
    if not rows:
        raise AdaptiveError("bad-candles", "no candles")
    df = candles_to_df(rows)
    df = df.dropna(subset=["o", "h", "l", "c"])
    df = df[(df["h"] >= df["l"]) & (df["c"] > 0)]
    if len(df) < MIN_BARS:
        raise AdaptiveError(
            "insufficient-data",
            f"{len(df)} usable candles; the ATR percentile needs {MIN_BARS}",
            {"bars": len(df), "required": MIN_BARS},
        )
    return df.reset_index(drop=True)


def atr_percentile(df: pd.DataFrame, window: int = LOOKBACK) -> dict[str, Any]:
    """Where the current ATR sits inside its own recent history (0..1).

    A percentile, not a ratio to price: a 0.4 % ATR means "quiet" on a coin whose
    recent range was 0.3 % and "violent" on one that has been flat at 0.05 %.
    """
    series = atr_series(df, ATR_WINDOW).dropna()
    if series.empty:
        raise AdaptiveError("insufficient-data", "ATR could not be computed")
    tail = series.iloc[-window:]
    current = float(tail.iloc[-1])
    if current <= 0:
        raise AdaptiveError(
            "flat-market",
            "ATR is zero over the window: a percentile of zero variance is undefined",
            {"atr": current, "bars": len(tail)},
        )
    below = int((tail < current).sum())
    pct = below / max(1, len(tail) - 1)
    return {
        "atr": round(current, 10),
        "atr_abs": current,
        "atr_pct_price": round(current / float(df["c"].iloc[-1]) * 100, 4),
        "percentile": round(min(1.0, max(0.0, pct)), 4),
        "window": len(tail),
        "period": ATR_WINDOW,
    }


def regime_for(percentile: float) -> str:
    if percentile < CALM_MAX:
        return "calm"
    if percentile > VOLATILE_MIN:
        return "volatile"
    return "normal"


def signal_frequency(df: pd.DataFrame, start: int = 60) -> dict[str, Any]:
    """How often *this* candle set clears the fixed thresholds.

    The number that makes the argument: the same 68 is a different policy on
    every timeframe, and until it is counted nobody can tell a quiet market from
    a threshold that is simply unreachable.
    """
    scored = [row for row in ta_score_series(df, start=start) if row]
    total = len(scored)
    if not total:
        raise AdaptiveError("insufficient-data", "no scored bars", {"required": start + 1})
    counts = {"LONG": 0, "NEUTRAL": 0, "EXIT": 0}
    for row in scored:
        counts[row["direction"]] = counts.get(row["direction"], 0) + 1
    return {
        "bars": total,
        "skipped": start,
        "shares": {k: round(v / total, 4) for k, v in counts.items()},
        "counts": counts,
        "long_share": round(counts["LONG"] / total, 4),
        "median_score": round(
            sorted(row["score"] for row in scored)[total // 2], 2
        ),
    }


def _direction(tf: str, factor: float) -> str:
    """One sentence whose direction matches the number next to it.

    The first version of this text said "shorter bars are noisier" no matter what
    the factor was, which read as a contradiction on 4h (0.85x) — a small thing,
    and exactly the kind of prose that makes an operator distrust the arithmetic.
    """
    if factor > 1.0001:
        return (f"{tf} is noisier than {REFERENCE_TF}: the same score is weaker evidence "
                f"there, so the bar rises to {factor:.2f}x.")
    if factor < 0.9999:
        return (f"{tf} is quieter than {REFERENCE_TF}: the same score is stronger evidence "
                f"there, so the bar falls to {factor:.2f}x.")
    return f"{tf} is the reference timeframe, so only the regime moves the bar."


def report(rows: list[dict], tf: str) -> dict[str, Any]:
    """The full measurement. Always ``applied: False`` — see the module docstring."""
    df = _clean(rows)
    vol = atr_percentile(df)
    regime = regime_for(vol["percentile"])
    tf_factor = timeframe_factor(tf)
    entry_factor = round(_clamp(tf_factor * REGIME_ENTRY[regime], ENTRY_BOUNDS), 4)
    stop_factor = round(_clamp(REGIME_STOP[regime], STOP_BOUNDS), 4)
    freq = signal_frequency(df)

    # A score is bounded 0..100, so the factor cannot be applied to the score
    # itself — it is applied to the *distance from an anchor*, the part that
    # carries the decision. Anchoring at 0 would scale both lines and collapse
    # the LONG/NEUTRAL band into one (68 and 55 land 3 points apart at 1.25x);
    # anchoring at the line itself would freeze the number it is scaling. 40 is
    # the composite's neutral reading, below which the four components cancel.
    def move(base: float) -> float:
        shifted = ANCHOR + (base - ANCHOR) * entry_factor
        return round(min(99.0, max(ANCHOR + 0.5, shifted)), 1)

    recommended = {"long": move(LONG_SCORE_MIN), "neutral": move(NEUTRAL_SCORE_MIN)}
    if recommended["neutral"] >= recommended["long"] - 0.5:
        # Never let a factor invert the decision order; refuse instead of lying.
        raise AdaptiveError(
            "band-collapsed",
            "the scaled thresholds would cross; the factor is out of range for this anchor",
            {"entry": entry_factor, "anchor": ANCHOR, "recommended": recommended},
        )
    return {
        "ok": True,
        "applied": False,
        "tf": tf,
        "reference_tf": REFERENCE_TF,
        "regime": regime,
        "volatility": {k: v for k, v in vol.items() if k != "atr_abs"},
        "scales": {
            "timeframe": round(tf_factor, 4),
            "regime_entry": REGIME_ENTRY[regime],
            "regime_stop": REGIME_STOP[regime],
            "entry": entry_factor,
            "stop": stop_factor,
        },
        "thresholds": {
            "base": {"long": LONG_SCORE_MIN, "neutral": NEUTRAL_SCORE_MIN},
            "recommended": recommended,
            "delta": {
                "long": round(recommended["long"] - LONG_SCORE_MIN, 1),
                "neutral": round(recommended["neutral"] - NEUTRAL_SCORE_MIN, 1),
            },
        },
        "frequency": freq,
        "why": f"{_direction(tf, tf_factor)} ATR sits at the "
               f"{vol['percentile'] * 100:.0f}th percentile of the last "
               f"{vol['window']} bars, which reads as a {regime} regime.",
        "note": (
            "measurement only: nothing here changes a decision. Applying it needs an "
            "ADR and an explicit switch."
        ),
    }
