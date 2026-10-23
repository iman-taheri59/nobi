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

ROOT_HTML = Path(__file__).resolve().parents[2] / "index.html"


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


#: 1.0.20 — the same rule on the backtest result, the largest set of numbers on the page.
def test_the_backtest_routes_report_freshness_too() -> None:
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert src.count('"freshness": freshness_block(rows, tf)') == 2, \
        "expected freshness on both walk-forward routes (run and quick)"


def test_a_failed_age_check_degrades_instead_of_taking_the_numbers_down() -> None:
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    helper = src[src.index("def freshness_block("):src.index('@app.get("/api/adaptive/thresholds')]
    # Named code for the cases the module knows, the exception's own name otherwise,
    # and never a re-raise: a card that loses its numbers because the age check
    # failed is the worst of both worlds.
    assert "AdaptiveRefusal" in helper and "e.code" in helper
    assert "type(e).__name__" in helper
    assert "raise" not in helper


def test_the_backtest_card_shows_the_age_and_hides_itself_when_current() -> None:
    page = (ROOT_HTML).read_text(encoding="utf-8")
    assert 'id="btFreshChip"' in page
    fn = page[page.index("function btRenderFresh(r)"):page.index("function btRender(r)")]
    # Hidden on current data...
    assert "chip.classList.add('hidden')" in fn
    # ...and the visible state carries the arithmetic, formatted like the page.
    assert "Math.abs(f.age_bars)" in fn and "fmtNum(" in fn
    assert "t('bt.dataStaleWhy').replace('{bars}'" in fn
    assert "t('bt.dataStale')" in fn
    # It must be called from btRender with the *response*, because freshness lives
    # beside the report, not inside it.
    assert "btRenderFresh(r);" in page[page.index("function btRender(r){"):][:400]
    for key in ("bt.dataStale", "bt.dataStaleWhy"):
        assert page.count(f"'{key}':") == 2, f"{key} is not defined in both languages"


#: 1.0.21 — the market scan answers the same question from a single stamp.
def test_the_two_doors_are_the_same_rule() -> None:
    """`assess()` and `assess_bar()` must not be two opinions.

    The scan keeps scores and discards rows, so it can only ask about one
    timestamp. If that path computed its own arithmetic, the same candles would
    be "stale" on one card and "current" on another — the drift this repo has
    already paid for once with the fee and the starting equity.
    """
    now = datetime.now(UTC).replace(microsecond=0)
    rows = series("15m", bars=12, last_age_minutes=60, now=now)
    newest = max(r["t"] for r in rows)
    from_series = freshness.assess(rows, "15m", now=now)
    from_stamp = freshness.assess_bar(newest, "15m", now=now)
    for key in ("stale", "reason", "bar_seconds", "tolerance_bars",
                "age_bars", "age_seconds", "last_bar"):
        assert from_series[key] == from_stamp[key], key
    # The one field that may differ is the count of rows, which only one caller has.
    assert from_series["bars"] == 12
    assert from_stamp["bars"] is None


def test_a_missing_bar_is_an_answer_and_an_unknown_timeframe_is_a_refusal() -> None:
    out = freshness.assess_bar(None, "1h")
    assert out["stale"] is True and out["reason"] == "no-candles"
    assert out["age_bars"] is None and out["bars"] is None
    with pytest.raises(adaptive.AdaptiveError) as err:
        freshness.assess_bar(None, "7m")
    assert err.value.code == "bad-timeframe"
    with pytest.raises(adaptive.AdaptiveError) as bad:
        freshness.assess_bar("not-a-time", "1h")
    assert bad.value.code == "bad-candles"


def test_the_scan_keeps_the_newest_bar_it_read_and_reports_it() -> None:
    """The scores are a claim about the candles the scan read — including when the
    venue hands back a series that stopped days ago.

    1.0.23: per *symbol*, not one number for the whole scan. A single `max()` across
    symbols is what let one dead feed hide behind twenty-three healthy ones.
    """
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert '"signals_bars": {}' in src, "the cache has no slot for the per-symbol bars"
    # Recorded inside the scan, from the rows it just fetched...
    assert 'bars_seen[u["symbol"]] = max(float(r["t"]) for r in rows if r.get("t") is not None)' in src
    assert '_cached["signals_bars"][tf] = dict(bars_seen)' in src
    # ...and attached to both answers of the ranking route, empty or not.
    assert src.count('"freshness": scan_freshness_block(_cached.get("signals_bars", {}).get(tf) or {}, tf)') == 2


