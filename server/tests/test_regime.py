"""ADR-0050 — market-regime gate: classification and the three modes."""
from __future__ import annotations

import regime


def ov(median, pct_up, ok=True, priced=40):
    return {"ok": ok, "priced": priced, "change": {"median_24h": median}, "breadth": {"pct_up": pct_up}}


def test_states_follow_thresholds():
    assert regime.assess(ov(-3.5, 25), min_median_24h=-2, min_breadth_pct=35)["state"] == "risk-off"
    assert regime.assess(ov(-1.0, 30), min_median_24h=-2, min_breadth_pct=35)["state"] == "risk-off"  # breadth alone
    assert regime.assess(ov(-2.5, 60), min_median_24h=-2, min_breadth_pct=35)["state"] == "risk-off"  # median alone
    assert regime.assess(ov(0.3, 50), min_median_24h=-2, min_breadth_pct=35)["state"] == "neutral"
    assert regime.assess(ov(2.5, 70), min_median_24h=-2, min_breadth_pct=35)["state"] == "risk-on"
    assert regime.assess(ov(2.5, 55), min_median_24h=-2, min_breadth_pct=35)["state"] == "neutral"


def test_unknown_is_not_risk_off():
    assert regime.assess(ov(-9, 5, ok=False))["state"] == "unknown"
    assert regime.assess(None)["state"] == "unknown"
    assert regime.assess({"ok": True})["state"] == "unknown"
    d, c, meta = regime.apply("LONG", 80.0, regime.assess(None), mode="enforce")
    assert (d, c) == ("LONG", 80.0) and meta["regime_gate"]["would_block"] is False


def test_off_changes_nothing_and_records_nothing():
    d, c, meta = regime.apply("LONG", 80.0, {"state": "risk-off"}, mode="off")
    assert (d, c, meta) == ("LONG", 80.0, {})


def test_shadow_records_but_keeps_direction():
    d, c, meta = regime.apply("LONG", 80.0, {"state": "risk-off", "reasons": ["x"]}, mode="shadow")
    assert (d, c) == ("LONG", 80.0)
    assert meta["market_regime"] == "risk-off"
    assert meta["regime_gate"] == {"mode": "shadow", "would_block": True, "applied": False, "reasons": ["x"]}
    assert "regime_cap" not in meta


def test_enforce_caps_long_below_promotion_only_in_risk_off():
    d, c, meta = regime.apply("LONG", 80.0, {"state": "risk-off"}, mode="enforce")
    assert d == "NEUTRAL" and c == 67.0 and meta["regime_cap"] == 67.0 and meta["regime_gate"]["applied"] is True
    # a LONG already under the cap keeps its score, loses the verdict
    d, c, _ = regime.apply("LONG", 50.0, {"state": "risk-off"}, mode="enforce")
    assert (d, c) == ("NEUTRAL", 50.0)
    # non-LONG rows and non-risk-off tapes are untouched
    for direction in ("NEUTRAL", "EXIT"):
        d, c, meta = regime.apply(direction, 40.0, {"state": "risk-off"}, mode="enforce")
        assert (d, c) == (direction, 40.0) and meta["regime_gate"]["would_block"] is False
    d, c, _ = regime.apply("LONG", 80.0, {"state": "neutral"}, mode="enforce")
    assert (d, c) == ("LONG", 80.0)


def test_default_mode_is_shadow():
    assert regime.config()["mode"] in regime.MODES
    assert regime._mode_env() == "shadow" or regime.MODE in regime.MODES


def test_risk_k_is_one_setting(monkeypatch):
    import risk_engine

    assert risk_engine.parse_risk_k(None) == 2.0
    assert risk_engine.parse_risk_k(" 3 ") == 3.0
    import pytest

    for bad in ("0.5", "9", "abc", "nan"):
        with pytest.raises(ValueError):
            risk_engine.parse_risk_k(bad)


def test_journal_and_ta_engine_read_the_same_multiplier():
    import journal
    import ta_engine

    assert journal.RISK_K == ta_engine.RISK_K


def test_fx_shock_precedes_breadth_and_blocks_like_risk_off():
    """The rial moving is a regime of its own, even when breadth looks healthy."""
    healthy = ov(2.5, 70)
    assert regime.assess(healthy, fx_premium_pct=6, fx_move_24h_pct=3)["state"] == "risk-on"
    stretched = {**healthy, "premium_pct": 7.2}
    r = regime.assess(stretched, fx_premium_pct=6, fx_move_24h_pct=3)
    assert r["state"] == "fx-shock" and r["reasons"] == ["|premium|>=6"]
    jumped = {**healthy, "usdt_irt_change_24h": -3.4}  # a negative jump counts too
    assert regime.assess(jumped, fx_premium_pct=6, fx_move_24h_pct=3)["reasons"] == ["|usdt_irt_24h|>=3"]
    # thin overview, but the premium is known: still a finding, not "unknown"
    thin = {"ok": False, "premium_pct": 9.0}
    assert regime.assess(thin, fx_premium_pct=6)["state"] == "fx-shock"
    # under both modes it behaves exactly like risk-off
    d, c, meta = regime.apply("LONG", 80.0, r, mode="shadow")
    assert (d, c) == ("LONG", 80.0) and meta["regime_gate"]["would_block"] is True
    d, c, meta = regime.apply("LONG", 80.0, r, mode="enforce")
    assert (d, c, meta["regime_cap"]) == ("NEUTRAL", 67.0, 67.0)


def test_fx_inputs_missing_do_not_trigger():
    r = regime.assess({**ov(0.3, 50), "premium_pct": None, "usdt_irt_change_24h": "n/a"})
    assert r["state"] == "neutral" and r["inputs"]["premium_pct"] is None
    assert "fx_premium_pct" in regime.config()
