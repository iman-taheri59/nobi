"""How old is the newest candle — asked in bars, answered with arithmetic (1.0.18).

A percentile measured on a series that stopped three days ago is not wrong, it is
*mislabelled*: the card says "now" and the data says "then". This module exists
to stop that, and the tests pin the three decisions that make it useful:

1. **Bars, not seconds.** The same wall-clock age is fresh at `4h` and stale at
   `15m`; a fixed seconds threshold would call every 4h series stale.
2. **A named reason.** `behind`, `ahead-of-clock`, `no-candles` — never a bare
   boolean, because "stale" invites an argument about opinion and `age_bars` does
   not.
3. **The boundary is inclusive.** A series exactly `tolerance_bars` behind is
   current; one bar more is not. Off-by-one here is the difference between a
   warning that means something and one users learn to ignore.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import adaptive
import freshness


def series(tf: str = "1h", *, bars: int = 20, last_age_minutes: float = 0.0,
           now: datetime | None = None) -> list[dict]:
    """A synthetic series whose newest candle sits `last_age_minutes` in the past."""
    # Whole seconds on purpose: `datetime.now()` carries microseconds, and the
    # boundary test below is about the rule, not about float drift.
    stamp = (now or datetime.now(UTC)).replace(microsecond=0)
    step = adaptive.tf_minutes(tf) * 60
    last = int(stamp.timestamp() - last_age_minutes * 60)
    return [{"t": last - (bars - 1 - i) * step, "o": 100.0, "h": 101.0, "l": 99.0,
             "c": 100.5, "v": 10.0} for i in range(bars)]


def test_a_fresh_series_is_current() -> None:
    out = freshness.assess(series("1h", last_age_minutes=5), "1h")
    assert out["stale"] is False
    assert out["reason"] == "current"
    assert out["age_bars"] == pytest.approx(5 / 60, abs=0.02)


def test_the_same_age_is_fresh_at_4h_and_stale_at_15m() -> None:
    """The whole reason the tolerance is in bars. Two hours behind is nothing for
    a 4h series and eight bars for a 15m one."""
    two_hours = 120.0
    fresh_4h = freshness.assess(series("4h", last_age_minutes=two_hours), "4h")
    stale_15m = freshness.assess(series("15m", last_age_minutes=two_hours), "15m")
    assert fresh_4h["stale"] is False, fresh_4h
    assert stale_15m["stale"] is True, stale_15m
    assert stale_15m["age_bars"] == pytest.approx(8.0, abs=0.05)
    assert stale_15m["reason"] == "behind"


def test_the_boundary_is_inclusive_at_the_tolerance() -> None:
    tf = "1h"
    now = datetime(2026, 10, 22, 12, 0, 0, tzinfo=UTC)
    exactly = freshness.assess(series(tf, last_age_minutes=120, now=now), tf, now=now)
    beyond = freshness.assess(series(tf, last_age_minutes=121, now=now), tf, now=now)
    assert exactly["stale"] is False, "a series exactly at the tolerance was called stale"
    assert beyond["stale"] is True, "a series past the tolerance was called current"


def test_a_candle_from_the_future_is_named_not_averaged() -> None:
    """Clock skew, or a feed that stamps ahead. Either way the age cannot be
    trusted, and a negative `age_bars` must never read as 'very fresh'."""
    now = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
    rows = series("1h", bars=5, now=now)
    rows[-1]["t"] = int(now.timestamp()) + 3 * 3600
    out = freshness.assess(rows, "1h", now=now)
    assert out["stale"] is True
    assert out["reason"] == "ahead-of-clock"
    assert out["age_bars"] < 0


def test_an_empty_series_is_a_result_not_an_exception() -> None:
    out = freshness.assess([], "1h")
    assert out["stale"] is True
    assert out["reason"] == "no-candles"
    assert out["age_bars"] is None


def test_an_unknown_timeframe_is_refused() -> None:
    with pytest.raises(adaptive.AdaptiveError) as e:
        freshness.assess(series("1h"), "7m")
    assert e.value.code == "bad-timeframe"


def test_unusable_timestamps_are_refused_with_a_reason() -> None:
    with pytest.raises(adaptive.AdaptiveError) as e:
        freshness.assess([{"t": "yesterday"}], "1h")
    assert e.value.code == "bad-candles"


def test_the_line_says_the_number_it_used() -> None:
    stale = freshness.assess(series("15m", last_age_minutes=120), "15m")
    text = freshness.line(stale)
    assert "8.0 bars old" in text, text
    assert "tolerance" in text
    current = freshness.assess(series("1h", last_age_minutes=5), "1h")
    assert "current" in freshness.line(current)


def test_the_endpoints_attach_freshness_to_both_cards() -> None:
    """The dashboard reads `freshness` from the same JSON it already parses; a
    backend that stopped attaching it would leave the card silently confident."""
    main_src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert main_src.count('out["freshness"] = await asyncio.to_thread(freshness.assess, rows, tf)') == 2, \
        "expected freshness on both the thresholds and the evidence route"
    # And a failure to assess must not take the numbers down with it.
    assert '{"stale": True, "reason": e.code, "age_bars": None}' in main_src


def test_a_tolerance_override_is_honoured() -> None:
    rows = series("1h", last_age_minutes=90)
    assert freshness.assess(rows, "1h")["stale"] is False
    strict = freshness.assess(rows, "1h", tolerance_bars=1.0)
    assert strict["stale"] is True
    assert strict["tolerance_bars"] == 1.0


def test_the_age_never_disagrees_with_its_own_fields() -> None:
    for minutes in (0, 60, 121, 600):
        out = freshness.assess(series("1h", last_age_minutes=minutes), "1h")
        assert out["age_bars"] == pytest.approx(out["age_seconds"] / out["bar_seconds"], abs=0.01)
        assert out["stale"] == (out["age_bars"] > out["tolerance_bars"])


def test_last_bar_is_iso_and_matches_the_newest_row() -> None:
    rows = series("1h", bars=4, last_age_minutes=30)
    out = freshness.assess(rows, "1h")
    newest = max(r["t"] for r in rows)
    assert out["last_bar"] == datetime.fromtimestamp(newest, UTC).isoformat()
    # A stamp far in the past, expressed once, not re-derived by hand:
    old = series("1h", bars=4, last_age_minutes=0, now=datetime(2020, 1, 1, tzinfo=UTC))
    out_old = freshness.assess(old, "1h")
    assert out_old["stale"] and out_old["reason"] == "behind"
    assert datetime.fromisoformat(out_old["last_bar"]) == datetime(2020, 1, 1, tzinfo=UTC) - timedelta(hours=0)
