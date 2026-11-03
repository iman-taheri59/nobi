# mypy: check_untyped_defs = False
"""A thin exit book may sell only part of an open leg.

The live probe showed a 2,448,651-unit entry closed by a 2,096,055-unit exit,
while the model reported CLOSED / remaining_open=0 and booked PnL against the
full entry cost. These tests pin the corrected contract: sell what the book
supports, release only that fraction's cost basis, keep the residual open, and
close only when the residual is gone.
"""
from __future__ import annotations

import asyncio

import pytest

import main
import paper_trading
from paper_trading import PaperBroker
from risk_engine import RiskConfig, RiskEngine


class DepthFeed:
    def __init__(self, bids=None, asks=None):
        self.bids = bids if bids is not None else [[100.0, 1000.0]]
        self.asks = asks if asks is not None else [[100.0, 1000.0]]

    async def get_orderbook(self, symbol):
        return {"bids": self.bids, "asks": self.asks}


@pytest.fixture()
def broker(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_trading, "STATE_FILE", tmp_path / "paper.json")
    monkeypatch.setattr(paper_trading, "hot_mid_hook", None)
    monkeypatch.setattr(paper_trading, "market_closed_hook", None)
    feed = DepthFeed()
    b = PaperBroker(100_000, feed, RiskEngine(RiskConfig(equity=100_000)))
    return b, feed


def open_buy(b, qty=100.0, price=100.0, stop=96.0, tp1=104.0, tp2=108.0):
    pos = asyncio.run(b.place_limit("TESTUSDT", "buy", qty, price=price,
                                    stop=stop, tp1=tp1, tp2=tp2))
    assert pos is not None, b.last_error
    return pos


def test_thin_exit_keeps_residual_open_and_releases_only_sold_cost_basis(broker):
    b, feed = broker
    pos = open_buy(b)
    feed.bids = [[95.0, 40.0]]

    asyncio.run(b._close(pos.id, "sl-hit"))

    assert pos.state == "PARTIAL"
    assert pos.filled_qty == pytest.approx(60.0)
    assert pos.size_quote == pytest.approx(6000.0)
    assert pos.size_usd == pytest.approx(6000.0)
    assert pos.closed_at is None
    assert pos.close_reason is None
    # Sold 40 at 95: proceeds 3800, exit fee 7.6, cost basis 4000.
    assert pos.realized_pnl == pytest.approx(-207.6)
    assert b.cash == pytest.approx(89_980.0 + 3_792.4)
    assert b.risk.open_heat == pytest.approx(0.24)  # 0.4% entry risk * 60% remaining

    event = b.orders[-1]
    assert event.kind == "partial"
    assert event.qty == pytest.approx(40.0)
    assert event.extra["remaining_open"] == pytest.approx(60.0)
    assert event.extra["requested_exit_qty"] == pytest.approx(100.0)


def test_residual_can_close_later_and_realized_pnl_accumulates(broker):
    b, feed = broker
    pos = open_buy(b)
    feed.bids = [[95.0, 40.0]]
    asyncio.run(b._close(pos.id, "sl-hit"))
    first_pnl = pos.realized_pnl

    feed.bids = [[96.0, 100.0]]
    asyncio.run(b._close(pos.id, "sl-hit"))

    assert pos.state == "CLOSED"
    assert pos.filled_qty == 0.0
    assert pos.size_quote == 0.0
    assert pos.size_usd == 0.0
    assert pos.closed_at is not None
    assert pos.close_reason == "sl-hit"
    # Second leg: 60 at 96, fee 11.52, cost basis 6000.
    assert pos.realized_pnl == pytest.approx(first_pnl - 251.52)
    assert b.risk.open_heat == 0.0
    assert [e.kind for e in b.orders[-2:]] == ["partial", "close"]


def test_no_exit_depth_rejects_without_closing_or_zeroing_residual(broker):
    b, feed = broker
    pos = open_buy(b)
    feed.bids = []

    asyncio.run(b._close(pos.id, "sl-hit"))

    assert pos.state == "OPEN"
    assert pos.filled_qty == pytest.approx(100.0)
    assert pos.realized_pnl == 0.0
    assert b.orders[-1].kind == "reject"
    assert b.orders[-1].extra["reason"] == "revert-failed"


def test_full_depth_exit_still_closes_in_one_event(broker):
    b, feed = broker
    pos = open_buy(b)
    feed.bids = [[99.0, 100.0]]

    asyncio.run(b._close(pos.id, "test"))

    assert pos.state == "CLOSED"
    assert pos.filled_qty == 0.0
    assert b.orders[-1].kind == "close"
    assert b.orders[-1].qty == pytest.approx(100.0)


def test_sell_side_partial_exit_uses_proportional_cost_basis(broker):
    b, feed = broker
    pos = asyncio.run(b.place_limit("TESTUSDT", "sell", 100.0, price=100.0,
                                    stop=104.0, tp1=96.0, tp2=92.0))
    assert pos is not None, b.last_error
    feed.asks = [[105.0, 30.0]]

    asyncio.run(b._close(pos.id, "sl-hit"))

    assert pos.state == "PARTIAL"
    assert pos.filled_qty == pytest.approx(70.0)
    # Bought back 30 at 105 against 100 entry: -150 price PnL, 6.3 fee.
    assert pos.realized_pnl == pytest.approx(-156.3)
    assert b.orders[-1].kind == "partial"


def test_order_projection_reports_partial_exit_as_partial_not_closed(broker):
    b, feed = broker
    pos = open_buy(b)
    feed.bids = [[95.0, 40.0]]
    asyncio.run(b._close(pos.id, "sl-hit"))

    row = main._order_rows(b)[0]
    assert row["state"] == "PARTIAL"
    assert row["position_state"] == "PARTIAL"
    assert row["entry_filled"] == pytest.approx(100.0)
    assert row["retained_exit_qty"] == pytest.approx(40.0)
    assert row["remaining_open"] == pytest.approx(60.0)
    assert row["closed_at"] is None
    assert row["pnl_usd"] == pytest.approx(-207.6, abs=1e-4)


def test_metrics_count_partial_exit_events(broker):
    b, feed = broker
    pos = open_buy(b)
    feed.bids = [[95.0, 40.0]]
    asyncio.run(b._close(pos.id, "sl-hit"))

    metrics = b.metrics()
    assert metrics["exit_events"] == 1
    assert metrics["openPositions"] == 1
    assert metrics["measurement"]["partial_exits_separate"] is True


def test_partial_exit_survives_restart_with_residual_risk(broker):
    b, feed = broker
    pos = open_buy(b)
    feed.bids = [[95.0, 40.0]]
    asyncio.run(b._close(pos.id, "sl-hit"))

    restored = PaperBroker(100_000, feed, RiskEngine(RiskConfig(equity=100_000)))
    restored_pos = restored.positions[pos.id]
    assert restored_pos.state == "PARTIAL"
    assert restored_pos.filled_qty == pytest.approx(60.0)
    assert restored.risk.open_heat == pytest.approx(0.24)


def test_evaluate_positions_retries_residual_on_next_tick(broker):
    b, feed = broker
    pos = open_buy(b, stop=96.0)
    feed.bids = [[95.0, 40.0]]
    feed.asks = [[95.0, 100.0]]

    asyncio.run(b.evaluate_positions())
    assert pos.state == "PARTIAL"
    assert pos.filled_qty == pytest.approx(60.0)

    feed.bids = [[95.0, 100.0]]
    asyncio.run(b.evaluate_positions())
    assert pos.state == "CLOSED"
    assert pos.filled_qty == 0.0
