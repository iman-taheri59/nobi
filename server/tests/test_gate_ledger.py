"""ADR-0022 stage 2 — the ledger that decides whether a gate may ever block.

The rule being tested is the one in the ADR: a gate leaves shadow mode for **one
gate**, with **30 verdicts** across **5 days**, and only if the rows it would have
removed actually did worse afterwards. Everything below is a way that rule can be
quietly broken, so most of these tests assert a refusal.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime

import pandas as pd
import pytest

import gate_ledger as gl

TF = "4h"
STEP = 14_400
#: A day in seconds, so "5 days of markets" is five *distinct dates*, not five bars.
DAY = 86_400


def frame(prices: list[float], start: int, step: int = STEP) -> pd.DataFrame:
    """A minimal candle frame: what `forward_bp` actually reads."""
    opens = [start + i * step for i in range(len(prices))]
    return pd.DataFrame({"t": opens, "c": prices})


def ledger(tmp_path):
    return gl.connect(tmp_path / "ledger.db")


def write(conn, verdicts: dict, *, ts_of: dict, tf: str = TF, source: str | None = None) -> int:
    return gl.record(conn, verdicts, tf=tf, ts_of=ts_of, source=source)


# --------------------------------------------------------------------------- #
# The table: one row per bar, no matter how often the scan runs
# --------------------------------------------------------------------------- #
def test_the_module_codes_match_the_gates_it_reads():
    # `ok` is not a gate and the unknown codes are not claims; both must be absent
    # from what can be measured, or a gate could be "supported" by a missing book.
    assert gl.MEASURABLE == ("halted", "stale-data", "spread-wide", "book-thin")
    assert "no-book" not in gl.MEASURABLE and "no-data" not in gl.MEASURABLE


def test_rescanning_the_same_bar_cannot_inflate_the_sample(tmp_path):
    conn = ledger(tmp_path)
    v = {"BTCIRT": {"code": "spread-wide", "codes": ["spread-wide"], "spread_bps": 90.0}}
    ts = {"BTCIRT": 1_700_000_000 + STEP}
    first = write(conn, v, ts_of=ts)
    second = write(conn, v, ts_of=ts)      # the scan runs every 90 s; the bar is the same
    assert (first, second) == (1, 0)
    assert len(gl.rows(conn)) == 1


def test_the_first_verdict_for_a_bar_is_the_one_that_stood(tmp_path):
    """A book that arrives late must not rewrite what was known at the close."""
    conn = ledger(tmp_path)
    ts = {"BTCIRT": 1_700_000_000 + STEP}
    write(conn, {"BTCIRT": {"code": "no-book", "codes": ["no-book"]}}, ts_of=ts)
    write(conn, {"BTCIRT": {"code": "spread-wide", "codes": ["spread-wide"],
                            "spread_bps": 80.0}}, ts_of=ts)
    row = gl.rows(conn)[0]
    assert row["code"] == "no-book" and row["spread_bps"] is None


def test_a_verdict_without_a_bar_stamp_is_not_stored(tmp_path):
    conn = ledger(tmp_path)
    n = write(conn, {"BTCIRT": {"code": "spread-wide", "codes": ["spread-wide"]}}, ts_of={})
    assert n == 0 and gl.rows(conn) == []


def test_both_the_codes_and_the_measurement_survive_a_round_trip(tmp_path):
    conn = ledger(tmp_path)
    ts = {"BTCIRT": 1_700_000_000 + STEP}
    write(conn, {"BTCIRT": {"code": "spread-wide",
                            "codes": ["stale-data", "spread-wide"],
                            "spread_bps": 41.5, "depth_usd": 12_000.0,
                            "age_bars": 3.0}}, ts_of=ts, source="live")
    row = gl.rows(conn)[0]
    assert row["code"] == "spread-wide"
    assert '"stale-data"' in row["codes"] and '"spread-wide"' in row["codes"]
    assert row["spread_bps"] == pytest.approx(41.5)
    assert row["depth_usd"] == pytest.approx(12_000.0)
    assert row["source"] == "live"


def test_the_table_can_be_created_twice(tmp_path):
    conn = ledger(tmp_path)
    gl.ensure(conn)
    gl.ensure(conn)
    assert isinstance(conn, sqlite3.Connection)


# --------------------------------------------------------------------------- #
# Counting: verdicts and *days of markets*
# --------------------------------------------------------------------------- #
def test_counts_separate_verdicts_from_days(tmp_path):
    conn = ledger(tmp_path)
    base = 1_700_000_000
    # 40 verdicts, all inside one afternoon: one day of markets, not five.
    ts_of = {f"S{i}": base + STEP for i in range(40)}
    write(conn, {s: {"code": "spread-wide", "codes": ["spread-wide"]} for s in ts_of},
          ts_of=ts_of)
    c = gl.counts(conn)["gates"]["spread-wide"]
    assert c["verdicts"] == 40
    assert c["days"] == 1
    assert c["ready"] is False
    assert c["missing"] == {"verdicts": 0, "days": 4}


def test_ready_needs_both_thirty_verdicts_and_five_days(tmp_path):
    conn = ledger(tmp_path)
    base = 1_700_000_000
    verdicts, ts_of = {}, {}
    for d in range(5):
        for i in range(6):                       # 30 verdicts across 5 days
            sym = f"S{d}{i}"
            verdicts[sym] = {"code": "book-thin", "codes": ["book-thin"]}
            ts_of[sym] = base + d * DAY + STEP
    write(conn, verdicts, ts_of=ts_of)
    out = gl.counts(conn)
    assert out["gates"]["book-thin"]["ready"] is True
    assert out["ready"] == ["book-thin"]


def test_an_empty_ledger_says_ready_and_has_limits_rather_than_raising(tmp_path):
    conn = ledger(tmp_path)
    out = gl.counts(conn)
    assert out["gates"] == {} and out["ready"] == []
    assert out["limits"]["min_verdicts"] == 30 and out["limits"]["min_days"] == 5


# --------------------------------------------------------------------------- #
# Forward returns: close-to-close, and "not yet" is not "zero"
# --------------------------------------------------------------------------- #
def test_forward_return_is_close_to_close_from_the_bar_that_closed(tmp_path):
    f = frame([100.0, 101.0, 102.0], start=1_700_000_000)
    # The row is stamped with the close of bar 0 (open + step).
    got = gl.forward_bp(f, 1_700_000_000 + STEP, 1)
    assert got == pytest.approx(100.0)          # 100 → 101 is +100 bp


def test_a_row_whose_future_bar_does_not_exist_yet_returns_none():
    f = frame([100.0, 101.0], start=1_700_000_000)
    assert gl.forward_bp(f, 1_700_000_000 + STEP, 4) is None


def test_a_stamp_that_is_not_a_bar_close_returns_none():
    f = frame([100.0, 101.0, 102.0], start=1_700_000_000)
    assert gl.forward_bp(f, 1_700_000_123, 1) is None


# --------------------------------------------------------------------------- #
# The verdict: sample first, then direction
# --------------------------------------------------------------------------- #
def _evidence(conn):
    return gl.rows(conn)


def test_twenty_nine_verdicts_over_five_days_is_still_insufficient(tmp_path):
    conn = ledger(tmp_path)
    base = 1_700_000_000
    verdicts, ts_of, frames = {}, {}, {}
    for i in range(29):
        sym = f"S{i}"
        verdicts[sym] = {"code": "spread-wide", "codes": ["spread-wide"]}
        ts_of[sym] = base + (i % 5) * DAY + STEP
        frames[sym] = frame([100.0, 90.0, 90.0, 90.0, 90.0], start=base + (i % 5) * DAY)
    # one allowed row per bar so the comparison could have been made
    for d in range(5):
        sym = f"OK{d}"
        verdicts[sym] = {"code": "ok", "codes": []}
        ts_of[sym] = base + d * DAY + STEP
        frames[sym] = frame([100.0, 100.0, 100.0, 100.0, 100.0], start=base + d * DAY)
    write(conn, verdicts, ts_of=ts_of)
    out = gl.measure(_evidence(conn), frames, gate="spread-wide", horizon=1)
    assert out["verdict"] == "insufficient"
    assert out["blocked"]["verdicts"] == 29
    assert out["missing"]["verdicts"] == 1
    assert out["applied"] is False


def test_thirty_verdicts_in_one_day_is_still_insufficient(tmp_path):
    """Sample size is not days: 200 verdicts in an afternoon is one market day."""
    conn = ledger(tmp_path)
    base = 1_700_000_000
    verdicts, ts_of, frames = {}, {}, {}
    for i in range(30):
        sym = f"S{i}"
        verdicts[sym] = {"code": "spread-wide", "codes": ["spread-wide"]}
        ts_of[sym] = base + STEP
        frames[sym] = frame([100.0, 90.0], start=base)
    verdicts["OK"] = {"code": "ok", "codes": []}
    ts_of["OK"] = base + STEP
    frames["OK"] = frame([100.0, 100.0], start=base)
    write(conn, verdicts, ts_of=ts_of)
    out = gl.measure(_evidence(conn), frames, gate="spread-wide", horizon=1)
    assert out["verdict"] == "insufficient"
    assert "days covered 1" in out["reason"]


def _big_sample(conn, *, blocked_drop_bp: float, days: int = 5, per_day: int = 8,
                allowed_bp: float = 0.0, start: int = 1_700_000_000):
    """30+ verdicts across `days` days; the blocked names all move by one amount."""
    verdicts, ts_of, frames = {}, {}, {}
    for d in range(days):
        t0 = start + d * DAY
        for i in range(per_day):
            sym = f"B{d}_{i}"
            verdicts[sym] = {"code": "spread-wide", "codes": ["spread-wide"],
                             "spread_bps": 120.0}
            ts_of[sym] = t0 + STEP
            frames[sym] = frame([100.0, 100.0 * (1 + blocked_drop_bp / 10_000)], start=t0)
        sym = f"OK{d}"
        verdicts[sym] = {"code": "ok", "codes": []}
        ts_of[sym] = t0 + STEP
        frames[sym] = frame([100.0, 100.0 * (1 + allowed_bp / 10_000)], start=t0)
    write(conn, verdicts, ts_of=ts_of)
    return frames


def test_a_gate_that_removes_the_worse_rows_is_supported(tmp_path):
    conn = ledger(tmp_path)
    frames = _big_sample(conn, blocked_drop_bp=-80.0)     # blocked rows fall 80 bp
    out = gl.measure(_evidence(conn), frames, gate="spread-wide", horizon=1)
    assert out["verdict"] == "supports"
    assert out["mean_diff_bp"] == pytest.approx(-80.0, abs=0.5)
    assert out["share_worse"] == 1.0
    assert out["applied"] is False


def test_a_gate_that_removes_the_better_rows_contradicts_itself(tmp_path):
    conn = ledger(tmp_path)
    frames = _big_sample(conn, blocked_drop_bp=+120.0)    # blocked rows *outperform*
    out = gl.measure(_evidence(conn), frames, gate="spread-wide", horizon=1)
    assert out["verdict"] == "contradicts"
    assert "earned 120" in out["reason"]


def test_a_difference_inside_the_threshold_is_inconclusive(tmp_path):
    conn = ledger(tmp_path)
    frames = _big_sample(conn, blocked_drop_bp=-10.0)     # real, but smaller than 25 bp
    out = gl.measure(_evidence(conn), frames, gate="spread-wide", horizon=1)
    assert out["verdict"] == "inconclusive"
    assert out["mean_diff_bp"] == pytest.approx(-10.0, abs=0.5)


def test_the_comparison_is_paired_per_bar_not_against_the_whole_market(tmp_path):
    """A market-wide rally must not make a flat gate look good.

    Every bar rises by the same 200 bp on both sides, so the gate's own difference
    is zero; comparing blocked rows with an unpaired market mean would invent a
    result out of the rally.
    """
    conn = ledger(tmp_path)
    verdicts, ts_of, frames = {}, {}, {}
    start = 1_700_000_000
    for d in range(5):
        t0 = start + d * DAY
        for i in range(8):
            sym = f"B{d}_{i}"
            verdicts[sym] = {"code": "book-thin", "codes": ["book-thin"]}
            ts_of[sym] = t0 + STEP
            frames[sym] = frame([100.0, 102.0], start=t0)
        sym = f"OK{d}"
        verdicts[sym] = {"code": "ok", "codes": []}
        ts_of[sym] = t0 + STEP
        frames[sym] = frame([100.0, 102.0], start=t0)
    write(conn, verdicts, ts_of=ts_of)
    out = gl.measure(_evidence(conn), frames, gate="book-thin", horizon=1)
    assert out["mean_diff_bp"] == pytest.approx(0.0, abs=1e-6)
    assert out["verdict"] == "inconclusive"


def test_unresolved_rows_are_counted_and_never_imputed_as_zero(tmp_path):
    conn = ledger(tmp_path)
    base = 1_700_000_000
    verdicts, ts_of, frames = {}, {}, {}
    for d in range(5):
        t0 = base + d * DAY
        for i in range(8):
            sym = f"B{d}_{i}"
            verdicts[sym] = {"code": "spread-wide", "codes": ["spread-wide"]}
            ts_of[sym] = t0 + STEP
            # no future bars at all: this row cannot answer
        verdicts[f"OK{d}"] = {"code": "ok", "codes": []}
        ts_of[f"OK{d}"] = t0 + STEP
        frames[f"OK{d}"] = frame([100.0, 101.0], start=t0)
    write(conn, verdicts, ts_of=ts_of)
    out = gl.measure(_evidence(conn), frames, gate="spread-wide", horizon=1)
    assert out["blocked"] == {"verdicts": 40, "resolved": 0, "unresolved": 40}
    assert out["bars_used"] == 0
    assert out["verdict"] == "insufficient"
    # A day counts only when a *resolved* blocked row exists on it: an unresolved
    # bar is not a day of evidence, it is a day of waiting.
    assert out["days"] == 0
    assert "days covered 0" in out["reason"]


def test_a_frame_without_the_future_bar_is_unresolved_not_a_zero_return(tmp_path):
    """The dangerous imputation: "not known yet" read as "went nowhere".

    Here every blocked row *has* a frame, but the frame stops at the bar that was
    judged. Treating the missing future as a 0 bp return would score the gate at
    -100 bp against healthy rows and report `supports` on evidence that does not
    exist yet.
    """
    conn = ledger(tmp_path)
    base = 1_700_000_000
    verdicts, ts_of, frames = {}, {}, {}
    for d in range(5):
        t0 = base + d * DAY
        for i in range(8):
            sym = f"B{d}_{i}"
            verdicts[sym] = {"code": "spread-wide", "codes": ["spread-wide"]}
            ts_of[sym] = t0 + STEP
            frames[sym] = frame([100.0], start=t0)       # judged bar only
        verdicts[f"OK{d}"] = {"code": "ok", "codes": []}
        ts_of[f"OK{d}"] = t0 + STEP
        frames[f"OK{d}"] = frame([100.0, 101.0], start=t0)
    write(conn, verdicts, ts_of=ts_of)
    out = gl.measure(_evidence(conn), frames, gate="spread-wide", horizon=1)
    assert out["blocked"]["unresolved"] == 40
    assert out["mean_diff_bp"] is None
    assert out["verdict"] == "insufficient"


def test_reference_data_can_never_promote_a_gate(tmp_path):
    """ADR-0009/ADR-0014: simulated candles may show a method, never certify one."""
    conn = ledger(tmp_path)
    frames = _big_sample(conn, blocked_drop_bp=-400.0)
    out = gl.measure(_evidence(conn), frames, gate="spread-wide", horizon=1,
                     source="reference")
    assert out["verdict"] == "inconclusive"
    assert out["reason"] == "reference data cannot promote a gate"


def test_an_unknown_code_is_refused_by_name_rather_than_measured(tmp_path):
    conn = ledger(tmp_path)
    frames = _big_sample(conn, blocked_drop_bp=-400.0)
    for code in ("no-book", "no-data"):
        out = gl.measure(_evidence(conn), frames, gate=code, horizon=1)
        assert out["verdict"] == "refused"
        assert out["reason"] == "unknown-is-not-a-block"
        assert out["applied"] is False


def test_a_code_that_is_not_a_gate_raises():
    with pytest.raises(ValueError):
        gl.measure([], {}, gate="moon-phase")


# --------------------------------------------------------------------------- #
# The promise, enforced
# --------------------------------------------------------------------------- #
def test_measuring_never_writes_to_the_ledger(tmp_path):
    conn = ledger(tmp_path)
    frames = _big_sample(conn, blocked_drop_bp=-80.0)
    before = len(gl.rows(conn))
    gl.measure(_evidence(conn), frames, gate="spread-wide", horizon=1)
    assert len(gl.rows(conn)) == before


def test_every_measure_carries_applied_false(tmp_path):
    """Including the verdict that favours the gate: evidence is not an instruction."""
    conn = ledger(tmp_path)
    frames = _big_sample(conn, blocked_drop_bp=-400.0)
    out = gl.measure(_evidence(conn), frames, gate="spread-wide", horizon=1)
    assert out["verdict"] == "supports" and out["applied"] is False


def test_the_module_has_no_way_to_change_a_limit():
    """If this module could edit `GateLimits`, "shadow" would be a word, not a fact."""
    assert not hasattr(gl, "apply")
    assert not hasattr(gl, "enforce")
    assert not hasattr(gl, "set_limits")


def test_the_days_are_counted_in_utc_whatever_the_host_believes(tmp_path):
    conn = ledger(tmp_path)
    ts = int(datetime(2026, 3, 1, 23, 30, tzinfo=UTC).timestamp())
    write(conn, {"BTCIRT": {"code": "spread-wide", "codes": ["spread-wide"]}},
          ts_of={"BTCIRT": ts})
    row = gl.rows(conn)[0]
    assert datetime.fromtimestamp(row["ts"], UTC).date().isoformat() == "2026-03-01"


# --------------------------------------------------------------------------- #
# Wiring: written after a scan, read by a route, never written by that route
# --------------------------------------------------------------------------- #
def _main_source() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")


def test_the_scan_writes_the_ledger_after_the_journal():
    src = _main_source()
    assert "judged = gate_write(tf)" in src
    # After the journal write, so a crash in one recorder cannot cost the other.
    assert src.index("added = await journal_scan(") < src.index("judged = gate_write(tf)")


def test_a_ledger_failure_cannot_break_a_scan():
    src = _main_source()
    at = src.index("judged = gate_write(tf)")
    window = src[max(0, at - 200):at + 400]
    # The guard's *shape* is the contract, not its column: nested one level deeper
    # or reformatted by `ruff format` must not fail this. The exact-spacing version
    # of this assertion went red after a formatter-only change to main.py.
    assert re.search(r"except Exception as e:\s+# the ledger must never break a scan", window)


def test_the_evidence_route_only_reads():
    src = _main_source()
    at = src.index("async def gates_evidence(")
    body = src[at:src.index("\ndef ", at)]      # the route's own body, nothing after it
    assert "gate_ledger.rows(" in body and "gate_ledger.measure(" in body
    assert "gate_ledger.record(" not in body      # a reader that writes is not a ledger
    assert '"applied": False' in body


def test_the_signals_block_reports_counts_and_never_rows():
    """/api/signals gets the readiness counts; the expensive join lives on its own route."""
    src = _main_source()
    at = src.index("def gates_block(")
    body = src[at:src.index("\ndef ", at)]   # the block's own body, however long it grows
    assert "gate_ledger_summary()" in body
    assert "gate_ledger.measure(" not in body
    assert "gate_ledger.record(" not in body
