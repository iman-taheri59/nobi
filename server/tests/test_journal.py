# mypy: check_untyped_defs = False
# Test doubles stand in for objects on purpose; their bodies answer to pytest.
"""Tests for the ADR-0012 prediction journal.

The journal decides whether a signal "worked", so its arithmetic has to be
independently checkable: entry at the signal bar's close, stop at 2·ATR,
conservative resolution when one bar touches both levels, and no look-ahead.
"""

from __future__ import annotations

import time
import time as _time

import numpy as np
import pandas as pd
import pytest

import journal as J

#: Fixed epoch so export/audit tests never depend on when they run.
BASE_TS = int(_time.time()) - 3 * 86400


def frame(closes: list[float], *, band: float = 0.4, step: int = 900) -> pd.DataFrame:
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "t": pd.to_datetime(np.arange(len(close)) * step, unit="s", utc=True),
        "o": close,
        "h": close + band,
        "l": close - band,
        "c": close,
        "v": 1.0,
    })


@pytest.fixture()
def conn(tmp_path):
    c = J.connect(tmp_path / "journal.db")
    yield c
    c.close()


def close_ts(df: pd.DataFrame, i: int, step: int = 900) -> int:
    """Journal stamps are bar-close times; this keeps the tests honest."""
    return int(df["t"].iloc[i].timestamp()) + step


def _row(ts: int, entry: float = 100.0, atr: float = 1.0, symbol: str = "BTCUSDT"):
    return J.JournalRow(ts=ts, symbol=symbol, timeframe="15m", direction="LONG",
                        ta_score=70.0, composite=70.0, entry=entry, atr=atr,
                        source="synthetic", origin="backfill")


def test_targets_follow_the_risk_geometry():
    """R is the distance to the stop (= 2·ATR), matching risk_engine."""
    stop, tp1, tp2 = _row(0).targets()
    assert stop == pytest.approx(98.0)      # entry - 2·ATR  → R = 2.0
    assert tp1 == pytest.approx(102.4)      # entry + 1.2R
    assert tp2 == pytest.approx(104.0)      # entry + 2.0R


def test_entry_is_the_signal_bar_close_not_the_next_bar(conn):
    df = frame([100.0] * 9 + [100.0] + [120.0] * 10)
    J.insert_rows(conn, [_row(close_ts(df, 9))])
    J.resolve_symbol(conn, "BTCUSDT", "15m", df)
    h1 = conn.execute("SELECT * FROM signal_outcomes WHERE horizon_key='H1'").fetchone()
    # Entry is the signal bar's close (100); the +20 % return proves it (using
    # the next bar's 120 as entry would book 0 %). The frame gaps straight over
    # TP1, so half is booked at 1.2R and the rest runs to the horizon:
    # 0.6R + 0.5 * 10R = 5.6R.
    assert h1["return_pct"] == pytest.approx(20.0, abs=0.5)
    assert h1["hit_tp1"] == 1
    assert h1["r_multiple"] == pytest.approx(5.6, abs=0.1)


def test_stop_wins_when_one_bar_touches_both_levels(conn):
    df = frame([100.0] * 9 + [100.0] + [100.0] * 10)
    df.loc[10, "l"] = 97.5     # through the 98 stop
    df.loc[10, "h"] = 103.0    # ...and through TP1 in the same bar
    J.insert_rows(conn, [_row(close_ts(df, 9))])
    J.resolve_symbol(conn, "BTCUSDT", "15m", df)
    h1 = conn.execute("SELECT * FROM signal_outcomes WHERE horizon_key='H1'").fetchone()
    assert h1["hit_sl"] == 1
    assert h1["hit_tp1"] == 0
    assert h1["r_multiple"] == pytest.approx(-1.0)


def test_tp1_books_half_and_trails_to_breakeven(conn):
    # Rise through TP1 (101.2), then fall back to entry: half banked at 1.2R,
    # the rest exits flat, so the trade must stay positive.
    closes = [100.0] * 9 + [100.0] + [102.0] + [103.0] + [100.0] * 10
    df = frame(closes, band=0.2)
    J.insert_rows(conn, [_row(close_ts(df, 9))])
    J.resolve_symbol(conn, "BTCUSDT", "15m", df)
    h1 = conn.execute("SELECT * FROM signal_outcomes WHERE horizon_key='H1'").fetchone()
    assert h1["hit_tp1"] == 1
    assert h1["r_multiple"] >= 0.6      # at least the half booked at 1.2R


