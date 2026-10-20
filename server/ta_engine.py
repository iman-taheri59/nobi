"""
Technical Analysis engine (ADR-0005) — pandas/numpy implementation with the
same scoring contract as the browser prototype so results are comparable.

Public API:
    candles_to_df(rows) -> pd.DataFrame
    ta_score(df) -> TA_signal dict
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

#: Single source of truth for the shape of the score. `ta_score` (one bar) and
#: `ta_score_series` (vectorised) both read these, and `/api/settings` renders
#: them straight from here. The JS mirror in `nbx-core.js` must match; a
#: duplicated literal is how a settings page starts lying.
COMPONENT_WEIGHTS = {"trend": 0.40, "momentum": 0.25, "volume": 0.15, "structure": 0.20}
RANGE_GATE = 0.85          # multiplier applied outside a trending regime
ADX_TREND_MIN = 25.0       # ADX above this counts as a trend
LONG_SCORE_MIN = 68.0      # score >= this -> LONG
NEUTRAL_SCORE_MIN = 55.0   # score >= this -> NEUTRAL, else EXIT
RISK_K = 2.0               # stop distance = 2 * ATR (kept in step with journal)


def component_score(comp: dict) -> float:
    """Weighted sum of the four components, before the regime gate."""
    return sum(w * float(comp[name]) for name, w in COMPONENT_WEIGHTS.items())


def regime_gate(regime: str) -> float:
    return 1.0 if regime == "TREND" else RANGE_GATE


def direction_for(score: float) -> str:
    return "LONG" if score >= LONG_SCORE_MIN else ("NEUTRAL" if score >= NEUTRAL_SCORE_MIN else "EXIT")


def candles_to_df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df = df[["t", "o", "h", "l", "c", "v"]].astype(float)
    df["t"] = pd.to_datetime(df["t"], unit="s", utc=True)
    return df


# ---------- primitives ----------
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def macd(close: pd.Series, f=12, s=26, sig=9):
    m = ema(close, f) - ema(close, s)
    sg = ema(m, sig)
    return m, sg, m - sg


def tr(df: pd.DataFrame) -> pd.Series:
    pc = df["c"].shift(1)
    return pd.concat([df["h"] - df["l"], (df["h"] - pc).abs(), (df["l"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n=14) -> pd.Series:
    return tr(df).ewm(alpha=1 / n, adjust=False).mean()


def bollinger(close: pd.Series, n=20, m=2):
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std()
    up, low = mid + m * sd, mid - m * sd
    pctb = (close - low) / (up - low)
    return mid, up, low, pctb


def adx_di(df: pd.DataFrame, n=14):
    dmi = tr(df)
    up = df["h"].diff()
    dn = -df["l"].diff()
    pdm = np.where((up > dn) & (up > 0), up, 0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0)
    atr_s = dmi.ewm(alpha=1 / n, adjust=False).mean()
    pdi = 100 * pd.Series(pdm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr_s.replace(0, np.nan)
    ndi = 100 * pd.Series(ndm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / atr_s.replace(0, np.nan)
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return pdi, ndi, dx.ewm(alpha=1 / n, adjust=False).mean()


# ---------- composite score ----------
def ta_score(df: pd.DataFrame) -> dict[str, Any]:
    if len(df) < 60:
        return {"score": 0, "direction": "NEUTRAL", "regime": "UNKNOWN", "components": {}, "reason": "insufficient_data"}
    close = df["c"]
    vol = df["v"].fillna(0)
    e9, e21, e50, e200 = ema(close, 9), ema(close, 21), ema(close, 50), ema(close, 200)
    pdi, ndi, adx = adx_di(df, 14)
    r = rsi(close, 14)
    _, _signal_line, hist = macd(close)
    _mid, _up, _low, pctb = bollinger(close, 20, 2)
    atr_i = atr(df, 14)

    i = len(df) - 1
    comp = {}

    trend = 0
    trend += 20 if close.iat[i] > e21.iat[i] else 0
    trend += 20 if e9.iat[i] > e21.iat[i] else 0
    trend += 25 if e21.iat[i] > e50.iat[i] else 0
    if not np.isnan(e200.iat[i]) and close.iat[i] > e200.iat[i]:
        trend += 15
    if not np.isnan(adx.iat[i]) and adx.iat[i] > 22 and pdi.iat[i] > ndi.iat[i]:
        trend += 20
    comp["trend"] = max(0, min(100, trend))

    mom = 50
    ri = r.iat[i]
    if not np.isnan(ri):
        mom += 20 if 55 < ri < 72 else (5 if ri >= 72 else (-15 if ri < 45 else 0))
    if hist.iat[i] > hist.iat[i - 1] and hist.iat[i] > 0:
        mom += 25
    elif hist.iat[i] < hist.iat[i - 1] and hist.iat[i] < 0:
        mom -= 10
    comp["momentum"] = max(0, min(100, mom))

    vavg = vol.iloc[-30:-1].mean() if len(vol) >= 31 else vol.mean()
    vnow = vol.iat[i]
    vol_s = 50 + (30 if vnow > vavg * 1.4 else (-15 if vnow < vavg * 0.6 else 0))
    if close.iat[i] > close.iat[i - 1] and vnow > vavg:
        vol_s += 10
    comp["volume"] = max(0, min(100, vol_s))

    strc = 50
    pb = pctb.iat[i]
    if not np.isnan(pb):
        strc += 25 if pb > 0.7 else (-15 if pb < 0.25 else 0)
    recent = close.iloc[-20:-1] if len(close) >= 21 else close
    if close.iat[i] > recent.max():
        strc += 10
    comp["structure"] = max(0, min(100, strc))

    regime = "TREND" if (not np.isnan(adx.iat[i]) and adx.iat[i] > ADX_TREND_MIN) else "RANGE"
    score = component_score(comp) * regime_gate(regime)
    score = round(float(max(0, min(100, score))), 1)
    direction = direction_for(score)
    atr_val = float(atr_i.iat[i])
    out = {
        "score": score,
        "direction": direction,
        "regime": regime,
        "components": comp,
        "risk": {"atr": atr_val, "stopDistance": 2 * atr_val, "stopPct": round((2 * atr_val / close.iat[i]) * 100, 2)},
        "diagnostics": {
            "rsi": round(float(ri), 1) if not np.isnan(ri) else None,
            "adx": round(float(adx.iat[i]), 1) if not np.isnan(adx.iat[i]) else None,
            "pctB": round(float(pb), 3) if not np.isnan(pb) else None,
            "hist": round(float(hist.iat[i]), 5),
        },
        "lastClose": float(close.iat[i]),
    }
    return out



# --------------------------------------------------------------------------- #
# Vectorised signal series (identical output to ta_score on a growing slice)
# --------------------------------------------------------------------------- #
def ta_score_series(df: pd.DataFrame, start: int = 60) -> list[dict[str, Any] | None]:
    """Compute `ta_score` for every bar in one vectorised pass.

    `ta_score(df.iloc[:i+1])` recomputes ~15 pandas operations per bar, which
    makes a walk-forward backtest O(n^2) and slow. All the primitives used here
    are causal, so evaluating them once over the full frame and reading row *i*
    yields exactly the same numbers. `tests/test_core.py::test_ta_series_matches_scalar`
    asserts that equivalence bar-by-bar.

    Returns a list of length ``len(df)``; entries before ``start`` are ``None``.
    """
    n = len(df)
    out: list[dict[str, Any] | None] = [None] * n
    if n <= start:
        return out

    close = df["c"]
    vol = df["v"].fillna(0)
    e9, e21, e50, e200 = ema(close, 9), ema(close, 21), ema(close, 50), ema(close, 200)
    pdi, ndi, adx = adx_di(df, 14)
    r = rsi(close, 14)
    _, _, hist = macd(close)
    _, _, _, pctb = bollinger(close, 20, 2)
    atr_i = atr(df, 14)

    # `vol.iloc[-30:-1].mean()` on a growing slice == 29-bar mean shifted by one.
    vavg = vol.rolling(29, min_periods=1).mean().shift(1)
    # `close.iloc[-20:-1].max()` == 19-bar max shifted by one.
    recent_max = close.rolling(19, min_periods=1).max().shift(1)
    prev_close = close.shift(1)
    prev_hist = hist.shift(1)

    c = close.to_numpy(float)
    for i in range(start, n):
        trend = 0
        trend += 20 if c[i] > e21.iat[i] else 0
        trend += 20 if e9.iat[i] > e21.iat[i] else 0
        trend += 25 if e21.iat[i] > e50.iat[i] else 0
        if not np.isnan(e200.iat[i]) and c[i] > e200.iat[i]:
            trend += 15
        if not np.isnan(adx.iat[i]) and adx.iat[i] > 22 and pdi.iat[i] > ndi.iat[i]:
            trend += 20
        comp_trend = max(0, min(100, trend))

        mom = 50
        ri = r.iat[i]
        if not np.isnan(ri):
            mom += 20 if 55 < ri < 72 else (5 if ri >= 72 else (-15 if ri < 45 else 0))
        if hist.iat[i] > prev_hist.iat[i] and hist.iat[i] > 0:
            mom += 25
        elif hist.iat[i] < prev_hist.iat[i] and hist.iat[i] < 0:
            mom -= 10
        comp_mom = max(0, min(100, mom))

        vnow = vol.iat[i]
        va = vavg.iat[i]
        vol_s = 50 + (30 if vnow > va * 1.4 else (-15 if vnow < va * 0.6 else 0))
        if c[i] > prev_close.iat[i] and vnow > va:
            vol_s += 10
        comp_vol = max(0, min(100, vol_s))

        strc = 50
        pb = pctb.iat[i]
        if not np.isnan(pb):
            strc += 25 if pb > 0.7 else (-15 if pb < 0.25 else 0)
        if c[i] > recent_max.iat[i]:
            strc += 10
        comp_str = max(0, min(100, strc))

        regime = "TREND" if (not np.isnan(adx.iat[i]) and adx.iat[i] > ADX_TREND_MIN) else "RANGE"
        score = component_score({"trend": comp_trend, "momentum": comp_mom,
                                 "volume": comp_vol, "structure": comp_str}) * regime_gate(regime)
        score = round(float(max(0, min(100, score))), 1)
        direction = direction_for(score)
        atr_val = float(atr_i.iat[i])
        out[i] = {
            "score": score,
            "direction": direction,
            "regime": regime,
            "components": {
                "trend": comp_trend,
                "momentum": comp_mom,
                "volume": comp_vol,
                "structure": comp_str,
            },
            "risk": {
                "atr": atr_val,
                "stopDistance": 2 * atr_val,
                "stopPct": round((2 * atr_val / c[i]) * 100, 2),
            },
            "diagnostics": {
                "rsi": round(float(ri), 1) if not np.isnan(ri) else None,
                "adx": round(float(adx.iat[i]), 1) if not np.isnan(adx.iat[i]) else None,
                "pctB": round(float(pb), 3) if not np.isnan(pb) else None,
                "hist": round(float(hist.iat[i]), 5),
            },
            "lastClose": float(c[i]),
        }
    return out


__all__ = ["adx_di", "atr", "bollinger", "candles_to_df", "ema", "macd", "rsi", "sma", "ta_score", "ta_score_series"]
