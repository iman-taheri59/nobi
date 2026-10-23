"""Costs are part of the backtest, not an option (ADR-0009 amendment, 1.0.7).

The fee was always modelled; the *fill* was not. `slippage_bps` defaulted to 0
in three places (the simulator, `run_and_save`, and the HTTP route), so every
report — and every gate verdict built on it — described a market where orders
fill at the exact price. `SLIP_BPS = 6` existed but nothing read it.

These tests pin the three properties that make that impossible to regress:

1. the default is the model, wherever you enter the code;
2. an explicit 0 still runs, but the gate records it and refuses to certify;
3. the effective cost model travels with the numbers it produced.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _rows(n: int = 700, seed: int = 4):
    """A deterministic random walk shaped like the exchange's candles."""
    import random

    rnd = random.Random(seed)
    px = 100.0
    out = []
    for i in range(n):
        o = px
        px = max(1.0, px * (1 + rnd.gauss(0.0004, 0.009)))
        hi, lo = max(o, px) * 1.004, min(o, px) * 0.996
        out.append({"t": 1_700_000_000 + i * 3600, "o": o, "h": hi, "l": lo,
                    "c": px, "v": rnd.uniform(10, 100)})
    return out


def _report(**cfg):
    from backtest import run_and_save

    return run_and_save("TUSDT", "1h", _rows(), {"initial_equity": 50_000, **cfg},
                        mode="wf", auto_window=True)


# --------------------------------------------------------------------------- #
# 1. the default is the model
# --------------------------------------------------------------------------- #
def test_the_simulator_defaults_to_the_declared_cost_model():
    from backtest import SLIP_BPS, EventBacktester

    bt = EventBacktester({"initial_equity": 100_000})
    assert SLIP_BPS > 0
    assert bt.slippage == pytest.approx(SLIP_BPS / 10_000.0)


def test_a_run_without_a_slippage_argument_costs_something():
    rep = _report()
    from backtest import SLIP_BPS

    assert rep["slippage_bps"] == pytest.approx(float(SLIP_BPS))
    assert rep["costs"]["slippage_bps"] == pytest.approx(float(SLIP_BPS))
    assert rep["costs"]["fee_pct"] > 0
    assert rep["costs"]["modeled"] is True


def test_the_cost_model_is_part_of_the_config_hash():
    """Two runs that differ only in cost are different experiments."""
    a = _report(slippage_bps=0)
    b = _report(slippage_bps=25)
    assert a["config_hash"] != b["config_hash"]


def test_slippage_moves_the_result_in_the_direction_it_should():
    """Same candles, same parameters: a costlier fill cannot be a better run."""
    free = _report(slippage_bps=0)
    costly = _report(slippage_bps=60)
    assert costly["aggregate"]["expectancy_r"] <= free["aggregate"]["expectancy_r"]
    assert costly["aggregate"]["final_equity"] <= free["aggregate"]["final_equity"]


def test_an_explicit_value_is_echoed_not_overridden():
    rep = _report(slippage_bps=25)
    assert rep["slippage_bps"] == pytest.approx(25.0)
    assert rep["costs"]["modeled"] is True


# --------------------------------------------------------------------------- #
# 2. a frictionless run may run, but it may not certify anything
# --------------------------------------------------------------------------- #
def test_the_gate_has_a_cost_check_and_it_fails_at_zero():
    rep = _report(slippage_bps=0)
    check = rep["gate"]["checks"]["costs_modeled"]
    assert check["pass"] is False
    assert check["value"] == pytest.approx(0.0)
    assert "costs_modeled" in rep["gate"]["failed"]
    assert rep["gate"]["state"] != "PASS"


def test_the_gate_cost_check_passes_with_a_real_model():
    rep = _report()
    check = rep["gate"]["checks"]["costs_modeled"]
    assert check["pass"] is True
    assert "costs_modeled" not in rep["gate"]["failed"]


def test_a_failing_cost_check_is_named_in_the_message():
    rep = _report(slippage_bps=0)
    g = rep["gate"]
    if g["would_be"] == "FAIL":
        assert "costs_modeled" in g["message"]


# --------------------------------------------------------------------------- #
# 3. the number and its cost travel together
# --------------------------------------------------------------------------- #
def test_the_persisted_report_keeps_its_cost_model():
    """The JSON on disk is what gets quoted later; it must say how it was filled."""
    import json

    from backtest import REPORTS

    rep = _report(slippage_bps=12)
    saved = json.loads((REPORTS / rep["report_file"]).read_text(encoding="utf-8"))
    assert saved["slippage_bps"] == pytest.approx(12.0)
    assert saved["costs"]["fee_pct"] > 0
    assert saved["gate"]["checks"]["costs_modeled"]["value"] == pytest.approx(12.0)


def test_grid_search_uses_the_same_default_as_the_simulator():
    from backtest import SLIP_BPS, grid_search

    res = grid_search("TUSDT", "1h", _rows(320),
                      {"risk_pct": [0.75], "atr_stop_k": [2.0]})
    assert res["slippage_bps"] == pytest.approx(float(SLIP_BPS))


# --------------------------------------------------------------------------- #
# 4. the HTTP route does not quietly switch the model off
# --------------------------------------------------------------------------- #
def _route_payload(monkeypatch, rows, **body):
    """Call the real `/api/backtest/run` handler with a fake candle feed."""
    import asyncio

    import main

    async def fake_candles(symbol, tf, count):
        return rows, "live"

    monkeypatch.setattr(main.client, "get_candles_ex", fake_candles)
    return asyncio.run(main.backtest_run({"symbol": "TUSDT", "tf": "1h",
                                          "bars": 700, **body}))


def test_the_route_does_not_force_a_frictionless_run(monkeypatch):
    """The old default was `b.get("slippage_bps", 0.0)` — a cost-free report."""
    from backtest import SLIP_BPS

    out = _route_payload(monkeypatch, _rows())
    assert out["ok"] is True
    assert out["slippage_bps"] == pytest.approx(float(SLIP_BPS))
    assert out["report"]["costs"]["modeled"] is True


def test_the_route_still_honours_an_explicit_zero(monkeypatch):
    out = _route_payload(monkeypatch, _rows(), slippage_bps=0)
    assert out["slippage_bps"] == pytest.approx(0.0)
    assert out["report"]["gate"]["checks"]["costs_modeled"]["pass"] is False
