# mypy: check_untyped_defs = False
"""Execution-price guards: reject an obsolete plan, never silently re-anchor it."""
from __future__ import annotations

import asyncio
import copy
import math

import pytest

import paper_trading
from paper_trading import PaperBroker
from risk_engine import MIN_RR, RiskConfig, RiskEngine


@pytest.fixture()
def broker(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "market_closed_hook", None)
    monkeypatch.setattr(paper_trading, "hot_mid_hook", None)

    class Feed:
        async def get_orderbook(self, symbol):
            return {"bids": [["99.99", "1000"]], "asks": [["100.01", "1000"]]}

        async def place_order(self, *args, **kwargs):
            pytest.fail("Paper must not call the exchange order endpoint")

    return PaperBroker(100_000, Feed(), RiskEngine(RiskConfig(equity=100_000)))


def account_snapshot(broker):
    return copy.deepcopy((broker.cash, broker.equity, broker.risk.open_heat,
                          broker.positions, broker.equity_curve))


@pytest.mark.parametrize("side,stop,tp1,tp2", [
    ("buy", 95, 99.9, 110),  # TP1 behind actual fill
    ("buy", 95, 100, 110),  # TP1 exactly at entry
    ("buy", 100, 106, 110),
    ("buy", 101, 106, 110),
    ("buy", 95, 111, 110),
    ("sell", 105, 100.1, 90),
    ("sell", 105, 100, 90),
    ("sell", 100, 94, 90),
    ("sell", 99, 94, 90),
    ("sell", 105, 89, 90),
])
def test_wrong_side_or_reversed_levels_leave_account_untouched(broker, side, stop, tp1, tp2):
    before = account_snapshot(broker)
    pos = asyncio.run(broker.place_limit("TESTUSDT", side, 1, 100, stop, tp1, tp2, "QUEUE"))
    assert pos is None
    assert broker.last_error.startswith("execution-plan-invalid:")
    assert account_snapshot(broker) == before
    assert [e.kind for e in broker.orders] == ["reject"]
    assert broker.orders[-1].extra["source"] == "QUEUE"
    assert broker.orders[-1].extra["tp1"] == tp1
    restored = PaperBroker(100_000, broker.client, RiskEngine(RiskConfig(equity=100_000)))
    assert not restored.positions and restored.cash == before[0]
    assert restored.orders[-1].kind == "reject"


@pytest.mark.parametrize("field", ["stop", "tp1", "tp2"])
@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf, -math.inf])
def test_nonfinite_or_nonpositive_levels_are_rejected(broker, field, value):
    levels = {"stop": 95, "tp1": 106, "tp2": 110, field: value}
    before = account_snapshot(broker)
    assert asyncio.run(broker.place_limit("TESTUSDT", "buy", 1, 100, **levels)) is None
    assert broker.last_error.startswith("execution-plan-invalid:")
    assert account_snapshot(broker) == before


@pytest.mark.parametrize("side,stop,tp1,tp2", [
    ("buy", 95, 106, 107),  # 1.4, though both targets are above entry
    ("sell", 105, 94, 93),
])
def test_low_rr_uses_tp2_and_actual_fill_not_absolute_distance(broker, side, stop, tp1, tp2):
    before = account_snapshot(broker)
    assert asyncio.run(broker.place_limit("TESTUSDT", side, 1, 100, stop, tp1, tp2)) is None
    assert broker.last_error.startswith("execution-rr-too-low:")
    assert account_snapshot(broker) == before
    assert broker.orders[-1].extra["rr"] == pytest.approx(1.4)
    assert broker.orders[-1].extra["min_rr"] == MIN_RR


@pytest.mark.parametrize("side,stop,tp1,tp2", [
    ("buy", 95, 106, 110),  # TP1 R=1.2 is valid; policy uses TP2
    ("sell", 105, 94, 90),
    ("buy", 96, 104, 106),  # exactly MIN_RR
    ("sell", 104, 96, 94),
    ("buy", 95, 110, 110),  # equal targets allowed by the contract
])
def test_valid_levels_are_preserved_without_reanchoring(broker, side, stop, tp1, tp2):
    pos = asyncio.run(broker.place_limit("TESTUSDT", side, 1, 100, stop, tp1, tp2))
    assert pos is not None, broker.last_error
    assert (pos.stop, pos.tp1, pos.tp2) == (stop, tp1, tp2)
    assert broker.cash < 100_000


def test_historical_default_levels_still_work_at_min_rr(broker):
    pos = asyncio.run(broker.place_limit("TESTUSDT", "buy", 1, 100))
    assert pos is not None, broker.last_error
    assert (pos.stop, pos.tp1, pos.tp2) == (96, 104, 106)


def test_market_slippage_can_invalidate_previously_valid_tp1(broker, monkeypatch):
    async def execution(*args):
        return 106, 1

    monkeypatch.setattr(broker, "_execution_price", execution)
    before = account_snapshot(broker)
    assert asyncio.run(broker.place_limit("TESTUSDT", "buy", 1, None, 95, 106, 120, "QUEUE")) is None
    assert broker.last_error.startswith("execution-plan-invalid:")
    assert broker.orders[-1].price > 106  # book walk plus modelled slip
    assert account_snapshot(broker) == before


