"""Timeframe- and regime-aware thresholds: measured, bounded, never applied (1.0.12).

`LONG_SCORE_MIN = 68` is one number for every symbol and every timeframe. The
tests below pin the three claims that make `adaptive.py` worth having:

1. **The measurement is honest about its inputs.** Too few candles, a flat
   market (ATR == 0) and an unknown timeframe are named refusals, not numbers
   that happen to be computable on a near-empty window.
2. **The recommendation is bounded and ordered.** A factor can never push the
   LONG line out of reach or make NEUTRAL overtake it, and a shorter timeframe
   always gets a bar at least as high as a longer one.
3. **Nothing is applied.** `applied` is `False` on every path, including the
   route: this is evidence for an ADR, not a silent retune of the engine.

Candles are generated locally — a test that needs the internet fails for reasons
that have nothing to do with the code.
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import adaptive as ad


def candles(n: int = 400, *, seed: int = 7, vol: float = 0.01, trend: float = 0.0,
            tf_seconds: int = 3600, flat: bool = False) -> list[dict]:
    """A deterministic random walk with a known amount of noise."""
    rng = random.Random(seed)
    price = 100.0
    rows: list[dict] = []
    for i in range(n):
        drift = trend * price
        move = 0.0 if flat else rng.uniform(-vol, vol) * price
        o = price
        c = max(0.01, o + drift + move)
        h = max(o, c) if flat else max(o, c) * (1 + vol / 3)
        lo = min(o, c) if flat else min(o, c) * (1 - vol / 3)
        rows.append({"t": 1_700_000_000 + i * tf_seconds, "o": o, "h": h, "l": lo,
                     "c": c, "v": 10.0 + i % 7})
        price = c
    return rows


# --------------------------------------------------------------------------- #
# 1. refusals
# --------------------------------------------------------------------------- #
def test_too_few_candles_is_a_named_refusal() -> None:
    with pytest.raises(ad.AdaptiveError) as exc:
        ad.report(candles(40), "1h")
    assert exc.value.code == "insufficient-data"
    assert exc.value.meta["required"] == ad.MIN_BARS


def test_a_flat_market_refuses_instead_of_reporting_percentile_zero() -> None:
    """ATR == 0 means the percentile of variance is undefined, not "calm"."""
    with pytest.raises(ad.AdaptiveError) as exc:
        ad.report(candles(300, flat=True), "1h")
    assert exc.value.code == "flat-market"


def test_an_unknown_timeframe_names_itself() -> None:
    with pytest.raises(ad.AdaptiveError) as exc:
        ad.timeframe_factor("7m")
    assert exc.value.code == "bad-timeframe"
    assert "1h" in exc.value.meta["allowed"]


def test_candles_with_no_rows_are_refused() -> None:
    with pytest.raises(ad.AdaptiveError) as exc:
        ad.report([], "1h")
    assert exc.value.code == "bad-candles"


def test_an_impossible_candle_is_dropped_rather_than_scored() -> None:
    """A high below its low is a broken row, not an opportunity."""
    rows = candles(300)
    rows[10]["h"] = rows[10]["l"] - 1
    rep = ad.report(rows, "1h")
    assert rep["ok"] is True
    assert rep["frequency"]["bars"] < len(rows)


# --------------------------------------------------------------------------- #
# 2. the recommendation
# --------------------------------------------------------------------------- #
def test_a_shorter_timeframe_asks_for_at_least_as_much_proof() -> None:
    """Noise scales with time, so 15m must never be *easier* than 4h."""
    f15, f1h, f4h, fd = (ad.timeframe_factor(tf) for tf in ("15m", "1h", "4h", "1d"))
    assert f15 >= f1h >= f4h >= fd
    assert f1h == pytest.approx(1.0)          # the reference timeframe is neutral
    assert f15 > 1.0 > fd


def test_the_factors_are_bounded_in_both_directions() -> None:
    low, high = ad.ENTRY_BOUNDS
    for tf in ad.TF_MINUTES:
        assert low <= ad.timeframe_factor(tf) <= high


def test_the_long_line_stays_reachable_and_above_neutral() -> None:
    """A factor that flattens the decision order is refused, not reported."""
    rep = ad.report(candles(400), "15m")
    rec = rep["thresholds"]["recommended"]
    assert 40.0 < rec["neutral"] < rec["long"] <= 99.0


def test_the_recommendation_moves_with_the_timeframe_in_one_direction() -> None:
    fast = ad.report(candles(400), "15m")["thresholds"]["recommended"]["long"]
    hour = ad.report(candles(400), "1h")["thresholds"]["recommended"]["long"]
    slow = ad.report(candles(400), "4h")["thresholds"]["recommended"]["long"]
    assert fast > hour > slow, "the same candles must not clear a lower bar on 15m"
    assert hour == 68.0, "the reference timeframe reproduces the live constant"


def test_the_base_thresholds_are_reported_unchanged_next_to_the_guess() -> None:
    """The operator must be able to see what is live *and* what is proposed."""
    rep = ad.report(candles(400), "1h")
    assert rep["thresholds"]["base"] == {"long": 68.0, "neutral": 55.0}
    assert rep["reference_tf"] == "1h"
    assert rep["tf"] == "1h"


def test_the_stop_widens_in_a_volatile_regime_and_tightens_in_a_calm_one() -> None:
    assert ad.REGIME_STOP["volatile"] > 1.0 > ad.REGIME_STOP["calm"]


def test_regime_cuts_are_ordered_and_cover_every_percentile() -> None:
    assert ad.regime_for(0.0) == "calm"
    assert ad.regime_for(ad.CALM_MAX - 1e-9) == "calm"
    assert ad.regime_for(ad.CALM_MAX) == "normal"
    assert ad.regime_for(ad.VOLATILE_MIN) == "normal"
    assert ad.regime_for(min(1.0, ad.VOLATILE_MIN + 1e-9)) in {"volatile"}
    assert ad.regime_for(1.0) == "volatile"


def test_a_quiet_series_and_a_violent_one_land_in_different_regimes() -> None:
    calm = ad.report(candles(400, vol=0.002, seed=3), "1h")
    wild = ad.report(candles(400, vol=0.05, seed=3), "1h")
    assert calm["volatility"]["atr_pct_price"] < wild["volatility"]["atr_pct_price"]
    assert 0.0 <= calm["volatility"]["percentile"] <= 1.0
    assert 0.0 <= wild["volatility"]["percentile"] <= 1.0


def test_the_percentile_ignores_the_scale_of_the_asset() -> None:
    """A percentile, not an ATR/price ratio: the same series ×1000 reads the same."""
    base = candles(400, seed=11)
    scaled = [{**row, "o": row["o"] * 1000, "h": row["h"] * 1000,
               "l": row["l"] * 1000, "c": row["c"] * 1000} for row in base]
    a, b = ad.atr_percentile(ad._clean(base)), ad.atr_percentile(ad._clean(scaled))
    assert b["percentile"] == pytest.approx(a["percentile"], abs=1e-9)
    assert b["atr_pct_price"] == pytest.approx(a["atr_pct_price"], rel=1e-6)


# --------------------------------------------------------------------------- #
# 3. nothing is applied, and the frequency is a fact
# --------------------------------------------------------------------------- #
def test_the_report_never_claims_to_have_changed_a_decision() -> None:
    for tf in ("15m", "1h", "4h", "1d"):
        rep = ad.report(candles(400), tf)
        assert rep["applied"] is False
        assert "measurement only" in rep["note"]


def test_the_frequency_counts_every_scored_bar_exactly_once() -> None:
    rep = ad.report(candles(400), "1h")
    freq = rep["frequency"]
    assert freq["bars"] == len(candles(400)) - freq["skipped"]
    assert sum(freq["counts"].values()) == freq["bars"]
    assert sum(freq["shares"].values()) == pytest.approx(1.0, abs=2e-3)
    assert freq["long_share"] == pytest.approx(freq["counts"]["LONG"] / freq["bars"], abs=1e-4)


def test_the_same_candles_always_produce_the_same_report() -> None:
    """Determinism matters: a threshold that moves by itself cannot be reviewed."""
    a = ad.report(candles(300, seed=99), "1h")
    b = ad.report(candles(300, seed=99), "1h")
    assert a == b


def test_a_trending_series_scores_higher_than_a_noisy_one() -> None:
    trend = ad.report(candles(400, trend=0.004, vol=0.004), "1h")
    noise = ad.report(candles(400, trend=0.0, vol=0.02), "1h")
    assert trend["frequency"]["median_score"] > noise["frequency"]["median_score"]


# --------------------------------------------------------------------------- #
# 4. the route
# --------------------------------------------------------------------------- #
def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_the_route_returns_a_recommendation_and_says_it_is_not_applied(monkeypatch):
    import main

    async def fake_candles(symbol, tf, limit, fx=None):
        return candles(400), {"provider": "kucoin", "pair": "BTC-USDT", "derived": False}

    monkeypatch.setattr(main.reference_feed, "fetch_candles", fake_candles)
    out = _run(main.adaptive_thresholds("BTCUSDT", tf="15m", source="reference", limit=400))
    assert out["ok"] is True
    assert out["candles_source"] == "reference"
    assert out["reference"]["provider"] == "kucoin"
    assert out["applied"] is False
    assert out["thresholds"]["base"]["long"] == 68.0
    assert out["thresholds"]["recommended"]["long"] > 68.0


def test_the_route_surfaces_a_refusal_instead_of_a_partial_report(monkeypatch):
    import main

    async def too_short(symbol, tf, limit, fx=None):
        return candles(30), {"provider": "kucoin"}

    monkeypatch.setattr(main.reference_feed, "fetch_candles", too_short)
    out = _run(main.adaptive_thresholds("BTCUSDT", tf="1h", source="reference", limit=400))
    assert out["ok"] is False
    assert out["error"] == "insufficient-data"
    assert "thresholds" not in out


def test_the_route_marks_an_unreachable_venue_as_unavailable_not_flat(monkeypatch):
    import main

    async def boom(symbol, tf, limit):
        raise main.UpstreamUnavailable("Nobitex timeout")

    monkeypatch.setattr(main.client, "get_candles_ex", boom)
    out = _run(main.adaptive_thresholds("BTCIRT", tf="1h", source="nobitex", limit=400))
    assert out["ok"] is False
    assert out["error"] == "unavailable" or "unavailable" in str(out["error"])
    assert "thresholds" not in out, "an unreachable venue must not become a flat market"


def test_an_unknown_timeframe_never_reaches_the_engine(monkeypatch):
    import main

    async def fake_candles(symbol, tf, limit, fx=None):
        return candles(400), {"provider": "kucoin"}

    monkeypatch.setattr(main.reference_feed, "fetch_candles", fake_candles)
    out = _run(main.adaptive_thresholds("BTCUSDT", tf="7m", source="reference", limit=400))
    assert out["ok"] is False
    assert out["error"] == "bad-timeframe"


def test_the_report_is_json_safe() -> None:
    import json

    rep = ad.report(candles(400), "1h")
    assert json.loads(json.dumps(rep))["ok"] is True
    for value in rep["thresholds"]["recommended"].values():
        assert math.isfinite(value)


def test_the_explanation_follows_the_sign_of_its_own_factor() -> None:
    """Prose that contradicts the arithmetic teaches operators to distrust both."""
    fast = ad.report(candles(400), "15m")
    slow = ad.report(candles(400), "4h")
    assert fast["scales"]["entry"] > 1 and "is noisier than" in fast["why"]
    assert slow["scales"]["entry"] < 1 and "is quieter than" in slow["why"]
    middle = ad.report(candles(400), "1h")
    assert "reference timeframe" in middle["why"]


def test_crossed_thresholds_are_refused_at_the_engine_boundary():
    """1.0.16 turned the two bars into parameters. Crossed bars would make
    NEUTRAL unreachable and turn three states into two without anyone noticing,
    so the engine refuses instead of rendering."""
    import ta_engine

    assert ta_engine.direction_for(70.0) == "LONG"
    assert ta_engine.direction_for(70.0, long_min=75.0, neutral_min=55.0) == "NEUTRAL"
    assert ta_engine.direction_for(58.0, long_min=57.8, neutral_min=46.75) == "LONG"
    with pytest.raises(ValueError):
        ta_engine.direction_for(70.0, long_min=40.0, neutral_min=60.0)