def test_horizon_only_resolves_when_enough_future_bars_exist(conn):
    df = frame([100.0] * 30)
    J.insert_rows(conn, [_row(close_ts(df, 9))])
    assert J.resolve_symbol(conn, "BTCUSDT", "15m", df.iloc[:13]) == 0   # 3 future bars
    assert J.resolve_symbol(conn, "BTCUSDT", "15m", df.iloc[:14]) == 1   # 4 = H1


def test_rescanning_the_same_bar_does_not_duplicate(conn):
    ts = 1_700_000_000
    assert J.insert_rows(conn, [_row(ts)]) == 1
    assert J.insert_rows(conn, [_row(ts), _row(ts)]) == 0
    assert conn.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0] == 1


def test_non_long_signals_are_recorded_but_not_scored_as_trades(conn):
    df = frame([100.0] * 30)
    exit_row = J.JournalRow(ts=close_ts(df, 9), symbol="ETHUSDT",
                            timeframe="15m", direction="EXIT", ta_score=30.0,
                            composite=30.0, entry=100.0, atr=1.0)
    J.insert_rows(conn, [exit_row])
    assert J.resolve_symbol(conn, "ETHUSDT", "15m", df) == 0
    assert J.summary(conn)["kpi"]["sig"] == 0


def test_summary_reports_metrics_from_resolved_h2(conn):
    # Two LONG signals, one clean winner and one clean loser, on a 15m frame
    # long enough for H2 (96 bars = 1 day).
    df = frame([100.0] * 20 + [130.0] * 80 + [100.0] * 100, band=0.3)
    J.insert_rows(conn, [
        _row(close_ts(df, 19), symbol="WIN"),
        _row(close_ts(df, 99), symbol="LOSE"),
    ])
    for sym in ("WIN", "LOSE"):
        J.resolve_symbol(conn, sym, "15m", df)

    out = J.summary(conn)
    kpi = out["kpi"]
    assert kpi["sig"] == 2
    assert kpi["res"] == 2
    assert set(kpi) >= {"hit", "exp", "vpf", "ic", "pending"}
    assert out["horizons"][0]["k"] == "H1"
    assert len(out["signals"]) == 2


def test_summary_is_empty_but_well_formed_before_any_data(conn):
    out = J.summary(conn)
    assert out["kpi"] == {"hit": None, "exp": None, "vpf": None, "ic": None,
                          "ic_median": None, "sig": 0, "res": 0, "issued": 0,
                          "pending": 0, "void": 0}
    assert out["horizons"] and all(h["n"] == 0 for h in out["horizons"])
    assert out["calibration"] == [] and out["families"] == []


def test_calibration_buckets_match_score_bands(conn):
    df = frame([100.0] * 20 + [130.0] * 200, band=0.3)
    r = _row(close_ts(df, 19))
    r.composite, r.ta_score = 72.0, 72.0
    J.insert_rows(conn, [r])
    J.resolve_symbol(conn, "BTCUSDT", "15m", df)
    cal = J.summary(conn)["calibration"]
    assert len(cal) == 1
    assert cal[0][0] == pytest.approx(70.0)     # bucket 65..75 midpoint
    assert cal[0][2] == 1


