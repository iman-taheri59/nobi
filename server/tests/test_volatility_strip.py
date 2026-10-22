"""The ATR percentile, drawn (1.0.22).

Since 1.0.17 the card could *name* a percentile ("ATR sits at the 12th"). A rank
with no shape is hard to act on: 12 can be the tail of a quiet month or one calm
day inside a violent one. This module draws the last bars of that same rank, and
the tests pin the three things that make the drawing trustworthy:

1. **It is the same formula.** The last point of the strip equals the headline
   percentile, because both call `_percentile_of_last`. A drawing that quietly
   used a different rule would disagree with the number printed next to it.
2. **Every point says which window it was ranked against.** With fewer bars than
   the lookback, the shortest window is reported rather than hidden.
3. **A refusal is a named result, not a chart.** Too few bars or a flat window
   produce a code the page prints; no zeros are drawn as if they were ranks.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import adaptive

ROOT_HTML = Path(__file__).resolve().parents[2] / "index.html"
MAIN = Path(__file__).resolve().parents[1] / "main.py"


def rows_for(df: pd.DataFrame) -> list[dict]:
    """The frame as `/api/candles` carries it: epoch seconds, plain floats."""
    out = df.copy()
    out["t"] = (out["t"].astype("int64") // 10**9).astype(float)
    return out[["t", "o", "h", "l", "c", "v"]].to_dict("records")


def frame(bars: int = 260, *, seed: int = 7, flat: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 0.4, bars))
    high = close + np.abs(rng.normal(0, 0.3, bars))
    low = close - np.abs(rng.normal(0, 0.3, bars))
    df = pd.DataFrame({
        "t": pd.date_range("2026-01-01", periods=bars, freq="15min", tz="UTC"),
        "o": close, "h": high, "l": low, "c": close, "v": 1.0,
    })
    if flat:
        df[["o", "h", "l", "c"]] = 100.0
    return df


def test_the_rank_is_the_rank_of_below_the_last_value() -> None:
    """The definition, written out by hand — not compared to itself.

    Every other test here compares two callers of the same function, so a change to
    the formula would move both and look fine. This one says what the formula is:
    the share of the window strictly below the newest value, over `n - 1`.
    """
    def rank(values: list[float]) -> float:
        return adaptive._percentile_of_last(pd.Series(values, dtype=float))

    assert rank([1, 2, 3, 4]) == 1.0          # newest is the highest
    assert rank([4, 3, 2, 1]) == 0.0          # newest is the lowest
    assert rank([1, 3, 2]) == 0.5             # one of two possible positions
    # Ties are *not* below: a flat newest value ranks nothing, and a flat window
    # has no rank at all.
    assert rank([2, 2, 2]) == 0.0
    assert rank([2, 2, 2, 3]) == 1.0
    with pytest.raises(adaptive.AdaptiveError) as err:
        rank([0.0, 0.0, 0.0])
    assert err.value.code == "flat-market"


def test_the_headline_number_is_that_rank_over_the_lookback() -> None:
    df = frame(seed=11)
    series = adaptive.atr_series(df, adaptive.ATR_WINDOW).dropna()
    tail = series.iloc[-adaptive.LOOKBACK:]
    expected = round(float((tail < float(tail.iloc[-1])).sum()) / (len(tail) - 1), 4)
    assert adaptive.atr_percentile(df)["percentile"] == expected
    assert adaptive.atr_percentile_path(df)["points"][-1]["pct"] == expected


def test_the_strip_ends_on_the_headline_number() -> None:
    """One formula, so the picture and the number cannot disagree."""
    df = frame()
    current = adaptive.atr_percentile(df)
    path = adaptive.atr_percentile_path(df)
    assert path["points"][-1]["pct"] == current["percentile"]
    assert path["period"] == current["period"] == adaptive.ATR_WINDOW
    assert path["window"] == current["window"] == adaptive.LOOKBACK


def test_every_point_reports_the_window_it_was_ranked_against() -> None:
    path = adaptive.atr_percentile_path(frame(260))
    assert path["count"] == adaptive.STRIP_BARS
    assert path["windows"] == {"min": adaptive.LOOKBACK, "max": adaptive.LOOKBACK}
    assert all(0.0 <= p["pct"] <= 1.0 for p in path["points"])
    # The timestamps travel with the points: a strip with no time axis hides the
    # one thing it exists to show (when).
    assert path["points"][-1]["t"] is not None
    assert path["points"][-1]["t"] > path["points"][0]["t"]


def test_a_short_history_is_refused_instead_of_ranked() -> None:
    with pytest.raises(adaptive.AdaptiveError) as err:
        adaptive.atr_percentile_path(frame(80))
    assert err.value.code == "insufficient-data"
    assert err.value.meta["required"] == adaptive.MIN_BARS


def test_a_flat_window_has_no_rank_and_is_named() -> None:
    """A percentile of zero variance is undefined; 0.5 there would be an average
    of nothing, and 0 % would draw a calm that never happened."""
    with pytest.raises(adaptive.AdaptiveError) as err:
        adaptive.atr_percentile_path(frame(flat=True))
    assert err.value.code == "flat-market"


def test_the_block_is_descriptive_and_declines_before_it_draws() -> None:
    import main

    ok = main.volatility_block(rows_for(frame(260)))
    assert ok["applied"] is False
    assert "not a forecast" in ok["note"]
    assert ok["regime"] in {"calm", "normal", "volatile"}
    assert ok["cuts"] == {"calm_max": adaptive.CALM_MAX, "volatile_min": adaptive.VOLATILE_MIN}

    short = main.volatility_block(rows_for(frame(60)))
    assert short["ok"] is False and short["error"] == "insufficient-data"
    assert short["applied"] is False and short["bars"] == 60

    flat = main.volatility_block(rows_for(frame(flat=True)))
    assert flat["ok"] is False and flat["error"] == "flat-market"
    assert flat["applied"] is False


def test_the_candles_route_carries_the_strip_and_the_age() -> None:
    src = MAIN.read_text(encoding="utf-8")
    route = src[src.index('@app.get("/api/candles/{symbol}")'):]
    route = route[: route.index("@app.get(", 10)]
    assert '"volatility": volatility_block(merged)' in route
    assert '"freshness": freshness_block(merged, tf)' in route


def test_the_page_draws_what_the_response_says_and_nothing_else() -> None:
    page = ROOT_HTML.read_text(encoding="utf-8")
    # The detail box holds one strip per open row, and asks once, for that pair.
    assert 'id="atrStrip"' in page
    assert "atrStripLoad(sym" in page
    fn = page[page.index("function renderAtrStrip("):page.index("async function atrStripLoad(")]
    # It draws from `volatility` and refuses by name...
    assert "resp.volatility" in fn and "insufficient-data" in fn
    # It reads the age of the bars it is drawing, and warns with the same words
    # the threshold and backtest cards use.
    assert "resp.freshness" in fn and "bt.dataStale" in fn
    # Both languages, so a missing translation cannot fall back to a key name.
    for key in ("atr.title", "atr.desc", "atr.window", "atr.needBars", "atr.ofPrice",
                "atr.regimeCalm", "atr.regimeNormal", "atr.regimeVolatile",
                "atr.calmBand", "atr.volBand", "atr.failed"):
        assert page.count(f"'{key}':") == 2, f"{key} is not defined in both languages"
    # The sentence that keeps the drawing honest travels with it.
    assert "atr.desc" in page[page.index("function renderAtrStrip("):page.index("async function atrStripLoad(")]
