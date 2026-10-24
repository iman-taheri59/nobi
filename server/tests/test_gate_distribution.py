"""ADR-0026 — a threshold comes from the distribution, never from taste.

The finding behind this ADR: `gates_block` calls `gates.evaluate(rows)` with no
limits, and `GateLimits` defaults `max_spread_bps`/`min_depth_usd` to `None`
("do not judge"), so `spread-wide` and `book-thin` cannot fire and read zero
forever. The bottleneck was never the sample size — it was the absence of a
number to set the limit from.

Everything here is a *measurement*: `applied` stays false and no limit is moved.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

import gate_ledger
import gates


def _seed(conn, values, *, source="live", days=1, metric="spread_bps", symbol="S0IRT"):
    """Write `values` as verdict rows spread evenly across `days` distinct days.

    The table is keyed on `(symbol, timeframe, ts)`, so the stamps have to be
    unique or later seeds silently replace earlier ones — which is exactly how the
    first version of this helper made the backfill test pass for the wrong reason.
    """
    values = list(values)
    now = int(time.time())
    step = max(60, int(days * 86400 / max(1, len(values))))
    for i, value in enumerate(values):
        ts = now - (len(values) - i) * step
        verdict = {symbol: {"code": "ok", "codes": [],
                            metric: float(value),
                            "depth_usd": 1000.0,
                            "age_bars": 0.0}}
        gate_ledger.record(conn, verdict, tf="15m", ts_of={symbol: ts}, source=source)
    return conn


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    gate_ledger.ensure(c)
    yield c
    c.close()


# --------------------------------------------------------------------------- #
# 1. The arithmetic
# --------------------------------------------------------------------------- #
def test_percentile_matches_the_closed_form_answers():
    data = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert gate_ledger.percentile(data, 0) == 1.0
    assert gate_ledger.percentile(data, 100) == 5.0
    assert gate_ledger.percentile(data, 50) == 3.0
    # linear interpolation between 4 and 5
    assert gate_ledger.percentile(data, 92.5) == pytest.approx(4.7)


def test_percentile_is_interpolated_not_nearest_rank():
    """With thirty samples a nearest-rank p95 is the maximum, which flatters the tail."""
    data = [float(i) for i in range(1, 31)]
    p95 = gate_ledger.percentile(data, 95)
    assert p95 is not None
    assert p95 == pytest.approx(28.55, abs=0.01)
    assert p95 < max(data), "p95 landed on the worst observation"


def test_percentile_refuses_an_impossible_quantile():
    with pytest.raises(ValueError):
        gate_ledger.percentile([1.0], 140)


def test_no_samples_is_an_answer_with_a_reason(conn):
    out = gate_ledger.distributions(conn)
    assert out["spread_bps"]["n"] == 0
    assert out["spread_bps"]["sufficient"] is False
    assert "no usable sample" in out["spread_bps"]["reason"]
    assert out["proposal"]["max_spread_bps"] is None


# --------------------------------------------------------------------------- #
# 2. The distribution and the proposal
# --------------------------------------------------------------------------- #
def test_the_distribution_describes_the_inputs(conn):
    _seed(conn, range(1, 41), days=20)
    out = gate_ledger.distributions(conn)["spread_bps"]
    assert out["n"] == 40 and out["days"] >= 5
    assert out["median"] == pytest.approx(20.5, abs=0.01)
    assert out["min"] == 1.0 and out["max"] == 40.0
    assert out["min"] < out["p95"] <= out["max"]


def test_the_proposal_is_p95_and_p05(conn):
    _seed(conn, range(1, 41), days=20)
    proposal = gate_ledger.distributions(conn)["proposal"]
    assert proposal["applied"] is False and proposal["proposed"] is True
    assert proposal["max_spread_bps"] == pytest.approx(38.0, abs=0.5)
    assert proposal["min_depth_usd"] == pytest.approx(1000.0)
    assert proposal["reason"] is None
    assert proposal["basis"]["max_spread_bps"] == "p95(spread_bps)"


def test_a_thin_sample_proposes_nothing(conn):
    """Thirty rows in one afternoon is one day of markets, not five."""
    _seed(conn, range(1, 41), days=1)
    out = gate_ledger.distributions(conn)
    assert out["spread_bps"]["n"] >= 30
    if out["spread_bps"]["days"] < gate_ledger.MIN_DAYS:
        assert out["proposal"]["max_spread_bps"] is None
        assert "days" in out["proposal"]["reason"]


def test_too_few_rows_propose_nothing(conn):
    _seed(conn, [10.0, 20.0, 30.0], days=20)
    proposal = gate_ledger.distributions(conn)["proposal"]
    assert proposal["max_spread_bps"] is None
    assert "samples" in proposal["reason"]


# --------------------------------------------------------------------------- #
# 3. The two refusals
# --------------------------------------------------------------------------- #
def test_backfilled_rows_are_not_a_distribution(conn):
    """A spread taken from reconstructed verdicts is not a measured spread."""
    _seed(conn, range(1, 41), source="backfilled", days=20)
    out = gate_ledger.distributions(conn)
    assert out["spread_bps"]["n"] == 0
    assert out["skipped_backfilled"] == 40
    assert out["proposal"]["max_spread_bps"] is None


def test_live_rows_still_count_when_backfilled_ones_are_present(conn):
    _seed(conn, range(1, 41), source="live", days=20)
    _seed(conn, [999.0] * 10, source="backfilled", days=20, symbol="S1IRT")
    out = gate_ledger.distributions(conn)
    assert out["spread_bps"]["max"] <= 40.0, "a backfilled outlier leaked into the tail"
    assert out["skipped_backfilled"] >= 10


def test_the_whole_block_says_it_is_measurement_only(conn):
    _seed(conn, range(1, 41), days=20)
    out = gate_ledger.distributions(conn)
    assert out["applied"] is False
    assert "measurement only" in out["note"]


# --------------------------------------------------------------------------- #
# 4. And nothing was switched on
# --------------------------------------------------------------------------- #
def test_the_gate_limits_are_still_switched_off():
    default = gates.GateLimits()
    assert default.max_spread_bps is None and default.min_depth_usd is None, (
        "ADR-0026 measures; it does not apply. Setting a limit here would be a "
        "trading-behaviour change with no ADR and no evidence."
    )
    assert default.enforcing is False


def test_a_proposal_is_not_read_by_the_evaluator():
    """The evaluator must not quietly start reading a proposal from anywhere."""
    source = (Path(gate_ledger.__file__).parent / "gates.py").read_text(encoding="utf-8")
    assert "distributions" not in source and "propose_limits" not in source

# --------------------------------------------------------------------------- #
# 5. The ledger has to carry the numbers in the first place
# --------------------------------------------------------------------------- #
def test_an_ok_verdict_still_carries_its_measured_inputs(conn):
    """The defect behind the empty distribution: only failing verdicts had a spread."""
    from gates import evaluate_symbol

    out = evaluate_symbol("BTCIRT", age_bars=1.0,
                          book={"spread_bps": 12.5, "depth_usd": 90_000.0})
    assert out["code"] == "ok"
    assert out["inputs"] == {"spread_bps": 12.5, "depth_usd": 90_000.0, "age_bars": 1.0}


def test_the_ledger_stores_the_inputs_of_an_ok_row(conn):
    from gates import evaluate

    block = evaluate([{"symbol": "BTCIRT", "age_bars": 1.0,
                       "book": {"spread_bps": 12.5, "depth_usd": 90_000.0}}])
    gate_ledger.record(conn, block["verdicts"], tf="15m", ts_of={"BTCIRT": 1_700_000_100},
                       source="live")
    stored = conn.execute("SELECT spread_bps, depth_usd FROM gate_shadow").fetchone()
    assert stored == (12.5, 90_000.0), "an ok row lost the numbers the gate looked at"


def test_a_no_book_row_has_no_inputs_to_report():
    from gates import evaluate_symbol

    out = evaluate_symbol("BTCIRT", age_bars=0.5, book=None)
    assert out["code"] == "no-book"
    assert out["inputs"]["spread_bps"] is None and out["inputs"]["depth_usd"] is None
