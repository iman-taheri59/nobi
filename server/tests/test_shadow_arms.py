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
    assert set(arms) == {"B_fa_liq", "C_gate", "D_comp"}
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