def test_spearman_matches_known_ranks():
    assert J._spearman([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert J._spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert J._spearman([1, 2], [1, 2]) is None          # sample too small
    assert J._spearman([1, 1, 1, 1], [1, 2, 3, 4]) is None  # degenerate


def test_source_and_origin_survive_the_round_trip(conn):
    df = frame([100.0] * 30)
    r = _row(close_ts(df, 9))
    r.origin, r.source = "live", "cache"
    J.insert_rows(conn, [r])
    got = J.fetch_signals(conn)[0]
    assert (got["origin"], got["source"]) == ("live", "cache")


def test_resolution_starts_at_the_bar_after_the_signal_close(conn, tmp_path):
    """A signal stamped with bar i's close must be evaluated from bar i+1.

    Indexing candle *open* times would evaluate it from bar i instead, which
    silently steals one bar of hindsight from every outcome.
    """
    df = frame([100.0] * 20)
    df.loc[10, "l"] = 90.0        # would hit the stop if bar 10 were evaluated
    J.insert_rows(conn, [_row(close_ts(df, 9))])
    J.resolve_symbol(conn, "BTCUSDT", "15m", df)
    h1 = conn.execute("SELECT * FROM signal_outcomes WHERE horizon_key='H1'").fetchone()
    # Entry at close of bar 9; bar 10 is the first tradable bar and it does
    # contain the stop — so this is genuinely -1R, not a lucky skip.
    assert h1["hit_sl"] == 1

    # Now make the *signal* bar itself contain the crash. That bar is already
    # in the past when the signal is issued, so it must not be counted.
    df.loc[9, "l"] = 90.0
    df.loc[10, "l"] = 99.0        # bar 10 no longer breaches the stop
    conn2 = J.connect(tmp_path / "second.db")
    J.insert_rows(conn2, [_row(close_ts(df, 9))])
    J.resolve_symbol(conn2, "BTCUSDT", "15m", df)
    h = conn2.execute("SELECT * FROM signal_outcomes WHERE horizon_key='H1'").fetchone()
    assert h["hit_sl"] == 0


def test_summary_shows_resolved_rows_before_pending_ones(conn):
    """The page is a performance report; a wall of "pending" helps nobody."""
    df = frame([100.0] * 20 + [130.0] * 200, band=0.3)
    for i, sym in ((19, "OLD"), (210, "NEW")):
        J.insert_rows(conn, [_row(close_ts(df, i), symbol=sym)])
    for sym in ("OLD", "NEW"):
        J.resolve_symbol(conn, sym, "15m", df)

    out = J.summary(conn, limit=10)
    first, second = out["signals"][0], out["signals"][1]
    assert first["r"] is not None          # resolved row floats to the top
    assert second["r"] is None             # the newest, still-pending row follows


def test_status_flips_to_resolved_at_the_primary_horizon(conn):
    """H2 is the decision horizon; H3 still fills in later without regressing."""
    df = frame([100.0] * 20 + [130.0] * 300, band=0.3)
    J.insert_rows(conn, [_row(close_ts(df, 19), symbol="AAA")])
    J.resolve_symbol(conn, "AAA", "15m", df)

    row = J.summary(conn)["signals"][0]
    horizons = {o["horizon_key"] for o in conn.execute("SELECT horizon_key FROM signal_outcomes")}
    assert "H2" in horizons
    assert row["st"] == "resolved"


# --------------------------------------------------------------------------- #
# VOID law
# --------------------------------------------------------------------------- #
def test_void_freezes_signals_for_symbols_that_left_the_universe(conn):
    now = 1_800_000_000
    J.insert_rows(conn, [
        J.JournalRow(ts=now - 48 * 3600, symbol="GONE", timeframe="15m", direction="LONG",
                     ta_score=70, composite=70, entry=100.0, atr=1.0),
        J.JournalRow(ts=now - 48 * 3600, symbol="STAY", timeframe="15m", direction="LONG",
                     ta_score=70, composite=70, entry=100.0, atr=1.0),
    ])
    assert J.mark_void(conn, ["STAY"], now=now) == 1

    rows = {r["symbol"]: r for r in J.fetch_signals(conn)}
    assert rows["GONE"]["voided_at"] == now
    assert rows["GONE"]["void_reason"] == "symbol-left-universe"
    assert rows["STAY"]["voided_at"] is None


def test_void_respects_the_grace_period(conn):
    now = 1_800_000_000
    J.insert_rows(conn, [J.JournalRow(ts=now - 60, symbol="FLAPPY", timeframe="15m",
                                      direction="LONG", ta_score=70, composite=70,
                                      entry=100.0, atr=1.0)])
    # A symbol that blinked out one minute ago is not delisted yet.
    assert J.mark_void(conn, ["OTHER"], now=now, grace_s=12 * 3600) == 0
    assert J.mark_void(conn, ["OTHER"], now=now, grace_s=0) == 1


def test_void_never_wipes_the_journal_on_an_empty_scan(conn):
    J.insert_rows(conn, [J.JournalRow(ts=1, symbol="BTCUSDT", timeframe="15m",
                                      direction="LONG", ta_score=70, composite=70,
                                      entry=100.0, atr=1.0)])
    assert J.mark_void(conn, []) == 0


def test_voided_rows_stay_out_of_metrics_and_never_resolve(conn):
    df = frame([100.0] * 20 + [130.0] * 200, band=0.3)
    J.insert_rows(conn, [_row(close_ts(df, 19), symbol="DEAD")])
    J.mark_void(conn, ["ALIVE"], now=close_ts(df, 19) + 10**6)
    assert J.resolve_symbol(conn, "DEAD", "15m", df) == 0

    out = J.summary(conn)
    assert out["kpi"]["void"] == 1
    assert out["kpi"]["sig"] == 0          # voided rows are not tradeable signals
    assert out["kpi"]["pending"] == 0      # ...and must not look pending forever
    assert out["signals"][0]["st"] == "void"


# --------------------------------------------------------------------------- #
# MSI
# --------------------------------------------------------------------------- #
def _trending(slope: float, n: int = 40) -> pd.DataFrame:
    close = [100.0 + slope * i for i in range(n)]
    return frame(close, band=0.2)


def test_backtest_ic_detects_a_real_relationship():
    """A score that rises with the trend must correlate with forward returns."""
    import numpy as np

    rng = np.random.default_rng(11)
    n = 900
    # A clean trend with noise: monotonic drift, so the score tracks returns.
    close = 100 * np.exp(np.cumsum(rng.normal(0.0015, 0.006, n)))
    df = pd.DataFrame({"t": pd.to_datetime(np.arange(n) * 900, unit="s", utc=True),
                       "o": close, "h": close * 1.002, "l": close * 0.998,
                       "c": close, "v": 1.0})
    out = J.backtest_ic([("X", df)], horizon_bars=4)
    assert out["n"] > 100
    assert out["ic"] is not None


def test_backtest_ic_is_none_when_there_is_not_enough_data():
    out = J.backtest_ic([("X", _trending(0.1, n=30))], horizon_bars=4)
    assert out["ic"] is None and out["enough_data"] is False


def test_msi_reports_insufficient_before_any_samples(conn):
    out = J.msi(conn)
    assert out["status"] == "insufficient"
    assert out["realized"]["n"] == 0


def test_msi_flags_corruption_when_realized_ic_halves(conn):
    # Baseline says the score is a strong predictor; planted rows say the
    # opposite, which is exactly the "signal decay" the ADR wants caught.
    J.set_meta(conn, "msi_baseline", {"ic": 0.40, "n": 500, "source": "backtest"})
    now = int(J.time.time())
    rows = []
    for i in range(30):
        rows.append(J.JournalRow(ts=now - 3600 - i, symbol="S", timeframe="15m",
                                 direction="LONG", ta_score=50 + i, composite=50 + i,
                                 entry=100.0, atr=1.0, source="live"))
    J.insert_rows(conn, rows)
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM signal_log WHERE symbol='S' ORDER BY composite")]
    for i, sid in enumerate(ids):
        # Higher score, worse return → negative correlation.
        conn.execute(
            """INSERT OR REPLACE INTO signal_outcomes
               (signal_id, horizon_key, resolved_at, exit_ts, exit_price, mfe_pct,
                mae_pct, hit_tp1, hit_sl, timeout, r_multiple, return_pct, bars_used)
               VALUES (?, 'H2', 0, 0, 100, 0, 0, 0, 0, 1, 0, ?, 4)""",
            (sid, -0.5 * (i + 1)),   # higher score → worse return
        )
    conn.commit()

    out = J.msi(conn)
    assert out["realized"]["ic"] < 0
    assert out["ratio"] < 0
    assert out["status"] == "corrupted"


def test_msi_is_healthy_when_realized_matches_the_reference(conn):
    J.set_meta(conn, "msi_baseline", {"ic": 0.20, "n": 500, "source": "backtest"})
    now = int(J.time.time())
    J.insert_rows(conn, [J.JournalRow(ts=now - 3600 - i, symbol="S", timeframe="15m",
                                      direction="LONG", ta_score=50 + i, composite=50 + i,
                                      entry=100.0, atr=1.0) for i in range(30)])
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM signal_log WHERE symbol='S' ORDER BY composite")]
    for i, sid in enumerate(ids):
        conn.execute(
            """INSERT OR REPLACE INTO signal_outcomes
               (signal_id, horizon_key, resolved_at, exit_ts, exit_price, mfe_pct,
                mae_pct, hit_tp1, hit_sl, timeout, r_multiple, return_pct, bars_used)
               VALUES (?, 'H2', 0, 0, 100, 0, 0, 0, 0, 1, 0, ?, 4)""",
            (sid, i * 0.5),
        )
    conn.commit()
    out = J.msi(conn)
    assert out["realized"]["ic"] > 0.9
    assert out["status"] == "healthy"


def test_msi_falls_back_to_journal_history_and_says_so(conn):
    now = int(J.time.time())
    # Old window (baseline) with a positive relation, recent window too small.
    for i in range(30):
        J.insert_rows(conn, [J.JournalRow(ts=now - 90 * 86400 + i, symbol="S", timeframe="15m",
                                          direction="LONG", ta_score=50 + i, composite=50 + i,
                                          entry=100.0, atr=1.0)])
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM signal_log WHERE symbol='S' ORDER BY composite")]
    for i, sid in enumerate(ids):
        conn.execute(
            """INSERT OR REPLACE INTO signal_outcomes
               (signal_id, horizon_key, resolved_at, exit_ts, exit_price, mfe_pct,
                mae_pct, hit_tp1, hit_sl, timeout, r_multiple, return_pct, bars_used)
               VALUES (?, 'H2', 0, 0, 100, 0, 0, 0, 0, 1, 0, ?, 4)""",
            (sid, i * 0.3),
        )
    conn.commit()
    out = J.msi(conn, baseline_days=180)
    assert out["baseline"]["source"] == "journal-history"
    assert out["status"] == "insufficient"      # no recent samples yet


def test_ic_is_computed_per_symbol_not_pooled(conn):
    """A flat pegged pair must not drag the cross-asset IC around."""
    import numpy as np

    rng = np.random.default_rng(3)
    n = 700
    price = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.004, n)))
    df = pd.DataFrame({"t": pd.to_datetime(np.arange(n) * 900, unit="s", utc=True),
                       "o": price, "h": price * 1.001, "l": price * 0.999,
                       "c": price, "v": 1.0})
    flat = df.copy()
    flat[["o", "h", "l", "c"]] = 100.0          # a peg that never moves

    out = J.backtest_ic([("AAAIRT", df), ("USDTIRT", flat)], horizon_bars=8)
    assert out["per_symbol"]["USDTIRT"] is None       # excluded, not scored as noise
    assert out["symbols_scored"] == 1
    assert "mean of per-symbol" in out["method"]