def test_price_precision_is_applied_before_rr_guard(broker):
    # BTCUSDT rounds this quote to 100: reject/accept on 100, not 100.004.
    pos = asyncio.run(broker.place_limit("BTCUSDT", "buy", 1, 100.004, 96, 104, 106))
    assert pos is not None, broker.last_error
    assert pos.entry_price == 100


def test_existing_legacy_position_is_not_rewritten_on_load(broker):
    pos = asyncio.run(broker.place_limit("TESTUSDT", "buy", 1, 100, 95, 106, 110))
    assert pos is not None
    # Simulate a persisted position created before execution-plan validation.
    pos.tp1 = 99
    broker.save()
    cash = broker.cash
    restored = PaperBroker(100_000, broker.client, RiskEngine(RiskConfig(equity=100_000)))
    assert restored.positions[pos.id].tp1 == 99
    assert restored.positions[pos.id].entry_price == 100
    assert restored.cash == cash


def test_rejected_order_does_not_disturb_an_existing_position(broker):
    pos = asyncio.run(broker.place_limit("TESTUSDT", "buy", 1, 100, 95, 106, 110))
    assert pos is not None
    before = account_snapshot(broker)
    assert asyncio.run(broker.place_limit("OTHERUSDT", "buy", 1, 100, 95, 99, 110)) is None
    assert account_snapshot(broker) == before
    restored = PaperBroker(100_000, broker.client, RiskEngine(RiskConfig(equity=100_000)))
    assert account_snapshot(restored) == before
    assert [e.kind for e in restored.orders] == ["submit", "fill", "reject"]


@pytest.mark.parametrize("side,stop,tp1,tp2", [("buy", 95, 106, 107.499999),
                                             ("sell", 105, 94, 92.500001)])
def test_materially_below_rr_boundary_is_not_rounded_up(broker, side, stop, tp1, tp2):
    assert asyncio.run(broker.place_limit("TESTUSDT", side, 1, 100, stop, tp1, tp2)) is None
    assert broker.last_error.startswith("execution-rr-too-low:")


def test_python_javascript_execution_guard_parity():
    import json
    import random
    import shutil
    import subprocess
    from pathlib import Path

    from paper_trading import _execution_plan_error

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    rng = random.Random(20261029)
    cases = [[side, entry, entry * stop, entry * tp1, entry * tp2]
             for side in ("buy", "sell") for entry in (0.08, 100, 56611, 498500000)
             for stop, tp1, tp2 in [(0.95, 1.06, 1.1), (1.05, .94, .9),
                                     (.95, 1.06, 1.07), (.96, 1.04, 1.06),
                                     (1.04, .96, .94), (.95, .999, 1.1),
                                     (.95, 1.1, 1.06), (1, 1.06, 1.1)]]
    for _ in range(1000):
        cases.append([rng.choice(["buy", "sell"]), 100,
                      rng.uniform(80, 120), rng.uniform(80, 120), rng.uniform(80, 120)])
    script = """
      global.window=global;
      global.localStorage={getItem:()=>null,setItem:()=>{}};
      require('./nbx-core.js');
      const cases=JSON.parse(require('fs').readFileSync(0,'utf8'));
      console.log(JSON.stringify({min_rr:NBX.Risk.MIN_RR,
        results:cases.map(c=>NBX.Risk.executionPlanError(...c))}));
    """
    result = subprocess.run([node, "-e", script], input=json.dumps(cases), text=True, encoding="utf-8",
                            capture_output=True, cwd=Path(__file__).resolve().parents[2],
                            timeout=30, check=True)
    data = json.loads(result.stdout)
    assert data["min_rr"] == MIN_RR
    for case, js in zip(cases, data["results"], strict=True):
        error, rr = _execution_plan_error(*case)
        assert js["error"] == error, case
        assert js["rr"] == (pytest.approx(rr) if rr is not None else None), case


def test_accepted_plan_still_supports_persisted_partial_and_natural_stop(broker, monkeypatch):
    pos = asyncio.run(broker.place_limit("TESTUSDT", "buy", 1, 100, 95, 106, 110))
    assert pos is not None
    price = [107]

    async def mid(symbol):
        return price[0]

    monkeypatch.setattr(broker, "_mid_price", mid)
    asyncio.run(broker.evaluate_positions())
    assert pos.tp1_filled and pos.state == "PARTIAL" and pos.filled_qty == 0.5
    price[0] = 100  # crosses the engine's existing post-TP1 trailing stop
    asyncio.run(broker.evaluate_positions())
    assert pos.state == "CLOSED" and pos.close_reason == "trail"
    restored = PaperBroker(100_000, broker.client, RiskEngine(RiskConfig(equity=100_000)))
    assert restored.positions[pos.id].state == "CLOSED"
    assert restored.risk.open_heat == 0
    assert [e.kind for e in restored.orders] == ["submit", "fill", "tp1", "close"]
