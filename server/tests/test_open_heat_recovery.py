# mypy: check_untyped_defs = False
"""A persisted open leg cannot disappear from the portfolio risk budget."""
from __future__ import annotations

import asyncio

import pytest

import paper_trading
from paper_trading import PaperBroker
from risk_engine import RiskConfig, RiskEngine


class OfflineFeed:
    def __init__(self):
        self.price = 100.0

    async def get_orderbook(self, symbol):
        return {"bids": [[self.price, 10000]], "asks": [[self.price, 10000]]}


@pytest.fixture()
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "hot_mid_hook", None)
    monkeypatch.setattr(paper_trading, "market_closed_hook", None)
    feed = OfflineFeed()
    broker = PaperBroker(100000, feed, RiskEngine(RiskConfig(equity=100000)))
    pos = asyncio.run(broker.place_limit("TESTUSDT", "buy", 100, price=100,
                                        stop=94, tp1=107.2, tp2=112))
    assert pos is not None
    return feed, broker, pos


def test_restart_restores_heat_and_does_not_double_count(fixture):
    feed, _broker, pos = fixture
    for _ in range(3):
        restored = PaperBroker(100000, feed, RiskEngine(RiskConfig(equity=100000)))
        assert len(restored.positions) == 1
        assert restored.risk.open_heat == pytest.approx(0.6)
        restored.save()
    assert pos.risk_pct == pytest.approx(0.6)


def test_partial_exit_reserves_remaining_entry_risk_and_persists(fixture):
    feed, broker, pos = fixture
    feed.price = 108
    asyncio.run(broker.evaluate_positions())
    assert pos.state == "PARTIAL"
    assert pos.filled_qty == 50
    assert broker.risk.open_heat == pytest.approx(0.3)
    # Historical entry risk is still available for R multiples.
    assert pos.risk_pct == pytest.approx(0.6)
    assert pos.risk_usd == pytest.approx(600)
    restored = PaperBroker(100000, feed, RiskEngine(RiskConfig(equity=100000)))
    assert restored.risk.open_heat == pytest.approx(0.3)


def test_full_exit_and_reload_have_zero_heat(fixture):
    feed, broker, pos = fixture
    asyncio.run(broker._close(pos.id, "offline-test"))
    assert broker.risk.open_heat == 0
    restored = PaperBroker(100000, feed, RiskEngine(RiskConfig(equity=100000)))
    assert restored.risk.open_heat == 0


def test_recovered_heat_still_blocks_excess_portfolio_risk(fixture):
    feed, _broker, _pos = fixture
    risk = RiskEngine(RiskConfig(equity=100000, max_heat_pct=1.0))
    restored = PaperBroker(100000, feed, risk)
    refused = asyncio.run(restored.place_limit("OTHERUSDT", "buy", 100,
                                               price=100, stop=94))
    assert refused is None
    assert restored.last_error.startswith("heat-cap:")
    assert len(restored.positions) == 1
    event = restored.orders[-1]
    assert event.extra["detail"]
    assert event.extra["gate_value"] > event.extra["gate_limit"]
    reloaded = PaperBroker(100000, feed, RiskEngine(RiskConfig(equity=100000)))
    assert reloaded.orders[-1].extra["reason"] == "heat-cap"
    assert reloaded.orders[-1].extra["gate_value"] == event.extra["gate_value"]


def test_plan_reports_risk_after_caps_not_original_budget():
    engine = RiskEngine(RiskConfig(equity=100000))
    plan = engine.evaluate("DOGEIRT", "buy", 21655.2, 90.45,
                           equity_quote=27070000000, preview=True)
    actual = abs(plan.entry - plan.stop) * plan.size_quote / plan.entry / 27070000000 * 100
    assert plan.risk_pct == pytest.approx(actual, abs=1e-6)
    assert plan.risk_budget_pct == 0.75
    assert plan.risk_pct < plan.risk_budget_pct
    assert engine.open_heat == 0


def test_low_price_usdt_stop_does_not_round_to_entry():
    plan = RiskEngine(RiskConfig(equity=100000)).evaluate(
        "DOGEUSDT", "buy", 0.08, 0.001, preview=True)
    assert plan.entry == 0.08
    assert plan.stop == 0.078
    assert plan.tp1 > plan.entry
    assert plan.risk_pct > 0


def test_backup_recovery_also_rebuilds_open_heat(fixture):
    feed, broker, _pos = fixture
    broker.save()  # retain a second good snapshot as .bak
    paper_trading.STATE_FILE.write_text('{broken', encoding='utf-8')
    restored = PaperBroker(100000, feed, RiskEngine(RiskConfig(equity=100000)))
    assert restored.state_recovery is not None
    assert len(restored.positions) == 1
    assert restored.risk.open_heat == pytest.approx(0.6)


def test_clamp_and_heat_reductions_are_not_applied_twice():
    engine = RiskEngine(RiskConfig(equity=100000, risk_pct=5,
                                    max_risk_pct=2, max_heat_pct=0.9))
    engine.open_heat = 0.4
    plan = engine.evaluate('TESTUSDT', 'buy', 1000, 100, preview=True)
    assert plan.risk_budget_pct == pytest.approx(0.5)
    assert plan.risk_pct == pytest.approx(0.5)
    assert plan.size_quote == pytest.approx(2500)
    assert engine.open_heat == 0.4