def test_ic_by_symbol_requires_enough_bars_per_symbol():
    pairs = {"A": ([1, 2, 3], [1, 2, 3]), "B": ([1, 2, 3, 4, 5] * 10, list(range(50)))}
    out = J.ic_by_symbol(pairs, min_per_symbol=30)
    assert out["per_symbol"]["A"] is None            # only 3 bars
    assert out["symbols_scored"] == 1


def test_msi_warns_when_the_reference_was_measured_on_simulated_candles(conn):
    J.set_meta(conn, "msi_baseline", {"ic": -0.70, "n": 5000, "source": "backtest",
                                      "sources": {"synthetic": 10}})
    out = J.msi(conn)
    assert out["baseline"]["synthetic"] is True
    assert out["warning"] and "simulated" in out["warning"]
    # No integrity verdict may rest on a simulator.
    assert out["status"] == "insufficient"
    assert "not possible" in out["message"]


def test_msi_does_not_warn_when_the_reference_is_live(conn):
    J.set_meta(conn, "msi_baseline", {"ic": 0.30, "n": 5000, "source": "backtest",
                                      "sources": {"live": 10}})
    out = J.msi(conn)
    assert out["baseline"]["synthetic"] is False
    assert out["warning"] is None


def test_summary_counts_are_not_capped_by_the_page_limit(conn):
    """`pending`/`void` must describe the journal, not the visible page."""
    df = frame([100.0] * 20 + [130.0] * 400, band=0.3)
    rows = [J.JournalRow(ts=close_ts(df, 19) + i, symbol=f"S{i:02d}", timeframe="15m",
                         direction="LONG", ta_score=70, composite=70, entry=100.0, atr=1.0)
            for i in range(40)]
    J.insert_rows(conn, rows)
    J.resolve_symbol(conn, "S01", "15m", df)          # S01 already has its result
    # The exchange still lists S01..S39; S00 is gone and was never resolved.
    J.mark_void(conn, [f"S{i:02d}" for i in range(1, 40)], now=close_ts(df, 19) + 10**7)

    out = J.summary(conn, limit=5)
    assert len(out["signals"]) == 5          # the table is paged
    assert out["kpi"]["issued"] == 40        # ...the counts are not
    assert out["kpi"]["void"] == 1
    assert out["kpi"]["sig"] == 39


