"""Which strategy did the backtest actually test? (ADR-0009 amendment, 1.0.8)

The live decision blends three components (TA 0.55, fundamentals 0.25, liquidity
0.20) and can then be capped by vetoes. The walk-forward simulator replays the TA
component only, because fundamentals and liquidity are fetched point-in-time and
have no history in this repository.

That is a defensible limitation; leaving it unsaid is not. A report that says
"PF 1.42, gate PASS" while measuring a quarter of the live rule is more
dangerous than a report that says "PF 1.42 — technical component only". These
tests pin the disclosure:

1. every report declares its basis, and the basis says it is not the live one;
2. the declaration is part of the experiment identity (config hash);
3. the gate sentence carries the caveat, in the state the operator trusts most;
4. the declared live weights cannot drift from the engine's own constants.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _rows(n: int = 700, seed: int = 5):
    import random

    rnd = random.Random(seed)
    px = 100.0
    out = []
    for i in range(n):
        o = px
        px = max(1.0, px * (1 + rnd.gauss(0.0004, 0.009)))
        out.append({"t": 1_700_000_000 + i * 3600, "o": o, "h": max(o, px) * 1.004,
                    "l": min(o, px) * 0.996, "c": px, "v": rnd.uniform(10, 100)})
    return out


def _report(**cfg):
    from backtest import run_and_save

    return run_and_save("TUSDT", "1h", _rows(), {"initial_equity": 50_000, **cfg},
                        mode="wf", auto_window=True)


# --------------------------------------------------------------------------- #
# 1. every report declares its basis
# --------------------------------------------------------------------------- #
def test_the_report_declares_which_components_it_tested():
    rep = _report()
    basis = rep["decision_basis"]
    assert basis["components"] == ["ta"]
    assert basis["weights"] == {"ta": 1.0}
    assert basis["vetoes"] is False
    assert basis["live_composite"] == "ta+fa+liq"
    assert basis["live_weights"] == {"ta": 0.55, "fa": 0.25, "liq": 0.20}


def test_the_basis_is_honest_about_not_matching_live():
    rep = _report()
    assert rep["decision_basis"]["matches_live"] is False
    # and the gate carries the same declaration, not a second opinion
    assert rep["gate"]["decision_basis"] == rep["decision_basis"]


def test_a_ta_only_run_is_not_dressed_up_as_the_live_rule():
    """The claim inside the report must not exceed what the simulator ran."""
    rep = _report()
    basis = rep["decision_basis"]
    assert set(basis["components"]) != set(basis["live_weights"])
    assert sum(basis["weights"].values()) == 1.0


# --------------------------------------------------------------------------- #
# 2. the basis is part of the experiment identity
# --------------------------------------------------------------------------- #
def test_the_basis_is_folded_into_the_config_hash(monkeypatch):
    """A report that tested a different rule set is a different experiment."""
    import backtest

    cfg = {"atr_stop_k": 2.0, "tp1_r": 1.5, "tp2_r": 2.5, "slippage_bps": 6}
    ta_only = backtest._cfg_hash(cfg)
    assert ta_only == backtest._cfg_hash(dict(cfg))      # stable for equal inputs
    monkeypatch.setattr(backtest, "BACKTEST_COMPONENTS", ("ta", "liq"))
    assert backtest._cfg_hash(cfg) != ta_only


# --------------------------------------------------------------------------- #
# 3. the caveat rides on the verdict, whatever the verdict is
# --------------------------------------------------------------------------- #
def test_the_gate_message_carries_the_caveat_for_a_passing_report():
    rep = _report()
    g = rep["gate"]
    assert "technical component only" in g["message"]


def test_the_caveat_is_present_even_when_the_run_fails_or_lacks_data():
    rep = _report(slippage_bps=0)      # guaranteed FAIL on costs_modeled
    assert rep["gate"]["state"] in {"FAIL", "INSUFFICIENT_DATA", "SYNTHETIC_DATA"}
    assert "technical component only" in rep["gate"]["message"]


def test_the_caveat_does_not_replace_the_reason():
    """The caveat is appended, never a substitute for the actual finding."""
    from backtest import run_and_save

    rep = run_and_save("TUSDT", "1h", _rows(4000, seed=8),
                       {"initial_equity": 50_000, "slippage_bps": 0},
                       mode="wf", auto_window=True)
    g = rep["gate"]
    assert g["state"] == "FAIL"          # enough data, one real check failed
    assert "costs_modeled" in g["failed"]
    msg = g["message"]
    assert msg.startswith("Failed: ")
    assert "costs_modeled" in msg
    assert msg.index("costs_modeled") < msg.index("technical component only")


# --------------------------------------------------------------------------- #
# 4. the declared live weights cannot drift from the engine
# --------------------------------------------------------------------------- #
def test_declared_live_weights_match_the_decision_engine():
    import main
    from backtest import LIVE_COMPOSITE_WEIGHTS

    assert LIVE_COMPOSITE_WEIGHTS["ta"] == main.TA_W
    assert LIVE_COMPOSITE_WEIGHTS["fa"] == main.FA_W
    assert LIVE_COMPOSITE_WEIGHTS["liq"] == main.LIQ_W
    assert sum(LIVE_COMPOSITE_WEIGHTS.values()) == 1.0


def test_the_engine_still_caps_vetoes_below_promotion():
    """If this ever stops being true, the caveat about vetoes stops being true."""
    import main

    capped, direction, info = main.apply_vetoes(90.0, "LONG", ["thin-tvl"])
    assert capped < 68.0
    assert direction != "LONG"
    assert info["uncapped_direction"] == "LONG"
