"""ADR-0052 — fees in R (new columns only) and the frozen holdout."""
from __future__ import annotations

import pandas as pd
import pytest

import journal


def _df(n=400, start=1_700_000_000, px=100.0):
    return pd.DataFrame([{"t": pd.Timestamp(start + i * 900, unit="s"), "o": px, "h": px * 1.004,
                          "l": px * 0.996, "c": px, "v": 1.0} for i in range(n)])


def test_simulate_long_reports_net_of_fee_without_touching_gross():
    df = _df()
    out = journal.simulate_long(df, 10, 100.0, 0.5, 4)
    assert out is not None
    assert out["fee_pct"] == pytest.approx(0.4)
    assert out["return_net_pct"] == pytest.approx(out["return_pct"] - 0.4, abs=1e-6)
    # stop = 2 * 0.5 = 1.0 -> 1 % of entry; 0.4 % fee = 0.4 R
    assert out["r_net"] == pytest.approx(out["r_multiple"] - 0.4, abs=1e-6)
    out3 = journal.simulate_long(df, 10, 100.0, 0.5, 4, risk_k=3.0)
    assert out3 is not None
    assert out3["r_net"] == pytest.approx(out3["r_multiple"] - 0.4 / 1.5, abs=1e-3)


def test_net_columns_are_written_and_history_is_not_rewritten(tmp_path):
    conn = journal.connect(tmp_path / "j.db")
    df = _df()
    ts0 = 1_700_000_000 + 11 * 900
    r = journal.make_row({"symbol": "AAAUSDT", "tf": "15m", "direction": "LONG", "score": 70,
                          "liquidity_score": 60, "composite": 70, "lastClose": 100.0,
                          "risk": {"atr": 0.5}}, ts0, origin="live", source="test")
    journal.insert_rows(conn, [r])
    # a legacy outcome row with NULL net columns stays NULL
    conn.execute("INSERT INTO signal_outcomes(signal_id, horizon_key, r_multiple, return_pct) VALUES (1,'H2',-1.0,-1.2)")
    conn.commit()
    journal.resolve_symbol(conn, "AAAUSDT", "15m", df)
    rows = {k: (rn, fee) for k, rn, fee in conn.execute(
        "SELECT horizon_key, r_net, fee_pct FROM signal_outcomes WHERE signal_id=1")}
    assert rows["H2"] == (None, None)
    assert rows["H1"][1] == pytest.approx(0.4) and rows["H1"][0] is not None
    cols = {r[1] for r in conn.execute("PRAGMA table_info(signal_shadow_outcomes)")}
    assert {"return_net_pct", "r_net", "fee_pct"} <= cols


def test_holdout_freezes_once(tmp_path):
    conn = journal.connect(tmp_path / "j.db")
    assert journal.holdout(conn) is None
    v = journal.freeze_holdout(conn, 1_800_000_000, note="post-soak")
    assert v["cutoff_ts"] == 1_800_000_000 and v["config_hash"] == journal.CONFIG_HASH
    with pytest.raises(ValueError):
        journal.freeze_holdout(conn, 1_800_000_100)
    frozen = journal.holdout(conn)
    assert frozen is not None and frozen["cutoff_ts"] == 1_800_000_000
    moved = journal.freeze_holdout(conn, 1_800_000_100, force=True)
    assert moved["previous"]["cutoff_ts"] == 1_800_000_000