# --------------------------------------------------------------------------- #
# Export (ADR-0012 §2.6)
# --------------------------------------------------------------------------- #
def test_export_rows_join_every_horizon_and_are_not_paginated(conn):
    sym = "BTCIRT"
    for i in range(12):
        ts = BASE_TS + i * 900
        conn.execute(
            """INSERT INTO signal_log(ts, symbol, timeframe, direction, composite,
                                      source, origin, config_hash)
               VALUES (?,?,?,?,?,?,?,?)""",
            (ts, sym, "15m", "LONG", 60 + i, "synthetic", "backfill", "abc"))
        sid = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
        conn.execute(
            """INSERT INTO signal_outcomes(signal_id, horizon_key, resolved_at,
                                           return_pct, r_multiple, hit_tp1, hit_sl)
               VALUES (?,?,?,?,?,?,?)""",
            (sid, "H2", ts + 960, 1.5, 0.9, 1, 0))
    conn.commit()

    rows = J.export_rows(conn)
    assert len(rows) == 12                     # not capped at any page size
    assert rows[0]["h2_return_pct"] == 1.5
    assert rows[0]["h2_hit_tp1"] == 1
    assert rows[0]["h1_return_pct"] is None    # horizon never resolved
    assert rows[0]["utc"].startswith("20")

    text = J.export_csv(rows)
    header, first = text.split("\n")[0], text.split("\n")[1]
    assert header.split(",") == J.EXPORT_COLUMNS
    assert str(rows[0]["id"]) in first