def test_a_failed_scan_age_degrades_instead_of_emptying_the_ranking() -> None:
    src = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    helper = src[src.index("def freshness_block_at("):]
    helper = helper[:helper.index("@app.get(")]
    assert "freshness.assess_bar" in helper        # the shared rule, not a copy
    assert "AdaptiveRefusal" in helper and "e.code" in helper
    assert "raise" not in helper


def test_the_market_scan_card_names_the_age_and_hides_itself_when_current() -> None:
    page = ROOT_HTML.read_text(encoding="utf-8")
    assert 'id="sgFreshChip"' in page
    fn = page[page.index("function sgRenderFresh(f)"):page.index("function renderSignals()")]
    assert "chip.classList.add('hidden')" in fn          # current: no chip
    assert "Math.abs(fresh.age_bars)" in fn and "fmtNum(" in fn
    assert "t('sg.dataStaleWhy').replace('{bars}'" in fn
    assert "t('sg.dataStale')" in fn
    # It is drawn with the list and again when a scan lands, because the scan is
    # what changes the answer.
    assert "sgRenderFresh(sgFreshNow());" in page
    assert page.count("'sg.dataStale':") == 2 and page.count("'sg.dataStaleWhy':") == 2
    assert page.count("'sg.dataStaleNone':") == 2


#: 1.0.23 — a list of symbols is only as fresh as its worst name.
def test_one_dead_feed_makes_the_list_stale_and_is_named() -> None:
    """The failure 1.0.21 could not see: twenty-three current symbols and one whose
    candles stopped three days ago used to average into "current"."""
    import main

    now = datetime.now(UTC).replace(microsecond=0)
    fresh_ts = (now - timedelta(minutes=5)).timestamp()
    dead_ts = (now - timedelta(days=3)).timestamp()
    out = main.scan_freshness_block(
        {"BTCIRT": fresh_ts, "ETHIRT": fresh_ts, "DEADIRT": dead_ts}, "15m", now=now)

    assert out["stale"] is True
    assert out["reason"] == "behind"
    assert out["worst"]["symbol"] == "DEADIRT"
    assert out["worst"]["age_bars"] == out["age_bars"] == pytest.approx(288.0, abs=0.1)
    assert out["symbols"] == {"count": 3, "current": 2, "stale": 1, "no_candles": 0}


def test_a_list_with_no_lagging_symbol_is_current() -> None:
    import main

    now = datetime.now(UTC).replace(microsecond=0)
    ts = (now - timedelta(minutes=5)).timestamp()
    out = main.scan_freshness_block({"BTCIRT": ts, "ETHIRT": ts}, "15m", now=now)
    assert out["stale"] is False and out["reason"] == "current"
    assert out["worst"] is None
    assert out["symbols"]["stale"] == 0


def test_a_symbol_with_no_candles_outranks_any_age() -> None:
    """An unknown age is not a small age: it cannot be hidden by a number."""
    import main

    now = datetime.now(UTC).replace(microsecond=0)
    fresh_ts = (now - timedelta(minutes=5)).timestamp()
    bars: dict[str, float | None] = {"BTCIRT": fresh_ts, "EMPTYIRT": None}
    out = main.scan_freshness_block(bars, "15m", now=now)
    assert out["stale"] is True
    assert out["worst"]["symbol"] == "EMPTYIRT"
    assert out["worst"]["reason"] == "no-candles"
    assert out["symbols"]["no_candles"] == 1


def test_one_refused_symbol_does_not_refuse_the_list() -> None:
    """A `nan` stamp belongs to one row; the healthy ones keep their measurement."""
    import main

    now = datetime.now(UTC).replace(microsecond=0)
    ts = (now - timedelta(minutes=5)).timestamp()
    out = main.scan_freshness_block({"BTCIRT": ts, "GHOSTIRT": float("nan")}, "15m", now=now)
    assert out["worst"]["symbol"] == "GHOSTIRT"
    assert out["worst"]["reason"] == "bad-candles"
    assert out["symbols"]["current"] == 1


def test_an_empty_scan_reports_no_candles_not_silence() -> None:
    import main

    out = main.scan_freshness_block({}, "15m")
    assert out["stale"] is True and out["reason"] == "no-candles"
    assert out["worst"] is None and out["symbols"]["count"] == 0
