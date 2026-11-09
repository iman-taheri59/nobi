"""ADR-0051 — shadow arms and the shadow outcomes table."""
from __future__ import annotations

import json

import pandas as pd
import pytest

import journal
import shadow_arms


def snap(ta, fa, liq, direction="NEUTRAL", severe=False):
    return {"score": ta, "fa_score": fa, "liquidity_score": liq, "direction": direction,
            "composite": 50.0, "veto_severe": severe}


def test_arms_are_declared_and_read_only():
    s = snap(50, 90, 90, direction="NEUTRAL")
    before = dict(s)
    arms = shadow_arms.score(s, "neutral")
    assert s == before
    assert set(arms) == {"B_fa_liq", "C_gate", "D_comp", "E_nochase"}
    # FA/liq heavy arm promotes what the live TA-led verdict did not
    assert arms["B_fa_liq"]["composite"] == pytest.approx(78.0)
    assert arms["B_fa_liq"]["direction"] == "LONG"
    # current weights by composite: 0.55*50+0.25*90+0.2*90 = 68.0 -> LONG at the edge
    assert arms["D_comp"]["direction"] == "LONG"
    assert arms["C_gate"]["direction"] == "NEUTRAL"
    assert shadow_arms.any_long(arms) is True


def test_gate_arm_blocks_live_long_only_in_risk_off():
    assert shadow_arms.score(snap(80, 20, 20, "LONG"), "risk-off")["C_gate"]["direction"] == "NEUTRAL"
    assert shadow_arms.score(snap(80, 20, 20, "LONG"), "neutral")["C_gate"]["direction"] == "LONG"
    assert shadow_arms.score(snap(80, 20, 20, "LONG"), None)["C_gate"]["direction"] == "LONG"


def test_severe_veto_and_missing_inputs():
    arms = shadow_arms.score(snap(90, 90, 90, "EXIT", severe=True), "risk-on")
    assert arms["B_fa_liq"]["direction"] == "EXIT" and arms["D_comp"]["direction"] == "EXIT"
    arms = shadow_arms.score({"direction": "LONG"}, None)
    assert arms["B_fa_liq"] == {"composite": None, "direction": "NEUTRAL"}
    assert shadow_arms.any_long(None) is False


def test_without_fa_weights_renormalise():
    arms = shadow_arms.score(snap(60, None, 100), None)
    assert arms["B_fa_liq"]["composite"] == pytest.approx(80.0)  # (0.3*60+0.3*100)/0.6


def test_flag_default_on(monkeypatch):
    monkeypatch.delenv("NBX_SHADOW_ARMS", raising=False)
    assert shadow_arms.enabled() is True
    monkeypatch.setenv("NBX_SHADOW_ARMS", "off")
    assert shadow_arms.enabled() is False


def _df(n=400, start=1_700_000_000):
    rows = []
    px = 100.0
    for i in range(n):
        rows.append({"t": pd.Timestamp(start + i * 900, unit="s"), "o": px, "h": px * 1.004,
                     "l": px * 0.996, "c": px, "v": 1.0})
    return pd.DataFrame(rows)


def test_shadow_outcomes_cover_shadow_only_longs_and_alt_k(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "SHADOW_RISK_KS", (3.0,))
    conn = journal.connect(tmp_path / "j.db")
    df = _df()
    ts0 = 1_700_000_000 + 10 * 900 + 900  # close of bar 10
    rows = []
    for sym, direction, shadow in (("AAAUSDT", "LONG", False), ("BBBUSDT", "NEUTRAL", True),
                                   ("CCCUSDT", "NEUTRAL", False)):
        r = journal.make_row(
            {"symbol": sym, "tf": "15m", "direction": direction, "score": 70, "liquidity_score": 60,
             "composite": 70, "lastClose": 100.0, "risk": {"atr": 0.5},
             "shadow_arms": {"B_fa_liq": {"composite": 80, "direction": "LONG" if shadow else "NEUTRAL"}},
             "shadow_any_long": shadow},
            ts0, origin="live", source="test")
        assert r is not None
        rows.append(r)
    journal.insert_rows(conn, rows)
    for sym in ("AAAUSDT", "BBBUSDT", "CCCUSDT"):
        journal.resolve_symbol(conn, sym, "15m", df)
    prod = {r[0] for r in conn.execute("SELECT s.symbol FROM signal_outcomes o JOIN signal_log s ON s.id=o.signal_id")}
    assert prod == {"AAAUSDT"}  # production metrics untouched by shadow rows
    shadow_rows = conn.execute(
        "SELECT s.symbol, o.risk_k, COUNT(*) FROM signal_shadow_outcomes o JOIN signal_log s ON s.id=o.signal_id "
        "GROUP BY 1,2 ORDER BY 1,2").fetchall()
    got = {(r[0], r[1]) for r in shadow_rows}
    assert ("AAAUSDT", 3.0) in got and ("AAAUSDT", 2.0) not in got  # live LONG: alt k only
    assert ("BBBUSDT", 2.0) in got and ("BBBUSDT", 3.0) in got        # shadow-only LONG: both
    assert not any(sym == "CCCUSDT" for sym, _ in got)
    feats = json.loads(conn.execute("SELECT features FROM signal_log WHERE symbol='BBBUSDT'").fetchone()[0])
    assert feats["shadow_any_long"] is True and feats["shadow_arms"]["B_fa_liq"]["direction"] == "LONG"
    # resolving again adds nothing, and the production count never includes shadow fills
    n_before = conn.execute("SELECT COUNT(*) FROM signal_shadow_outcomes").fetchone()[0]
    assert sum(journal.resolve_symbol(conn, s, "15m", df) for s in ("AAAUSDT", "BBBUSDT")) == 0
    assert conn.execute("SELECT COUNT(*) FROM signal_shadow_outcomes").fetchone()[0] == n_before