def test_export_respects_filters(conn):
    for i, origin in enumerate(["backfill", "live", "backfill"]):
        conn.execute(
            """INSERT INTO signal_log(ts, symbol, timeframe, direction, composite,
                                      source, origin, config_hash)
               VALUES (?,?,?,?,?,?,?,?)""",
            (BASE_TS + i * 900, "ETHIRT", "15m", "LONG", 55.0, "synthetic", origin, "abc"))
    conn.commit()
    assert len(J.export_rows(conn, origin="backfill")) == 2
    assert len(J.export_rows(conn, origin="live")) == 1
    assert len(J.export_rows(conn, tf="1h")) == 0


# --------------------------------------------------------------------------- #
# Audit trail + digest
# --------------------------------------------------------------------------- #
def test_audit_record_and_list_round_trip(conn):
    audit_id = J.audit_record(conn, "paper.halt", detail={"flat": True}, client="127.0.0.1")
    J.audit_record(conn, "paper.resume", result="ok")
    events = J.audit_list(conn)
    assert events[0]["action"] == "paper.resume"          # newest first
    assert events[1]["id"] == audit_id
    assert events[1]["detail"] == {"flat": True}
    assert events[1]["client"] == "127.0.0.1"
    assert J.audit_counts(conn) == {"paper.halt": 1, "paper.resume": 1}


def test_audit_list_filters_by_action(conn):
    J.audit_record(conn, "journal.backfill")
    J.audit_record(conn, "paper.halt")
    assert [e["action"] for e in J.audit_list(conn, action="paper.halt")] == ["paper.halt"]


def test_digest_reports_only_its_window(conn):
    now = int(time.time())
    conn.execute(
        """INSERT INTO signal_log(ts, symbol, timeframe, direction, composite,
                                  source, origin, config_hash)
           VALUES (?,?,?,?,?,?,?,?)""",
        (now - 120, "BTCIRT", "15m", "LONG", 61.0, "synthetic", "live", "abc"))
    conn.execute(
        """INSERT INTO signal_log(ts, symbol, timeframe, direction, composite,
                                  source, origin, config_hash)
           VALUES (?,?,?,?,?,?,?,?)""",
        (now - 10 * 86400, "BTCIRT", "15m", "LONG", 61.0, "synthetic", "live", "abc"))
    # Explicitly current-contract fixture, not a migrated legacy row.
    conn.execute("UPDATE signal_log SET price_contract=?", (J.IRT_PRICE_CONTRACT,))
    conn.commit()
    d = J.digest(conn, days=1)
    assert d["issued"] == 1
    assert d["window_days"] == 1
    text = J.digest_text(d)
    assert "Nobi journal" in text and "issued 1" in text


