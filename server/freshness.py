"""How old is the newest candle? Asked in bars, because seconds do not generalise.

A card that says "ATR sits at the 12th percentile" is making a claim about *now*.
If the newest candle is three days old, the claim is about three days ago and the
number is not wrong so much as mislabelled — which is the failure mode this whole
module exists to prevent, in the same family as `applied: false` and the
reference-feed stamp: **never let a number wear a context it did not earn**.

Two decisions worth writing down
--------------------------------
*Bars, not seconds.* "Stale" means different things at 15m and 1h, and a fixed
120-second threshold would call every 4h series stale and every 15m series fresh.
The tolerance is expressed in bars (`tolerance_bars`), so one rule fits every
timeframe: more than two bars behind the clock is stale.

*Cheap and explicit.* No network, no cache: the caller already holds the candles
it just fetched. The result is a small dict a dashboard can print and a test can
assert, with a named `reason` rather than a boolean alone — `no-candles`,
`behind`, `ahead-of-clock` — because "stale" without a number invites an argument
about opinion, and `age_bars: 41.2` does not.

*One rule, two doors.* `assess()` takes a candle series; `assess_bar()` takes the
single newest timestamp the caller already extracted. They are the same arithmetic
by construction — `assess()` is `assess_bar()` plus "find the newest row" — because
the scan (1.0.21) fetches 260 rows per symbol and keeps only the scores: asking it
to retain a series just to measure its age would have been a second, drift-prone
copy of the rule.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from adaptive import AdaptiveError, tf_minutes

#: More than this many bar-lengths behind the clock reads as stale. Two, not one:
#: the newest candle is the *open* one, and a series fetched a minute after the
#: open is legitimately one bar "old" by wall-clock arithmetic.
DEFAULT_TOLERANCE_BARS = 2.0

#: A candle timestamp further ahead than this many bars means the clock or the
#: feed is wrong, and either way the age cannot be trusted.
AHEAD_TOLERANCE_BARS = 1.0


def _ts_seconds(value: Any) -> float:
    """Epoch seconds from an int/float or a pandas/numpy timestamp."""
    if hasattr(value, "timestamp"):
        ts = value.timestamp()
        return float(ts)
    return float(value)


def assess_bar(
    bar_at: Any,
    tf: str,
    *,
    now: datetime | None = None,
    tolerance_bars: float = DEFAULT_TOLERANCE_BARS,
) -> dict[str, Any]:
    """Where a bar timestamp sits relative to the clock, in bars.

    The single implementation of the rule. ``bar_at`` is the *open* time of the
    newest bar the caller based its number on, in epoch seconds (or a pandas
    timestamp, or ``None`` when there is no bar at all).

    Callers that hold a series use `assess()`; callers that kept only the newest
    stamp — the market scan discards its rows — call this one.
    """
    bar_seconds = float(tf_minutes(tf) * 60)
    stamp = now or datetime.now(UTC)
    now_s = stamp.timestamp()

    if bar_at is None:
        return {
            "stale": True,
            "reason": "no-candles",
            "bars": None,
            "bar_seconds": bar_seconds,
            "tolerance_bars": tolerance_bars,
            "age_bars": None,
            "age_seconds": None,
            "last_bar": None,
            "as_of": stamp.isoformat(),
        }

    try:
        last = _ts_seconds(bar_at)
    except (TypeError, ValueError) as exc:
        raise AdaptiveError("bad-candles", f"candle timestamps are not usable: {exc}") from exc

    age_seconds = now_s - last
    age_bars = age_seconds / bar_seconds
    if age_bars < -AHEAD_TOLERANCE_BARS:
        reason = "ahead-of-clock"
        stale = True
    elif age_bars > tolerance_bars:
        reason = "behind"
        stale = True
    else:
        reason = "current"
        stale = False

    return {
        "stale": stale,
        "reason": reason,
        "bars": None,
        "bar_seconds": bar_seconds,
        "tolerance_bars": tolerance_bars,
        "age_bars": round(age_bars, 2),
        "age_seconds": round(age_seconds, 1),
        "last_bar": datetime.fromtimestamp(last, UTC).isoformat(),
        "as_of": stamp.isoformat(),
    }


def assess(
    rows: list[dict],
    tf: str,
    *,
    now: datetime | None = None,
    tolerance_bars: float = DEFAULT_TOLERANCE_BARS,
) -> dict[str, Any]:
    """Where the newest candle sits relative to the clock, in bars.

    Returns ``stale`` plus the arithmetic behind it. Raises `AdaptiveError` only
    for an unknown timeframe or unusable timestamps — an empty series is a
    *result* here (``no-candles``), not an exception, because callers report it
    next to the numbers.
    """
    if not rows:
        out = assess_bar(None, tf, now=now, tolerance_bars=tolerance_bars)
        out["bars"] = 0
        return out

    try:
        newest = max(_ts_seconds(r.get("t")) for r in rows if r.get("t") is not None)
    except (TypeError, ValueError) as exc:
        raise AdaptiveError("bad-candles", f"candle timestamps are not usable: {exc}") from exc

    out = assess_bar(newest, tf, now=now, tolerance_bars=tolerance_bars)
    out["bars"] = len(rows)
    return out


def line(fresh: dict[str, Any]) -> str:
    """One sentence for a log or a CLI — the reason and the arithmetic together."""
    reason = fresh.get("reason")
    if reason == "no-candles":
        return "freshness: no candles"
    if reason == "ahead-of-clock":
        return f"freshness: newest candle is {abs(fresh.get('age_bars') or 0):.1f} bars ahead of the clock"
    if reason == "behind":
        return (f"freshness: newest candle is {fresh.get('age_bars')} bars old "
                f"(tolerance {fresh.get('tolerance_bars')})")
    return f"freshness: current ({fresh.get('age_bars')} bars behind, tolerance {fresh.get('tolerance_bars')})"
