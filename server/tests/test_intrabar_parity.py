"""The intrabar assumption must be the same in both engines (ADR-0009 §3, 1.0.9).

A candle that reaches both the stop and the target hides the order in which it
did so. The backtester cannot know the path, so it takes the adverse one. That
is the honest choice — and until 1.0.9 the browser engine made the *opposite*
one: `if (tr.tp1 && c.h >= tr.tp1) … else if (c.l <= tr.stop)` let the target
win whenever a candle spanned both levels.

That is not a detail. It is the one bias a backtest must never have, and it ran
in the engine that produces the phone's offline numbers. These tests pin:

1. the server's rule (stop, mark ambiguous) still holds;
2. the browser's rule is now the same, and it counts what it assumed;
3. the share travels with the numbers and the gate reacts to it;
4. the two engines agree on the same fixture candle, not just in spirit.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[2]


def _rows(n: int = 700, seed: int = 6):
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
# 1. the server's rule
# --------------------------------------------------------------------------- #
def test_a_candle_spanning_both_levels_takes_the_stop_and_says_so():
    from backtest import EventBacktester, _rows_to_frames

    rows = [{"t": 1_700_000_000 + i * 3600, "o": 100.0, "h": 100.0, "l": 100.0,
             "c": 100.0, "v": 1.0} for i in range(34)]
    df = _rows_to_frames(rows)
    df.loc[31, "h"] = 110.0     # spans stop (98) and TP1 (103) at once
    df.loc[31, "l"] = 90.0
    signals = [None] * 30 + [{"direction": "LONG", "score": 80.0, "risk": {"atr": 1.0}}] \
        + [None] * (len(df) - 31)
    bt = EventBacktester({"initial_equity": 100_000, "risk_pct": 1.0,
                          "atr_stop_k": 2.0, "tp1_r": 1.5, "tp2_r": 2.5,
                          "slippage_bps": 0})
    bt.run_symbol(df, "XUSDT", "1h", signals=signals, start=30, end=len(df))
    assert bt.closed, "no trade was opened"
    tr = bt.closed[0]
    assert tr.ambiguous is True
    assert tr.reason == "sl-ambiguous"


# --------------------------------------------------------------------------- #
# 2. the share is reported, not just the count
# --------------------------------------------------------------------------- #
def test_the_aggregate_reports_the_share_of_exits_that_were_assumed():
    rep = _report()
    agg = rep["aggregate"]
    if agg["trades"]:
        expected = round(agg["ambiguous_exits"] / agg["trades"] * 100, 1)
        assert agg["ambiguous_share"] == pytest.approx(expected, abs=0.05)
    else:
        assert agg["ambiguous_share"] == 0.0
    assert 0.0 <= agg["ambiguous_share"] <= 100.0


def test_an_ambiguous_heavy_run_cannot_pass():
    """Wide intrabar ranges make the stop-or-target question decide the result.

    The fixture alternates ordinary bars with occasional news candles: ordinary
    ranges keep the ATR (and therefore the stop distance) small, while the news
    candle is wide enough to touch both levels. The ATR is what the *ordinary*
    bars set, so this is the realistic version of the problem, not a toy one.
    """
    import random

    from backtest import run_and_save

    rnd = random.Random(13)
    px = 100.0
    rows = []
    for i in range(1400):
        o = px
        px = max(1.0, px * (1 + rnd.gauss(0, 0.004)))
        w = 0.10 if i % 9 == 0 else 0.004
        rows.append({"t": 1_700_000_000 + i * 3600, "o": o, "h": max(o, px) * (1 + w),
                     "l": min(o, px) * (1 - w), "c": px, "v": 10.0})
    rep = run_and_save("TUSDT", "1h", rows, {"initial_equity": 50_000},
                       mode="wf", auto_window=True)
    agg, gate = rep["aggregate"], rep["gate"]
    assert agg["trades"] >= 30, "fixture stopped producing trades"
    assert agg["ambiguous_share"] > 10.0, "fixture is no longer ambiguity-heavy"
    assert gate["checks"]["ambiguous_share_le_10"]["pass"] is False
    assert "ambiguous_share_le_10" in gate["failed"]
    assert gate["state"] != "PASS"


def test_the_check_is_present_even_when_nothing_is_ambiguous():
    rep = _report()
    check = rep["gate"]["checks"]["ambiguous_share_le_10"]
    assert set(check) == {"value", "pass", "required"}
    assert check["pass"] is (check["value"] <= 10.0)


# --------------------------------------------------------------------------- #
# 3. the browser engine agrees — same rule, same counter
# --------------------------------------------------------------------------- #
def test_the_browser_engine_takes_the_stop_when_a_candle_spans_both_levels():
    script = ROOT / "scripts" / "js_engine_parity.js"
    assert script.exists(), "the parity harness is missing"
    import json
    import subprocess

    out = subprocess.run(["node", str(script)], capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    data = json.loads(out.stdout)

    assert data["browser"]["reason"] == "sl-ambiguous", data["browser"]
    assert data["browser"]["ambiguous_exits"] == 1
    assert data["browser"]["r_multiple"] < 0, "the ambiguous exit was priced as a win"
    assert data["server"]["reason"] == data["browser"]["reason"]