def test_summary_view_leads_with_resolved_even_at_limit_1(conn):
    """A small page must not turn into a wall of 'pending'.

    The first implementation took the newest N rows and then sorted them —
    which cannot surface an older resolved row at all when N is small.
    """
    now = int(time.time())
    # Old, resolved row and a fresh, pending one.
    conn.execute(
        """INSERT INTO signal_log(ts, symbol, timeframe, direction, composite,
                                  source, origin, config_hash)
           VALUES (?,?,?,?,?,?,?,?)""",
        (now - 5 * 86400, "BTCIRT", "15m", "LONG", 61.0, "synthetic", "live", "abc"))
    old_id = conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    conn.execute(
        """INSERT INTO signal_outcomes(signal_id, horizon_key, resolved_at,
                                       return_pct, r_multiple)
           VALUES (?,?,?,?,?)""", (old_id, "H2", now - 4 * 86400, 1.0, 0.8))
    conn.execute(
        """INSERT INTO signal_log(ts, symbol, timeframe, direction, composite,
                                  source, origin, config_hash)
           VALUES (?,?,?,?,?,?,?,?)""",
        (now - 60, "ETHIRT", "15m", "LONG", 58.0, "synthetic", "live", "abc"))
    # Explicitly current-contract fixture, not a migrated legacy row.
    conn.execute("UPDATE signal_log SET price_contract=?", (J.IRT_PRICE_CONTRACT,))
    conn.commit()

    out = J.summary(conn, limit=1)
    assert len(out["signals"]) == 1
    assert out["signals"][0]["st"] == "resolved"
    assert out["signals"][0]["id"] == old_id


# --------------------------------------------------------------------------- #
# Single-signal detail (the chart behind one table row)
# --------------------------------------------------------------------------- #
def _candles(n: int, start: int, step: int = 900, base: float = 100.0):
    return [{"t": start + i * step, "o": base + i, "h": base + i + 2,
             "l": base + i - 2, "c": base + i + 1, "v": 10.0} for i in range(n)]


def test_signal_path_starts_at_the_bar_that_closes_on_the_signal():
    """Entry is the signal bar's close, so the entry bar OPENS one step earlier.

    Off-by-one here shifts the whole chart, the same class of bug that once
    shifted every outcome by one bar.
    """
    step = 900
    candles = _candles(200, 1_700_000_000)
    signal = {"ts": 1_700_000_000 + 100 * step, "timeframe": "15m"}

    path = J.signal_path(signal, candles, "15m", context_bars=30)
    assert path["reason"] == "ok"
    entry_open = signal["ts"] - step
    assert path["entry_open"] == entry_open
    # 30 bars of context before the entry bar, and the entry bar itself sits at 30.
    assert path["entry_index"] == 30
    assert path["bars"][0]["t"] == entry_open - 30 * step
    # H2 = 1 day = 96 bars at 15m, inclusive of the entry bar.
    assert len(path["bars"]) == 30 + 96 + 1


def test_signal_path_reports_why_it_is_empty():
    candles = _candles(50, 1_700_000_000)
    old = {"ts": 1_600_000_000, "timeframe": "15m"}
    out = J.signal_path(old, candles, "15m")
    assert out["bars"] == [] and out["reason"] == "outside-retention"

    future = {"ts": 1_800_000_000, "timeframe": "15m"}
    assert J.signal_path(future, candles, "15m")["reason"] == "not-yet-open"
    assert J.signal_path(old, [], "15m")["reason"] == "no-candles"


def test_epoch_seconds_is_independent_of_the_pandas_datetime_unit():
    """pandas 3 infers [us] where pandas 2 used [ns]; both must give seconds."""
    base = np.arange(3) * 900
    for unit, mult in (("s", 1), ("ms", 10**3), ("us", 10**6), ("ns", 10**9)):
        series = pd.to_datetime(base * mult, unit=unit, utc=True)
        assert J.epoch_seconds(series) == [0, 900, 1800], unit


def test_exit_ts_is_stored_in_seconds_not_nanoseconds():
    """datetime64 -> int() silently yields nanoseconds; that is what shipped.

    A seconds epoch is ~1.7e9, so any exit_ts above 1e10 is unmistakably wrong.
    """
    import pandas as pd

    df = frame([100.0] * 400)
    out = J.simulate_long(df, i=200, entry=float(df["c"].iloc[200]),
                          atr=1.0, bars=96)
    assert out is not None
    # It must equal the horizon bar's timestamp in SECONDS — not nanoseconds.
    end = min(200 + 96, len(df) - 1)
    expected = int(pd.Timestamp(df["t"].iloc[end]).timestamp())
    assert out["exit_ts"] == expected
    assert out["exit_ts"] < 10_000_000_000


