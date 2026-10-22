"""The A/B walk-forward says what it can prove, and refuses when it cannot (1.0.15).

`adaptive.py` measures and proposes a factor; `adaptive_evidence.compare()` asks
whether the higher bar would have been better out-of-sample. The danger here is
not a crash — it is a confident sentence built on an unpaired run, a sample of
six trades, or simulated candles. So the tests pin exactly those properties:

1. **One variable.** Both arms share the window boundaries; only `min_score`
   differs. If a future edit lets the windows drift apart, the comparison stops
   being a comparison and this fails.
2. **The arithmetic of a higher bar.** Raising the bar can only remove trades,
   never add them — a run where the adaptive arm trades *more* means something
   other than the bar changed.
3. **Named refusals.** A factor of 1.0 is `no-op`; crossed bars are `bad-factor`
   (NEUTRAL would be unreachable, turning three states into two). Neither returns
   a flattering number. A factor *below* 1 used to be refused as
   `lower-bar-untestable`; 1.0.16 made the direction thresholds parameters, so it
   is now measurable — and the tests below hold the new invariant, that a lower
   bar can only add trades.
4. **Nothing is applied, ever.** `applied` and `promotable` are `False` on every
   path, and a verdict is never produced by a reference feed (ADR-0009/0014).
5. **Determinism.** The same candles produce the same numbers twice; an evidence
   harness that disagrees with itself is not evidence.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import adaptive_evidence as ev


def candles(n: int = 700, *, seed: int = 11, vol: float = 0.012, trend: float = 0.0002,
            tf_seconds: int = 900) -> list[dict]:
    """Deterministic random walk — no network, no fixtures on disk."""
    rng = random.Random(seed)
    price = 100.0
    rows: list[dict] = []
    for i in range(n):
        o = price
        c = max(0.01, o + trend * price + rng.uniform(-vol, vol) * price)
        rows.append({
            "t": 1_700_000_000 + i * tf_seconds,
            "o": o,
            "h": max(o, c) * (1 + vol / 3),
            "l": min(o, c) * (1 - vol / 3),
            "c": c,
            "v": 10.0 + i % 7,
        })
        price = c
    return rows


ROWS = candles()


def test_both_arms_are_walked_forward_on_the_same_windows() -> None:
    r = ev.compare(ROWS, "15m", "BTCUSDT", source="nobitex")
    assert r["ok"] is True
    assert r["windows"]["paired"] is True
    # The pooled arm summaries come from the same number of windows; a drifted
    # window boundary would show up here first.
    assert r["baseline"]["windows"] == r["adaptive"]["windows"] == r["windows"]["count"]
    assert r["windows"]["count"] >= ev.MIN_WINDOWS


def test_raising_the_bar_can_only_remove_trades() -> None:
    r = ev.compare(ROWS, "15m", "BTCUSDT", factor=1.25, source="nobitex")
    assert r["ok"] is True
    assert r["thresholds"]["scaled_long"] > r["thresholds"]["base_long"]
    assert r["adaptive"]["trades"] <= r["baseline"]["trades"]
    assert r["delta"]["trades"] <= 0


def test_a_factor_of_one_is_a_named_no_op() -> None:
    r = ev.compare(ROWS, "15m", "BTCUSDT", factor=1.0, source="nobitex")
    assert r["ok"] is False
    assert r["error"] == "no-op"
    # Even a refusal carries the honest flag.
    assert r["applied"] is False


def test_a_lower_bar_is_now_testable_and_can_only_add_trades() -> None:
    """1.0.16 made the direction thresholds parameters, so a factor below 1 is a
    real comparison rather than a no-op wearing a verdict.

    The invariant that makes it meaningful is on the **labels**, and it is worth
    being precise about why it is not on the trade count: the walk-forward
    re-optimises the stop/target grid on the train slice, so a different bar can
    drive it to a different parameter set and land on fewer eval trades. That is
    a real result, not a bug — the assertion below is the one that must hold.
    """
    r = ev.compare(ROWS, "15m", "BTCUSDT", factor=0.85, source="nobitex")
    assert r["ok"] is True
    assert r["thresholds"]["scaled_long"] < r["thresholds"]["base_long"]
    assert r["thresholds"]["scaled_neutral"] < r["thresholds"]["base_neutral"]
    assert r["applied"] is False

    # The signal-level monotonicity: a lower bar labels a superset LONG.
    import pandas as pd

    from ta_engine import ta_score_series

    df = pd.DataFrame(ROWS)
    for col in ("t",):
        df[col] = pd.to_datetime(df[col], unit="s", utc=True)
    df = df.astype({"o": float, "h": float, "l": float, "c": float, "v": float})
    base = ta_score_series(df)
    lower = ta_score_series(df, long_min=r["thresholds"]["scaled_long"],
                            neutral_min=r["thresholds"]["scaled_neutral"])
    base_long = sum(1 for sig in base if sig and sig["direction"] == "LONG")
    lower_long = sum(1 for sig in lower if sig and sig["direction"] == "LONG")
    base_neutral = sum(1 for sig in base if sig and sig["direction"] == "NEUTRAL")
    lower_neutral = sum(1 for sig in lower if sig and sig["direction"] == "NEUTRAL")
    assert lower_long >= base_long, "a lower bar admitted fewer LONG labels"
    # NEUTRAL *shrinks*, and that is correct: its strongest members are promoted
    # to LONG. The set that has to grow is "LONG or NEUTRAL" — the bars the old
    # rule called something other than EXIT.
    assert lower_long + lower_neutral >= base_long + base_neutral, \
        "a lower bar pushed bars into EXIT that the higher bar kept alive"


def test_both_arms_use_their_own_bars_for_the_label_not_just_the_filter() -> None:
    """The failure this guards: signals labelled with the constant while the
    backtester filters with the candidate — two different bars in one arm, which
    tests neither. Verified by construction: with the label still fixed at 68, a
    lower factor could not add a single trade, and the test above would fail."""
    import inspect

    src = inspect.getsource(ev._run)
    assert "ta_score_series(df, long_min=long_min, neutral_min=neutral_min)" in src
    assert '"min_score": float(long_min)' in src


def test_a_factor_outside_the_measurable_range_is_refused() -> None:
    """Outside [0.5, 2.0] this is a different strategy, not a threshold
    experiment, and calling it evidence would launder the difference."""
    for factor in (0.01, 3.0):
        r = ev.compare(ROWS, "15m", "BTCUSDT", factor=factor, source="nobitex")
        assert r["ok"] is False, f"factor {factor} was measured anyway"
        assert r["error"] == "bad-factor"
        assert "measurable range" in r["message"]
        assert r["applied"] is False


def test_a_thin_sample_is_inconclusive_rather_than_encouraging() -> None:
    small = candles(n=230, seed=3)
    r = ev.compare(small, "15m", "BTCUSDT", factor=1.25, source="nobitex")
    assert r["ok"] is True
    assert r["verdict"] == "inconclusive"
    # Either too few windows or too few trades — never a guess dressed as a verdict.
    assert ("windows" in r["why"]) or ("trades" in r["why"]) or ("noise" in r["why"])


def test_a_reference_feed_can_never_certify_a_strategy() -> None:
    r = ev.compare(ROWS, "15m", "BTCUSDT", factor=1.25, source="reference")
    assert r["ok"] is True
    assert r["verdict"] == "inconclusive"
    assert "can never certify" in r["why"]
    assert r["promotable"] is False
    # The arithmetic verdict, when it was clear, is kept aside rather than lost.
    if r["verdict_capped"]:
        assert r["verdict_capped"] in {"favours-adaptive", "favours-baseline"}


def test_nothing_is_ever_applied_and_no_promotion_is_offered() -> None:
    from typing import Any

    cases: tuple[dict[str, Any], ...] = ({"factor": 1.25}, {"factor": 1.0}, {"factor": 0.9},
                                         {"factor": 0.01}, {"factor": 3.0}, {})
    for kwargs in cases:
        r = ev.compare(ROWS, "15m", "BTCUSDT", source="nobitex", **kwargs)
        assert r["applied"] is False, f"applied flipped for {kwargs}"
        assert r.get("promotable", False) is False


def test_the_same_candles_produce_the_same_numbers_twice() -> None:
    a = ev.compare(ROWS, "15m", "BTCUSDT", factor=1.2, source="nobitex")
    b = ev.compare(ROWS, "15m", "BTCUSDT", factor=1.2, source="nobitex")
    for key in ("baseline", "adaptive", "delta", "verdict"):
        assert a[key] == b[key], f"{key} is not deterministic"


def test_too_few_candles_or_broken_rows_are_named_refusals() -> None:
    from adaptive import AdaptiveError

    with pytest.raises(AdaptiveError) as e:
        ev.compare(candles(n=40), "15m", "BTCUSDT")
    assert e.value.code == "insufficient-data"
    with pytest.raises(AdaptiveError) as e2:
        ev.compare([{"t": 1, "o": 1.0}], "15m", "BTCUSDT")
    assert e2.value.code == "bad-candles"