# ---- 2.5.2 item 8: E_nochase --------------------------------------------------


def _long(diag):
    return {**snap(80, 60, 60, direction="LONG"), "composite": 75.0, "diagnostics": diag}


@pytest.mark.parametrize("diag,expect,chase", [
    ({"run4h_atr": 2.0, "pctB": 0.5}, "NEUTRAL", True),     # run-up at the 2×ATR edge refuses
    ({"run4h_atr": 1.99, "pctB": 0.99}, "LONG", False),      # just under both: live LONG stands
    ({"run4h_atr": 0.3, "pctB": 1.0}, "NEUTRAL", True),      # %B at the band refuses
    ({"run4h_atr": -1.5, "pctB": 0.2}, "LONG", False),       # a fall is not a chase
    ({"run4h_atr": None, "pctB": 1.2}, "NEUTRAL", True),     # one input missing, the other decides
    ({"pctB": 0.4}, "LONG", False),                          # run-up unknown, %B fine
    ({}, "LONG", None),                                      # no inputs: equals the live row, says so
])
def test_e_nochase_refuses_chased_longs_only(diag, expect, chase):
    arms = shadow_arms.score(_long(diag), "neutral")
    e = arms["E_nochase"]
    assert e["direction"] == expect and e["chase"] is chase
    assert e["composite"] == 75.0, "the arm never rescored; it only refused"
    assert e["run4h_atr"] == diag.get("run4h_atr") and e["pctB"] == diag.get("pctB")


def test_e_nochase_never_promotes_and_leaves_other_arms_alone():
    s = {**snap(50, 90, 90, direction="NEUTRAL"), "diagnostics": {"run4h_atr": 0.0, "pctB": 0.1}}
    arms = shadow_arms.score(s, "neutral")
    assert arms["E_nochase"]["direction"] == "NEUTRAL" and arms["E_nochase"]["chase"] is False
    assert arms["B_fa_liq"]["direction"] == "LONG"  # unchanged from the earlier test
    ex = {**snap(80, 60, 60, direction="EXIT"), "diagnostics": {"run4h_atr": 5.0, "pctB": 1.5}}
    assert shadow_arms.score(ex, "neutral")["E_nochase"]["direction"] == "EXIT"
    assert shadow_arms.config()["version"] == 2 and "E_nochase" in shadow_arms.config()["arms"]


def test_run_up_is_measured_by_time_not_bar_count():
    import ta_engine
    t0 = 1_793_000_000
    # 15m bars: 20 bars, price +3 over the last 16 bars (4 h), ATR 1.5
    rows = [{"t": t0 + i * 900, "o": 100, "h": 101, "l": 99, "c": 100 + (max(0, i - 3)) * (3 / 16), "v": 1} for i in range(20)]
    df = ta_engine.candles_to_df(rows)
    pct, in_atr = ta_engine.run_up(df, 4 * 3600, 1.5)
    assert in_atr == pytest.approx(2.0, abs=0.01) and pct == pytest.approx(3.0, abs=0.01)
    # 1h bars: the same clock span is 4 bars back
    rows_h = [{"t": t0 + i * 3600, "o": 1, "h": 1, "l": 1, "c": 10 + i, "v": 1} for i in range(10)]
    pct_h, atr_h = ta_engine.run_up(ta_engine.candles_to_df(rows_h), 4 * 3600, 2.0)
    assert atr_h == pytest.approx(2.0) and pct_h == pytest.approx(4 / 15 * 100, abs=0.01)
    # too short a history, or a zero ATR, never invents a number
    assert ta_engine.run_up(ta_engine.candles_to_df(rows_h[:3]), 4 * 3600, 2.0) == (None, None)
    assert ta_engine.run_up(df, 4 * 3600, 0.0)[1] is None
    # ta_score carries both in diagnostics
    big = [{"t": t0 + i * 900, "o": 100, "h": 101, "l": 99, "c": 100 + i * 0.01, "v": 1} for i in range(80)]
    d = ta_engine.ta_score(ta_engine.candles_to_df(big))["diagnostics"]
    assert "run4h_pct" in d and "run4h_atr" in d and d["run4h_pct"] == pytest.approx(0.16 / (100 + 0.63) * 100, abs=0.01)