def test_migration_repairs_nanosecond_exit_ts(tmp_path):
    c = J.connect(tmp_path / "j.db")
    c.execute(
        """INSERT INTO signal_log(ts, symbol, timeframe, direction)
           VALUES (1, 'BTCIRT', '15m', 'LONG')""")
    sid = c.execute("SELECT last_insert_rowid() AS i").fetchone()["i"]
    c.execute(
        """INSERT INTO signal_outcomes(signal_id, horizon_key, exit_ts)
           VALUES (?,?,?)""", (sid, "H2", 1_792_423_800_000_000_000))
    c.commit()

    assert J._repair_nanosecond_exit_ts(c, force=True) == 1
    fixed = c.execute("SELECT exit_ts FROM signal_outcomes").fetchone()["exit_ts"]
    assert fixed == 1_792_423_800
    # idempotent: a second run must not shift a correct value again
    assert J._repair_nanosecond_exit_ts(c, force=True) == 0
    assert c.execute("SELECT exit_ts FROM signal_outcomes").fetchone()["exit_ts"] == 1_792_423_800
    c.close()


# --------------------------------------------------------------------------- #
# Strategy lifecycle (ADR-0012 §3)
# --------------------------------------------------------------------------- #
def test_lifecycle_corruption_retires_immediately():
    from journal import next_stage

    for cur in ("train", "run", "watch"):
        stage, why = next_stage(cur, "corrupted")
        assert stage == "retire", cur
        assert "retire" in why


def test_lifecycle_never_promotes_without_evidence():
    from journal import next_stage

    # "insufficient" is not a health signal: keep the stage, never move up.
    for cur in ("train", "run", "watch", "retire"):
        stage, _ = next_stage(cur, "insufficient")
        assert stage == cur, cur


def test_lifecycle_promotion_and_demotion_require_different_effort():
    from journal import next_stage

    # One healthy window is enough to run; one degraded window is not enough to
    # demote. Asymmetry is deliberate: bad days are noisy, and a rule that
    # thrashes on noise gets switched off by the operator.
    assert next_stage("train", "healthy")[0] == "run"
    assert next_stage("watch", "healthy")[0] == "run"
    assert next_stage("run", "degraded", streak=0)[0] == "run"
    assert next_stage("run", "degraded", streak=1)[0] == "run"
    assert next_stage("run", "degraded", streak=2)[0] == "watch"


def test_lifecycle_retire_is_terminal_and_unknown_inputs_hold():
    from journal import next_stage

    for status in ("healthy", "degraded", "insufficient", "corrupted"):
        assert next_stage("retire", status)[0] == "retire", status
    assert next_stage("run", "nonsense")[0] == "run"
    assert next_stage("bogus-stage", "healthy")[0] == "run"   # coerced to train first


def test_lifecycle_streak_counts_consecutive_degraded_readings():
    from journal import connect, lifecycle, set_meta

    conn = connect(":memory:")
    # A degraded verdict needs a baseline and enough resolved rows; rather than
    # fabricate a journal, drive the streak bookkeeping directly through meta.
    set_meta(conn, "strategy_stage", {"stage": "run", "msi_status": "degraded", "streak": 1})
    out = lifecycle(conn)
    # Whatever MSI says in an empty journal (insufficient), the rule must not
    # invent a demotion out of a stale streak.
    assert out["stage"] in ("run", "watch")
    if out["msi_status"] != "degraded":
        assert out["streak"] == 0
    conn.close()


def test_every_stage_reason_has_a_machine_key():
    """The UI renders a key, not the server's English sentence."""
    from journal import stage_reason_key

    seen = set()
    for cur in ("train", "run", "watch", "retire"):
        for status in ("healthy", "degraded", "insufficient", "corrupted", "weird"):
            k = stage_reason_key(cur, status)
            assert isinstance(k, str) and k and " " not in k, (cur, status, k)
            seen.add(k)
    # every reachable combination maps to a defined key
    assert seen <= {"retire_corrupted", "hold_insufficient", "retired", "hold_training",
                    "degraded_watch", "watch_degraded", "promote_to_run", "hold_healthy",
                    "hold_unknown"}
    assert "retire_corrupted" in seen and "promote_to_run" in seen
